import json
from datetime import UTC, datetime
from pathlib import Path

import click

from ssd.api import SlackAPI
from ssd.output import (
    _atomic_write,
    channel_dir,
    format_markdown,
    merge_messages,
    read_cursor,
    write_cursor,
    write_messages,
    write_users,
)
from ssd.parser import parse_target


def _refresh_old_threads(
    api: SlackAPI,
    channel_id: str,
    out_dir: Path,
    sync_floor: str,
    token: str | None = None,
    attachments_enabled: bool = False,
) -> None:
    """Fetch new replies for threads on messages older than sync_floor.

    conversations_history with oldest= misses replies added to pre-sync_floor messages.
    This closes that gap by polling each known thread for replies newer than the
    last reply we already have.
    """
    import time

    messages_path = out_dir / "messages.json"
    if not messages_path.exists():
        return
    stored: list[dict] = json.loads(messages_path.read_text())
    refreshed = 0
    for msg in stored:
        # Skip messages at or after the sync floor — already enriched in this run
        if float(msg["ts"]) >= float(sync_floor):
            continue
        thread = msg.get("thread")
        if not thread:
            continue
        latest_reply_ts = max(r["ts"] for r in thread)
        time.sleep(api.delay)  # respect rate limit between per-thread API calls
        new_raw = api.get_replies(channel_id, msg["ts"], oldest=latest_reply_ts)
        # oldest= is inclusive; skip the reply we already have
        new_raw = [r for r in new_raw if float(r["ts"]) > float(latest_reply_ts)]
        if not new_raw:
            continue
        new_enriched = [api.enrich_reply(r) for r in new_raw]  # collect fully before mutating
        if attachments_enabled and token:
            from ssd.attachments import download_attachments

            # download_attachments expects message dicts; wrap each reply as a standalone message
            wrapped = [
                {
                    "ts": r["ts"],
                    "user_name": r.get("user_name", ""),
                    "text": r.get("text", ""),
                    "reactions": [],
                    "thread": [],
                    "files": r.get("files", []),
                }
                for r in new_enriched
            ]
            downloaded = download_attachments(out_dir, wrapped, token)
            # Merge file info back into new_enriched
            file_map = {m["ts"]: m.get("files", []) for m in downloaded}
            new_enriched = [
                {**r, "files": file_map.get(r["ts"], r.get("files", []))}
                for r in new_enriched
            ]
        msg["thread"].extend(new_enriched)
        msg["thread"].sort(key=lambda r: float(r["ts"]))
        refreshed += 1
    if refreshed:
        write_messages(out_dir, stored)
        click.echo(f"  {refreshed} threads refreshed with new replies")


def _since_to_ts(since: str) -> str:
    """Convert YYYY-MM-DD or Unix timestamp string to Unix timestamp string."""
    try:
        float(since)
        return since
    except ValueError:
        pass
    try:
        dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
        return str(dt.timestamp())
    except ValueError:
        raise ValueError(
            f"Invalid --since value: {since!r}. Use YYYY-MM-DD or a Unix timestamp."
        ) from None


def run_sync(
    api: SlackAPI,
    workspace: str,
    target: str,
    output_root: str,
    since: str | None,
    token: str | None = None,
    attachments_enabled: bool = False,
) -> None:
    parsed = parse_target(target)

    if parsed.thread_ts:
        channel_id = parsed.channel_id
        _, channel_name = api.resolve_channel(channel_id)
        out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
        thread_dir = out_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        thread_dir.mkdir(parents=True, exist_ok=True)  # single mkdir
        oldest = _since_to_ts(since) if since else read_cursor(thread_dir)
        raw_replies = api.get_replies(channel_id, parsed.thread_ts, oldest=oldest)
        # oldest= is inclusive — filter to strictly newer replies to avoid reprocessing cursor
        if oldest:
            raw_replies = [r for r in raw_replies if float(r["ts"]) > float(oldest)]
        if not raw_replies:
            click.echo("  no new replies")
            return
        enriched = [api.enrich_reply(r) for r in raw_replies]
        if attachments_enabled and token:
            from ssd.attachments import download_attachments

            enriched = download_attachments(thread_dir, enriched, token)
        # load existing thread replies and merge by ts (incremental sync)
        existing_path = thread_dir / "thread.json"
        existing: list[dict] = (
            json.loads(existing_path.read_text()) if existing_path.exists() else []
        )
        by_ts = {m["ts"]: m for m in existing}
        for m in enriched:
            by_ts[m["ts"]] = m
        sorted_msgs = sorted(by_ts.values(), key=lambda m: float(m["ts"]))
        _atomic_write(
            thread_dir / "thread.json", json.dumps(sorted_msgs, indent=2, ensure_ascii=False)
        )
        _atomic_write(thread_dir / "thread.md", format_markdown(sorted_msgs))
        write_cursor(thread_dir, max(m["ts"] for m in enriched))
        write_users(thread_dir, api.get_user_profiles())
        click.echo(f"  thread {parsed.thread_ts}: {len(enriched)} new replies")
        return

    if parsed.channel_id:
        channel_id, channel_name = api.resolve_channel(parsed.channel_id)
    else:
        channel_id, channel_name = api.resolve_channel(parsed.channel_name)

    out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    since_ts = _since_to_ts(since) if since else None
    cursor_ts = read_cursor(out_dir)
    # since= is a floor — use the later of cursor vs floor so we never
    # re-fetch messages already captured, but never go older than the floor.
    if since_ts and cursor_ts:
        oldest = since_ts if float(since_ts) > float(cursor_ts) else cursor_ts
    else:
        oldest = cursor_ts or since_ts

    click.echo(f"  #{channel_name} ({channel_id}) oldest={oldest or 'all'} -> {out_dir}")

    raw_msgs = api.get_messages(channel_id, oldest=oldest)
    if raw_msgs:
        enriched = api.enrich(channel_id, raw_msgs)
        if attachments_enabled and token:
            from ssd.attachments import download_attachments

            enriched = download_attachments(out_dir, enriched, token)
        merge_messages(out_dir, enriched)
        latest = max(m["ts"] for m in enriched)
        write_cursor(out_dir, latest)
        click.echo(f"  {len(enriched)} new messages merged")
    else:
        click.echo("  no new top-level messages")

    # conversations_history with oldest= never returns thread replies for messages
    # older than the cursor. Scan all stored threads for new replies explicitly.
    # Only check threads for messages older than the cursor — newer messages were
    # just enriched above and already have current replies.
    if oldest:
        _refresh_old_threads(
            api, channel_id, out_dir, oldest, token=token, attachments_enabled=attachments_enabled
        )

    write_users(out_dir, api.get_user_profiles())
