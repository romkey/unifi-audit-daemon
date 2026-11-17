import asyncio

import pytest

from unifi_audit_daemon import __version__
from unifi_audit_daemon.metrics import Metrics


class Clock:
    def __init__(self) -> None:
        self.now = 1_000.0

    def __call__(self) -> float:
        return self.now


def lines(metrics: Metrics) -> list[str]:
    return metrics.render().splitlines()


def test_renders_help_type_and_starting_values() -> None:
    text = Metrics(Clock()).render()
    assert text.endswith("\n")
    assert "# HELP unifi_audit_job_runs_total Job runs finished, by outcome." in text
    assert "# TYPE unifi_audit_job_runs_total counter" in text
    assert "# TYPE unifi_audit_job_duration_seconds histogram" in text
    assert "# TYPE unifi_audit_protect_users gauge" in text
    assert (
        'unifi_audit_job_runs_total{job="sync",trigger="scheduled",result="failure"} 0'
        in text
    )
    assert 'unifi_audit_job_running{job="contact_sheet"} 0' in text
    assert f'unifi_audit_build_info{{version="{__version__}",' in text
    assert "unifi_audit_start_time_seconds 1000\n" in text
    assert 'unifi_audit_contact_sheet_snapshots_total{result="offline"} 0' in text
    # Every sample line is "name{labels} value", parseable as a float.
    for line in text.splitlines():
        if not line.startswith("#"):
            float(line.rsplit(" ", 1)[1].replace("+Inf", "inf"))


def test_job_success() -> None:
    clock = Clock()
    metrics = Metrics(clock)
    clock.now = 2_000.5
    with metrics.job("sync"):
        assert metrics.job_running.get(job="sync") == 1
    assert metrics.job_running.get(job="sync") == 0
    assert metrics.job_runs.get(job="sync", trigger="scheduled", result="success") == 1
    assert metrics.job_last_success.get(job="sync") == 2_000.5
    assert metrics.job_last_failure.get(job="sync") is None
    text = metrics.render()
    assert 'unifi_audit_job_duration_seconds_bucket{job="sync",le="0.1"} 1' in text
    assert 'unifi_audit_job_duration_seconds_bucket{job="sync",le="+Inf"} 1' in text
    assert 'unifi_audit_job_duration_seconds_count{job="sync"} 1' in text
    assert "unifi_audit_job_last_success_timestamp_seconds" in text


def test_job_failure_passes_the_error_on() -> None:
    metrics = Metrics(Clock())
    with pytest.raises(TimeoutError), metrics.job("contact_sheet", "manual"):
        raise TimeoutError
    assert (
        metrics.job_runs.get(job="contact_sheet", trigger="manual", result="failure")
        == 1
    )
    assert metrics.job_errors.get(job="contact_sheet", error="TimeoutError") == 1
    assert metrics.job_last_failure.get(job="contact_sheet") == 1_000
    assert metrics.job_running.get(job="contact_sheet") == 0


def test_cancelled_job_is_not_counted() -> None:
    metrics = Metrics(Clock())

    async def cancelled() -> None:
        with metrics.job("sync"):
            raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(cancelled())
    assert metrics.job_running.get(job="sync") == 0
    assert metrics.job_runs.get(job="sync", trigger="scheduled", result="failure") == 0
    assert metrics.job_duration.count(job="sync") == 0


def test_histogram_buckets_are_cumulative() -> None:
    metrics = Metrics(Clock())
    for seconds in (0.001, 0.2, 0.2, 100):
        metrics.http_duration.observe(seconds, route="/metrics")
    text = metrics.render()
    route = 'route="/metrics"'
    assert (
        f'unifi_audit_http_request_duration_seconds_bucket{{{route},le="0.005"}} 1'
        in text
    )
    assert (
        f'unifi_audit_http_request_duration_seconds_bucket{{{route},le="0.25"}} 3'
        in text
    )
    assert (
        f'unifi_audit_http_request_duration_seconds_bucket{{{route},le="30"}} 3' in text
    )
    assert (
        f'unifi_audit_http_request_duration_seconds_bucket{{{route},le="+Inf"}} 4'
        in text
    )
    assert f"unifi_audit_http_request_duration_seconds_sum{{{route}}} 100.401" in text


def test_label_values_are_escaped() -> None:
    metrics = Metrics(Clock())
    metrics.protect_info.only(1, name='Main "NVR"\\\n', version="6.1")
    assert (
        'unifi_audit_protect_console_info{name="Main \\"NVR\\"\\\\\\n",version="6.1"} 1'
        in lines(metrics)
    )
    # only() drops the old series when the version changes.
    metrics.protect_info.only(1, name="NVR", version="6.2")
    info = [x for x in lines(metrics) if x.startswith("unifi_audit_protect_console")]
    assert info == ['unifi_audit_protect_console_info{name="NVR",version="6.2"} 1']


def test_values_are_formatted() -> None:
    metrics = Metrics(Clock())
    metrics.audit_log_bytes.set(float("inf"))
    metrics.contact_sheet_bytes.set(float("nan"))
    metrics.uptime.set(1.5)
    metrics.protect_users.set(-float("inf"))
    metrics._collectors.clear()  # keep the uptime set above
    text = metrics.render()
    assert "unifi_audit_audit_log_bytes +Inf" in text
    assert "unifi_audit_contact_sheet_dir_bytes NaN" in text
    assert "unifi_audit_uptime_seconds 1.5" in text
    assert "unifi_audit_protect_users -Inf" in text


def test_wrong_labels_and_negative_counts_are_refused() -> None:
    metrics = Metrics(Clock())
    with pytest.raises(ValueError, match="takes labels"):
        metrics.job_runs.inc(job="sync")
    with pytest.raises(ValueError, match="only goes up"):
        metrics.events_fetched.inc(-1)


def test_collectors_run_on_render() -> None:
    clock = Clock()
    metrics = Metrics(clock)
    calls: list[int] = []
    metrics.add_collector(lambda: calls.append(1))
    clock.now = 1_060
    text = metrics.render()
    assert calls == [1]
    assert "unifi_audit_uptime_seconds 60" in text
    assert 'unifi_audit_process_cpu_seconds_total{mode="user"}' in text
    assert "unifi_audit_process_threads " in text
    assert "unifi_audit_process_max_resident_memory_bytes " in text
