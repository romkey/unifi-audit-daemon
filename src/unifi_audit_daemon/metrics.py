"""Prometheus metrics, in the text exposition format, without a library.

`Metrics` holds every series the daemon reports; the jobs and the HTTP
server update it as they run, and `GET /metrics` renders it. Gauges that
describe what is on disk are filled in at scrape time by collectors.
docs/metrics.md lists the metrics.

Nothing here reads the audit log's records or the contact sheets' images:
only counts, sizes and times.
"""

import contextlib
import math
import platform
import resource
import sys
import threading
import time
from collections.abc import Callable, Iterator, Sequence
from pathlib import Path

from unifi_audit_daemon import __version__

PREFIX = "unifi_audit_"
CONTENT_TYPE = "text/plain; version=0.0.4; charset=utf-8"

JOBS = ("sync", "contact_sheet")
JOB_BUCKETS = (0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30, 60, 120, 300, 600, 1800)
HTTP_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2.5, 5, 10, 30)

type Labels = tuple[str, ...]


def _number(value: float) -> str:
    if math.isinf(value):
        return "+Inf" if value > 0 else "-Inf"
    if math.isnan(value):
        return "NaN"
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return repr(value)


def _escape(value: str) -> str:
    return value.replace("\\", r"\\").replace("\n", r"\n").replace('"', r"\"")


def _label_text(names: Sequence[str], values: Sequence[str]) -> str:
    if not names:
        return ""
    pairs = ",".join(f'{n}="{_escape(v)}"' for n, v in zip(names, values, strict=True))
    return "{" + pairs + "}"


class _Metric:
    kind = ""

    def __init__(
        self, name: str, help: str, labels: Sequence[str], lock: threading.RLock
    ) -> None:
        self.name = name
        self.help = help
        self.labels = tuple(labels)
        self._lock = lock

    def _key(self, labels: dict[str, str]) -> Labels:
        if set(labels) != set(self.labels):
            raise ValueError(f"{self.name} takes labels {self.labels}, not {labels}")
        return tuple(str(labels[n]) for n in self.labels)

    def header(self) -> list[str]:
        return [f"# HELP {self.name} {self.help}", f"# TYPE {self.name} {self.kind}"]

    def samples(self) -> list[str]:
        raise NotImplementedError


class Counter(_Metric):
    kind = "counter"

    def __init__(
        self, name: str, help: str, labels: Sequence[str], lock: threading.RLock
    ) -> None:
        super().__init__(name, help, labels, lock)
        self._values: dict[Labels, float] = {}

    def inc(self, amount: float = 1, **labels: str) -> None:
        if amount < 0:
            raise ValueError("a counter only goes up")
        key = self._key(labels)
        with self._lock:
            self._values[key] = self._values.get(key, 0) + amount

    def set_total(self, value: float, **labels: str) -> None:
        """For a total something else keeps, like the kernel's CPU time."""
        key = self._key(labels)
        with self._lock:
            self._values[key] = value

    def get(self, **labels: str) -> float:
        with self._lock:
            return self._values.get(self._key(labels), 0)

    def samples(self) -> list[str]:
        return [
            f"{self.name}{_label_text(self.labels, k)} {_number(v)}"
            for k, v in sorted(self._values.items())
        ]


class Gauge(_Metric):
    kind = "gauge"

    def __init__(
        self, name: str, help: str, labels: Sequence[str], lock: threading.RLock
    ) -> None:
        super().__init__(name, help, labels, lock)
        self._values: dict[Labels, float] = {}

    def set(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            self._values[key] = float(value)

    def get(self, **labels: str) -> float | None:
        with self._lock:
            return self._values.get(self._key(labels))

    def only(self, value: float, **labels: str) -> None:
        """Set this series and drop every other, as for an _info metric
        whose labels changed."""
        key = self._key(labels)
        with self._lock:
            self._values = {key: float(value)}

    def samples(self) -> list[str]:
        return [
            f"{self.name}{_label_text(self.labels, k)} {_number(v)}"
            for k, v in sorted(self._values.items())
        ]


class Histogram(_Metric):
    kind = "histogram"

    def __init__(
        self,
        name: str,
        help: str,
        labels: Sequence[str],
        lock: threading.RLock,
        buckets: Sequence[float],
    ) -> None:
        super().__init__(name, help, labels, lock)
        self.buckets = tuple(sorted(buckets))
        # Per label set: count in each bucket (not cumulative), sum, count.
        self._values: dict[Labels, tuple[list[int], float, int]] = {}

    def observe(self, value: float, **labels: str) -> None:
        key = self._key(labels)
        with self._lock:
            counts, total, count = self._values.get(
                key, ([0] * len(self.buckets), 0.0, 0)
            )
            for i, bound in enumerate(self.buckets):
                if value <= bound:
                    counts[i] += 1
                    break
            self._values[key] = (counts, total + value, count + 1)

    def count(self, **labels: str) -> int:
        with self._lock:
            return self._values.get(self._key(labels), ([], 0.0, 0))[2]

    def samples(self) -> list[str]:
        lines = []
        names = (*self.labels, "le")
        for key, (counts, total, count) in sorted(self._values.items()):
            running = 0
            for bound, n in zip(self.buckets, counts, strict=True):
                running += n
                le = _label_text(names, (*key, _number(bound)))
                lines.append(f"{self.name}_bucket{le} {running}")
            le = _label_text(names, (*key, "+Inf"))
            lines.append(f"{self.name}_bucket{le} {count}")
            labels = _label_text(self.labels, key)
            lines.append(f"{self.name}_sum{labels} {_number(total)}")
            lines.append(f"{self.name}_count{labels} {count}")
        return lines


class Metrics:
    """Every metric the daemon exports. Safe to use from any thread."""

    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self.clock = clock
        self._lock = threading.RLock()
        self._metrics: list[_Metric] = []
        self._collectors: list[Callable[[], None]] = []

        g, c, h = self._gauge, self._counter, self._histogram

        self.build_info = g(
            "build_info",
            "Version of the daemon and Python.",
            ["version", "python_version"],
        )
        self.start_time = g(
            "start_time_seconds", "When the daemon started, in Unix time."
        )

        # Jobs: the sync and the contact sheet.
        self.job_runs = c(
            "job_runs_total",
            "Job runs finished, by outcome.",
            ["job", "trigger", "result"],
        )
        self.job_errors = c(
            "job_errors_total",
            "Failed job runs, by the kind of error.",
            ["job", "error"],
        )
        self.job_duration = h(
            "job_duration_seconds", "How long job runs took.", ["job"], JOB_BUCKETS
        )
        self.job_running = g("job_running", "1 while the job is running.", ["job"])
        self.job_last_success = g(
            "job_last_success_timestamp_seconds",
            "When the job last succeeded, in Unix time.",
            ["job"],
        )
        self.job_last_failure = g(
            "job_last_failure_timestamp_seconds",
            "When the job last failed, in Unix time.",
            ["job"],
        )
        self.job_next_run = g(
            "job_next_run_timestamp_seconds",
            "When the job next runs, in Unix time.",
            ["job"],
        )

        # The sync.
        self.events_fetched = c(
            "sync_events_fetched_total",
            "Events read from Protect, including ones already stored.",
        )
        self.events_stored = c(
            "sync_events_stored_total",
            "New events appended to the audit log, by Protect event type.",
            ["type"],
        )
        self.sync_last_added = g(
            "sync_last_added", "Events the last successful sync stored."
        )
        self.sync_cursor = g(
            "sync_cursor_timestamp_seconds",
            "How far Protect has been read, in Unix time.",
        )

        # Protect, as of the last sync.
        self.protect_info = g(
            "protect_console_info",
            "The Protect console the daemon reads.",
            ["name", "version"],
        )
        self.protect_users = g("protect_users", "Users Protect knows.")
        self.protect_cameras = g("protect_cameras", "Cameras Protect knows.")

        # The contact sheet.
        self.snapshots = c(
            "contact_sheet_snapshots_total",
            "Camera snapshots attempted for contact sheets, by outcome.",
            ["result"],
        )
        self.sheet_cameras = g(
            "contact_sheet_cameras",
            "Cameras on the latest contact sheet, by outcome.",
            ["result"],
        )
        self.contact_sheet_requests = c(
            "contact_sheet_requests_total", "Contact sheets requested over the API."
        )

        # On disk, at scrape time.
        self.audit_log_bytes = g("audit_log_bytes", "Size of the audit log files.")
        self.audit_log_files = g(
            "audit_log_files", "Audit log files (one per UTC day)."
        )
        self.contact_sheets_stored = g("contact_sheets", "Contact sheets kept.")
        self.contact_sheet_bytes = g(
            "contact_sheet_dir_bytes", "Size of everything in CONTACT_SHEET_DIR."
        )

        # HTTP API.
        self.http_requests = c(
            "http_requests_total",
            "HTTP requests answered.",
            ["method", "route", "status"],
        )
        self.http_duration = h(
            "http_request_duration_seconds",
            "How long HTTP requests took to answer.",
            ["route"],
            HTTP_BUCKETS,
        )

        # The process.
        self.cpu_seconds = c(
            "process_cpu_seconds_total", "CPU time used by the process.", ["mode"]
        )
        self.max_rss = g(
            "process_max_resident_memory_bytes", "Peak resident memory of the process."
        )
        self.open_fds = g("process_open_fds", "Open file descriptors.")
        self.threads = g("process_threads", "Python threads running.")
        self.uptime = g("uptime_seconds", "Seconds since the daemon started.")

        self._started = clock()
        self.build_info.set(
            1, version=__version__, python_version=platform.python_version()
        )
        self.start_time.set(self._started)
        # Zeroes, so rate() and absent() work before the first run.
        for job in JOBS:
            self.job_running.set(0, job=job)
            triggers = (
                ("scheduled", "manual") if job == "contact_sheet" else ("scheduled",)
            )
            for trigger in triggers:
                for result in ("success", "failure"):
                    self.job_runs.inc(0, job=job, trigger=trigger, result=result)
        for result in ("ok", "offline", "failed"):
            self.snapshots.inc(0, result=result)
        self.events_fetched.inc(0)
        self.contact_sheet_requests.inc(0)
        self.add_collector(self._collect_process)

    def _register[M: _Metric](self, metric: M) -> M:
        self._metrics.append(metric)
        return metric

    def _counter(self, name: str, help: str, labels: Sequence[str] = ()) -> Counter:
        return self._register(Counter(PREFIX + name, help, labels, self._lock))

    def _gauge(self, name: str, help: str, labels: Sequence[str] = ()) -> Gauge:
        return self._register(Gauge(PREFIX + name, help, labels, self._lock))

    def _histogram(
        self, name: str, help: str, labels: Sequence[str], buckets: Sequence[float]
    ) -> Histogram:
        return self._register(
            Histogram(PREFIX + name, help, labels, self._lock, buckets)
        )

    def add_collector(self, collect: Callable[[], None]) -> None:
        """Run `collect` before each render, to bring gauges up to date."""
        self._collectors.append(collect)

    @contextlib.contextmanager
    def job(self, job: str, trigger: str = "scheduled") -> Iterator[None]:
        """Time a job run and count its outcome. Exceptions pass through;
        a cancelled run (shutdown) isn't counted."""
        self.job_running.set(1, job=job)
        started = time.monotonic()
        result: str | None = None
        try:
            yield
            result = "success"
        except Exception as e:
            result = "failure"
            self.job_errors.inc(job=job, error=type(e).__name__)
            raise
        finally:
            self.job_running.set(0, job=job)
            if result is not None:
                self.job_duration.observe(time.monotonic() - started, job=job)
                self.job_runs.inc(job=job, trigger=trigger, result=result)
                last = (
                    self.job_last_success
                    if result == "success"
                    else self.job_last_failure
                )
                last.set(self.clock(), job=job)

    def _collect_process(self) -> None:
        usage = resource.getrusage(resource.RUSAGE_SELF)
        self.cpu_seconds.set_total(usage.ru_utime, mode="user")
        self.cpu_seconds.set_total(usage.ru_stime, mode="system")
        # ru_maxrss is in kilobytes on Linux, bytes on macOS.
        scale = 1 if sys.platform == "darwin" else 1024
        self.max_rss.set(usage.ru_maxrss * scale)
        with contextlib.suppress(OSError):  # not Linux
            self.open_fds.set(sum(1 for _ in Path("/proc/self/fd").iterdir()))
        self.threads.set(threading.active_count())
        self.uptime.set(self.clock() - self._started)

    def render(self) -> str:
        """Everything, in Prometheus's text format."""
        for collect in self._collectors:
            collect()
        lines: list[str] = []
        with self._lock:
            for metric in self._metrics:
                lines += metric.header()
                lines += metric.samples()
        return "\n".join(lines) + "\n"
