from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from gptlog_core.db import connect_db, database_stats, ensure_schema, search_messages
from gptlog_core.importer import import_exports
from gptlog_core.parser import normalize_conversations
from gptlog_core.safety import discover_json_exports


class SchemaCompatibilityRegressionTests(unittest.TestCase):
    def test_stable_ids_match_fixed_release_vector(self):
        payload = [
            {
                "id": "fixed-conversation-id",
                "messages": [
                    {
                        "id": "fixed-message-id",
                        "role": "user",
                        "content": "Fixed vector text",
                    }
                ],
            }
        ]
        conversation = normalize_conversations(payload)[0]
        self.assertEqual(conversation.conversation_id, "conv_61ab17ac895efc5dd468dc42b316c88b")
        self.assertEqual(
            conversation.messages[0].message_id,
            "msg_2c020776237d26b86127ff9f6f3ab1cf",
        )

    def test_unknown_content_type_does_not_promote_text_shaped_metadata(self):
        payload = [
            {
                "id": "unknown-content-text-key",
                "messages": [
                    {"id": "known", "role": "user", "content": "KNOWN_VISIBLE_TEXT"},
                    {
                        "id": "opaque",
                        "role": "assistant",
                        "content": {
                            "content_type": "future_widget_v100",
                            "text": "UNKNOWN_TEXT_SHAPED_METADATA",
                        },
                    },
                ],
            }
        ]
        with tempfile.TemporaryDirectory() as temp:
            temp_path = Path(temp)
            export = temp_path / "conversations.json"
            export.write_text(json.dumps(payload), encoding="utf-8")
            db_path = temp_path / "core.sqlite3"
            result = import_exports(export, db_path)
            self.assertEqual((result.conversations, result.messages), (1, 2))

            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(len(search_messages(conn, "KNOWN_VISIBLE_TEXT")), 1)
                self.assertEqual(search_messages(conn, "UNKNOWN_TEXT_SHAPED_METADATA"), [])
            finally:
                conn.close()

    def test_shape_preflight_keeps_legitimate_zero_message_conversation(self):
        with tempfile.TemporaryDirectory() as temp:
            export_dir = Path(temp) / "export"
            export_dir.mkdir()
            (export_dir / "empty-chat.json").write_text(
                json.dumps([{"id": "legitimate-empty", "title": "Empty", "messages": []}]),
                encoding="utf-8",
            )
            (export_dir / "account.json").write_text(
                json.dumps({"items": [{"id": "profile", "title": "Sidecar"}]}),
                encoding="utf-8",
            )
            self.assertEqual(
                [path.name for path in discover_json_exports(export_dir)],
                ["empty-chat.json"],
            )

            db_path = Path(temp) / "core.sqlite3"
            result = import_exports(export_dir, db_path)
            self.assertEqual((result.files_seen, result.conversations, result.messages), (1, 1, 0))
            conn = connect_db(db_path)
            try:
                ensure_schema(conn)
                self.assertEqual(database_stats(conn)["conversations"], 1)
            finally:
                conn.close()


if __name__ == "__main__":
    unittest.main()
