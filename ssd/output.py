import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path


def channel_dir(output_root: str, workspace: str, channel_name: str, channel_id: str) -> Path:
    return Path(output_root) / workspace / f"{channel_name}_{channel_id}"


def _ts_to_dt(ts: str) -> datetime:
    return datetime.fromtimestamp(float(ts), tz=UTC)


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


def _atomic_write(path: Path, content: str) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            import warnings

            warnings.warn(f"Could not clean up temp file: {tmp}", RuntimeWarning, stacklevel=2)
        raise


def write_messages(dir: Path, messages: list[dict]) -> None:
    dir.mkdir(parents=True, exist_ok=True)
    sorted_msgs = sorted(messages, key=lambda m: float(m["ts"]))
    json_path = dir / "messages.json"
    md_path = dir / "messages.md"
    _atomic_write(json_path, json.dumps(sorted_msgs, indent=2, ensure_ascii=False))
    _atomic_write(md_path, format_markdown(sorted_msgs))


def merge_messages(dir: Path, new_messages: list[dict]) -> list[dict]:
    json_path = dir / "messages.json"
    existing: list[dict] = []
    if json_path.exists():
        existing = json.loads(json_path.read_text())
    by_ts: dict[str, dict] = {m["ts"]: m for m in existing}
    for m in new_messages:
        by_ts[m["ts"]] = m
    write_messages(dir, list(by_ts.values()))
    return sorted(by_ts.values(), key=lambda m: float(m["ts"]))


def read_cursor(dir: Path) -> str | None:
    cursor_path = dir / ".cursor"
    if not cursor_path.exists():
        return None
    return cursor_path.read_text().strip()


def write_cursor(dir: Path, ts: str) -> None:
    dir.mkdir(parents=True, exist_ok=True)
    (dir / ".cursor").write_text(ts)
