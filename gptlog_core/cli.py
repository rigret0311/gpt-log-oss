from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

from .db import connect_db, database_stats, ensure_schema, reindex_fts, search_messages, verify_db
from .importer import import_exports
from .parser import ExportFormatError
from .safety import LocalPathError, sanitize_diagnostic


def _emit(value: Any, *, as_json: bool) -> None:
    if is_dataclass(value):
        value = asdict(value)
    if as_json:
        print(json.dumps(value, ensure_ascii=False, sort_keys=True))
    elif isinstance(value, dict):
        print(" ".join(f"{key}={item}" for key, item in value.items()))
    elif isinstance(value, list):
        for item in value:
            print(json.dumps(item, ensure_ascii=False, sort_keys=True))
    else:
        print(value)


def _db_command(path: str, operation):
    conn = connect_db(path)
    try:
        ensure_schema(conn)
        return operation(conn)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="gptlog-core", description="Local-only ChatGPT export search core")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="initialize a local SQLite database")
    init.add_argument("--db", required=True)
    init.add_argument("--json", action="store_true")

    importer = sub.add_parser("import", help="atomically import a local JSON export")
    importer.add_argument("export")
    importer.add_argument("--db", required=True)
    importer.add_argument("--branch", choices=("active", "all"), default="active")
    importer.add_argument("--force", action="store_true", help="reprocess an already imported exact hash")
    importer.add_argument("--json", action="store_true")

    search = sub.add_parser("search", help="search message text with SQLite FTS5")
    search.add_argument("query")
    search.add_argument("--db", required=True)
    search.add_argument("--limit", type=int, default=20)
    search.add_argument("--json", action="store_true")

    stats = sub.add_parser("stats", help="show local database counts")
    stats.add_argument("--db", required=True)
    stats.add_argument("--json", action="store_true")

    verify = sub.add_parser("verify", help="verify SQLite, foreign keys, and FTS row coverage")
    verify.add_argument("--db", required=True)
    verify.add_argument("--json", action="store_true")

    reindex = sub.add_parser("reindex", help="atomically rebuild the FTS5 index")
    reindex.add_argument("--db", required=True)
    reindex.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "init":
            result = _db_command(args.db, lambda conn: database_stats(conn))
        elif args.command == "import":
            result = import_exports(
                args.export,
                args.db,
                branch=args.branch,
                force=args.force,
            )
        elif args.command == "search":
            result = _db_command(args.db, lambda conn: search_messages(conn, args.query, limit=args.limit))
        elif args.command == "stats":
            result = _db_command(args.db, database_stats)
        elif args.command == "verify":
            result = _db_command(args.db, verify_db)
        elif args.command == "reindex":
            result = _db_command(args.db, reindex_fts)
        else:
            raise AssertionError(f"unhandled command: {args.command}")
        _emit(result, as_json=args.json)
        if args.command == "verify" and not result.ok:
            return 3
        return 0
    except (ExportFormatError, LocalPathError, FileNotFoundError, ValueError, RuntimeError, sqlite3.Error) as exc:
        diagnostic_paths = {
            "INPUT_ROOT": getattr(args, "export", None),
            "DB_ROOT": getattr(args, "db", None),
        }
        print(f"ERROR: {sanitize_diagnostic(str(exc), diagnostic_paths)}", file=sys.stderr)
        return 1
