import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


def channel_dir(
    output_root: str, workspace: str, channel_name: str, channel_id: str
) -> Path:
    return Path(output_root) / workspace / f"{channel_name}_{channel_id}"


def _ts_to_dt(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=timezone.utc)


def _file_link_lines(files: list[dict], prefix: str = "") -> list[str]:
    result = []
    for f in files:
        name = f.get("name") or "file"
        local_path = f.get("local_path") or ""
        url = f.get("url") or ""
        if local_path:
            result.append(f"{prefix}[{name}](attachments/{Path(local_path).name})")
        elif url:
            result.append(f"{prefix}[{name}]({url})")
    return result


def format_markdown(messages: list[dict]) -> str:
    lines = []
    for msg in messages:
        dt = _ts_to_dt(msg["ts"])
        header = f"## {dt.strftime('%Y-%m-%d %H:%M UTC')} - {msg['user_name']}"
        lines.append(header)
        lines.append("")
        lines.append(msg.get("text", ""))
        for r in msg.get("reactions", []):
            lines.append(f":{r['name']}: x{r['count']}")
        lines.extend(_file_link_lines(msg.get("files", [])))
        for reply in msg.get("thread", []):
            rdt = _ts_to_dt(reply["ts"])
            lines.append(
                f"> **{reply['user_name']}** *({rdt.strftime('%H:%M')})*: {reply.get('text', '')}"
            )
            lines.extend(_file_link_lines(reply.get("files", []), prefix="> "))
        lines.append("")
    return "\n".join(lines)


def write_messages(dir: Path, messages: list[dict]) -> None:
    dir.mkdir(parents=True, exist_ok=True)
    sorted_msgs = sorted(messages, key=lambda m: float(m["ts"]))
    (dir / "messages.json").write_text(
        json.dumps(sorted_msgs, indent=2, ensure_ascii=False)
    )
    (dir / "messages.md").write_text(format_markdown(sorted_msgs))


def merge_messages(dir: Path, new_messages: list[dict]) -> None:
    json_path = dir / "messages.json"
    existing: list[dict] = []
    if json_path.exists():
        existing = json.loads(json_path.read_text())
    by_ts: dict[str, dict] = {m["ts"]: m for m in existing}
    for m in new_messages:
        by_ts[m["ts"]] = m
    write_messages(dir, list(by_ts.values()))


def read_cursor(dir: Path) -> Optional[str]:
    cursor_path = dir / ".cursor"
    if not cursor_path.exists():
        return None
    return cursor_path.read_text().strip()


def write_cursor(dir: Path, ts: str) -> None:
    dir.mkdir(parents=True, exist_ok=True)
    (dir / ".cursor").write_text(ts)
