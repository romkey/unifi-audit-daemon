"""UniFi Protect audit log daemon: syncs Protect's event history to disk,
serves it over HTTP, and takes a nightly contact sheet of every camera."""

from importlib.metadata import version

__version__ = version("unifi-audit-daemon")
