import json
import time
import click
from pathlib import Path
from ssd.api import SlackAPI
from ssd.parser import parse_target
from ssd.output import channel_dir, write_messages, write_cursor, format_markdown


def run_dump(api: SlackAPI, workspace: str, target: str, output_root: str, token: str = None, attachments_enabled: bool = False) -> None:
    parsed = parse_target(target)

    if parsed.thread_ts:
        channel_id = parsed.channel_id
        channel_name, _ = _resolve_name(api, channel_id)
        out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
        thread_dir = out_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        t0 = time.monotonic()
        raw_replies = api.get_replies(channel_id, parsed.thread_ts)
        enriched = _enrich_list(api, channel_id, raw_replies)
        sorted_msgs = sorted(enriched, key=lambda m: float(m["ts"]))
        thread_dir.mkdir(parents=True, exist_ok=True)
        (thread_dir / "thread.json").write_text(json.dumps(sorted_msgs, indent=2, ensure_ascii=False))
        (thread_dir / "thread.md").write_text(format_markdown(sorted_msgs))
        if enriched:
            write_cursor(thread_dir, enriched[-1]["ts"])
        elapsed = time.monotonic() - t0
        click.echo(f"  thread {parsed.thread_ts}: {len(enriched)} replies in {elapsed:.1f}s -> {thread_dir}")
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
    click.echo(f"  fetched {len(raw_msgs)} messages in {fetch_elapsed:.1f}s ({len(raw_msgs)/max(fetch_elapsed,0.1):.0f} msg/s)")

    t1 = time.monotonic()
    enriched = api.enrich(channel_id, raw_msgs)
    thread_count = sum(1 for m in enriched if m.get("thread"))
    reply_count = sum(len(m.get("thread", [])) for m in enriched)
    enrich_elapsed = time.monotonic() - t1

    if attachments_enabled:
        from ssd.attachments import download_attachments
        files_count = sum(len(m.get("files", [])) for m in enriched)
        click.echo(f"  downloading {files_count} attachments...")
        enriched = download_attachments(out_dir, enriched, token)

    write_messages(out_dir, enriched)
    if enriched:
        write_cursor(out_dir, max(m["ts"] for m in enriched))

    total_elapsed = time.monotonic() - t0
    click.echo(
        f"  {len(enriched)} messages | {thread_count} threads | {reply_count} replies"
        f" | {total_elapsed:.1f}s total ({len(enriched)/max(total_elapsed,0.1):.0f} msg/s)"
    )


def _resolve_name(api: SlackAPI, channel_id: str) -> tuple[str, str]:
    cid, name = api.resolve_channel(channel_id)
    return name, cid


def _enrich_list(api: SlackAPI, channel_id: str, raw: list[dict]) -> list[dict]:
    return api.enrich(channel_id, raw)
