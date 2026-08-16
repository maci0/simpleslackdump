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


def _write_sidecars(api: SlackAPI, out_dir: Path, channel_id: str) -> None:
    try:
        write_json(out_dir, "channel.json", api.get_channel_info(channel_id))
    except Exception as exc:
        click.echo(f"  channel info skipped: {exc}", err=True)
    try:
        write_json(out_dir, "members.json", api.get_channel_members(channel_id))
    except Exception as exc:
        click.echo(f"  members skipped: {exc}", err=True)
    emoji_path = out_dir.parent / "emoji.json"
    if not emoji_path.exists():
        try:
            write_json(out_dir.parent, "emoji.json", api.get_emoji())
        except Exception as exc:
            click.echo(f"  emoji skipped: {exc}", err=True)
    cats_path = out_dir.parent / "emoji_categories.json"
    if not cats_path.exists():
        try:
            write_json(out_dir.parent, "emoji_categories.json", api.get_emoji_categories())
        except Exception as exc:
            click.echo(f"  emoji categories skipped: {exc}", err=True)
    auth_path = out_dir.parent / "auth.json"
    if not auth_path.exists():
        try:
            write_json(out_dir.parent, "auth.json", api.get_auth())
        except Exception as exc:
            click.echo(f"  auth skipped: {exc}", err=True)
    try:
        write_json(out_dir, "bookmarks.json", api.get_bookmarks(channel_id))
    except Exception as exc:
        click.echo(f"  bookmarks skipped: {exc}", err=True)
    try:
        write_json(out_dir, "pins.json", api.get_pins(channel_id))
    except Exception as exc:
        click.echo(f"  pins skipped: {exc}", err=True)
    groups_path = out_dir.parent / "usergroups.json"
    if not groups_path.exists():
        try:
            write_json(out_dir.parent, "usergroups.json", api.get_usergroups())
        except Exception as exc:
            click.echo(f"  usergroups skipped: {exc}", err=True)
    users_path = out_dir.parent / "users.json"
    if not users_path.exists():
        try:
            write_users(out_dir.parent, api.fetch_workspace_users())
        except Exception as exc:
            click.echo(f"  workspace users skipped: {exc}", err=True)
    conv_path = out_dir.parent / "conversations.json"
    if not conv_path.exists():
        try:
            write_json(out_dir.parent, "conversations.json", api.list_conversations())
        except Exception as exc:
            click.echo(f"  conversations skipped: {exc}", err=True)
    stars_path = out_dir.parent / "stars.json"
    if not stars_path.exists():
        try:
            write_json(out_dir.parent, "stars.json", api.get_stars())
        except Exception as exc:
            click.echo(f"  stars skipped: {exc}", err=True)
    reminders_path = out_dir.parent / "reminders.json"
    if not reminders_path.exists():
        try:
            write_json(out_dir.parent, "reminders.json", api.get_reminders())
        except Exception as exc:
            click.echo(f"  reminders skipped: {exc}", err=True)
    dnd_path = out_dir.parent / "dnd.json"
    if not dnd_path.exists():
        try:
            write_json(out_dir.parent, "dnd.json", api.get_dnd())
        except Exception as exc:
            click.echo(f"  dnd skipped: {exc}", err=True)
    team_profile_path = out_dir.parent / "team_profile.json"
    if not team_profile_path.exists():
        try:
            write_json(out_dir.parent, "team_profile.json", api.get_team_profile())
        except Exception as exc:
            click.echo(f"  team profile skipped: {exc}", err=True)
    scheduled_path = out_dir.parent / "scheduled_messages.json"
    if not scheduled_path.exists():
        try:
            write_json(out_dir.parent, "scheduled_messages.json", api.get_scheduled_messages())
        except Exception as exc:
            click.echo(f"  scheduled messages skipped: {exc}", err=True)
    team_path = out_dir.parent / "team.json"
    if not team_path.exists():
        try:
            write_json(out_dir.parent, "team.json", api.get_team_info())
        except Exception as exc:
            click.echo(f"  team info skipped: {exc}", err=True)
    files_path = out_dir.parent / "files.json"
    if not files_path.exists():
        try:
            write_json(out_dir.parent, "files.json", api.get_files())
        except Exception as exc:
            click.echo(f"  files list skipped: {exc}", err=True)
    remote_path = out_dir.parent / "remote_files.json"
    if not remote_path.exists():
        try:
            write_json(out_dir.parent, "remote_files.json", api.get_remote_files())
        except Exception as exc:
            click.echo(f"  remote files skipped: {exc}", err=True)
    presence_path = out_dir.parent / "presence.json"
    if not presence_path.exists():
        try:
            uid = (api.get_auth() or {}).get("user_id") or ""
            info = api.get_presence(uid or None)
            write_json(out_dir.parent, "presence.json", {uid: info} if uid else {})
        except Exception as exc:
            click.echo(f"  presence skipped: {exc}", err=True)
    billable_path = out_dir.parent / "billable_info.json"
    if not billable_path.exists():
        try:
            write_json(out_dir.parent, "billable_info.json", api.get_billable_info())
        except Exception as exc:
            click.echo(f"  billable info skipped: {exc}", err=True)
    logs_path = out_dir.parent / "integration_logs.json"
    if not logs_path.exists():
        try:
            write_json(out_dir.parent, "integration_logs.json", api.get_integration_logs())
        except Exception as exc:
            click.echo(f"  integration logs skipped: {exc}", err=True)
    access_path = out_dir.parent / "access_logs.json"
    if not access_path.exists():
        try:
            write_json(out_dir.parent, "access_logs.json", api.get_access_logs())
        except Exception as exc:
            click.echo(f"  access logs skipped: {exc}", err=True)
    prefs_path = out_dir.parent / "team_preferences.json"
    if not prefs_path.exists():
        try:
            write_json(out_dir.parent, "team_preferences.json", api.get_team_preferences())
        except Exception as exc:
            click.echo(f"  team preferences skipped: {exc}", err=True)
    ext_path = out_dir.parent / "external_teams.json"
    if not ext_path.exists():
        try:
            write_json(out_dir.parent, "external_teams.json", api.get_external_teams())
        except Exception as exc:
            click.echo(f"  external teams skipped: {exc}", err=True)
    teams_path = out_dir.parent / "teams.json"
    if not teams_path.exists():
        try:
            write_json(out_dir.parent, "teams.json", api.get_auth_teams())
        except Exception as exc:
            click.echo(f"  auth teams skipped: {exc}", err=True)


def write_channel_stats(out_dir: Path, messages: list[dict[str, Any]] | None = None) -> None:
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
    write_json(out_dir, "files.json", _file_rows(messages))
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
) -> None:
    parsed = parse_target(target)

    if parsed.thread_ts:
        channel_id = parsed.channel_id
        _, channel_name = api.resolve_channel(channel_id)
        out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
        thread_dir = out_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        t0 = time.monotonic()
        raw_replies = api.get_replies(channel_id, parsed.thread_ts)
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
        _write_sidecars(api, out_dir, channel_id)
        elapsed = time.monotonic() - t0
        click.echo(
            f"  thread {parsed.thread_ts}: {len(enriched)} replies"
            f" in {elapsed:.1f}s -> {thread_dir}"
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

    enriched = api.enrich(channel_id, raw_msgs)
    thread_count = sum(1 for m in enriched if m.get("thread"))
    reply_count = sum(len(m.get("thread", [])) for m in enriched)

    if attachments_enabled and token:
        from ssd.attachments import download_attachments

        files_count = sum(len(m.get("files", [])) for m in enriched)
        click.echo(f"  downloading {files_count} attachments...")
        enriched = download_attachments(out_dir, enriched, token)

    write_messages(out_dir, enriched)
    write_channel_stats(out_dir, enriched)
    if enriched:
        write_cursor(out_dir, max(m["ts"] for m in enriched))
    write_users(out_dir, api.get_user_profiles())
    _write_sidecars(api, out_dir, channel_id)

    total_elapsed = time.monotonic() - t0
    click.echo(
        f"  {len(enriched)} messages | {thread_count} threads | {reply_count} replies"
        f" | {total_elapsed:.1f}s total ({len(enriched) / max(total_elapsed, 0.1):.0f} msg/s)"
    )
