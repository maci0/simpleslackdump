import json
import click
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from ssd.api import SlackAPI
from ssd.parser import parse_target
from ssd.output import channel_dir, read_cursor, write_cursor, merge_messages


def _since_to_ts(since: str) -> str:
    """Convert YYYY-MM-DD or Unix timestamp string to Unix timestamp string."""
    try:
        float(since)  # test if it's already a number
        return since
    except ValueError:
        dt = datetime.strptime(since, "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return str(dt.timestamp())


def run_sync(
    api: SlackAPI,
    workspace: str,
    target: str,
    output_root: str,
    since: Optional[str],
    token: str = None,
    attachments_enabled: bool = False,
) -> None:
    parsed = parse_target(target)

    if parsed.thread_ts:
        channel_id = parsed.channel_id
        _, channel_name = api.resolve_channel(channel_id)
        out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
        thread_dir = out_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        thread_dir.mkdir(parents=True, exist_ok=True)
        cursor = None if since else read_cursor(thread_dir)
        raw_replies = api.get_replies(channel_id, parsed.thread_ts)
        if not raw_replies:
            click.echo("  no new replies")
            return
        enriched = api.enrich(channel_id, raw_replies)
        sorted_msgs = sorted(enriched, key=lambda m: float(m["ts"]))
        thread_dir.mkdir(parents=True, exist_ok=True)
        (thread_dir / "thread.json").write_text(json.dumps(sorted_msgs, indent=2, ensure_ascii=False))
        from ssd.output import format_markdown
        (thread_dir / "thread.md").write_text(format_markdown(sorted_msgs))
        write_cursor(thread_dir, max(m["ts"] for m in enriched))
        click.echo(f"  thread {parsed.thread_ts}: {len(enriched)} replies")
        if attachments_enabled and token:
            from ssd.attachments import download_attachments
            download_attachments(thread_dir, enriched, token)
        return

    if parsed.channel_id:
        channel_id, channel_name = api.resolve_channel(parsed.channel_id)
    else:
        channel_id, channel_name = api.resolve_channel(parsed.channel_name)

    out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    if since:
        oldest = _since_to_ts(since)
    else:
        oldest = read_cursor(out_dir)

    click.echo(f"  #{channel_name} ({channel_id}) oldest={oldest or 'all'} -> {out_dir}")

    raw_msgs = api.get_messages(channel_id, oldest=oldest)
    if not raw_msgs:
        click.echo("  no new messages")
        return

    enriched = api.enrich(channel_id, raw_msgs)
    if attachments_enabled:
        from ssd.attachments import download_attachments
        enriched = download_attachments(out_dir, enriched, token)
    merge_messages(out_dir, enriched)
    latest = max(m["ts"] for m in enriched)
    write_cursor(out_dir, latest)
    click.echo(f"  {len(enriched)} new messages merged")
