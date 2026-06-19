import click
from pathlib import Path
from ssd.api import SlackAPI
from ssd.parser import parse_target
from ssd.output import channel_dir, write_messages, write_cursor


def run_dump(api: SlackAPI, workspace: str, target: str, output_root: str) -> None:
    parsed = parse_target(target)

    if parsed.thread_ts:
        # thread-only dump
        channel_id = parsed.channel_id
        channel_name, _ = _resolve_name(api, channel_id)
        out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
        thread_dir = out_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        raw_replies = api.get_replies(channel_id, parsed.thread_ts)
        enriched = _enrich_list(api, channel_id, raw_replies)
        write_messages(thread_dir, enriched)
        if enriched:
            write_cursor(thread_dir, enriched[-1]["ts"])
        click.echo(f"  thread {parsed.thread_ts}: {len(enriched)} replies -> {thread_dir}")
        return

    # channel dump
    if parsed.channel_id:
        channel_id, channel_name = api.resolve_channel(parsed.channel_id)
    else:
        channel_id, channel_name = api.resolve_channel(parsed.channel_name)

    out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
    click.echo(f"  #{channel_name} ({channel_id}) -> {out_dir}")

    raw_msgs = api.get_messages(channel_id)
    enriched = api.enrich(channel_id, raw_msgs)
    write_messages(out_dir, enriched)
    if enriched:
        write_cursor(out_dir, max(m["ts"] for m in enriched))
    click.echo(f"  {len(enriched)} messages written")


def _resolve_name(api: SlackAPI, channel_id: str) -> tuple[str, str]:
    cid, name = api.resolve_channel(channel_id)
    return name, cid


def _enrich_list(api: SlackAPI, channel_id: str, raw: list[dict]) -> list[dict]:
    return api.enrich(channel_id, raw)
