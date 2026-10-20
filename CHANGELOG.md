# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Sync of UniFi Protect's event history through its private API into an
  append-only JSON Lines audit log, catching up after downtime.
- Nightly contact sheet of every camera at `CONTACT_SHEET_TIME` (default
  03:00 local), kept for `CONTACT_SHEET_RETENTION_DAYS`.
- HTTP API with bearer-token auth: status, events since a time, contact
  sheets, and taking a sheet on demand.
- `contrib/member-zone/unifi_protect_audit.py`, a Member Zone audit log
  program that pulls the log from the daemon.
