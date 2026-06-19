import click
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from ssd.api import SlackAPI
from ssd.parser import parse_target
from ssd.output import channel_dir, read_cursor, write_cursor, merge_messages


def _since_to_ts(since: str) -> str:
    """Convert YYYY-MM-DD string to Unix timestamp string."""
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
