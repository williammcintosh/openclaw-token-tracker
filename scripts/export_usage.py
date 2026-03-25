#!/usr/bin/env python3
from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


SCHEMA_VERSION = 1


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Export sanitized OpenClaw usage data")
    parser.add_argument(
        "--openclaw-home",
        default="~/.openclaw",
        help="Path to OpenClaw home directory (default: ~/.openclaw)",
    )
    parser.add_argument(
        "--rules",
        default="config/category-rules.json",
        help="Path to category rules JSON",
    )
    parser.add_argument(
        "--private-out",
        default="data/private/history.json",
        help="Path to local private aggregate output",
    )
    parser.add_argument(
        "--public-out",
        default="site/data/summary.json",
        help="Path to sanitized public aggregate output",
    )
    return parser.parse_args()


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def write_json(path: Path, data: Any) -> None:
    ensure_parent(path)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(data, handle, indent=2, sort_keys=False)
        handle.write("\n")


def iso_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def normalize_text(value: str | None, limit: int = 240) -> str:
    if not value:
        return ""
    compact = re.sub(r"\s+", " ", value).strip()
    return compact[:limit]


def extract_text_parts(content: Any) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, str):
            parts.append(item)
            continue
        if not isinstance(item, dict):
            continue
        item_type = item.get("type")
        if item_type in {"text", "input_text", "output_text"} and isinstance(item.get("text"), str):
            parts.append(item["text"])
            continue
        if isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def canonical_session_id_from_name(path: Path) -> str:
    name = path.name
    marker = ".jsonl"
    idx = name.find(marker)
    return name[:idx] if idx != -1 else path.stem


def iter_session_files(sessions_dir: Path) -> list[Path]:
    files: list[Path] = []
    for path in sorted(sessions_dir.glob("*.jsonl*")):
        if path.name.endswith(".lock"):
            continue
        files.append(path)
    return files


def load_current_sessions(sessions_json_path: Path) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    sessions_json = load_json(sessions_json_path, {})
    by_session_id: dict[str, dict[str, Any]] = {}
    by_session_key: dict[str, dict[str, Any]] = {}
    for session_key, meta in sessions_json.items():
        if not isinstance(meta, dict):
            continue
        session_id = meta.get("sessionId")
        if isinstance(session_id, str):
            by_session_id[session_id] = meta
        by_session_key[session_key] = meta
    return by_session_id, by_session_key


def parse_conversation_info(first_user_text: str) -> dict[str, Any]:
    match = re.search(
        r"Conversation info .*?```json\s*(\{.*?\})\s*```",
        first_user_text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return {}
    try:
        payload = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def infer_session_kind(meta: dict[str, Any], first_user_text: str, conversation_info: dict[str, Any]) -> str:
    if meta.get("spawnDepth") or meta.get("spawnedBy") or "[Subagent Context]" in first_user_text:
        return "subagent"
    chat_type = meta.get("chatType")
    if chat_type in {"group", "direct"}:
        return str(chat_type)
    is_group = conversation_info.get("is_group_chat")
    if is_group is True:
        return "group"
    if is_group is False:
        return "direct"
    return "unknown"


def infer_channel(meta: dict[str, Any], session_kind: str, first_user_text: str, conversation_info: dict[str, Any]) -> str:
    for candidate in (
        meta.get("channel"),
        (meta.get("deliveryContext") or {}).get("channel"),
        (meta.get("origin") or {}).get("provider"),
    ):
        if isinstance(candidate, str) and candidate:
            return candidate
    if session_kind == "subagent":
        return "internal"
    if conversation_info or "Conversation info" in first_user_text:
        return "telegram"
    return "unknown"


def session_hash(session_id: str) -> str:
    return hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:12]


def ordered_unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        ordered.append(value)
    return ordered


def condition_matches(condition: dict[str, Any], fields: dict[str, Any]) -> bool:
    raw_value = fields.get(condition.get("field", ""), "")
    value = "" if raw_value is None else str(raw_value)
    if "equals" in condition:
        return value.lower() == str(condition["equals"]).lower()
    if "regex" in condition:
        return re.search(str(condition["regex"]), value, flags=re.IGNORECASE) is not None
    if "contains" in condition:
        return str(condition["contains"]).lower() in value.lower()
    return False


def apply_category_rules(fields: dict[str, Any], rules_config: dict[str, Any]) -> str:
    rules = rules_config.get("rules") or []
    default_category = str(rules_config.get("defaultCategory") or "General")
    for rule in rules:
        if not isinstance(rule, dict):
            continue
        label = rule.get("label")
        conditions = rule.get("conditions") or []
        if not label or not conditions:
            continue
        mode = str(rule.get("mode") or "all").lower()
        matches = [condition_matches(condition, fields) for condition in conditions if isinstance(condition, dict)]
        if not matches:
            continue
        if mode == "any" and any(matches):
            return str(label)
        if mode != "any" and all(matches):
            return str(label)
    return default_category


def usage_from_message(obj: dict[str, Any]) -> dict[str, Any] | None:
    message = obj.get("message") or {}
    if not isinstance(message, dict):
        return None
    usage = message.get("usage") or obj.get("usage")
    if not isinstance(usage, dict):
        return None
    if not any(key in usage for key in ("input", "output", "cacheRead", "cacheWrite", "totalTokens")):
        return None
    return usage


def message_provider(obj: dict[str, Any], current_meta: dict[str, Any]) -> str:
    message = obj.get("message") or {}
    return str(
        message.get("provider")
        or obj.get("provider")
        or current_meta.get("modelProvider")
        or "unknown"
    )


def message_model(obj: dict[str, Any], current_meta: dict[str, Any]) -> str:
    message = obj.get("message") or {}
    return str(
        message.get("model")
        or obj.get("model")
        or current_meta.get("model")
        or "unknown"
    )


def message_stop_reason(obj: dict[str, Any]) -> str:
    message = obj.get("message") or {}
    return str(message.get("stopReason") or obj.get("stopReason") or "unknown")


def empty_rollup() -> dict[str, Any]:
    return {
        "assistantMessages": 0,
        "inputTokens": 0,
        "outputTokens": 0,
        "cacheReadTokens": 0,
        "cacheWriteTokens": 0,
        "totalTokens": 0,
        "costTotal": 0.0,
    }


def serialise_rollup(rollup: dict[str, Any]) -> dict[str, Any]:
    result = dict(rollup)
    result["costTotal"] = round(float(result.get("costTotal", 0.0)), 6)
    return result


def build_breakdown(entries: list[dict[str, Any]], field: str) -> list[dict[str, Any]]:
    buckets: dict[str, dict[str, Any]] = defaultdict(empty_rollup)
    for entry in entries:
        label = str(entry.get(field) or "unknown")
        buckets[label]["assistantMessages"] += 1
        buckets[label]["inputTokens"] += entry["inputTokens"]
        buckets[label]["outputTokens"] += entry["outputTokens"]
        buckets[label]["cacheReadTokens"] += entry["cacheReadTokens"]
        buckets[label]["cacheWriteTokens"] += entry["cacheWriteTokens"]
        buckets[label]["totalTokens"] += entry["totalTokens"]
        buckets[label]["costTotal"] += entry["costTotal"]
    total_tokens_all = sum(bucket["totalTokens"] for bucket in buckets.values())
    rows: list[dict[str, Any]] = []
    for label, bucket in buckets.items():
        row = {"label": label, **serialise_rollup(bucket)}
        row["shareOfTotalTokens"] = 0 if total_tokens_all == 0 else round(row["totalTokens"] / total_tokens_all, 4)
        rows.append(row)
    rows.sort(key=lambda item: (-item["totalTokens"], item["label"]))
    return rows


def build_daily(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    daily: dict[str, dict[str, Any]] = {}
    for entry in entries:
        day = entry["day"]
        if day not in daily:
            daily[day] = {
                "day": day,
                **empty_rollup(),
                "byCategory": defaultdict(empty_rollup),
                "byModel": defaultdict(empty_rollup),
            }
        bucket = daily[day]
        bucket["assistantMessages"] += 1
        bucket["inputTokens"] += entry["inputTokens"]
        bucket["outputTokens"] += entry["outputTokens"]
        bucket["cacheReadTokens"] += entry["cacheReadTokens"]
        bucket["cacheWriteTokens"] += entry["cacheWriteTokens"]
        bucket["totalTokens"] += entry["totalTokens"]
        bucket["costTotal"] += entry["costTotal"]

        for dimension, label in (("byCategory", entry["category"]), ("byModel", entry["model"])):
            sub = bucket[dimension][label]
            sub["assistantMessages"] += 1
            sub["inputTokens"] += entry["inputTokens"]
            sub["outputTokens"] += entry["outputTokens"]
            sub["cacheReadTokens"] += entry["cacheReadTokens"]
            sub["cacheWriteTokens"] += entry["cacheWriteTokens"]
            sub["totalTokens"] += entry["totalTokens"]
            sub["costTotal"] += entry["costTotal"]

    rows: list[dict[str, Any]] = []
    for day in sorted(daily):
        bucket = daily[day]
        row = {
            "day": day,
            **serialise_rollup(bucket),
            "byCategory": [
                {"label": label, **serialise_rollup(values)}
                for label, values in sorted(
                    bucket["byCategory"].items(), key=lambda item: (-item[1]["totalTokens"], item[0])
                )
            ],
            "byModel": [
                {"label": label, **serialise_rollup(values)}
                for label, values in sorted(
                    bucket["byModel"].items(), key=lambda item: (-item[1]["totalTokens"], item[0])
                )
            ],
        }
        rows.append(row)
    return rows


def main() -> int:
    args = parse_args()
    repo_root = Path(__file__).resolve().parent.parent
    openclaw_home = Path(args.openclaw_home).expanduser().resolve()
    rules_path = (repo_root / args.rules).resolve() if not Path(args.rules).is_absolute() else Path(args.rules).resolve()
    private_out = (repo_root / args.private_out).resolve() if not Path(args.private_out).is_absolute() else Path(args.private_out).resolve()
    public_out = (repo_root / args.public_out).resolve() if not Path(args.public_out).is_absolute() else Path(args.public_out).resolve()

    sessions_dir = openclaw_home / "agents" / "main" / "sessions"
    sessions_json_path = sessions_dir / "sessions.json"
    if not sessions_dir.exists():
        raise SystemExit(f"Sessions directory not found: {sessions_dir}")

    rules_config = load_json(rules_path, {"version": 1, "defaultCategory": "General", "rules": []})
    current_by_session_id, _ = load_current_sessions(sessions_json_path)
    session_files = iter_session_files(sessions_dir)

    seen_event_ids: set[str] = set()
    entries: list[dict[str, Any]] = []
    sessions_private: dict[str, dict[str, Any]] = {}
    files_scanned = 0
    raw_usage_events = 0

    for session_file in session_files:
        files_scanned += 1
        session_id = canonical_session_id_from_name(session_file)
        current_meta = current_by_session_id.get(session_id, {})
        first_user_text = ""
        assistant_events: list[dict[str, Any]] = []

        with session_file.open("r", encoding="utf-8") as handle:
            for raw_line in handle:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") == "session" and isinstance(obj.get("id"), str):
                    session_id = obj["id"]
                    current_meta = current_by_session_id.get(session_id, current_meta)
                if obj.get("type") != "message":
                    continue
                message = obj.get("message") or {}
                role = message.get("role")
                if role == "user" and not first_user_text:
                    first_user_text = extract_text_parts(message.get("content"))
                if role != "assistant":
                    continue
                usage = usage_from_message(obj)
                if not usage:
                    continue
                raw_usage_events += 1
                assistant_events.append({"obj": obj, "usage": usage})

        conversation_info = parse_conversation_info(first_user_text)
        session_kind = infer_session_kind(current_meta, first_user_text, conversation_info)
        channel = infer_channel(current_meta, session_kind, first_user_text, conversation_info)
        subject = normalize_text(
            str(current_meta.get("subject") or conversation_info.get("group_subject") or "")
        )
        prompt_snippet = normalize_text(first_user_text.lower())
        category_fields = {
            "sessionKind": session_kind,
            "channel": channel,
            "subject": subject,
            "displayName": normalize_text(str(current_meta.get("displayName") or "")),
            "promptSnippet": prompt_snippet,
        }
        category = apply_category_rules(category_fields, rules_config)
        hashed_session = session_hash(session_id)

        if hashed_session not in sessions_private:
            sessions_private[hashed_session] = {
                "sessionHash": hashed_session,
                "category": category,
                "sessionKind": session_kind,
                "channel": channel,
                "firstSeen": None,
                "lastSeen": None,
                "assistantMessages": 0,
                "inputTokens": 0,
                "outputTokens": 0,
                "cacheReadTokens": 0,
                "cacheWriteTokens": 0,
                "totalTokens": 0,
                "costTotal": 0.0,
                "models": [],
                "providers": [],
            }

        for assistant_event in assistant_events:
            obj = assistant_event["obj"]
            usage = assistant_event["usage"]
            event_id = f"{session_id}:{obj.get('id') or hashlib.sha256(json.dumps(obj, sort_keys=True).encode('utf-8')).hexdigest()[:12]}"
            if event_id in seen_event_ids:
                continue
            seen_event_ids.add(event_id)

            timestamp = str(obj.get("timestamp") or (obj.get("message") or {}).get("timestamp") or "")
            day = timestamp[:10] if len(timestamp) >= 10 else "unknown"
            provider = message_provider(obj, current_meta)
            model = message_model(obj, current_meta)
            stop_reason = message_stop_reason(obj)
            input_tokens = int(usage.get("input") or 0)
            output_tokens = int(usage.get("output") or 0)
            cache_read_tokens = int(usage.get("cacheRead") or 0)
            cache_write_tokens = int(usage.get("cacheWrite") or 0)
            total_tokens = int(
                usage.get("totalTokens")
                or (input_tokens + output_tokens + cache_read_tokens + cache_write_tokens)
            )
            cost_total = float(((usage.get("cost") or {}).get("total")) or 0.0)

            entry = {
                "day": day,
                "timestamp": timestamp,
                "sessionHash": hashed_session,
                "category": category,
                "sessionKind": session_kind,
                "channel": channel,
                "provider": provider,
                "model": model,
                "stopReason": stop_reason,
                "inputTokens": input_tokens,
                "outputTokens": output_tokens,
                "cacheReadTokens": cache_read_tokens,
                "cacheWriteTokens": cache_write_tokens,
                "totalTokens": total_tokens,
                "costTotal": cost_total,
            }
            entries.append(entry)

            session_bucket = sessions_private[hashed_session]
            session_bucket["assistantMessages"] += 1
            session_bucket["inputTokens"] += input_tokens
            session_bucket["outputTokens"] += output_tokens
            session_bucket["cacheReadTokens"] += cache_read_tokens
            session_bucket["cacheWriteTokens"] += cache_write_tokens
            session_bucket["totalTokens"] += total_tokens
            session_bucket["costTotal"] += cost_total
            session_bucket["models"] = ordered_unique(session_bucket["models"] + [model])
            session_bucket["providers"] = ordered_unique(session_bucket["providers"] + [provider])
            session_bucket["firstSeen"] = min(filter(None, [session_bucket["firstSeen"], timestamp])) if session_bucket["firstSeen"] else timestamp
            session_bucket["lastSeen"] = max(filter(None, [session_bucket["lastSeen"], timestamp])) if session_bucket["lastSeen"] else timestamp

    entries.sort(key=lambda item: (item["timestamp"], item["sessionHash"]))

    totals = empty_rollup()
    totals["assistantMessages"] = len(entries)
    for entry in entries:
        totals["inputTokens"] += entry["inputTokens"]
        totals["outputTokens"] += entry["outputTokens"]
        totals["cacheReadTokens"] += entry["cacheReadTokens"]
        totals["cacheWriteTokens"] += entry["cacheWriteTokens"]
        totals["totalTokens"] += entry["totalTokens"]
        totals["costTotal"] += entry["costTotal"]
    totals = serialise_rollup(totals)

    days = sorted({entry["day"] for entry in entries if entry["day"] != "unknown"})
    date_range = {
        "from": days[0] if days else None,
        "to": days[-1] if days else None,
        "days": len(days),
    }

    daily = build_daily(entries)
    breakdowns = {
        "category": build_breakdown(entries, "category"),
        "sessionKind": build_breakdown(entries, "sessionKind"),
        "channel": build_breakdown(entries, "channel"),
        "model": build_breakdown(entries, "model"),
        "provider": build_breakdown(entries, "provider"),
        "stopReason": build_breakdown(entries, "stopReason"),
    }

    private_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": iso_now(),
        "source": {
            "openclawHome": str(openclaw_home),
            "sessionFilesScanned": files_scanned,
            "currentSessions": len(current_by_session_id),
            "assistantUsageEventsSeen": raw_usage_events,
            "assistantUsageEventsStored": len(entries),
        },
        "window": date_range,
        "totals": totals,
        "daily": daily,
        "sessions": [
            serialise_rollup(session)
            for session in sorted(
                sessions_private.values(),
                key=lambda item: (-item["totalTokens"], item["sessionHash"]),
            )
        ],
    }

    public_payload = {
        "schemaVersion": SCHEMA_VERSION,
        "generatedAt": iso_now(),
        "privacy": {
            "publishedFields": [
                "daily aggregates",
                "category totals",
                "model totals",
                "provider totals",
                "session kind totals",
                "stop reason totals",
                "token totals",
                "estimated cost totals",
            ],
            "omits": [
                "message text",
                "session ids",
                "chat ids",
                "user ids",
                "raw prompts",
                "tool arguments",
                "secrets",
            ],
        },
        "source": {
            "sessionFilesScanned": files_scanned,
            "assistantUsageEventsStored": len(entries),
            "currentSessions": len(current_by_session_id),
        },
        "window": date_range,
        "totals": totals,
        "breakdowns": breakdowns,
        "daily": daily,
    }

    write_json(private_out, private_payload)
    write_json(public_out, public_payload)

    print(f"Scanned {files_scanned} session files")
    print(f"Stored {len(entries)} assistant usage events")
    print(f"Public summary: {public_out}")
    print(f"Private history: {private_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
