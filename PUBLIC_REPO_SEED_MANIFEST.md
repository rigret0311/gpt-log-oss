# Public Repository Seed Manifest

This manifest defines the tracked-file boundary for the clean `rigret0311/gpt-log-oss` release history. It does not authorize publication, push, tagging, or release.

## INCLUDE

Only these candidate paths may seed the clean repository:

- `.github/workflows/ci.yml`
- `.gitignore`
- `LICENSE`
- `MANIFEST.in`
- `PUBLIC_REPO_SEED_MANIFEST.md`
- `README.md`
- `SECURITY.md`
- `pyproject.toml`
- `docs/minimal-runbook.md`
- `gptlog_core/*.py`
- `tests/__init__.py`
- `tests/test_core.py`
- `tests/test_release_metadata.py`
- `tests/test_schema_compatibility_regressions.py`
- `tests/test_schema_drift_p0.py`
- `tests/fixtures/**/*.json`

The JSON fixtures must remain synthetic and must pass the privacy/secret scan before every release seed is approved.

## EXCLUDE

Never migrate or seed:

- any other repository's Git history, branches, tags, submodules, reports, outputs, or internal documentation;
- real user exports, raw archives, SQLite databases, WAL/SHM files, logs, screenshots, credentials, API keys, account identifiers, private filesystem paths, home-directory paths, or personal records;
- private integrations, internal operational systems, unrelated predecessor components, or unrelated assets;
- unrelated local audit artifacts or files outside this candidate root.

## GENERATED

Generate locally or in CI, but do not seed as source:

- `dist/`
- `build/`
- `*.egg-info/`
- `__pycache__/`
- `*.pyc`
- `.venv/`
- `.work/`
- `*.sqlite3`, `*.sqlite3-shm`, and `*.sqlite3-wal`
- test/build logs, coverage files, caches, wheels, and source distributions

Release artifacts must be rebuilt from the reviewed clean commit and identified by new SHA-256 checksums.

## REVIEW_REQUIRED

Before this tracked tree is made public or released, review:

- the MIT copyright notice (`2026 GPT-Log contributors`);
- the repository and issue URLs for `rigret0311/gpt-log-oss`;
- GitHub Private Vulnerability Reporting enablement and readback;
- hosted Windows/Linux CI execution for the clean commit;
- the canonical `0.1.0` version on every package surface;
- the final seed against this exact allow-list and a second independent secret scanner;
- the final wheel/sdist contents and SHA-256 checksums.

Public visibility and all release actions remain separate Human Gates.
