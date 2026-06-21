import json
import time
from typing import Any

import click

from ssd.api import SlackAPI
from ssd.output import (
    _atomic_write,
    channel_dir,
    format_markdown,
    write_cursor,
    write_messages,
    write_users,
)
from ssd.parser import parse_target


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
        enriched = [api.enrich_reply(r) for r in raw_replies]
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
        _atomic_write(
            thread_dir / "thread.json", json.dumps(sorted_msgs, indent=2, ensure_ascii=False)
        )
        _atomic_write(thread_dir / "thread.md", format_markdown(sorted_msgs))
        if enriched:
            write_cursor(thread_dir, max(m["ts"] for m in enriched))
        write_users(thread_dir, api.get_user_profiles())
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
        f" ({len(raw_msgs)/max(fetch_elapsed,0.1):.0f} msg/s)"
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
    if enriched:
        write_cursor(out_dir, max(m["ts"] for m in enriched))
    write_users(out_dir, api.get_user_profiles())

    total_elapsed = time.monotonic() - t0
    click.echo(
        f"  {len(enriched)} messages | {thread_count} threads | {reply_count} replies"
        f" | {total_elapsed:.1f}s total ({len(enriched)/max(total_elapsed,0.1):.0f} msg/s)"
    )
