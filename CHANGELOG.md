# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- `CONTACT_SHEET_DIR/index.html`, rewritten whenever a sheet is saved or
  pruned: shows the newest contact sheet, pages to older ones (buttons,
  arrow keys, `#YYYY-MM-DD` links), and lists them all. Static and
  relative, so any web server serving the directory serves the page.

## [0.2.0] - 2026-10-21

### Added

- `CONTACT_SHEET_TOKEN`: an optional second token that can only list and
  download contact sheets.
- A release badge in the README.

### Changed

- Contact sheets are stored in `CONTACT_SHEET_DIR` (`/contact-sheets` in
  the image, its own volume), apart from the audit log in `DATA_DIR`, so
  they can be served without exposing the log. Sheets from 0.1.0 are in
  `DATA_DIR/contact-sheets`; move them if you want to keep them.

### Fixed

- A release no longer fails after pushing its image when saving the build
  cache fails ("failed to reserve cache"), which stopped v0.1.0's GitHub
  Release being created.

## [0.1.0] - 2026-10-20

### Added

- Sync of UniFi Protect's event history through its private API into an
  append-only JSON Lines audit log, catching up after downtime.
- Nightly contact sheet of every camera at `CONTACT_SHEET_TIME` (default
  03:00 local), kept for `CONTACT_SHEET_RETENTION_DAYS`.
- HTTP API with bearer-token auth: status, events since a time, contact
  sheets, and taking a sheet on demand.
- `contrib/member-zone/unifi_protect_audit.py`, a Member Zone audit log
  program that pulls the log from the daemon.

### Fixed

- The `.env.example` lint check runs with the project's Python 3.14, so it
  can parse 3.14-only syntax (CI's runner Python is older).
