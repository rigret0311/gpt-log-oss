# GPT-Log

Current source version: `0.1.0`. No package has been uploaded to a public registry.

This small Python CLI imports local ChatGPT JSON exports into SQLite, normalizes conversations/messages, and searches message text through FTS5. Runtime code uses the Python standard library only and contains no network client.

Repository: <https://github.com/rigret0311/gpt-log-oss>

## Included scope

- ChatGPT `mapping` exports and flat `messages` exports
- normalized conversation/message model
- explicit `active` branch or `all` branches parsing
- fail-closed graph validation (missing nodes, parent/child mismatch, cycles), always enabled
- SQLite + FTS5
- exact SHA-256 skip for the same file and import mode
- stable source-derived IDs; identical payloads are idempotent and conflicting payloads fail closed
- identical text retained when source message IDs differ
- one transaction for every file in one import command; any error rolls the command back
- `init`, `import`, `search`, `stats`, `verify`, and `reindex` commands
- synthetic-only tests and a pinned Windows/Linux CI workflow
- one explicit JSON file or direct-child JSON files in one explicit directory

## Explicitly excluded

Embeddings, unrelated integrations, private/personal assets, remote APIs, telemetry, complex migration, and backup/restore are not included.

## Install

From a reviewed source checkout:

```console
python -m pip install .
gptlog-core --help
```

After a release wheel is built and its checksum is verified:

```console
python -m pip install ./dist/gptlog_core-0.1.0-py3-none-any.whl
```

No package has been uploaded to a public registry.

## Usage and source validation

Python 3.11+ with SQLite FTS5 is required. From this directory:

```powershell
python -m unittest discover -s tests -v
python -m gptlog_core import tests/fixtures/synthetic_chatgpt_export.json --db .work/gptlog.sqlite3 --branch active --json
python -m gptlog_core search "SQLite FTS5" --db .work/gptlog.sqlite3 --json
python -m gptlog_core verify --db .work/gptlog.sqlite3 --json
```

The input may be one local JSON file or one explicitly selected local directory. Directory discovery is limited to direct-child JSON files and is not recursive. Direct ZIP import is not supported; extract an export first, then select its JSON file or directory.

Use `--branch all` to retain messages from every branch. Topology validation is always fail-closed; there is no bypass flag. The same SHA-256 is skipped within the selected branch mode, so an operator can intentionally import both `active` and `all` views.

Only a basename and SHA-256 are stored for an imported file. Absolute input paths are not persisted. URLs, URI inputs, UNC shares, and symbolic-link inputs are rejected.

## Safety boundary

Do not copy real exports, databases, logs, credentials, cloud object identifiers, screenshots, or personal paths into this source tree. CI must use only `tests/fixtures/synthetic_chatgpt_export.json`. The runtime never sends data anywhere, but the operator remains responsible for filesystem access and retention.

## Known limitations

- Python 3.11 or newer and a Python build with SQLite FTS5 are required.
- Direct ZIP import is not supported.
- Input discovery is limited to one explicit file or direct-child JSON files in one explicit directory.
- Search is local SQLite FTS5 keyword search. Embeddings, semantic search, remote APIs, cloud sync, backup/restore, and unrelated integrations are outside the `0.1.0` scope.

## Security

Do not post vulnerability details or private exports in a public Issue. Use GitHub Private Vulnerability Reporting only when the repository shows the **Report a vulnerability** option, as described in [SECURITY.md](SECURITY.md). No public security email is provided.

Issue tracker: <https://github.com/rigret0311/gpt-log-oss/issues>

## License

GPT-Log is licensed under the [MIT License](LICENSE).

See `docs/minimal-runbook.md` for the bounded verification flow.
