"""Full-channel dump orchestration (history fetch, attachments, sidecars)."""

import time

from ssd.api import SlackAPI
from ssd.attachments import download_attachments
from ssd.output import (
    channel_dir,
    merge_messages,
    write_cursor_from_messages,
    write_users,
)
from ssd.parser import parse_target
from ssd.sidecars import (
    persist_thread_dump,
    prefetch_users,
    write_channel_stats,
    write_sidecars,
)


def run_dump(
    api: SlackAPI,
    workspace: str,
    target: str,
    output_root: str,
    token: str | None = None,
    attachments_enabled: bool = False,
    refresh_workspace: bool = True,
) -> None:
    """Fetch full channel or thread history, write messages.json/md, and refresh sidecars.

    Thread URLs write to ``<channel_dir>/thread_<ts>/``. Channel targets merge
    into any existing dump so a re-dump cannot wipe prior history.
    ``refresh_workspace`` controls whether workspace-level sidecars (users, emoji,
    conversations…) are overwritten or only created when missing.
    """
    parsed = parse_target(target)

    if parsed.thread_ts:
        assert parsed.channel_id is not None  # thread targets always carry a channel id
        channel_id = parsed.channel_id
        _, channel_name = api.resolve_channel(channel_id)
        out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
        thread_dir = out_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        t0 = time.monotonic()
        raw_replies = api.get_replies(channel_id, parsed.thread_ts, include_parent=True)
        if raw_replies:
            prefetch_users(api)
        try:
            team = api.get_workspace()
        except Exception:
            team = ""
        enriched = [api.enrich_reply(r, channel_id=channel_id, team=team) for r in raw_replies]
        if attachments_enabled and token:
            # README: files land in <channel_dir>/attachments/, including thread dumps.
            enriched = download_attachments(out_dir, enriched, token)
        persist_thread_dump(
            api,
            out_dir,
            thread_dir,
            parsed.thread_ts,
            channel_id,
            enriched,
            refresh_workspace=refresh_workspace,
        )
        elapsed = time.monotonic() - t0
        n_replies = sum(1 for m in enriched if m.get("ts") != parsed.thread_ts)
        print(
            f"  thread {parsed.thread_ts}: {n_replies} replies in {elapsed:.1f}s -> {thread_dir}",
            flush=True,
        )
        return

    ident = parsed.channel_id or parsed.channel_name
    assert ident  # parse_target always sets channel_id or channel_name
    channel_id, channel_name = api.resolve_channel(ident)

    out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
    print(f"  #{channel_name} ({channel_id}) -> {out_dir}", flush=True)

    t0 = time.monotonic()
    raw_msgs = api.get_messages(channel_id)
    fetch_elapsed = time.monotonic() - t0
    print(
        f"  fetched {len(raw_msgs)} messages in {fetch_elapsed:.1f}s"
        f" ({len(raw_msgs) / max(fetch_elapsed, 0.1):.0f} msg/s)",
        flush=True,
    )

    if raw_msgs:
        prefetch_users(api)
    enriched = api.enrich(channel_id, raw_msgs)
    thread_count = sum(1 for m in enriched if m.get("thread"))
    reply_count = sum(len(m.get("thread", [])) for m in enriched)

    if attachments_enabled and token:
        files_count = sum(len(m.get("files", [])) for m in enriched)
        print(f"  downloading {files_count} attachments...", flush=True)
        enriched = download_attachments(out_dir, enriched, token)

    # Merge into any existing dump so a partial/empty re-fetch cannot wipe the archive.
    merged = merge_messages(out_dir, enriched)
    write_channel_stats(out_dir, messages=merged, api=api)
    # .cursor follows messages.json (not the fetch batch) so a partial re-fetch
    # cannot rewind below archived history or leap past what was written.
    write_cursor_from_messages(out_dir, messages=merged)
    write_users(out_dir, api.get_user_profiles())
    write_sidecars(api, out_dir, channel_id, refresh_workspace=refresh_workspace)

    total_elapsed = time.monotonic() - t0
    print(
        f"  {len(enriched)} messages | {thread_count} threads | {reply_count} replies"
        f" | {total_elapsed:.1f}s total ({len(enriched) / max(total_elapsed, 0.1):.0f} msg/s)",
        flush=True,
    )
