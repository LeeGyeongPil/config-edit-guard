# Changelog

All notable changes to this project are documented in this file.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-13

### Added
- `locate`, `set` and `verify` commands in `skills/config-edit-guard/scripts/config_edit_guard.py`.
- YAML subset scanner with exact value spans, merge keys, anchors, tags and several documents; Markdown front matter.
- JSON/JSONC, TOML (Python 3.11+) and XML (`.csproj`, `pom.xml`, ...) position scanners.
- `set` keeps comments, quote style, line endings and BOM, quotes values that YAML 1.1 would misread, and saves only after an in-memory self-check.
- `verify` compares changed paths with expected paths (alias propagation reported separately) and checks comments, lines, quotes, key order, anchors, duplicate keys, YAML 1.1 traps, line endings, final newline and BOM.
- English and Korean output (`--lang`, `CONFIG_EDIT_GUARD_LANG`, `LC_ALL`, `LC_MESSAGES`, `LANG`), including argparse labels.
- Evaluation set (trigger, near-miss, far-miss and task cases with fixtures), tests, CI and plugin manifests.
