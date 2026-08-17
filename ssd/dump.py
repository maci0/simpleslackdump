import json
import time
from pathlib import Path
from typing import Any

import click

from ssd.api import SlackAPI
from ssd.output import (
    _atomic_write,
    _dumps,
    channel_dir,
    format_markdown,
    write_cursor,
    write_json,
    write_messages,
    write_users,
)
from ssd.parser import parse_target


def _prefetch_users(api: SlackAPI) -> None:
    try:
        api.fetch_workspace_users()
    except Exception as exc:
        click.echo(f"  workspace users skipped: {exc}", err=True)


def _write_sidecars(
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
            click.echo(f"  {label} skipped: {exc}", err=True)

    try:
        write_json(out_dir, "channel.json", api.get_channel_info(channel_id))
    except Exception as exc:
        click.echo(f"  channel info skipped: {exc}", err=True)
    try:
        write_json(out_dir, "members.json", api.get_channel_members(channel_id))
    except Exception as exc:
        click.echo(f"  members skipped: {exc}", err=True)
    write_ws("emoji.json", api.get_emoji, "emoji")
    write_ws("emoji_categories.json", api.get_emoji_categories, "emoji categories")
    write_ws("auth.json", api.get_auth, "auth")
    try:
        write_json(out_dir, "bookmarks.json", api.get_bookmarks(channel_id))
    except Exception as exc:
        click.echo(f"  bookmarks skipped: {exc}", err=True)
    try:
        write_json(out_dir, "pins.json", api.get_pins(channel_id))
    except Exception as exc:
        click.echo(f"  pins skipped: {exc}", err=True)
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
            click.echo(f"  files list skipped: {exc}", err=True)
    elif need_canvas:
        try:
            raw = json.loads(files_path.read_text()) if files_path.is_file() else []
            listed = raw if isinstance(raw, list) else []
            write_json(ws, "canvases.json", _canvas_rows(listed))
        except Exception as exc:
            click.echo(f"  canvases skipped: {exc}", err=True)
    write_ws("remote_files.json", api.get_remote_files, "remote files")
    try:
        _merge_presence(api, ws, channel_id)
    except Exception as exc:
        click.echo(f"  presence skipped: {exc}", err=True)
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
        raw = json.loads(path.read_text())
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
    merge_workspace_bots(out_dir.parent, messages)


def _channel_id_from_dir(out_dir: Path) -> str:
    name = out_dir.name
    if "_" not in name:
        return ""
    return name.rsplit("_", 1)[-1]


def _reaction_rows(out_dir: Path, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    channel_id = _channel_id_from_dir(out_dir)
    rows: list[dict[str, Any]] = []

    def walk(msg: dict[str, Any]) -> None:
        payload = {k: v for k, v in msg.items() if k != "thread"}
        for rx in msg.get("reactions") or []:
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
        for reply in msg.get("thread") or []:
            if isinstance(reply, dict):
                walk(reply)

    for msg in messages:
        if isinstance(msg, dict):
            walk(msg)
    return rows


def _walk_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []

    def walk(msg: dict[str, Any]) -> None:
        out.append(msg)
        for reply in msg.get("thread") or []:
            if isinstance(reply, dict):
                walk(reply)

    for msg in messages:
        if isinstance(msg, dict):
            walk(msg)
    return out


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
        raw = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
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
    delay = getattr(api, "delay", 0)
    if isinstance(delay, (int, float)) and delay > 0:
        return float(delay)
    return 0.0


def _attach_comments(api: SlackAPI, files: list[dict[str, Any]]) -> list[dict[str, Any]]:
    delay = _api_delay(api)
    first = True
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
        if first:
            first = False
        elif delay:
            time.sleep(delay)
        try:
            info = api.get_file_info(str(fid)) or {}
        except Exception:
            continue
        if not isinstance(info, dict):
            continue
        comments = info.get("comments")
        if comments:
            fobj["comments"] = comments
        if info.get("comments_count") is not None:
            fobj["comments_count"] = info["comments_count"]
    return files


def _merge_presence(api: SlackAPI, ws_dir: Path, channel_id: str) -> None:
    path = ws_dir / "presence.json"
    existing: dict[str, Any] = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            existing = raw
    uids: set[str] = set()
    try:
        for uid in api.get_channel_members(channel_id) or []:
            if uid:
                uids.add(str(uid))
    except Exception:
        pass
    try:
        auth_uid = (api.get_auth() or {}).get("user_id") or ""
        if auth_uid:
            uids.add(str(auth_uid))
    except Exception:
        pass
    delay = _api_delay(api)
    first = True
    for uid in sorted(uids):
        if uid in existing:
            continue
        if first:
            first = False
        elif delay:
            time.sleep(delay)
        try:
            existing[uid] = api.get_presence(uid)
        except Exception:
            continue
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
        thread = msg.get("thread") or []
        if thread:
            latest = thread[-1].get("ts") if isinstance(thread[-1], dict) else ""
            count = len(thread)
        elif msg.get("reply_count"):
            latest = msg.get("latest_reply") or ""
            count = int(msg.get("reply_count") or 0)
        else:
            continue
        users = [str(u) for u in (msg.get("reply_users") or []) if u]
        if not users:
            seen: set[str] = set()
            for reply in thread:
                if not isinstance(reply, dict):
                    continue
                uid = str(reply.get("user") or "")
                if uid and uid not in seen:
                    seen.add(uid)
                    users.append(uid)
        rows.append(
            {
                "channel": channel_id,
                "thread_ts": msg.get("ts") or "",
                "reply_count": count,
                "latest_reply": latest or msg.get("latest_reply") or "",
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
        try:
            raw = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            raw = {}
        if isinstance(raw, dict):
            existing = raw
    existing.update(bots)
    write_json(ws_dir, "bots.json", existing)


def _record_bot(bots: dict[str, Any], msg: dict[str, Any]) -> None:
    bid = msg.get("bot_id")
    if not bid:
        return
    profile = msg.get("bot_profile") if isinstance(msg.get("bot_profile"), dict) else {}
    bots[str(bid)] = {
        "id": str(bid),
        "app_id": msg.get("app_id") or profile.get("app_id") or "",
        "name": profile.get("name") or msg.get("username") or msg.get("user_name") or str(bid),
        "deleted": bool(profile.get("deleted")),
        "icons": profile.get("icons") or msg.get("icons") or {},
        "team_id": profile.get("team_id") or msg.get("team") or "",
        "updated": profile.get("updated") or 0,
        "is_workflow_bot": bool(profile.get("is_workflow_bot")),
    }


def run_dump(
    api: SlackAPI,
    workspace: str,
    target: str,
    output_root: str,
    token: str | None = None,
    attachments_enabled: bool = False,
    refresh_workspace: bool = True,
) -> None:
    parsed = parse_target(target)

    if parsed.thread_ts:
        channel_id = parsed.channel_id
        _, channel_name = api.resolve_channel(channel_id)
        out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
        thread_dir = out_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        t0 = time.monotonic()
        raw_replies = api.get_replies(channel_id, parsed.thread_ts, include_parent=True)
        if raw_replies:
            _prefetch_users(api)
        enriched = [api.enrich_reply(r, channel_id=channel_id) for r in raw_replies]
        if attachments_enabled and token:
            from ssd.attachments import download_attachments

            enriched = download_attachments(thread_dir, enriched, token)
        # Merge with any previously synced replies (same logic as run_sync thread path)
        existing_path = thread_dir / "thread.json"
        existing: list[dict[str, Any]] = (
            json.loads(existing_path.read_text()) if existing_path.exists() else []
        )
        by_ts = {m["ts"]: m for m in existing}
        for m in enriched:
            by_ts[m["ts"]] = m
        sorted_msgs = sorted(by_ts.values(), key=lambda m: float(m["ts"]))
        thread_dir.mkdir(parents=True, exist_ok=True)
        _atomic_write(thread_dir / "thread.json", _dumps(sorted_msgs))
        _atomic_write(thread_dir / "thread.md", format_markdown(sorted_msgs))
        merge_workspace_bots(out_dir.parent, sorted_msgs)
        if enriched:
            write_cursor(thread_dir, max(m["ts"] for m in enriched))
        write_users(thread_dir, api.get_user_profiles())
        _write_sidecars(api, out_dir, channel_id, refresh_workspace=refresh_workspace)
        elapsed = time.monotonic() - t0
        n_replies = sum(1 for m in enriched if m.get("ts") != parsed.thread_ts)
        click.echo(
            f"  thread {parsed.thread_ts}: {n_replies} replies in {elapsed:.1f}s -> {thread_dir}"
        )
        return

    if parsed.channel_id:
        channel_id, channel_name = api.resolve_channel(parsed.channel_id)
    else:
        channel_id, channel_name = api.resolve_channel(parsed.channel_name)

    out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
    click.echo(f"  #{channel_name} ({channel_id}) -> {out_dir}")

    t0 = time.monotonic()
    raw_msgs = api.get_messages(channel_id)
    fetch_elapsed = time.monotonic() - t0
    click.echo(
        f"  fetched {len(raw_msgs)} messages in {fetch_elapsed:.1f}s"
        f" ({len(raw_msgs) / max(fetch_elapsed, 0.1):.0f} msg/s)"
    )

    if raw_msgs:
        _prefetch_users(api)
    enriched = api.enrich(channel_id, raw_msgs)
    thread_count = sum(1 for m in enriched if m.get("thread"))
    reply_count = sum(len(m.get("thread", [])) for m in enriched)

    if attachments_enabled and token:
        from ssd.attachments import download_attachments

        files_count = sum(len(m.get("files", [])) for m in enriched)
        click.echo(f"  downloading {files_count} attachments...")
        enriched = download_attachments(out_dir, enriched, token)

    write_messages(out_dir, enriched)
    write_channel_stats(out_dir, enriched, api=api)
    if enriched:
        write_cursor(out_dir, max(m["ts"] for m in enriched))
    write_users(out_dir, api.get_user_profiles())
    _write_sidecars(api, out_dir, channel_id, refresh_workspace=refresh_workspace)

    total_elapsed = time.monotonic() - t0
    click.echo(
        f"  {len(enriched)} messages | {thread_count} threads | {reply_count} replies"
        f" | {total_elapsed:.1f}s total ({len(enriched) / max(total_elapsed, 0.1):.0f} msg/s)"
    )
