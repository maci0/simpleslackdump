import json
from datetime import UTC, datetime
from pathlib import Path

import click

from ssd.api import SlackAPI
from ssd.output import channel_dir, merge_messages, read_cursor, write_cursor, write_messages
from ssd.parser import parse_target


def _refresh_old_threads(api: SlackAPI, channel_id: str, out_dir: Path, cursor_ts: str) -> None:
    """Fetch new replies for threads on messages older than cursor_ts.

    conversations_history with oldest= misses replies added to pre-cursor messages.
    This closes that gap by polling each known thread for replies newer than the
    last reply we already have.
    """
    messages_path = out_dir / "messages.json"
    if not messages_path.exists():
        return
    stored: list[dict] = json.loads(messages_path.read_text())
    refreshed = 0
    for msg in stored:
        # Skip messages at or after the cursor — already enriched in this run
        if float(msg["ts"]) >= float(cursor_ts):
            continue
        thread = msg.get("thread")
        if not thread:
            continue
        latest_reply_ts = max(r["ts"] for r in thread)
        new_raw = api.get_replies(channel_id, msg["ts"], oldest=latest_reply_ts)
        # oldest= is inclusive; skip the reply we already have
        new_raw = [r for r in new_raw if float(r["ts"]) > float(latest_reply_ts)]
        if not new_raw:
            continue
        new_enriched = [api.enrich_reply(r) for r in new_raw]  # collect fully before mutating
        msg["thread"].extend(new_enriched)
        msg["thread"].sort(key=lambda r: float(r["ts"]))
        refreshed += 1
    if refreshed:
        write_messages(out_dir, stored)
        click.echo(f"  {refreshed} threads refreshed with new replies")


def _since_to_ts(since: str) -> str:
    """Convert YYYY-MM-DD or Unix timestamp string to Unix timestamp string."""
    try:
        float(since)  # test if it's already a number
        return since
    except ValueError:
        dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=UTC)
        return str(dt.timestamp())


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
        from ssd.output import _atomic_write, format_markdown

        _atomic_write(
            thread_dir / "thread.json", json.dumps(sorted_msgs, indent=2, ensure_ascii=False)
        )
        _atomic_write(thread_dir / "thread.md", format_markdown(sorted_msgs))
        write_cursor(thread_dir, max(m["ts"] for m in enriched))
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
        oldest = since_ts if since_ts > cursor_ts else cursor_ts
    else:
        oldest = cursor_ts or since_ts

    click.echo(f"  #{channel_name} ({channel_id}) oldest={oldest or 'all'} -> {out_dir}")

    raw_msgs = api.get_messages(channel_id, oldest=oldest)
    if not raw_msgs:
        click.echo("  no new messages")
        return

    enriched = api.enrich(channel_id, raw_msgs)
    if attachments_enabled and token:
        from ssd.attachments import download_attachments

        enriched = download_attachments(out_dir, enriched, token)
    merge_messages(out_dir, enriched)
    latest = max(m["ts"] for m in enriched)
    write_cursor(out_dir, latest)
    click.echo(f"  {len(enriched)} new messages merged")

    # conversations_history with oldest= never returns thread replies for messages
    # older than the cursor. Scan all stored threads for new replies explicitly.
    # Only check threads for messages older than the cursor — newer messages were
    # just enriched above and already have current replies.
    if oldest:
        _refresh_old_threads(api, channel_id, out_dir, oldest)
