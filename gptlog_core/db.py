from __future__ import annotations

import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

from .safety import local_path


SCHEMA_VERSION = "1"


@dataclass(frozen=True, slots=True)
class VerificationResult:
    ok: bool
    integrity: str
    foreign_key_violations: int
    messages: int
    fts_rows: int
    missing_fts_rows: int
    orphan_fts_rows: int

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def connect_db(raw_path: str | Path) -> sqlite3.Connection:
    path = local_path(raw_path)
    if path.exists() and path.is_dir():
        raise ValueError(f"database path is a directory: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            title TEXT,
            created_at REAL,
            updated_at REAL,
            message_count INTEGER NOT NULL DEFAULT 0 CHECK(message_count >= 0),
            imported_at TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS messages (
            message_id TEXT PRIMARY KEY,
            conversation_id TEXT NOT NULL,
            parent_message_id TEXT,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at REAL,
            position INTEGER NOT NULL CHECK(position >= 0),
            content_sha256 TEXT NOT NULL CHECK(length(content_sha256) = 64),
            is_active INTEGER NOT NULL CHECK(is_active IN (0, 1)),
            imported_at TEXT NOT NULL,
            FOREIGN KEY(conversation_id) REFERENCES conversations(conversation_id) ON DELETE CASCADE
        );

        CREATE INDEX IF NOT EXISTS idx_messages_conversation_position
            ON messages(conversation_id, position);
        CREATE INDEX IF NOT EXISTS idx_messages_created_at
            ON messages(created_at);
        CREATE INDEX IF NOT EXISTS idx_messages_content_sha256
            ON messages(content_sha256);

        CREATE TABLE IF NOT EXISTS imported_exports (
            export_key TEXT PRIMARY KEY,
            sha256 TEXT NOT NULL CHECK(length(sha256) = 64),
            source_name TEXT NOT NULL,
            branch_mode TEXT NOT NULL CHECK(branch_mode IN ('active', 'all')),
            conversation_count INTEGER NOT NULL,
            message_count INTEGER NOT NULL,
            imported_at TEXT NOT NULL,
            UNIQUE(sha256, branch_mode)
        );

        CREATE INDEX IF NOT EXISTS idx_imported_exports_sha256
            ON imported_exports(sha256);
        """
    )
    try:
        conn.execute(
            """
            CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
                content,
                role UNINDEXED,
                conversation_id UNINDEXED,
                message_id UNINDEXED,
                tokenize='unicode61'
            )
            """
        )
    except sqlite3.OperationalError as exc:
        raise RuntimeError("SQLite with FTS5 support is required") from exc
    conn.executescript(
        """
        CREATE TRIGGER IF NOT EXISTS messages_ai AFTER INSERT ON messages BEGIN
            INSERT INTO messages_fts(rowid, content, role, conversation_id, message_id)
            VALUES(new.rowid, new.content, new.role, new.conversation_id, new.message_id);
        END;

        CREATE TRIGGER IF NOT EXISTS messages_ad AFTER DELETE ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.rowid;
        END;

        CREATE TRIGGER IF NOT EXISTS messages_au AFTER UPDATE ON messages BEGIN
            DELETE FROM messages_fts WHERE rowid = old.rowid;
            INSERT INTO messages_fts(rowid, content, role, conversation_id, message_id)
            VALUES(new.rowid, new.content, new.role, new.conversation_id, new.message_id);
        END;
        """
    )
    conn.execute(
        "INSERT INTO meta(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (SCHEMA_VERSION,),
    )
    conn.commit()


def verify_db(conn: sqlite3.Connection) -> VerificationResult:
    integrity_rows = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
    integrity = "ok" if integrity_rows == ["ok"] else "; ".join(integrity_rows)
    foreign_key_violations = len(conn.execute("PRAGMA foreign_key_check").fetchall())
    messages = int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0])
    fts_rows = int(conn.execute("SELECT COUNT(*) FROM messages_fts").fetchone()[0])
    missing = int(
        conn.execute(
            "SELECT COUNT(*) FROM messages m LEFT JOIN messages_fts f ON f.rowid=m.rowid WHERE f.rowid IS NULL"
        ).fetchone()[0]
    )
    orphan = int(
        conn.execute(
            "SELECT COUNT(*) FROM messages_fts f LEFT JOIN messages m ON m.rowid=f.rowid WHERE m.rowid IS NULL"
        ).fetchone()[0]
    )
    ok = integrity == "ok" and foreign_key_violations == 0 and messages == fts_rows and missing == 0 and orphan == 0
    return VerificationResult(ok, integrity, foreign_key_violations, messages, fts_rows, missing, orphan)


def reindex_fts(conn: sqlite3.Connection) -> VerificationResult:
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("DELETE FROM messages_fts")
        conn.execute(
            """
            INSERT INTO messages_fts(rowid, content, role, conversation_id, message_id)
            SELECT rowid, content, role, conversation_id, message_id FROM messages
            """
        )
        result = verify_db(conn)
        if not result.ok:
            raise RuntimeError(f"FTS verification failed after reindex: {result.as_dict()}")
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise


def database_stats(conn: sqlite3.Connection) -> dict[str, object]:
    return {
        "schema_version": conn.execute("SELECT value FROM meta WHERE key='schema_version'").fetchone()[0],
        "conversations": int(conn.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]),
        "messages": int(conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]),
        "active_messages": int(conn.execute("SELECT COUNT(*) FROM messages WHERE is_active=1").fetchone()[0]),
        "imported_exports": int(conn.execute("SELECT COUNT(*) FROM imported_exports").fetchone()[0]),
    }


def search_messages(conn: sqlite3.Connection, query: str, *, limit: int = 20) -> list[dict[str, object]]:
    cleaned = query.strip()
    if not cleaned:
        raise ValueError("search query must not be empty")
    if limit < 1 or limit > 100:
        raise ValueError("limit must be between 1 and 100")
    match_query = '"' + cleaned.replace('"', '""') + '"'
    rows = conn.execute(
        """
        SELECT
            m.message_id,
            m.conversation_id,
            c.title,
            m.role,
            m.created_at,
            m.position,
            m.is_active,
            snippet(messages_fts, 0, '[', ']', '…', 24) AS snippet,
            bm25(messages_fts) AS rank
        FROM messages_fts
        JOIN messages m ON m.rowid = messages_fts.rowid
        JOIN conversations c ON c.conversation_id = m.conversation_id
        WHERE messages_fts MATCH ?
        ORDER BY rank, COALESCE(m.created_at, 0) DESC, m.message_id
        LIMIT ?
        """,
        (match_query, limit),
    ).fetchall()
    return [dict(row) for row in rows]
