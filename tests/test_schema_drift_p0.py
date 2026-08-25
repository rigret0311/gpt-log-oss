from __future__ import annotations

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from gptlog_core.db import connect_db, database_stats, ensure_schema, search_messages, verify_db
from gptlog_core.importer import import_exports
from gptlog_core.parser import ExportFormatError
from gptlog_core.safety import discover_json_exports


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "schema_drift_p0"


class SchemaDriftP0Tests(unittest.TestCase):
    def test_p0_01_current_node_excludes_inactive_branch_from_search(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            result = import_exports(FIXTURES / "branched_current_node.json", db_path, branch="active")
            self.assertEqual((result.conversations, result.messages), (1, 2))

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                current = search_messages(conn, "CURRENT_BRANCH_MARKER")
                inactive = search_messages(conn, "INACTIVE_BRANCH_MARKER")
                self.assertEqual(len(current), 1)
                self.assertEqual(current[0]["conversation_id"], conn.execute(
                    "SELECT conversation_id FROM conversations"
                ).fetchone()[0])
                self.assertEqual(inactive, [])
                self.assertTrue(verify_db(conn).ok)
            finally:
                conn.close()

    def test_p0_02_numbered_shards_are_complete_without_legacy_or_sidecar_duplication(self):
        fixture_dir = FIXTURES / "sharded_export"
        self.assertEqual(
            [path.name for path in discover_json_exports(fixture_dir)],
            ["conversations-001.json", "conversations-002.json"],
        )
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            result = import_exports(fixture_dir, db_path, branch="active")
            self.assertEqual((result.files_seen, result.files_imported), (2, 2))
            self.assertEqual((result.conversations, result.messages), (2, 2))

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(database_stats(conn)["conversations"], 2)
                self.assertEqual(len(search_messages(conn, "SHARD_ONE_MARKER")), 1)
                self.assertEqual(len(search_messages(conn, "SHARD_TWO_MARKER")), 1)
                self.assertEqual(search_messages(conn, "LEGACY_DUPLICATE_MARKER"), [])
                self.assertEqual(search_messages(conn, "SIDECAR_MARKER"), [])
            finally:
                conn.close()

    def test_p0_03_conflicting_id_aliases_are_observable(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            with self.assertRaisesRegex(
                (ExportFormatError, ValueError),
                r"(?i)(id.*conversation_id|conversation_id.*id|conflict)",
            ):
                import_exports(FIXTURES / "identity_conflict.json", db_path, branch="active")

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(database_stats(conn)["conversations"], 0)
                self.assertEqual(database_stats(conn)["messages"], 0)
                self.assertEqual(database_stats(conn)["imported_exports"], 0)
            finally:
                conn.close()

    def test_p0_04_asset_pointer_metadata_is_not_search_text(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            import_exports(FIXTURES / "multimodal_assets.json", db_path, branch="active")
            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(len(search_messages(conn, "VISIBLE_ASSET_MESSAGE")), 1)
                self.assertEqual(search_messages(conn, "PRIVATE_ASSET_METADATA_MARKER"), [])
                self.assertEqual(search_messages(conn, "ASSET_POINTER_URI_MARKER"), [])
            finally:
                conn.close()

    def test_p0_05_unknown_content_type_keeps_conversation_without_flattening_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            result = import_exports(FIXTURES / "unknown_content_type.json", db_path, branch="active")
            self.assertEqual((result.conversations, result.messages), (1, 2))
            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(len(search_messages(conn, "KNOWN_TEXT_BEFORE_UNKNOWN")), 1)
                self.assertEqual(search_messages(conn, "OPAQUE_METADATA_MARKER"), [])
                self.assertTrue(verify_db(conn).ok)
            finally:
                conn.close()

    def test_p0_06_truncated_shard_rolls_back_without_partial_success(self):
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            db_path = temp_path / "core.sqlite3"
            import_exports(FIXTURES / "forward_compatibility.json", db_path, branch="active")

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                before = database_stats(conn)
            finally:
                conn.close()

            export_dir = temp_path / "partial_export"
            export_dir.mkdir()
            shutil.copyfile(
                FIXTURES / "sharded_export" / "conversations-001.json",
                export_dir / "conversations-000.json",
            )
            shutil.copyfile(
                FIXTURES / "truncated_partial.json",
                export_dir / "conversations-001.json",
            )
            with self.assertRaises(json.JSONDecodeError):
                import_exports(export_dir, db_path, branch="active")

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(database_stats(conn), before)
                self.assertEqual(search_messages(conn, "SHARD_ONE_MARKER"), [])
                self.assertTrue(verify_db(conn).ok)
            finally:
                conn.close()

    def test_p0_07_sidecars_are_not_discovered_as_conversations(self):
        fixture_dir = FIXTURES / "sidecar_directory"
        self.assertEqual(
            [path.name for path in discover_json_exports(fixture_dir)],
            ["conversations.json"],
        )
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            result = import_exports(fixture_dir, db_path, branch="active")
            self.assertEqual((result.files_seen, result.conversations, result.messages), (1, 1, 1))
            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM conversations WHERE message_count=0"
                ).fetchone()[0], 0)
                self.assertEqual(len(search_messages(conn, "REAL_CONVERSATION_MARKER")), 1)
            finally:
                conn.close()

    def test_p0_08_unknown_fields_preserve_known_fields_and_do_not_flatten_metadata(self):
        with tempfile.TemporaryDirectory() as temp:
            db_path = Path(temp) / "core.sqlite3"
            result = import_exports(FIXTURES / "forward_compatibility.json", db_path, branch="active")
            self.assertEqual((result.conversations, result.messages), (1, 1))
            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                row = conn.execute(
                    "SELECT c.title, m.role, m.content FROM conversations c JOIN messages m USING(conversation_id)"
                ).fetchone()
                self.assertEqual(tuple(row), ("Forward compatible", "assistant", "KNOWN_FORWARD_TEXT"))
                self.assertEqual(len(search_messages(conn, "KNOWN_FORWARD_TEXT")), 1)
                self.assertEqual(search_messages(conn, "UNKNOWN_FIELD_MARKER"), [])
                self.assertTrue(verify_db(conn).ok)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
