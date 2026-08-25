# Minimal local runbook

## Preconditions

- Work from the extracted `gpt-log-core` directory.
- Use Python 3.11 or newer with SQLite FTS5.
- Keep real exports outside this source tree.
- Do not use a URL, URI, UNC share, or symlink as input.

## 1. Test the candidate

```powershell
python -m unittest discover -s tests -v
```

Expected: all tests pass, including fail-closed topology/content handling, atomic rollback, exact-hash skip, same-payload idempotency, changed-payload conflict rejection, distinct identical text, canonical Unicode hashing, FTS repair, and blocked-socket execution.

## 2. Create and import a synthetic database

```powershell
python -m gptlog_core init --db .work/gptlog.sqlite3 --json
python -m gptlog_core import tests/fixtures/synthetic_chatgpt_export.json --db .work/gptlog.sqlite3 --branch active --json
```

Re-run the import. Expected: `files_skipped_exact_sha` is `1`.

## 3. Search and verify

```powershell
python -m gptlog_core search "SQLite FTS5" --db .work/gptlog.sqlite3 --json
python -m gptlog_core stats --db .work/gptlog.sqlite3 --json
python -m gptlog_core verify --db .work/gptlog.sqlite3 --json
```

Expected: search returns a synthetic message and verify returns `"ok": true`.

## 4. Repair only the FTS projection if verification reports coverage drift

```powershell
python -m gptlog_core reindex --db .work/gptlog.sqlite3 --json
python -m gptlog_core verify --db .work/gptlog.sqlite3 --json
```

`reindex` is atomic and does not alter normalized message rows.

## Stop conditions

Stop and keep the candidate local if any test fails, FTS5 is unavailable, verification is not `ok`, the input contains unexpected private material, or a public release/license decision has not been approved.
