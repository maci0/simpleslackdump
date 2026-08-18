"""Incremental sync orchestration from cursors and config entries."""

import re
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ssd.api import SlackAPI
from ssd.attachments import download_attachments
from ssd.output import (
    channel_dir,
    max_ts,
    merge_by_ts,
    merge_messages,
    read_cursor,
    read_json,
    reconcile_thread_meta,
    reply_count_int,
    write_cursor_from_messages,
    write_cursor_from_thread,
    write_messages,
    write_users,
)
from ssd.parser import parse_target, ts_key
from ssd.sidecars import (
    persist_thread_dump,
    prefetch_users,
    write_channel_stats,
    write_sidecars,
)

# Unix seconds or Slack ts (sec.usec). Rejects float literals like inf/1e10.
_SINCE_TS_RE = re.compile(r"^\d+(\.\d+)?$")


def _refresh_old_threads(
    api: SlackAPI,
    channel_id: str,
    out_dir: Path,
    sync_floor: str,
    token: str | None = None,
    attachments_enabled: bool = False,
) -> list[dict[str, Any]] | None:
    """Fetch new replies for threads on messages older than sync_floor.

    conversations_history with oldest= misses replies added to pre-sync_floor messages.
    This closes that gap by polling each known thread for replies newer than the
    last reply we already have.

    Returns the in-memory archive (possibly rewritten) so callers can reuse it for
    stats/cursor without a second disk read. Returns None when messages.json is
    missing or unreadable.
    """
    messages_path = out_dir / "messages.json"
    if not messages_path.exists():
        return None
    raw = read_json(messages_path)
    if not isinstance(raw, list):
        return None
    stored: list[dict[str, Any]] = raw
    prefetch_users(api)
    refreshed = 0
    try:
        team = api.get_workspace()
    except Exception:
        team = ""
    for msg in stored:
        msg_ts = msg.get("ts")
        if not msg_ts:
            continue
        # Skip messages newer than the sync floor: those were just enriched.
        # Messages at the floor itself are not re-fetched (oldest= is filtered
        # exclusive), so their threads still need a refresh pass here.
        if ts_key(msg_ts) > ts_key(sync_floor):
            continue
        thread = [r for r in (msg.get("thread") or []) if isinstance(r, dict) and r.get("ts")]
        claimed = reply_count_int(msg)
        # Known threads, or parents that claimed replies but have none stored yet
        # (failed enrich / partial dump). Brand-new threads on old messages with
        # reply_count still 0 are not discoverable without an API call per message.
        if not thread and claimed <= 0:
            continue
        time.sleep(api.delay)  # respect rate limit between per-thread API calls
        try:
            if thread:
                latest_reply_ts = max_ts(r["ts"] for r in thread)
                new_raw = api.get_replies(channel_id, msg_ts, oldest=latest_reply_ts)
                # oldest= is inclusive; skip the reply we already have
                new_raw = [r for r in new_raw if ts_key(r["ts"]) > ts_key(latest_reply_ts)]
            else:
                new_raw = api.get_replies(channel_id, msg_ts)
            if not new_raw:
                continue
            new_enriched = [
                api.enrich_reply(r, channel_id=channel_id, team=team) for r in new_raw
            ]  # collect fully before mutating
            if attachments_enabled and token:
                # enrich_reply already returns message-shaped dicts with files/ts
                new_enriched = download_attachments(out_dir, new_enriched, token)
            msg["thread"] = merge_by_ts(thread, new_enriched)
            reconcile_thread_meta(msg)
            refreshed += 1
        except Exception as exc:
            print(f"  thread {msg_ts}: skipped ({exc})", file=sys.stderr, flush=True)
    if refreshed:
        write_messages(out_dir, stored)
        print(f"  {refreshed} threads refreshed with new replies", flush=True)
    return stored


def _since_to_ts(since: str) -> str:
    """Convert YYYY-MM-DD or Unix/Slack timestamp string to a Unix timestamp string."""
    raw = since.strip()
    if _SINCE_TS_RE.fullmatch(raw):
        return raw
    try:
        dt = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC)
        # Midnight UTC has no fractional seconds; avoid str(float) trailing ".0".
        return str(int(dt.timestamp()))
    except ValueError:
        raise ValueError(
            f"Invalid --since value: {since!r}. Use YYYY-MM-DD or a Unix timestamp."
        ) from None


def _sync_floor(since_ts: str | None, cursor_ts: str | None) -> str | None:
    """Later of --since floor vs .cursor; either alone wins when the other is missing."""
    if since_ts and cursor_ts:
        if ts_key(since_ts) > ts_key(cursor_ts):
            return since_ts
        return cursor_ts
    return cursor_ts or since_ts


def run_sync(
    api: SlackAPI,
    workspace: str,
    target: str,
    output_root: str,
    since: str | None,
    token: str | None = None,
    attachments_enabled: bool = False,
) -> None:
    """Fetch messages newer than ``.cursor`` (or ``since``), merge into the archive.

    Uses the later of ``--since`` and the stored cursor as the fetch floor.
    After fetching new top-level messages, scans each known thread for replies
    newer than the last stored reply so replies to older threads are not missed.
    Writes workspace sidecars only when missing (``refresh_workspace=False``);
    per-channel sidecars are always rewritten.
    """
    parsed = parse_target(target)

    if parsed.thread_ts:
        assert parsed.channel_id is not None  # thread targets always carry a channel id
        channel_id = parsed.channel_id
        _, channel_name = api.resolve_channel(channel_id)
        out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
        thread_dir = out_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        thread_dir.mkdir(parents=True, exist_ok=True)
        since_ts = _since_to_ts(since) if since else None
        cursor_ts = read_cursor(thread_dir)
        # Match channel sync: floor is the later of --since vs .cursor.
        oldest = _sync_floor(since_ts, cursor_ts)
        existing_path = thread_dir / "thread.json"
        existing: list[Any] = read_json(existing_path) if existing_path.exists() else []
        has_parent = any(isinstance(m, dict) and m.get("ts") == parsed.thread_ts for m in existing)
        # Always fetch the parent when it is missing from thread.json, even if
        # --since set an oldest floor (parent ts is typically older than that floor).
        include_parent = not has_parent
        raw_replies = api.get_replies(
            channel_id, parsed.thread_ts, oldest=oldest, include_parent=include_parent
        )
        # oldest= is inclusive: filter to strictly newer replies to avoid reprocessing cursor.
        # Keep a missing parent even when it falls at or before the floor.
        if oldest:
            raw_replies = [
                r
                for r in raw_replies
                if ts_key(r["ts"]) > ts_key(oldest)
                or (include_parent and r.get("ts") == parsed.thread_ts)
            ]
        if not raw_replies:
            print("  no new replies", flush=True)
        else:
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
                refresh_workspace=False,
            )
            n_replies = sum(1 for m in enriched if m.get("ts") != parsed.thread_ts)
            print(f"  thread {parsed.thread_ts}: {n_replies} new replies", flush=True)
        # Same as channel sync: archive is the watermark source of truth, so an
        # empty poll still heals a drifted-high .cursor back to thread.json.
        write_cursor_from_thread(thread_dir)
        return

    ident = parsed.channel_id or parsed.channel_name
    assert ident  # parse_target always sets channel_id or channel_name
    channel_id, channel_name = api.resolve_channel(ident)

    out_dir = channel_dir(output_root, workspace, channel_name, channel_id)
    out_dir.mkdir(parents=True, exist_ok=True)

    since_ts = _since_to_ts(since) if since else None
    cursor_ts = read_cursor(out_dir)
    # since= is a floor: use the later of cursor vs floor so we never
    # re-fetch messages already captured, but never go older than the floor.
    oldest = _sync_floor(since_ts, cursor_ts)

    print(f"  #{channel_name} ({channel_id}) oldest={oldest or 'all'} -> {out_dir}", flush=True)

    raw_msgs = api.get_messages(channel_id, oldest=oldest)
    # Slack oldest= is inclusive: keep only strictly newer messages, matching
    # thread sync and watch_messages, so the cursor message is not re-merged.
    if oldest:
        raw_msgs = [m for m in raw_msgs if m.get("ts") and ts_key(m["ts"]) > ts_key(oldest)]
    archive: list[dict[str, Any]] | None = None
    if raw_msgs:
        prefetch_users(api)
        enriched = api.enrich(channel_id, raw_msgs)
        if attachments_enabled and token:
            enriched = download_attachments(out_dir, enriched, token)
        archive = merge_messages(out_dir, enriched)
        print(f"  {len(enriched)} new messages merged", flush=True)
    else:
        print("  no new top-level messages", flush=True)

    # conversations_history with oldest= never returns thread replies for messages
    # at or before the cursor. Scan stored threads for new replies explicitly.
    # Messages newer than the cursor were just enriched above; the cursor message
    # itself is refreshed here (exclusive history filter skips re-enrich).
    if oldest:
        refreshed = _refresh_old_threads(
            api, channel_id, out_dir, oldest, token=token, attachments_enabled=attachments_enabled
        )
        if refreshed is not None:
            archive = refreshed

    write_users(out_dir, api.get_user_profiles())
    write_sidecars(api, out_dir, channel_id, refresh_workspace=False)
    write_channel_stats(out_dir, messages=archive, api=api)
    # Derive the watermark from the archive after merges and thread refresh.
    write_cursor_from_messages(out_dir, messages=archive)
