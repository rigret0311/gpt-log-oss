from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class NormalizedMessage:
    message_id: str
    conversation_id: str
    parent_message_id: str | None
    role: str
    content: str
    created_at: float | None
    position: int
    content_sha256: str
    is_active: bool


@dataclass(frozen=True, slots=True)
class NormalizedConversation:
    conversation_id: str
    title: str | None
    created_at: float | None
    updated_at: float | None
    messages: tuple[NormalizedMessage, ...]
