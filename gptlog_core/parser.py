from __future__ import annotations

import hashlib
import json
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from .models import NormalizedConversation, NormalizedMessage


MAX_JSON_BYTES = 256 * 1024 * 1024

_TEXT_CONTENT_TYPES = {"text", "multimodal_text"}
_KNOWN_UNSUPPORTED_CONTENT_TYPES = {"image"}


class ExportFormatError(ValueError):
    """Raised when an export does not contain recognizable conversations."""


class TopologyError(ExportFormatError):
    """Raised when a conversation graph is unsafe or ambiguous."""


def _canonical_hash(value: Any) -> str:
    payload = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _stable_id(kind: str, namespace: str, external_id: object | None, fallback: Any) -> str:
    identity = str(external_id).strip() if external_id is not None else ""
    seed = {"namespace": namespace, "external_id": identity} if identity else {"namespace": namespace, "fallback": fallback}
    return f"{kind}_{_canonical_hash(seed)[:32]}"


def _timestamp(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return float(text)
        except ValueError:
            pass
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    return None


def _flatten_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (int, float, bool)):
        return str(value)
    if isinstance(value, list):
        return "\n".join(part for item in value if (part := _flatten_text(item)))
    if isinstance(value, dict):
        content_type = str(value.get("content_type") or "").strip().lower()
        if "asset_pointer" in value or content_type.endswith("_asset_pointer"):
            return ""
        if content_type and content_type not in _TEXT_CONTENT_TYPES:
            return ""
        parts = value.get("parts")
        if isinstance(parts, list):
            return _flatten_text(parts)
        text_values: list[str] = []
        for key in ("text", "result", "output", "caption", "title", "name"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                text_values.append(candidate)
        return "\n".join(text_values)
    return ""


def _is_opaque_future_content(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    content_type = str(value.get("content_type") or "").strip().lower()
    return bool(
        content_type
        and content_type not in _TEXT_CONTENT_TYPES
        and content_type not in _KNOWN_UNSUPPORTED_CONTENT_TYPES
        and not content_type.endswith("_asset_pointer")
        and "asset_pointer" not in value
    )


def _canonical_text(value: Any) -> str:
    normalized_newlines = _flatten_text(value).replace("\r\n", "\n").replace("\r", "\n")
    return unicodedata.normalize("NFC", normalized_newlines)


def _role(message: dict[str, Any]) -> str:
    author = message.get("author")
    raw = author.get("role") if isinstance(author, dict) else author
    raw = raw or message.get("role") or "unknown"
    normalized = str(raw).strip().lower()
    return normalized if normalized in {"system", "user", "assistant", "tool"} else "unknown"


def _conversation_objects(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        values = payload
    elif isinstance(payload, dict) and (isinstance(payload.get("mapping"), dict) or isinstance(payload.get("messages"), list)):
        values = [payload]
    elif isinstance(payload, dict):
        values = []
        for key in ("conversations", "items", "data", "chats", "threads"):
            candidate = payload.get(key)
            if isinstance(candidate, list):
                values = candidate
                break
    else:
        values = []
    conversations = [item for item in values if isinstance(item, dict)]
    if not conversations:
        raise ExportFormatError("JSON contains no recognizable conversation objects")
    return conversations


def _parents(mapping: dict[str, Any]) -> dict[str, str | None]:
    result: dict[str, str | None] = {}
    for node_id, raw_node in mapping.items():
        node = raw_node if isinstance(raw_node, dict) else {}
        parent = node.get("parent")
        result[str(node_id)] = str(parent) if parent is not None else None
    return result


def _validate_topology(mapping: dict[str, Any], current_node: str | None) -> None:
    node_ids = {str(node_id) for node_id in mapping}
    parents = _parents(mapping)
    if current_node is not None and current_node not in node_ids:
        raise TopologyError(f"current_node is missing from mapping: {current_node}")

    for node_id, parent in parents.items():
        if parent is not None and parent not in node_ids:
            raise TopologyError(f"node {node_id} references missing parent {parent}")

    for node_id, raw_node in mapping.items():
        if not isinstance(raw_node, dict):
            raise TopologyError(f"mapping node is not an object: {node_id}")
        children = raw_node.get("children")
        if children is None:
            continue
        if not isinstance(children, list):
            raise TopologyError(f"children is not a list: {node_id}")
        for child in children:
            child_id = str(child)
            if child_id not in node_ids:
                raise TopologyError(f"node {node_id} references missing child {child_id}")
            if parents[child_id] != str(node_id):
                raise TopologyError(f"child/parent mismatch: {node_id} -> {child_id}")

    for node_id, parent in parents.items():
        if parent is None:
            continue
        parent_node = mapping.get(parent)
        if not isinstance(parent_node, dict) or parent_node.get("children") is None:
            continue
        listed_children = parent_node.get("children")
        if not isinstance(listed_children, list) or node_id not in {str(item) for item in listed_children}:
            raise TopologyError(f"parent/child mismatch: {node_id} declares parent {parent}")

    for start in node_ids:
        seen: set[str] = set()
        cursor: str | None = start
        while cursor is not None:
            if cursor in seen:
                raise TopologyError(f"cycle detected at node: {cursor}")
            seen.add(cursor)
            cursor = parents.get(cursor)


def _active_chain(mapping: dict[str, Any], current_node: str | None) -> list[str]:
    order = [str(node_id) for node_id in mapping]
    parents = _parents(mapping)
    if current_node is None:
        parent_ids = {parent for parent in parents.values() if parent is not None}
        leaves = [node_id for node_id in order if node_id not in parent_ids]
        if len(leaves) != 1:
            raise TopologyError("active branch is ambiguous because current_node is absent")
        else:
            current_node = leaves[0]
    if current_node is None:
        return []

    reverse_chain: list[str] = []
    seen: set[str] = set()
    cursor: str | None = current_node
    while cursor is not None and cursor in parents:
        if cursor in seen:
            raise TopologyError(f"cycle detected at node: {cursor}")
        seen.add(cursor)
        reverse_chain.append(cursor)
        cursor = parents[cursor]
    reverse_chain.reverse()
    return reverse_chain


def _all_nodes(mapping: dict[str, Any]) -> list[str]:
    order = [str(node_id) for node_id in mapping]
    parents = _parents(mapping)
    index = {node_id: position for position, node_id in enumerate(order)}

    def depth(node_id: str) -> int:
        seen: set[str] = set()
        cursor = node_id
        result = 0
        while parents.get(cursor) is not None:
            if cursor in seen:
                raise TopologyError(f"cycle detected at node: {cursor}")
            seen.add(cursor)
            parent = parents.get(cursor)
            if parent not in parents:
                break
            cursor = parent
            result += 1
        return result

    return sorted(order, key=lambda node_id: (depth(node_id), index[node_id]))


def _node_message_id(
    mapping: dict[str, Any], node_id: str, conversation_id: str, namespace: str
) -> str | None:
    node = mapping.get(node_id)
    if not isinstance(node, dict) or not isinstance(node.get("message"), dict):
        return None
    message = node["message"]
    external = message.get("id") or node.get("id") or node_id
    return _stable_id(
        "msg", f"{namespace}:{conversation_id}", external, {"conversation": conversation_id, "node": node_id}
    )


def _nearest_parent_message_id(
    mapping: dict[str, Any], node_id: str, conversation_id: str, namespace: str
) -> str | None:
    parents = _parents(mapping)
    cursor = parents.get(node_id)
    seen: set[str] = set()
    while cursor is not None and cursor not in seen:
        seen.add(cursor)
        resolved = _node_message_id(mapping, cursor, conversation_id, namespace)
        if resolved is not None:
            return resolved
        cursor = parents.get(cursor)
    return None


def _mapping_messages(
    conversation: dict[str, Any],
    conversation_id: str,
    namespace: str,
    *,
    branch: str,
) -> tuple[NormalizedMessage, ...]:
    raw_mapping = conversation.get("mapping")
    if not isinstance(raw_mapping, dict):
        return ()
    mapping = {str(key): value for key, value in raw_mapping.items()}
    current = conversation.get("current_node")
    current_node = str(current) if current is not None else None
    _validate_topology(mapping, current_node)
    active_nodes = _active_chain(mapping, current_node)
    active_set = set(active_nodes)
    selected = active_nodes if branch == "active" else _all_nodes(mapping)

    records: list[NormalizedMessage] = []
    for position, node_id in enumerate(selected):
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue
        raw_content = message.get("content")
        content = _canonical_text(raw_content)
        if not content and not _is_opaque_future_content(raw_content):
            raise ExportFormatError(f"message content is unsupported or empty at mapping node: {node_id}")
        message_id = _node_message_id(mapping, node_id, conversation_id, namespace)
        if message_id is None:
            continue
        records.append(
            NormalizedMessage(
                message_id=message_id,
                conversation_id=conversation_id,
                parent_message_id=_nearest_parent_message_id(mapping, node_id, conversation_id, namespace),
                role=_role(message),
                content=content,
                created_at=_timestamp(message.get("create_time") or node.get("create_time")),
                position=len(records),
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                is_active=node_id in active_set,
            )
        )
    return tuple(records)


def _list_messages(
    conversation: dict[str, Any], conversation_id: str, namespace: str
) -> tuple[NormalizedMessage, ...]:
    rows = conversation.get("messages")
    if not isinstance(rows, list):
        return ()
    result: list[NormalizedMessage] = []
    raw_to_stable: dict[str, str] = {}
    for position, raw in enumerate(rows):
        if not isinstance(raw, dict):
            continue
        content_value = raw.get("content") if raw.get("content") is not None else raw.get("text")
        content = _canonical_text(content_value)
        if not content and not _is_opaque_future_content(content_value):
            raise ExportFormatError(f"message content is unsupported or empty at list position: {position}")
        created_at = _timestamp(raw.get("create_time") or raw.get("created_at") or raw.get("timestamp"))
        raw_id = raw.get("id")
        message_id = _stable_id(
            "msg",
            f"{namespace}:{conversation_id}",
            raw_id,
            {
                "conversation": conversation_id,
                "created_at": created_at,
                "role": _role(raw),
                "position": position,
                "content_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            },
        )
        if raw_id is not None:
            raw_to_stable[str(raw_id)] = message_id
        parent = raw.get("parent") or raw.get("parent_id")
        result.append(
            NormalizedMessage(
                message_id=message_id,
                conversation_id=conversation_id,
                parent_message_id=raw_to_stable.get(str(parent)) if parent is not None else None,
                role=_role(raw),
                content=content,
                created_at=created_at,
                position=len(result),
                content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
                is_active=True,
            )
        )
    return tuple(result)


def normalize_conversations(
    payload: Any,
    *,
    source_namespace: str = "chatgpt",
    branch: str = "active",
) -> tuple[NormalizedConversation, ...]:
    if branch not in {"active", "all"}:
        raise ValueError("branch must be 'active' or 'all'")
    normalized: list[NormalizedConversation] = []
    for ordinal, conversation in enumerate(_conversation_objects(payload)):
        id_value = conversation.get("id")
        conversation_id_value = conversation.get("conversation_id")
        normalized_id = str(id_value).strip() if id_value is not None else ""
        normalized_conversation_id = (
            str(conversation_id_value).strip() if conversation_id_value is not None else ""
        )
        if normalized_id and normalized_conversation_id and normalized_id != normalized_conversation_id:
            raise ExportFormatError("conflicting conversation identity aliases: id != conversation_id")
        raw_id = id_value if normalized_id else conversation_id_value
        fallback = {
            "title": conversation.get("title") or conversation.get("conversation_title"),
            "created_at": conversation.get("create_time") or conversation.get("created_at"),
            "ordinal": ordinal,
        }
        conversation_id = _stable_id("conv", source_namespace, raw_id, fallback)
        mapping_messages = _mapping_messages(
            conversation,
            conversation_id,
            source_namespace,
            branch=branch,
        )
        messages = mapping_messages or _list_messages(conversation, conversation_id, source_namespace)
        normalized.append(
            NormalizedConversation(
                conversation_id=conversation_id,
                title=(str(conversation.get("title") or conversation.get("conversation_title")).strip() or None)
                if (conversation.get("title") or conversation.get("conversation_title")) is not None
                else None,
                created_at=_timestamp(conversation.get("create_time") or conversation.get("created_at")),
                updated_at=_timestamp(conversation.get("update_time") or conversation.get("updated_at")),
                messages=messages,
            )
        )
    return tuple(normalized)


def parse_export_file(
    path: Path, *, branch: str = "active"
) -> tuple[NormalizedConversation, ...]:
    size = path.stat().st_size
    if size > MAX_JSON_BYTES:
        raise ExportFormatError(f"JSON exceeds the {MAX_JSON_BYTES}-byte safety ceiling: {path.name}")
    with path.open("r", encoding="utf-8-sig") as handle:
        payload = json.load(handle)
    return normalize_conversations(payload, branch=branch)


def iter_messages(conversations: Iterable[NormalizedConversation]) -> Iterable[NormalizedMessage]:
    for conversation in conversations:
        yield from conversation.messages
