"""Dump filesystem I/O: channel dirs, message merge, cursors, and markdown.

Slack timestamp ordering lives in ``ssd.parser.ts_key`` so that ts comparisons
can be done without importing this module's filesystem machinery.
"""

import os
import re
import sys
import tempfile
import warnings
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import orjson as _fastjson

from ssd.parser import CHANNEL_DIR_RE, dir_rank, ts_from_thread_dir, ts_key

_SEP_RE = re.compile(r"[/\\]")


def _safe_dir_component(name: str) -> str:
    """Single path segment: no separators, nulls, newlines, or ``..`` escapes."""
    safe = name.replace("\x00", "").replace("\r", "").replace("\n", "")
    safe = _SEP_RE.sub("_", safe)
    safe = safe.replace("..", "__")
    return safe.strip(" .") or "_"


def channel_dir(output_root: str, workspace: str, channel_name: str, channel_id: str) -> Path:
    """Return the dump directory for a channel.

    Prefers ``{channel_name}_{channel_id}`` when it already exists. Otherwise
    reuses any existing workspace subdirectory for the same channel id so a
    rename does not fork the archive. Falls back to the preferred path when
    nothing is on disk yet.
    """
    ws = Path(output_root) / _safe_dir_component(workspace)
    preferred = ws / f"{_safe_dir_component(channel_name)}_{_safe_dir_component(channel_id)}"
    if preferred.is_dir():
        return preferred
    existing = _existing_channel_dir(ws, channel_id)
    if existing is not None:
        return existing
    return preferred


def _existing_channel_dir(ws: Path, channel_id: str) -> Path | None:
    if not ws.is_dir():
        return None
    matches: list[Path] = []
    for path in ws.iterdir():
        if not path.is_dir():
            continue
        matched = CHANNEL_DIR_RE.match(path.name)
        if matched and matched.group(2) == channel_id:
            matches.append(path)
    if not matches:
        return None
    if len(matches) == 1:
        return matches[0]
    return max(matches, key=dir_rank)


def _ts_to_dt(ts: str) -> datetime:
    usec = ts_key(ts)
    # Integer floor to whole seconds; keep the remainder as microsecond.
    return datetime.fromtimestamp(usec // 1_000_000, tz=UTC).replace(
        microsecond=usec % 1_000_000
    )


def _file_link_lines(
    files: list[dict[str, Any]], prefix: str = "", *, attachments_href: str = "attachments"
) -> list[str]:
    result = []
    for f in files:
        name = f.get("name") or "file"
        # Neutralize markdown link breakouts in attacker-influenced labels.
        label = str(name).replace("[", "\\[").replace("]", "\\]")
        local_path = f.get("local_path") or ""
        url = f.get("url") or ""
        if local_path:
            result.append(f"{prefix}[{label}]({attachments_href}/{Path(local_path).name})")
        elif url:
            # Only emit http(s) targets; javascript:/data: etc. must not become links.
            try:
                scheme = urlparse(str(url)).scheme.lower()
            except ValueError:
                scheme = ""
            if scheme in ("http", "https"):
                result.append(f"{prefix}[{label}]({url})")
            else:
                result.append(f"{prefix}{label}")
    return result


def format_markdown(
    messages: list[dict[str, Any]], *, attachments_href: str = "attachments"
) -> str:
    """Render enriched messages as human-readable Markdown.

    Each top-level message gets an ``##`` header with UTC timestamp and user
    name. Reactions appear as ``:name: xN``. Thread replies are indented with
    blockquote (``> ``). File attachments render as local relative links when
    ``local_path`` is set, or as https URLs otherwise; non-http(s) URLs are
    emitted as plain text to avoid javascript: links.

    ``attachments_href`` is the relative directory for local file links
    (``attachments`` from messages.md, ``../attachments`` from thread.md).
    """
    lines = []
    for msg in messages:
        ts = msg.get("ts")
        if not ts:
            continue
        dt = _ts_to_dt(str(ts))
        user = msg.get("user_name") or "unknown"
        header = f"## {dt.strftime('%Y-%m-%d %H:%M UTC')} - {user}"
        lines.append(header)
        lines.append("")
        lines.append(msg.get("text", ""))
        for r in msg.get("reactions", []):
            lines.append(f":{r['name']}: x{r['count']}")
        lines.extend(_file_link_lines(msg.get("files", []), attachments_href=attachments_href))
        for reply in msg.get("thread", []):
            if not isinstance(reply, dict) or not reply.get("ts"):
                continue
            rdt = _ts_to_dt(str(reply["ts"]))
            ruser = reply.get("user_name") or "unknown"
            lines.append(f"> **{ruser}** *({rdt.strftime('%H:%M')})*: {reply.get('text', '')}")
            lines.extend(
                _file_link_lines(
                    reply.get("files", []), prefix="> ", attachments_href=attachments_href
                )
            )
        lines.append("")
    return "\n".join(lines)


def dumps_bytes(data: Any) -> bytes:
    return _fastjson.dumps(data)


def read_json(path: Path) -> Any:
    return _fastjson.loads(path.read_bytes())


def load_merge_json(path: Path, expect: type) -> Any:
    """Load an existing dump JSON for merge. Never invent empty on corrupt I/O.

    Callers that treat a missing file as empty must check ``path.is_file()``
    (or ``exists()``) first. An unreadable or wrong-shaped file raises so a
    later write cannot silently replace a damaged archive with a partial merge.
    """
    try:
        raw = read_json(path)
    except (OSError, ValueError) as exc:
        raise RuntimeError(
            f"Cannot merge into {path}: existing file is unreadable ({exc})"
        ) from exc
    if not isinstance(raw, expect):
        raise RuntimeError(
            f"Cannot merge into {path}: expected {expect.__name__}, got {type(raw).__name__}"
        )
    return raw


def _atomic_write(path: Path, content: str | bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_")
    try:
        if isinstance(content, bytes):
            with os.fdopen(fd, "wb") as f:
                f.write(content)
        else:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(content)
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            warnings.warn(f"Could not clean up temp file: {tmp}", RuntimeWarning, stacklevel=2)
        raise


def _write_sorted_pair(
    directory: Path,
    json_name: str,
    md_name: str,
    messages: list[dict[str, Any]],
    *,
    attachments_href: str = "attachments",
) -> list[dict[str, Any]]:
    directory.mkdir(parents=True, exist_ok=True)
    # Drop rows without ts; ts_key requires a valid timestamp to sort.
    sortable = [m for m in messages if isinstance(m, dict) and m.get("ts")]
    sorted_msgs = sorted(sortable, key=lambda m: ts_key(str(m["ts"])))
    _atomic_write(directory / json_name, dumps_bytes(sorted_msgs))
    _atomic_write(
        directory / md_name, format_markdown(sorted_msgs, attachments_href=attachments_href)
    )
    return sorted_msgs


def write_messages(directory: Path, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return _write_sorted_pair(directory, "messages.json", "messages.md", messages)


def write_thread(directory: Path, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Write a thread dump as thread.json + thread.md (sorted by ts)."""
    # Files live in the parent channel's attachments/; thread.md is one level deeper.
    return _write_sorted_pair(
        directory,
        "thread.json",
        "thread.md",
        messages,
        attachments_href="../attachments",
    )


def _merge_files(
    base: list[dict[str, Any]], incoming: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Union files by id; keep local_path when a re-fetch omits it."""
    by_key: dict[str, dict[str, Any]] = {}
    order: list[str] = []
    anon_seq = 0
    for fobj in [*base, *incoming]:
        if not isinstance(fobj, dict):
            continue
        fid = str(fobj.get("id") or "")
        if fid:
            key = fid
        else:
            # Do not key on enumerate index: concatenating base+incoming shifts
            # indexes so the same id-less file would duplicate on every merge.
            name = str(fobj.get("name") or "")
            url = str(
                fobj.get("url") or fobj.get("url_private") or fobj.get("url_private_download") or ""
            )
            if name or url:
                key = f"anon:{name}:{url}"
            else:
                key = f"anon:{anon_seq}"
                anon_seq += 1
        prev = by_key.get(key)
        if prev is None:
            order.append(key)
            by_key[key] = dict(fobj)
            continue
        merged = {**prev, **fobj}
        if prev.get("local_path") and not fobj.get("local_path"):
            merged["local_path"] = prev["local_path"]
        by_key[key] = merged
    return [by_key[k] for k in order]


def reply_count_int(msg: dict[str, Any] | None) -> int:
    if not msg:
        return 0
    try:
        return int(msg.get("reply_count") or 0)
    except (TypeError, ValueError):
        return 0


def reconcile_thread_meta(msg: dict[str, Any], *extra: dict[str, Any] | None) -> None:
    """Keep reply_count / latest_reply coherent with the nested thread array.

    Stored history can hold more replies than a thin re-fetch claims, or fewer
    when replies are still missing. Never let reply_count fall below len(thread)
    or below any source claim, and never let latest_reply move backwards.
    """
    thread = [r for r in (msg.get("thread") or []) if isinstance(r, dict) and r.get("ts")]
    claimed = len(thread)
    for source in (msg, *extra):
        claimed = max(claimed, reply_count_int(source))
    if claimed:
        msg["reply_count"] = claimed
    candidates: list[str] = []
    for source in (msg, *extra):
        if not source:
            continue
        latest = source.get("latest_reply")
        if latest:
            candidates.append(str(latest))
    if thread:
        candidates.extend(str(r["ts"]) for r in thread)
    if candidates:
        msg["latest_reply"] = max_ts(candidates)


def thread_reply_meta(msg: dict[str, Any]) -> tuple[int, str] | None:
    """Return coherent ``(reply_count, latest_reply)`` for a thread parent.

    Uses the same rules as ``reconcile_thread_meta`` without mutating ``msg``.
    Returns None when there is no thread evidence (no stored replies and no
    claimed count). ``latest_reply`` is chosen by numeric ts order, not list order.
    """
    thread = [r for r in (msg.get("thread") or []) if isinstance(r, dict) and r.get("ts")]
    scratch: dict[str, Any] = {
        "thread": thread,
        "reply_count": msg.get("reply_count"),
        "latest_reply": msg.get("latest_reply"),
    }
    reconcile_thread_meta(scratch)
    count = reply_count_int(scratch)
    if not thread and count <= 0:
        return None
    return count, str(scratch.get("latest_reply") or "")


def _merge_one_message(base: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    """Prefer incoming fields, but never drop stored thread replies, reactions, or local paths."""
    merged = {**base, **incoming}
    base_thread = [r for r in (base.get("thread") or []) if isinstance(r, dict) and r.get("ts")]
    inc_thread = [r for r in (incoming.get("thread") or []) if isinstance(r, dict) and r.get("ts")]
    if base_thread or inc_thread:
        merged["thread"] = merge_by_ts(base_thread, inc_thread)
    if base.get("files") or incoming.get("files"):
        merged["files"] = _merge_files(base.get("files") or [], incoming.get("files") or [])
    # Empty incoming reactions are often an omitted API field normalized to [].
    # Keep the archived set unless the re-fetch actually reports reactions.
    if base.get("reactions") and not incoming.get("reactions"):
        merged["reactions"] = base["reactions"]
    # Same class of drift as reactions: thin re-fetches can claim reply_count=0
    # while the archive still holds the nested thread.
    if base_thread or inc_thread or reply_count_int(base) or reply_count_int(incoming):
        reconcile_thread_meta(merged, base, incoming)
    return merged


def merge_by_ts(base: list[dict[str, Any]], incoming: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Union two message lists by ``ts``, merging conflicts with ``_merge_one_message``.

    Incoming fields overwrite base fields, but thread replies, reactions, and
    ``local_path`` are never dropped. Returns a new list sorted by ts.
    Messages without ``ts`` are silently dropped.
    """
    by_ts: dict[str, dict[str, Any]] = {}
    for m in base:
        ts = m.get("ts")
        if ts:
            by_ts[str(ts)] = m
    for m in incoming:
        ts = m.get("ts")
        if not ts:
            continue
        key = str(ts)
        prev = by_ts.get(key)
        by_ts[key] = _merge_one_message(prev, m) if prev else m
    return sorted(by_ts.values(), key=lambda m: ts_key(m["ts"]))


def merge_messages(directory: Path, new_messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge ``new_messages`` into ``messages.json`` and return the written archive.

    Callers can pass the return value to ``write_channel_stats`` /
    ``write_cursor_from_messages`` to avoid a second full-file read.
    """
    json_path = directory / "messages.json"
    existing: list[dict[str, Any]] = []
    if json_path.exists():
        existing = load_merge_json(json_path, list)
    return write_messages(directory, merge_by_ts(existing, new_messages))


def merge_thread_into_channel(
    channel_directory: Path,
    thread_ts: str,
    thread_messages: list[dict[str, Any]],
) -> list[dict[str, Any]] | None:
    """Merge a standalone thread dump into messages.json when the parent exists.

    Thread URL dumps live under ``thread_<ts>/`` and can drift from the nested
    ``thread`` arrays in messages.json. When the parent is already archived,
    fold replies into that message so query and channel sync share one history.

    Returns the written archive, or None when the parent was not found / unreadable.
    """
    path = channel_directory / "messages.json"
    if not path.is_file():
        return None
    try:
        raw = read_json(path)
    except (OSError, ValueError) as exc:
        print(f"  skip unreadable {path}: {exc}", file=sys.stderr, flush=True)
        return None
    if not isinstance(raw, list):
        return None
    rest = [
        row
        for row in thread_messages
        if isinstance(row, dict) and str(row.get("ts") or "") != thread_ts
    ]
    for msg in raw:
        if not isinstance(msg, dict) or str(msg.get("ts") or "") != thread_ts:
            continue
        base_thread = [r for r in (msg.get("thread") or []) if isinstance(r, dict) and r.get("ts")]
        msg["thread"] = merge_by_ts(base_thread, rest)
        reconcile_thread_meta(msg)
        return write_messages(channel_directory, raw)
    return None


def refresh_thread_dump_dirs(
    channel_directory: Path, messages: list[dict[str, Any]] | None = None
) -> None:
    """Rewrite existing ``thread_*/thread.json`` dumps from nested channel messages.

    Only touches thread dirs that already exist; does not create a dir per thread.
    """
    if messages is None:
        path = channel_directory / "messages.json"
        if not path.is_file():
            return
        try:
            raw = read_json(path)
        except (OSError, ValueError) as exc:
            print(f"  skip unreadable {path}: {exc}", file=sys.stderr, flush=True)
            return
        messages = raw if isinstance(raw, list) else []
    dumps: dict[str, Path] = {}
    try:
        kids = channel_directory.iterdir()
    except OSError:
        return
    for sub in kids:
        if not sub.is_dir() or not sub.name.startswith("thread_"):
            continue
        ts = ts_from_thread_dir(sub.name)
        if ts and (sub / "thread.json").is_file():
            dumps[ts] = sub
    if not dumps:
        return
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        ts = str(msg.get("ts") or "")
        thread_dir = dumps.get(ts)
        if thread_dir is None:
            continue
        parent = {k: v for k, v in msg.items() if k != "thread"}
        replies = [r for r in (msg.get("thread") or []) if isinstance(r, dict)]
        write_thread(thread_dir, [parent, *replies])


def read_cursor(directory: Path) -> str | None:
    cursor_path = directory / ".cursor"
    if not cursor_path.exists():
        return None
    try:
        return cursor_path.read_text().strip() or None
    except OSError:
        return None


def write_cursor(directory: Path, ts: str) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(directory / ".cursor", ts)


def _cursor_from_rows(directory: Path, rows: list[dict[str, Any]]) -> str | None:
    stamped = [str(m["ts"]) for m in rows if isinstance(m, dict) and m.get("ts")]
    if not stamped:
        return None
    ts = max_ts(stamped)
    write_cursor(directory, ts)
    return ts


def _write_cursor_from_json(directory: Path, json_name: str) -> str | None:
    """Set ``.cursor`` from the latest ts in a message-list JSON archive.

    The archive is the sync watermark source of truth; a fetch batch must not
    advance the cursor past what was stored, or leave a drifted-high watermark
    that skips Slack history between the real archive max and the stale cursor.
    """
    path = directory / json_name
    if not path.is_file():
        return None
    try:
        raw = read_json(path)
    except (OSError, ValueError):
        return None
    if not isinstance(raw, list):
        return None
    return _cursor_from_rows(directory, raw)


def write_cursor_from_messages(
    directory: Path, messages: list[dict[str, Any]] | None = None
) -> str | None:
    """Set ``.cursor`` from the latest top-level ts in ``messages.json``.

    Pass ``messages`` when the archive is already in memory to skip a disk read.
    """
    if messages is not None:
        return _cursor_from_rows(directory, messages)
    return _write_cursor_from_json(directory, "messages.json")


def write_cursor_from_thread(
    directory: Path, messages: list[dict[str, Any]] | None = None
) -> str | None:
    """Set ``.cursor`` from the latest ts in ``thread.json`` (thread URL dumps).

    Pass ``messages`` when the archive is already in memory to skip a disk read.
    """
    if messages is not None:
        return _cursor_from_rows(directory, messages)
    return _write_cursor_from_json(directory, "thread.json")


def max_ts(timestamps: Iterable[str]) -> str:
    """Return the numerically latest Slack ts string (not lexicographic max)."""
    return max(timestamps, key=ts_key)


def write_users(directory: Path, profiles: dict[str, Any]) -> None:
    """Merge user profiles into users.json, keyed by user ID, sorted by display name."""
    if not profiles:
        return
    path = directory / "users.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        existing = load_merge_json(path, dict)
    existing.update(profiles)
    sorted_profiles = dict(
        sorted(
            existing.items(),
            key=lambda kv: (
                (kv[1].get("display_name") if isinstance(kv[1], dict) else None) or kv[0]
            ),
        )
    )
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, dumps_bytes(sorted_profiles))


def write_json(directory: Path, name: str, data: Any) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    _atomic_write(directory / name, dumps_bytes(data))
