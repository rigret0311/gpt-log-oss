from __future__ import annotations

import ast
import contextlib
import io
import json
import shutil
import socket
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace
from pathlib import Path
from unittest import mock

from gptlog_core.cli import main
from gptlog_core.db import connect_db, database_stats, ensure_schema, reindex_fts, search_messages, verify_db
from gptlog_core.importer import import_exports
from gptlog_core.parser import MAX_JSON_BYTES, ExportFormatError, TopologyError, normalize_conversations, parse_export_file
from gptlog_core.safety import LocalPathError, discover_json_exports, sanitize_diagnostic


ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "tests" / "fixtures" / "synthetic_chatgpt_export.json"


class ParserTests(unittest.TestCase):
    def test_active_and_all_branches_are_explicit(self):
        active = parse_export_file(FIXTURE, branch="active")
        all_branches = parse_export_file(FIXTURE, branch="all")
        self.assertEqual([len(item.messages) for item in active], [4, 2])
        self.assertEqual([len(item.messages) for item in all_branches], [6, 2])
        self.assertEqual(sum(message.content == "Use SQLite FTS5 locally." for message in active[0].messages), 1)
        self.assertEqual(sum(message.content == "Use SQLite FTS5 locally." for message in all_branches[0].messages), 2)

    def test_strict_topology_rejects_mismatch(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        payload[0]["mapping"]["assistant-current"]["parent"] = "missing-node"
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "invalid.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaises(TopologyError):
                parse_export_file(path, branch="active")

    def test_message_ids_are_scoped_to_conversation(self):
        payload = [
            {"id": "conversation-a", "messages": [{"id": "reused-id", "role": "user", "content": "one"}]},
            {"id": "conversation-b", "messages": [{"id": "reused-id", "role": "user", "content": "two"}]},
        ]
        conversations = normalize_conversations(payload)
        self.assertNotEqual(conversations[0].messages[0].message_id, conversations[1].messages[0].message_id)

    def test_canonical_text_normalizes_newlines_and_nfc_without_trimming(self):
        decomposed = [{"id": "conv", "messages": [{"id": "msg", "role": "user", "content": "  Cafe\u0301\r\nline  "}]}]
        composed = [{"id": "conv", "messages": [{"id": "msg", "role": "user", "content": "  Caf\u00e9\nline  "}]}]
        first = normalize_conversations(decomposed)[0].messages[0]
        second = normalize_conversations(composed)[0].messages[0]
        self.assertEqual(first.content, "  Caf\u00e9\nline  ")
        self.assertEqual(first.content, second.content)
        self.assertEqual(first.content_sha256, second.content_sha256)

    def test_json_size_ceiling_fails_before_read(self):
        with mock.patch.object(Path, "stat", return_value=SimpleNamespace(st_size=MAX_JSON_BYTES + 1)):
            with self.assertRaises(ExportFormatError):
                parse_export_file(FIXTURE)

    def test_missing_current_node_with_multiple_leaves_fails_closed(self):
        payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        del payload[0]["current_node"]
        with self.assertRaises(TopologyError):
            normalize_conversations(payload, branch="all")


class ImportTests(unittest.TestCase):
    def test_same_source_ids_and_payload_are_idempotent_across_file_hashes(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            export = Path(temp) / "conversations.json"
            payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
            export.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            import_exports(export, db_path, branch="all")
            export.write_text(json.dumps(payload, separators=(",", ":")), encoding="utf-8")
            second = import_exports(export, db_path, branch="all")
            self.assertEqual(second.files_imported, 1)

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(database_stats(conn)["messages"], 8)
                self.assertEqual(database_stats(conn)["imported_exports"], 2)
                self.assertTrue(verify_db(conn).ok)
            finally:
                conn.close()

    def test_exact_sha_skip_distinct_text_and_fts(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            first = import_exports(FIXTURE, db_path, branch="all")
            self.assertEqual(first.files_imported, 1)
            self.assertEqual(first.messages, 8)

            second = import_exports(FIXTURE, db_path, branch="all")
            self.assertEqual(second.files_skipped_exact_sha, 1)
            self.assertEqual(second.files_imported, 0)

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(database_stats(conn)["messages"], 8)
                repeated = conn.execute(
                    "SELECT COUNT(*) FROM messages WHERE content='Keep this repeated sentence.'"
                ).fetchone()[0]
                self.assertEqual(repeated, 2)
                self.assertEqual(len(search_messages(conn, "SQLite FTS5")), 2)
                self.assertTrue(verify_db(conn).ok)
            finally:
                conn.close()

    def test_changed_export_rejects_same_source_ids_and_rolls_back(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            changed = Path(temp) / "changed.json"
            payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
            changed.write_text(json.dumps(payload), encoding="utf-8")
            import_exports(changed, db_path, branch="all")
            payload[1]["messages"][1]["content"] = "No network client is ever required."
            changed.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "source mutation"):
                import_exports(changed, db_path, branch="all")

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(database_stats(conn)["messages"], 8)
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM messages WHERE content LIKE '%ever required%'").fetchone()[0],
                    0,
                )
                self.assertEqual(database_stats(conn)["imported_exports"], 1)
                self.assertTrue(verify_db(conn).ok)
            finally:
                conn.close()

    def test_multi_file_import_rolls_back_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            export_dir = Path(temp) / "export"
            export_dir.mkdir()
            shutil.copyfile(FIXTURE, export_dir / "conversations-000.json")
            invalid = json.loads(FIXTURE.read_text(encoding="utf-8"))
            invalid[0]["mapping"]["assistant-current"]["parent"] = "missing-node"
            (export_dir / "conversations-001.json").write_text(json.dumps(invalid), encoding="utf-8")
            db_path = Path(temp) / "core.sqlite3"
            with self.assertRaises(TopologyError):
                import_exports(export_dir, db_path, branch="active")

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                stats = database_stats(conn)
                self.assertEqual(stats["conversations"], 0)
                self.assertEqual(stats["messages"], 0)
                self.assertEqual(stats["imported_exports"], 0)
            finally:
                conn.close()

    def test_unsupported_content_rolls_back_atomically(self):
        with tempfile.TemporaryDirectory() as temp:
            export_dir = Path(temp) / "export"
            export_dir.mkdir()
            shutil.copyfile(FIXTURE, export_dir / "conversations-000.json")
            unsupported = [
                {
                    "id": "unsupported-conversation",
                    "messages": [
                        {
                            "id": "unsupported-message",
                            "role": "assistant",
                            "content": {"content_type": "image", "asset_pointer": "synthetic-only"},
                        }
                    ],
                }
            ]
            (export_dir / "conversations-001.json").write_text(json.dumps(unsupported), encoding="utf-8")
            db_path = Path(temp) / "core.sqlite3"
            with self.assertRaises(ExportFormatError):
                import_exports(export_dir, db_path, branch="all")

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(database_stats(conn)["messages"], 0)
                self.assertEqual(database_stats(conn)["imported_exports"], 0)
            finally:
                conn.close()

    def test_reindex_repairs_fts_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            import_exports(FIXTURE, db_path, branch="all")
            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                conn.execute("DELETE FROM messages_fts WHERE rowid=(SELECT MIN(rowid) FROM messages_fts)")
                conn.commit()
                self.assertFalse(verify_db(conn).ok)
                self.assertTrue(reindex_fts(conn).ok)
            finally:
                conn.close()


class SafetyAndCliTests(unittest.TestCase):
    def test_cli_error_redacts_absolute_input_path(self):
        with tempfile.TemporaryDirectory() as temp:
            missing = Path(temp) / "private" / "missing.json"
            db_path = Path(temp) / "core.sqlite3"
            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                exit_code = main(["import", str(missing), "--db", str(db_path)])
            diagnostic = stderr.getvalue()
            self.assertEqual(exit_code, 1)
            self.assertIn("<INPUT_ROOT>/missing.json", diagnostic)
            self.assertNotIn(str(missing), diagnostic)
            self.assertNotIn(str(Path(temp)), diagnostic)

    def test_diagnostic_sanitizer_redacts_user_home_prefixes(self):
        diagnostic = sanitize_diagnostic(
            r"failed at C:\Users\alice\private\export.json and /Users/bob/private/export.json",
            {},
        )
        self.assertNotIn(r"C:\Users\alice", diagnostic)
        self.assertNotIn("/Users/bob", diagnostic)
        self.assertEqual(diagnostic.count("<HOME>"), 2)

    def test_network_paths_are_rejected(self):
        with self.assertRaises(LocalPathError):
            discover_json_exports("https://example.invalid/export.json")
        with self.assertRaises(LocalPathError):
            discover_json_exports(r"\\server\share\export.json")

    def test_runtime_has_no_network_client_imports(self):
        forbidden = {"socket", "requests", "httpx", "urllib", "http.client", "ftplib"}
        found: set[str] = set()
        for source in (ROOT / "gptlog_core").glob("*.py"):
            tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name for alias in node.names if alias.name in forbidden)
                elif isinstance(node, ast.ImportFrom) and node.module in forbidden:
                    found.add(str(node.module))
        self.assertEqual(found, set())

    def test_import_and_search_work_when_socket_creation_is_blocked(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(socket, "socket", side_effect=AssertionError("network used")):
            db_path = str(Path(temp) / "core.sqlite3")
            self.assertEqual(main(["import", str(FIXTURE), "--db", db_path, "--branch", "all", "--json"]), 0)
            self.assertEqual(main(["search", "offline", "--db", db_path, "--json"]), 0)
            self.assertEqual(main(["verify", "--db", db_path, "--json"]), 0)


if __name__ == "__main__":
    unittest.main()
