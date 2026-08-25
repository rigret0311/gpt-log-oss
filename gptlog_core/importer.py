from __future__ import annotations

import hashlib
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path

from .db import connect_db, ensure_schema, verify_db
from .parser import MAX_JSON_BYTES, ExportFormatError, parse_export_file
from .safety import discover_json_exports


@dataclass(frozen=True, slots=True)
class ImportStats:
    files_seen: int = 0
    files_imported: int = 0
    files_skipped_exact_sha: int = 0
    conversations: int = 0
    messages: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _sha256(path: Path) -> str:
    if path.stat().st_size > MAX_JSON_BYTES:
        raise ExportFormatError(f"JSON exceeds the {MAX_JSON_BYTES}-byte safety ceiling: {path.name}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _export_key(sha256: str, branch: str) -> str:
    return f"{sha256}:{branch}"


def _reject_conversation_mutation(conn, conversation) -> None:
    existing = conn.execute(
        "SELECT title, created_at, updated_at FROM conversations WHERE conversation_id=?",
        (conversation.conversation_id,),
    ).fetchone()
    if existing is None:
        return
    incoming = (conversation.title, conversation.created_at, conversation.updated_at)
    stored = (existing["title"], existing["created_at"], existing["updated_at"])
    if stored != incoming:
        raise ValueError(f"source mutation detected for conversation_id={conversation.conversation_id}")


def _reject_message_mutation(conn, message) -> None:
    existing = conn.execute(
        """
        SELECT conversation_id, parent_message_id, role, content_sha256, created_at
        FROM messages WHERE message_id=?
        """,
        (message.message_id,),
    ).fetchone()
    if existing is None:
        return
    incoming = (
        message.conversation_id,
        message.parent_message_id,
        message.role,
        message.content_sha256,
        message.created_at,
    )
    stored = (
        existing["conversation_id"],
        existing["parent_message_id"],
        existing["role"],
        existing["content_sha256"],
        existing["created_at"],
    )
    if stored != incoming:
        raise ValueError(f"source mutation detected for message_id={message.message_id}")


def import_exports(
    export_path: str | Path,
    db_path: str | Path,
    *,
    branch: str = "active",
    force: bool = False,
) -> ImportStats:
    if branch not in {"active", "all"}:
        raise ValueError("branch must be 'active' or 'all'")
    files = discover_json_exports(export_path)
    inputs = [(path, _sha256(path)) for path in files]
    conn = connect_db(db_path)
    try:
        ensure_schema(conn)
        existing = {
            str(row[0])
            for row in conn.execute(
                "SELECT export_key FROM imported_exports WHERE export_key IN ({})".format(
                    ",".join("?" for _ in inputs)
                ),
                [_export_key(digest, branch) for _, digest in inputs],
            )
        }
        pending = [
            (path, digest)
            for path, digest in inputs
            if force or _export_key(digest, branch) not in existing
        ]
        skipped = len(inputs) - len(pending)
        if not pending:
            return ImportStats(files_seen=len(inputs), files_skipped_exact_sha=skipped)

        imported_at = datetime.now(timezone.utc).isoformat()
        conversations_count = 0
        messages_count = 0
        conn.execute("BEGIN IMMEDIATE")
        try:
            for path, digest in pending:
                conversations = parse_export_file(path, branch=branch)
                file_messages = 0
                for conversation in conversations:
                    _reject_conversation_mutation(conn, conversation)
                    conn.execute(
                        """
                        INSERT INTO conversations(
                            conversation_id, title, created_at, updated_at, message_count, imported_at
                        ) VALUES(?, ?, ?, ?, ?, ?)
                        ON CONFLICT(conversation_id) DO UPDATE SET
                            title=COALESCE(excluded.title, conversations.title),
                            created_at=COALESCE(conversations.created_at, excluded.created_at),
                            updated_at=COALESCE(excluded.updated_at, conversations.updated_at),
                            message_count=MAX(conversations.message_count, excluded.message_count),
                            imported_at=excluded.imported_at
                        """,
                        (
                            conversation.conversation_id,
                            conversation.title,
                            conversation.created_at,
                            conversation.updated_at,
                            len(conversation.messages),
                            imported_at,
                        ),
                    )
                    for message in conversation.messages:
                        _reject_message_mutation(conn, message)
                        conn.execute(
                            """
                            INSERT INTO messages(
                                message_id, conversation_id, parent_message_id, role, content,
                                created_at, position, content_sha256, is_active, imported_at
                            ) VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            ON CONFLICT(message_id) DO UPDATE SET
                                conversation_id=excluded.conversation_id,
                                parent_message_id=excluded.parent_message_id,
                                role=excluded.role,
                                content=excluded.content,
                                created_at=COALESCE(excluded.created_at, messages.created_at),
                                position=excluded.position,
                                content_sha256=excluded.content_sha256,
                                is_active=excluded.is_active,
                                imported_at=excluded.imported_at
                            """,
                            (
                                message.message_id,
                                message.conversation_id,
                                message.parent_message_id,
                                message.role,
                                message.content,
                                message.created_at,
                                message.position,
                                message.content_sha256,
                                int(message.is_active),
                                imported_at,
                            ),
                        )
                        file_messages += 1
                conn.execute(
                    """
                    INSERT INTO imported_exports(
                        export_key, sha256, source_name, branch_mode,
                        conversation_count, message_count, imported_at
                    ) VALUES(?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(export_key) DO UPDATE SET
                        source_name=excluded.source_name,
                        conversation_count=excluded.conversation_count,
                        message_count=excluded.message_count,
                        imported_at=excluded.imported_at
                    """,
                    (
                        _export_key(digest, branch),
                        digest,
                        path.name,
                        branch,
                        len(conversations),
                        file_messages,
                        imported_at,
                    ),
                )
                conversations_count += len(conversations)
                messages_count += file_messages

            verification = verify_db(conn)
            if not verification.ok:
                raise RuntimeError(f"database verification failed before commit: {verification.as_dict()}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

        return ImportStats(
            files_seen=len(inputs),
            files_imported=len(pending),
            files_skipped_exact_sha=skipped,
            conversations=conversations_count,
            messages=messages_count,
        )
    finally:
        conn.close()
