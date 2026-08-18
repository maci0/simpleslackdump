"""Workspace and channel sidecar writers shared by dump and sync."""

import sys
import time
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

from ssd.api import SlackAPI
from ssd.output import (
    load_merge_json,
    max_ts,
    merge_by_ts,
    merge_thread_into_channel,
    read_json,
    refresh_thread_dump_dirs,
    thread_reply_meta,
    write_cursor,
    write_json,
    write_thread,
    write_users,
)
from ssd.parser import CHANNEL_DIR_RE

__all__ = [
    "merge_workspace_bots",
    "persist_thread_dump",
    "prefetch_users",
    "write_channel_stats",
    "write_sidecars",
]


def prefetch_users(api: SlackAPI) -> None:
    try:
        api.fetch_workspace_users()
    except Exception as exc:
        print(f"  workspace users skipped: {exc}", file=sys.stderr, flush=True)


def persist_thread_dump(
    api: SlackAPI,
    out_dir: Path,
    thread_dir: Path,
    thread_ts: str,
    channel_id: str,
    enriched: list[dict[str, Any]],
    *,
    refresh_workspace: bool,
) -> list[dict[str, Any]]:
    """Merge thread replies into thread.json, nest them in messages.json, refresh sidecars."""
    assert thread_ts
    assert channel_id
    existing_path = thread_dir / "thread.json"
    existing: list[dict[str, Any]] = []
    if existing_path.exists():
        # Unreadable thread.json must not be treated as [] then overwritten.
        existing = load_merge_json(existing_path, list)
    sorted_msgs = merge_by_ts(existing, enriched)
    write_thread(thread_dir, sorted_msgs)
    # When the parent lives in messages.json, fold replies there and refresh
    # derived channel sidecars (stats/threads/reactions/…) so they cannot drift.
    merged = merge_thread_into_channel(out_dir, thread_ts, sorted_msgs)
    if merged is not None:
        write_channel_stats(out_dir, messages=merged, api=api)
    else:
        merge_workspace_bots(out_dir.parent, sorted_msgs)
    # Cursor tracks the merged thread archive, not just this fetch batch.
    stamped = [str(m["ts"]) for m in sorted_msgs if m.get("ts")]
    if stamped:
        write_cursor(thread_dir, max_ts(stamped))
    write_users(thread_dir, api.get_user_profiles())
    write_sidecars(api, out_dir, channel_id, refresh_workspace=refresh_workspace)
    return sorted_msgs


def write_sidecars(
    api: SlackAPI,
    out_dir: Path,
    channel_id: str,
    *,
    refresh_workspace: bool = False,
) -> None:
    ws = out_dir.parent

    def write_ws(name: str, fetch: Any, label: str) -> None:
        path = ws / name
        if path.exists() and not refresh_workspace:
            return
        try:
            payload = fetch()
            if name == "users.json":
                write_users(ws, payload)
            else:
                write_json(ws, name, payload)
        except Exception as exc:
            print(f"  {label} skipped: {exc}", file=sys.stderr, flush=True)

    def write_channel(name: str, fetch: Any, label: str) -> None:
        try:
            write_json(out_dir, name, fetch())
        except Exception as exc:
            print(f"  {label} skipped: {exc}", file=sys.stderr, flush=True)

    write_channel("channel.json", lambda: api.get_channel_info(channel_id), "channel info")
    write_channel("members.json", lambda: api.get_channel_members(channel_id), "members")
    write_ws("emoji.json", api.get_emoji, "emoji")
    write_ws("emoji_categories.json", api.get_emoji_categories, "emoji categories")
    write_ws("auth.json", api.get_auth, "auth")
    write_channel("bookmarks.json", lambda: api.get_bookmarks(channel_id), "bookmarks")
    write_channel("pins.json", lambda: api.get_pins(channel_id), "pins")
    write_ws("usergroups.json", api.get_usergroups, "usergroups")
    write_ws("users.json", api.fetch_workspace_users, "workspace users")
    write_ws("conversations.json", api.list_conversations, "conversations")
    write_ws("stars.json", api.get_stars, "stars")
    write_ws("reminders.json", api.get_reminders, "reminders")
    write_ws("dnd.json", api.get_dnd, "dnd")
    write_ws("team_profile.json", api.get_team_profile, "team profile")
    write_ws("scheduled_messages.json", api.get_scheduled_messages, "scheduled messages")
    write_ws("team.json", api.get_team_info, "team info")
    files_path = ws / "files.json"
    canvases_path = ws / "canvases.json"
    need_files = refresh_workspace or not files_path.exists()
    need_canvas = refresh_workspace or not canvases_path.exists()
    if need_files:
        try:
            listed = _attach_comments(api, list(api.get_files()))
            write_json(ws, "files.json", listed)
            if need_canvas:
                write_json(ws, "canvases.json", _canvas_rows(listed))
        except Exception as exc:
            print(f"  files list skipped: {exc}", file=sys.stderr, flush=True)
    elif need_canvas:
        try:
            raw = read_json(files_path) if files_path.is_file() else []
            listed = raw if isinstance(raw, list) else []
            write_json(ws, "canvases.json", _canvas_rows(listed))
        except Exception as exc:
            print(f"  canvases skipped: {exc}", file=sys.stderr, flush=True)
    write_ws("remote_files.json", api.get_remote_files, "remote files")
    try:
        _merge_presence(api, ws, channel_id, refresh=refresh_workspace)
    except Exception as exc:
        print(f"  presence skipped: {exc}", file=sys.stderr, flush=True)
    write_ws("billable_info.json", api.get_billable_info, "billable info")
    write_ws("integration_logs.json", api.get_integration_logs, "integration logs")
    write_ws("access_logs.json", api.get_access_logs, "access logs")
    write_ws("team_preferences.json", api.get_team_preferences, "team preferences")
    write_ws("external_teams.json", api.get_external_teams, "external teams")
    write_ws("teams.json", api.get_auth_teams, "auth teams")


def write_channel_stats(
    out_dir: Path,
    messages: list[dict[str, Any]] | None = None,
    api: SlackAPI | None = None,
) -> None:
    if messages is None:
        path = out_dir / "messages.json"
        if not path.is_file():
            return
        try:
            raw = read_json(path)
        except (OSError, ValueError):
            return
        messages = raw if isinstance(raw, list) else []
    n_replies = 0
    n_files = 0
    for msg in messages:
        thread = msg.get("thread") or []
        n_replies += len(thread)
        n_files += len(msg.get("files") or [])
        for reply in thread:
            n_files += len(reply.get("files") or [])
    write_json(
        out_dir,
        "stats.json",
        {"messages": len(messages), "replies": n_replies, "files": n_files},
    )
    write_json(out_dir, "reactions.json", _reaction_rows(out_dir, messages))
    files = _file_rows(messages)
    if api is not None:
        _copy_file_comments(out_dir / "files.json", files)
        _attach_comments(api, files)
    write_json(out_dir, "files.json", files)
    write_json(out_dir, "calls.json", _call_rows(messages))
    write_json(out_dir, "threads.json", _thread_rows(out_dir, messages))
    refresh_thread_dump_dirs(out_dir, messages)
    merge_workspace_bots(out_dir.parent, messages)


def _channel_id_from_dir(out_dir: Path) -> str:
    matched = CHANNEL_DIR_RE.match(out_dir.name)
    return matched.group(2) if matched else ""


def _walk_messages(messages: list[dict[str, Any]]) -> Iterator[dict[str, Any]]:
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        yield msg
        for reply in msg.get("thread") or []:
            if isinstance(reply, dict):
                yield reply


def _reaction_rows(out_dir: Path, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    channel_id = _channel_id_from_dir(out_dir)
    rows: list[dict[str, Any]] = []
    for msg in _walk_messages(messages):
        reactions = msg.get("reactions") or []
        if not reactions:
            continue
        # Copy once per reacted message (skip the common no-reaction case).
        payload = {k: v for k, v in msg.items() if k != "thread"}
        for rx in reactions:
            if not isinstance(rx, dict):
                continue
            name = rx.get("name") or ""
            for uid in rx.get("users") or []:
                if not uid:
                    continue
                rows.append(
                    {
                        "type": "message",
                        "channel": channel_id,
                        "reaction": name,
                        "user": uid,
                        "message": payload,
                    }
                )
    return rows


def _canvas_rows(listed: list[Any]) -> list[dict[str, Any]]:
    return [
        f
        for f in listed
        if isinstance(f, dict) and str(f.get("filetype") or "").lower() == "canvas"
    ]


def _copy_file_comments(path: Path, files: list[dict[str, Any]]) -> None:
    if not path.is_file():
        return
    try:
        raw = read_json(path)
    except (OSError, ValueError) as exc:
        print(f"  skip unreadable {path}: {exc}", file=sys.stderr, flush=True)
        return
    if not isinstance(raw, list):
        return
    by_id = {
        str(f.get("id")): f
        for f in raw
        if isinstance(f, dict) and f.get("id") and f.get("comments")
    }
    for fobj in files:
        if not isinstance(fobj, dict) or fobj.get("comments"):
            continue
        prev = by_id.get(str(fobj.get("id") or ""))
        if not prev:
            continue
        fobj["comments"] = prev["comments"]
        if prev.get("comments_count") is not None:
            fobj["comments_count"] = prev["comments_count"]


def _file_rows(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    files: list[dict[str, Any]] = []
    seen: set[str] = set()
    for msg in _walk_messages(messages):
        blobs: list[Any] = list(msg.get("files") or [])
        solo = msg.get("file")
        if isinstance(solo, dict):
            blobs.append(solo)
        for fobj in blobs:
            if not isinstance(fobj, dict):
                continue
            fid = str(fobj.get("id") or "")
            if not fid or fid in seen:
                continue
            seen.add(fid)
            files.append(fobj)
    return files


def _api_delay(api: SlackAPI) -> float:
    delay = api.delay
    if isinstance(delay, (int, float)) and delay > 0:
        return float(delay)
    return 0.0


def _rate_gate(delay: float) -> Callable[[], None]:
    """Return a callable that sleeps between successive API calls (not before the first)."""
    first = True

    def wait() -> None:
        nonlocal first
        if first:
            first = False
        elif delay:
            time.sleep(delay)

    return wait


def _attach_comments(api: SlackAPI, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    wait = _rate_gate(_api_delay(api))
    for fobj in files:
        if not isinstance(fobj, dict):
            continue
        try:
            count = int(fobj.get("comments_count") or 0)
        except (TypeError, ValueError):
            count = 0
        if count <= 0 or fobj.get("comments"):
            continue
        fid = fobj.get("id")
        if not fid:
            continue
        wait()
        try:
            info = api.get_file_info(str(fid)) or {}
        except Exception as exc:
            print(f"  file info {fid} skipped ({exc})", file=sys.stderr, flush=True)
            continue
        if not isinstance(info, dict):
            continue
        comments = info.get("comments")
        if comments:
            fobj["comments"] = comments
        if info.get("comments_count") is not None:
            fobj["comments_count"] = info["comments_count"]
    return files


def _merge_presence(api: SlackAPI, ws_dir: Path, channel_id: str, *, refresh: bool = False) -> None:
    path = ws_dir / "presence.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        # Corrupt presence.json must not be replaced with a partial re-fetch.
        existing = load_merge_json(path, dict)
    uids: set[str] = set()
    try:
        for uid in api.get_channel_members(channel_id) or []:
            if uid:
                uids.add(str(uid))
    except Exception as exc:
        print(f"  presence: channel members skipped ({exc})", file=sys.stderr, flush=True)
    try:
        auth_uid = (api.get_auth() or {}).get("user_id") or ""
        if auth_uid:
            uids.add(str(auth_uid))
    except Exception as exc:
        print(f"  presence: auth user skipped ({exc})", file=sys.stderr, flush=True)
    wait = _rate_gate(_api_delay(api))
    for uid in sorted(uids):
        if not refresh and uid in existing:
            continue
        wait()
        try:
            existing[uid] = api.get_presence(uid)
        except Exception as exc:
            print(f"  presence: {uid} skipped ({exc})", file=sys.stderr, flush=True)
    write_json(ws_dir, "presence.json", existing)


def _call_rows(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    calls: list[dict[str, Any]] = []
    seen: set[str] = set()
    for msg in _walk_messages(messages):
        for key in ("room", "call"):
            obj = msg.get(key)
            if not isinstance(obj, dict):
                continue
            cid = str(obj.get("id") or "")
            if not cid or cid in seen:
                continue
            seen.add(cid)
            calls.append(obj)
    return calls


def _thread_rows(out_dir: Path, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    channel_id = _channel_id_from_dir(out_dir)
    rows: list[dict[str, Any]] = []
    for msg in messages:
        if not isinstance(msg, dict):
            continue
        meta = thread_reply_meta(msg)
        if meta is None:
            continue
        count, latest = meta
        thread = [r for r in (msg.get("thread") or []) if isinstance(r, dict)]
        users = [str(u) for u in (msg.get("reply_users") or []) if u]
        if not users:
            seen: set[str] = set()
            for reply in thread:
                uid = str(reply.get("user") or "")
                if uid and uid not in seen:
                    seen.add(uid)
                    users.append(uid)
        rows.append(
            {
                "channel": channel_id,
                "thread_ts": msg.get("ts") or "",
                "reply_count": count,
                "latest_reply": latest,
                "reply_users": users,
                "reply_users_count": int(msg.get("reply_users_count") or len(users)),
            }
        )
    return rows


def merge_workspace_bots(ws_dir: Path, messages: list[dict[str, Any]]) -> None:
    bots: dict[str, Any] = {}
    for msg in messages:
        _record_bot(bots, msg)
        for reply in msg.get("thread") or []:
            if isinstance(reply, dict):
                _record_bot(bots, reply)
    if not bots:
        return
    path = ws_dir / "bots.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        # Corrupt bots.json must not be wiped by a partial bot re-merge.
        existing = load_merge_json(path, dict)
    existing.update(bots)
    write_json(ws_dir, "bots.json", existing)


def _record_bot(bots: dict[str, Any], msg: dict[str, Any]) -> None:
    bid = msg.get("bot_id")
    if not bid:
        return
    profile = msg.get("bot_profile")
    profile = profile if isinstance(profile, dict) else {}
    bots[str(bid)] = {
        "id": str(bid),
        "app_id": msg.get("app_id") or profile.get("app_id") or "",
        "name": profile.get("name") or msg.get("username") or msg.get("user_name") or str(bid),
        "deleted": bool(profile.get("deleted")),
        "icons": msg.get("icons") or profile.get("icons") or {},
        "team_id": profile.get("team_id") or msg.get("team") or "",
        "updated": profile.get("updated") or 0,
        "is_workflow_bot": bool(msg.get("is_workflow_bot") or profile.get("is_workflow_bot")),
    }
