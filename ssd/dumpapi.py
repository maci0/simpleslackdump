"""Read-only Slack Web API facade over ssd dumps and Slack export dirs. No network.

Construct from an output root, a workspace dir, a Slack export, or a channel dir::

    from ssd import DumpClient
    client = DumpClient("output")
    client.conversations_history(channel="C123")
    client.api_call("conversations.history", params={"channel": "C123"})

Open walks directory names only. Each ``messages.json`` / ``thread.json`` /
export day file is parsed at most once (cached). Catalog stubs with no
directory skip JSON parse. Sidecar ``*.search`` methods filter workspace or
per-channel JSON and do not walk messages when that sidecar exists.

``search.messages`` / ``search.files`` / ``search.all``: substring plus
``from:`` ``in:`` ``to:`` ``with:`` ``has:`` ``is:`` ``before:`` ``after:``
``around:`` ``on:`` ``during:``, ``from:me`` / ``to:me`` / ``with:me`` /
``in:me``, and ``-term`` exclusion. Relative dates on after/before/during:
``today`` ``yesterday`` ``week`` ``month`` ``year`` ``lastweek`` ``lastmonth``
``lastyear``. ``has::emoji:`` matches a named reaction.

``has:`` file, reaction, pin, link, canvas, image, video, audio, snippet,
attachment, mention, space, block, email, call, x_files, pdf, replies,
spreadsheet, metadata, remote, zip, presentation, list, doc, txt, button, gif,
json, csv, xml, md, yaml, toml, html, svg, python, js, ts, go, rust, sql, css,
sh, workflow. On ``search.messages``, ``has:star`` / ``has:stars`` /
``has:starred`` alias to ``is:starred``.

``is:`` on messages: thread, bot, starred/saved, edited, unthreaded, broadcast,
locked, tombstone/deleted, app, file_share, me, hidden, join, leave, topic,
purpose, parent, archive, unarchive, rename, subscribed, pinned, workflow,
call/huddle, ephemeral, creator, delayed, scheduled, guest, admin, owner,
app_user, me_message, stranger, invited, primary_owner, ultra_restricted,
canvas, forgotten, enterprise, moved, connector, workflow_bot.

``is:`` on channels (other channels skipped before parse): dm/im, mpim,
channel, group, private, public, shared, ext_shared, org_shared, general,
pending_ext_shared, member, open, org_default, frozen, global_shared,
org_mandatory, read_only, thread_only, non_threadable, user_deleted, muted,
unreads, pending_shared, has_canvas, im_blocked, connected, unlinked, internal,
host, connected_limited, archived.

Distinct pairs: ``is:me`` (authed user) vs ``is:me_message`` (``/me`` subtype);
``is:archive`` (subtype) vs ``is:archived`` (channel flag); ``is:app`` vs
``is:app_user``; ``is:canvas`` vs ``is:has_canvas``; ``is:pending_shared`` vs
``is:pending_ext_shared``; ``is:workflow`` vs ``is:workflow_bot``;
``is:connected`` vs ``is:connected_limited``; ``is:starred`` is a starred
message, not channel ``is_starred``.

Gaps vs live Slack:
- write methods are not implemented
- ``rtm.connect`` / ``rtm.start`` return snapshot dicts; no websocket
- not full Slack search syntax (no ranking)
- without ``channel.json`` or ``conversations.json``, a C-prefix id is treated as public
- dumped ``text`` has ``<@U...>`` resolved to ``@display_name``; ``text_raw`` keeps the original
- standalone thread dumps include the parent when Slack returned it
- standalone ``thread_*/thread.json`` dumps merge into the parent in
  ``messages.json`` when both exist (replies unioned by ts)
- ``conversations.members`` prefers ``members.json``; else IM ``user`` plus
  auth user; else people who posted, replied, or reacted
- ``files.comments`` reads comments stored on dumped file objects
- ``presence.json`` is channel members plus the authenticated user
"""

import heapq
import inspect
import sys
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path
from typing import Any, cast

from ssd.dumpload import (
    LOAD_WORKERS,
    Channel,
    Loaded,
    build_word_bigrams,
    discover,
    doc_text,
    docs_for_query,
    empty_loaded,
    ingest,
    ingest_users,
    iter_msgs,
    kinds_for,
    read_channel_messages,
    split_parents,
    threads_from_loaded,
)
from ssd.dumpsearch import (
    MSG_IS,
    WORD_RE,
    channel_flag_is_ok,
    channel_in_scope,
    channel_with_ok,
    compile_time,
    expand_me,
    file_has_ok,
    is_remote_file,
    msg_from_ok,
    msg_has_ok,
    msg_is_ok,
    msg_time_ok,
    msg_to_ok,
    msg_ts_key,
    norm_from,
    parse_bound,
    parse_search,
    split_negation,
)
from ssd.output import (
    dumps_bytes,
    merge_by_ts,
    read_json,
    reconcile_thread_meta,
    thread_reply_meta,
)
from ssd.parser import ALL_CONV_TYPES, ts_key

_MAX_LIMIT = 10_000  # ponytail: no Slack 999 cap; still bound so a typo cannot allocate forever
# search.messages keeps a top-N heap of size start+count; cap so page*count
# cannot allocate unbounded memory (total is still counted over the full scan).
_MAX_SEARCH_NEED = 100_000

_CATALOG_KEYS = (
    "is_private",
    "created",
    "num_members",
    "creator",
    "is_archived",
    "is_channel",
    "is_group",
    "is_im",
    "is_mpim",
    "is_shared",
    "is_ext_shared",
    "is_org_shared",
    "is_general",
    "is_pending_ext_shared",
    "locale",
    "updated",
    "previous_names",
    "unlinked",
    "is_member",
    "conversation_host_id",
    "connected_team_ids",
    "internal_team_ids",
    "pending_shared",
    "parent_conversation",
    "context_team_id",
    "is_open",
    "is_org_default",
    "is_frozen",
    "is_global_shared",
    "is_org_mandatory",
    "is_read_only",
    "is_thread_only",
    "is_non_threadable",
    "is_user_deleted",
    "is_muted",
    "is_starred",
    "shared_team_ids",
    "pending_connected_team_ids",
    "connected_limited_team_ids",
    "properties",
    "priority",
    "name_normalized",
    "user",
    "name",
    "topic_creator",
    "topic_last_set",
    "purpose_creator",
    "purpose_last_set",
    "is_moved",
    "use_case",
    "last_read",
    "unread_count",
    "unread_count_display",
    "latest",
    "enterprise_id",
    "file_id",
    "is_pending_shared",
    "has_canvas",
    "is_im_blocked",
)
_CATALOG_NAMES = (
    "conversations.json",
    "channels.json",
    "groups.json",
    "dms.json",
    "mpims.json",
)


def _usergroup_disabled(group: dict[str, Any]) -> bool:
    if group.get("deleted") or group.get("disabled"):
        return True
    raw = group.get("date_delete")
    try:
        return float(raw or 0) != 0
    except (TypeError, ValueError):
        return bool(raw)


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


def _err(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error}


def _search_rows(
    rows: Any,
    query: str,
    *,
    key: str,
    count: int | None = None,
    page: int | None = None,
    limit: int = _MAX_LIMIT,
    cursor: str | None = None,
    include_has_more: bool = False,
) -> dict[str, Any]:
    needle = query.strip().lower()
    if not needle:
        return _err("invalid_arguments")
    hits = [row for row in (rows or []) if isinstance(row, dict) and _sidecar_hit(row, needle)]
    return _paged(
        hits,
        count=count,
        page=page,
        limit=limit,
        cursor=cursor,
        key=key,
        include_has_more=include_has_more,
    )


def _search_map(mapping: Any, query: str, *, key: str) -> dict[str, Any]:
    needle = query.strip().lower()
    if not needle:
        return _err("invalid_arguments")
    rows = mapping if isinstance(mapping, dict) else {}
    hits: dict[str, Any] = {}
    for uid, row in rows.items():
        blob = str(uid).lower()
        if needle in blob:
            hits[uid] = row
            continue
        if isinstance(row, dict) and _sidecar_hit({**row, "user_id": str(uid)}, needle):
            hits[uid] = row
    return _ok(**{key: hits})


def _first_ts(row: dict[str, Any], *keys: str) -> int:
    """Return the first parseable timestamp field as usec int (matches msg_ts_key)."""
    for key in keys:
        v = row.get(key)
        if v is not None and v != "":
            return msg_ts_key(v)
    return 0


def _paged(
    items: list[Any],
    *,
    key: str,
    limit: int,
    cursor: str | None = None,
    count: int | None = None,
    page: int | None = None,
    include_has_more: bool = False,
) -> dict[str, Any]:
    if count is not None:
        limit = int(count)
    if page is not None and not cursor:
        cursor = str((max(int(page), 1) - 1) * min(max(limit, 1), _MAX_LIMIT))
    if cursor in (None, ""):
        offset = 0
    else:
        try:
            offset = int(cursor)
        except ValueError:
            return _err("invalid_cursor")
        if offset < 0:
            return _err("invalid_cursor")
    limit = min(max(limit, 1), _MAX_LIMIT)
    chunk = items[offset : offset + limit]
    next_off = offset + len(chunk)
    has_more = next_off < len(items)
    next_cursor = str(next_off) if has_more else ""
    payload: dict[str, Any] = {
        key: chunk,
        "response_metadata": {"next_cursor": next_cursor},
    }
    if include_has_more:
        payload["has_more"] = has_more
    return _ok(**payload)


def _filter_ts_range(
    items: list[Any],
    oldest: str | None,
    latest: str | None,
    inclusive: bool,
) -> list[Any]:
    if oldest is None and latest is None:
        return items
    start = msg_ts_key(oldest) if oldest is not None else None
    end = msg_ts_key(latest) if latest is not None else None
    out: list[Any] = []
    for m in items:
        if not isinstance(m, dict) or not m.get("ts"):
            continue
        t = msg_ts_key(str(m["ts"]))
        if start is not None and (t < start or (not inclusive and t == start)):
            continue
        if end is not None and (t > end or (not inclusive and t == end)):
            continue
        out.append(m)
    return out


def _slack_user(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": profile.get("id", ""),
        "name": profile.get("handle") or profile.get("display_name") or profile.get("id", ""),
        "is_bot": bool(profile.get("is_bot")),
        "deleted": bool(profile.get("deleted")),
        "is_admin": bool(profile.get("is_admin")),
        "is_owner": bool(profile.get("is_owner")),
        "is_restricted": bool(profile.get("is_restricted")),
        "is_ultra_restricted": bool(profile.get("is_ultra_restricted")),
        "is_app_user": bool(profile.get("is_app_user")),
        "is_stranger": bool(profile.get("is_stranger")),
        "is_invited_user": bool(profile.get("is_invited_user")),
        "is_primary_owner": bool(profile.get("is_primary_owner")),
        "always_active": bool(profile.get("always_active")),
        "is_email_confirmed": bool(profile.get("is_email_confirmed")),
        "huddle_state": profile.get("huddle_state") or "",
        "huddle_state_expiration_ts": profile.get("huddle_state_expiration_ts") or 0,
        "who_can_share_contact_card": profile.get("who_can_share_contact_card") or "",
        "team_id": profile.get("team_id") or "",
        "real_name": profile.get("real_name") or "",
        "is_forgotten": bool(profile.get("is_forgotten")),
        "is_workflow_bot": bool(profile.get("is_workflow_bot")),
        "has_2fa": bool(profile.get("has_2fa")),
        "two_factor_type": profile.get("two_factor_type") or "",
        "guest_invited_by": profile.get("guest_invited_by") or "",
        "is_connector": bool(profile.get("is_connector")),
        "enterprise_user": profile.get("enterprise_user") or {},
        "locale": profile.get("locale") or "",
        "color": profile.get("color") or "",
        "updated": profile.get("updated") or 0,
        "tz_offset": profile.get("tz_offset") or 0,
        "tz": profile.get("timezone") or "",
        "tz_label": profile.get("timezone_label") or "",
        "profile": {
            "real_name": profile.get("real_name") or "",
            "display_name": profile.get("display_name") or "",
            "email": profile.get("email") or "",
            "title": profile.get("title") or "",
            "phone": profile.get("phone") or "",
            "status_text": profile.get("status_text") or "",
            "status_emoji": profile.get("status_emoji") or "",
            "status_text_canonical": profile.get("status_text_canonical") or "",
            "image_192": profile.get("image_192") or profile.get("image") or "",
            "first_name": profile.get("first_name") or "",
            "last_name": profile.get("last_name") or "",
            "skype": profile.get("skype") or "",
            "status_expiration": profile.get("status_expiration") or 0,
            "avatar_hash": profile.get("avatar_hash") or "",
            "pronouns": profile.get("pronouns") or "",
            "start_date": profile.get("start_date") or "",
            "status_emoji_display_info": profile.get("status_emoji_display_info") or [],
            "image_72": profile.get("image_72") or "",
            "image_512": profile.get("image_512") or "",
            "image_original": profile.get("image_original") or "",
            "image_24": profile.get("image_24") or "",
            "image_32": profile.get("image_32") or "",
            "image_48": profile.get("image_48") or "",
            "image_1024": profile.get("image_1024") or "",
            "is_custom_image": bool(profile.get("is_custom_image")),
            "fields": profile.get("fields") or {},
            "display_name_normalized": profile.get("display_name_normalized")
            or profile.get("display_name")
            or "",
            "real_name_normalized": profile.get("real_name_normalized")
            or profile.get("real_name")
            or "",
            "guest_expiration_ts": profile.get("guest_expiration_ts") or 0,
            "bot_id": profile.get("bot_id") or "",
            "api_app_id": profile.get("api_app_id") or "",
            "team": profile.get("team") or "",
        },
    }


def _bot_obj(source: dict[str, Any], want: str) -> dict[str, Any]:
    profile = source.get("bot_profile")
    profile = profile if isinstance(profile, dict) else {}
    return {
        "id": source.get("id") or source.get("bot_id") or want,
        "app_id": source.get("app_id") or profile.get("app_id") or "",
        "name": source.get("name") or source.get("username") or source.get("user_name") or want,
        "deleted": bool(source.get("deleted") or profile.get("deleted")),
        "icons": source.get("icons") or profile.get("icons") or {},
        "team_id": source.get("team_id") or profile.get("team_id") or source.get("team") or "",
        "updated": source.get("updated") or profile.get("updated") or 0,
        "is_workflow_bot": bool(source.get("is_workflow_bot") or profile.get("is_workflow_bot")),
    }


def _reminder_done(item: dict[str, Any]) -> bool:
    if item.get("complete") is True:
        return True
    try:
        return float(item.get("complete_ts") or 0) != 0
    except (TypeError, ValueError):
        return bool(item.get("complete_ts"))


def _copy_extras(msg: dict[str, Any], item: dict[str, Any]) -> None:
    for key, value in msg.items():
        if key not in item and key != "thread":
            item[key] = value


def _history_item(msg: dict[str, Any]) -> dict[str, Any]:
    item = {
        "type": "message",
        "ts": msg.get("ts", ""),
        "user": msg.get("user") or "",
        "text": msg.get("text") or "",
        "user_name": msg.get("user_name") or "",
        "reactions": msg.get("reactions") or [],
        "files": msg.get("files") or [],
    }
    _copy_extras(msg, item)
    # Never shrink reply_count to len(thread) when the archive claims more;
    # latest_reply must follow numeric ts, not nested list order.
    meta = thread_reply_meta(msg)
    if meta is not None:
        count, latest = meta
        item["reply_count"] = count
        if latest:
            item["latest_reply"] = latest
        item["thread_ts"] = item.get("thread_ts") or msg["ts"]
    return item


def _reply_item(msg: dict[str, Any]) -> dict[str, Any]:
    item = {
        "type": "message",
        "ts": msg.get("ts", ""),
        "user": msg.get("user") or "",
        "text": msg.get("text") or "",
        "user_name": msg.get("user_name") or "",
        "reactions": msg.get("reactions") or [],
        "files": msg.get("files") or [],
        "thread_ts": msg.get("thread_ts") or msg.get("ts", ""),
    }
    _copy_extras(msg, item)
    return item


def _reaction_items(channel_id: str, msg: dict[str, Any], user: str | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    history: dict[str, Any] | None = None
    for rx in msg.get("reactions") or []:
        name = rx.get("name") or ""
        users = [u for u in (rx.get("users") or []) if u]
        if user:
            if user not in users:
                continue
            users = [user]
        if not users:
            continue
        if history is None:
            history = _history_item(msg)
        for uid in users:
            items.append(
                {
                    "type": "message",
                    "channel": channel_id,
                    "reaction": name,
                    "user": uid,
                    "message": history,
                }
            )
    return items


def _item_ts(item: dict[str, Any]) -> str:
    msg = item.get("message")
    msg = msg if isinstance(msg, dict) else {}
    return str(msg.get("ts") or item.get("ts") or "")


def _item_by_ts(items: Any, ts: str) -> dict[str, Any]:
    want = ts.strip()
    for item in items or []:
        if isinstance(item, dict) and _item_ts(item) == want:
            return _ok(item=item)
    return _err("not_found")


_SIDECAR_KEYS = (
    "id",
    "title",
    "link",
    "text",
    "channel",
    "channel_id",
    "type",
    "user",
    "user_id",
    "thread_ts",
    "reaction",
    "name",
    "filename",
    "external_id",
    "ip",
    "username",
    "app_id",
    "service_id",
    "change_type",
    "domain",
    "comment",
    "handle",
    "real_name",
    "email",
    "display_name",
    "slack_id",
    "label",
    "ts",
)


def _sidecar_hit(item: dict[str, Any], needle: str) -> bool:
    """True when ``needle`` appears in any searchable field (or across fields).

    Per-field check first so common hits exit without allocating a joined blob.
    Spaced needles that span field boundaries still match the joined form.
    """
    chunks: list[str] = []

    def _consider(val: Any) -> bool:
        if not val:
            return False
        s = str(val).lower()
        if needle in s:
            return True
        chunks.append(s)
        return False

    for key in _SIDECAR_KEYS:
        if _consider(item.get(key)):
            return True
    msg = item.get("message")
    if isinstance(msg, dict):
        for key in ("text", "ts", "user"):
            if _consider(msg.get(key)):
                return True
    profile = item.get("profile")
    if isinstance(profile, dict):
        for key in ("email", "display_name", "real_name", "title"):
            if _consider(profile.get(key)):
                return True
    return bool(chunks) and " " in needle and needle in " ".join(chunks)


def _uid_hit(uid: str, profile: dict[str, Any] | None, needle: str) -> bool:
    if needle in str(uid).lower():
        return True
    if not isinstance(profile, dict):
        return False
    if _sidecar_hit(profile, needle):
        return True
    # Dump profiles use handle; Slack-shaped users use name. Avoid building a
    # full _slack_user() dict just to re-run the same field scan.
    name = profile.get("name")
    if name and needle in str(name).lower():
        return True
    nested = profile.get("profile")
    if isinstance(nested, dict):
        for key in ("email", "display_name", "real_name", "title"):
            val = nested.get(key)
            if val and needle in str(val).lower():
                return True
    return False


class DumpClient:
    """Read-only Slack Web API over a local ssd dump or Slack export. No tokens, no network."""

    def __init__(self, path: str | Path):
        self.root = Path(path)
        if not self.root.exists():
            raise FileNotFoundError(self.root)
        self._channels = discover(self.root)
        self._profiles: dict[str, dict[str, Any]] | None = None
        self._files: dict[str, dict[str, Any]] = {}
        self._all_loaded = False
        self._emoji: dict[str, str] | None = None
        self._auth: dict[str, Any] | None = None
        self._catalog: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._load_all_cv = threading.Condition(self._lock)
        self._load_all_in_progress = False
        self._ws_files_loaded = False
        self._ws_files_in_progress = False
        self._files_complete = False
        self._users_merged: dict[str, dict[str, Any]] | None = None
        self._starred: set[tuple[str, str]] | None = None
        self._bots: dict[str, dict[str, Any]] | None = None
        self._json_cache: dict[str, Any] = {}
        self._calls: dict[str, dict[str, Any]] | None = None
        self._channels_by_name: dict[str, Channel] | None = None
        self._apply_catalog()

    @property
    def has_channels(self) -> bool:
        return bool(self._channels)

    def _workspace_auth(self) -> dict[str, Any]:
        with self._lock:
            if self._auth is not None:
                return self._auth
        raw = self._first_json("auth.json")
        auth = raw if isinstance(raw, dict) else {}
        with self._lock:
            if self._auth is not None:
                return self._auth
            self._auth = auth
            return auth

    def _first_json(self, name: str) -> Any:
        with self._lock:
            if name in self._json_cache:
                return self._json_cache[name]
        seen: set[Path] = set()
        candidates = [self.root / name]
        for ch in self._channels.values():
            candidates.append(ch.path.parent / name)
            candidates.append(ch.path / name)
        found: Any = None
        for path in candidates:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                found = read_json(path)
            except (OSError, ValueError):
                continue
            break
        with self._lock:
            if name in self._json_cache:
                return self._json_cache[name]
            self._json_cache[name] = found
            return found

    def _ingest_file_rows(self, raw: Any) -> None:
        if not isinstance(raw, list):
            return
        with self._lock:
            for fobj in raw:
                if not isinstance(fobj, dict):
                    continue
                fid = fobj.get("id")
                if fid and str(fid) not in self._files:
                    self._files[str(fid)] = fobj

    def _files_snapshot(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._files.values())

    def _files_get(self, fid: str) -> dict[str, Any] | None:
        with self._lock:
            return self._files.get(fid)

    def _files_len(self) -> int:
        with self._lock:
            return len(self._files)

    def _ensure_workspace_files(self) -> None:
        # Serialize concurrent callers: flag is published only after ingest
        # finishes so waiters never observe a half-filled _files map.
        with self._load_all_cv:
            while self._ws_files_in_progress:
                self._load_all_cv.wait()
            if self._ws_files_loaded:
                return
            self._ws_files_in_progress = True
        try:
            self._ingest_file_rows(self._first_json("files.json"))
            self._ingest_file_rows(self._first_json("remote_files.json"))
            self._ingest_file_rows(self._first_json("canvases.json"))
            if self._files_len():
                with self._load_all_cv:
                    self._files_complete = True
                    self._ws_files_loaded = True
                    self._ws_files_in_progress = False
                    self._load_all_cv.notify_all()
                return
            missing = False
            for ch in self._channels.values():
                raw = self._channel_sidecar(ch, "files.json")
                if isinstance(raw, list):
                    self._ingest_file_rows(raw)
                else:
                    missing = True
            with self._load_all_cv:
                self._files_complete = not missing
                self._ws_files_loaded = True
                self._ws_files_in_progress = False
                self._load_all_cv.notify_all()
        except BaseException:
            with self._load_all_cv:
                self._ws_files_in_progress = False
                self._load_all_cv.notify_all()
            raise

    def _fill_files(self) -> None:
        self._ensure_workspace_files()
        with self._load_all_cv:
            if self._files_complete:
                return
        self._load_all()
        with self._lock:
            self._files_complete = True

    def _channel_files(self, ch: Channel) -> list[dict[str, Any]]:
        raw = self._channel_sidecar(ch, "files.json")
        if isinstance(raw, list):
            return [f for f in raw if isinstance(f, dict)]
        return list(self._load(ch).files.values())

    def _listed_files(
        self,
        *,
        channel: str | None = None,
        user: str | None = None,
        ts_from: float | int | str | None = None,
        ts_to: float | int | str | None = None,
        types: str | None = None,
    ) -> list[dict[str, Any]] | dict[str, Any]:
        if channel:
            ch = self._get(channel)
            if ch is None:
                return _err("channel_not_found")
            files = list(self._channel_files(ch))
        else:
            self._fill_files()
            files = self._files_snapshot()
        if user:
            files = [f for f in files if f.get("user") == user]
        if ts_from is not None:
            start = msg_ts_key(ts_from)
            files = [f for f in files if _first_ts(f, "created", "timestamp", "updated") >= start]
        if ts_to is not None:
            end = msg_ts_key(ts_to)
            files = [f for f in files if _first_ts(f, "created", "timestamp", "updated") <= end]
        if types:
            wanted = {t.strip().lower() for t in types.split(",") if t.strip()}
            files = [
                f
                for f in files
                if str(f.get("filetype") or "").lower() in wanted
                or str(f.get("pretty_type") or "").lower() in wanted
                or str(f.get("mimetype") or "").lower() in wanted
            ]
        files.sort(key=lambda f: str(f.get("id") or ""))
        return files

    def _apply_catalog(self) -> None:
        seen: set[Path] = set()
        candidates: list[Path] = [self.root / name for name in _CATALOG_NAMES]
        for ch in self._channels.values():
            for name in _CATALOG_NAMES:
                candidates.append(ch.path.parent / name)
        for path in candidates:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                raw = read_json(path)
            except (OSError, ValueError):
                continue
            if not isinstance(raw, list):
                continue
            for entry in raw:
                if not isinstance(entry, dict):
                    continue
                cid = str(entry.get("id") or "")
                if cid:
                    self._catalog[cid] = entry
        if not self._catalog:
            return
        by_catalog_name: dict[str, str] = {}
        for cid, entry in self._catalog.items():
            name = str(entry.get("name") or "")
            if name and name not in by_catalog_name:
                by_catalog_name[name] = cid
        remap: list[tuple[str, str]] = []
        for old_id, ch in self._channels.items():
            if old_id in self._catalog:
                continue
            mapped_id = by_catalog_name.get(ch.name)
            if mapped_id:
                remap.append((old_id, mapped_id))
        for old_id, new_id in remap:
            if new_id in self._channels:
                continue
            ch = self._channels.pop(old_id)
            ch.id = new_id
            ch.kinds = kinds_for(new_id, self._catalog.get(new_id))
            self._channels[new_id] = ch
        sample = next(iter(self._channels.values()), None)
        workspace = sample.workspace if sample else self.root.name
        parent = sample.path.parent if sample else self.root
        for cid, entry in self._catalog.items():
            if cid in self._channels:
                continue
            name = str(entry.get("name") or cid)
            self._channels[cid] = Channel(
                id=cid,
                name=name,
                workspace=workspace,
                path=parent / f"{name}_{cid}",
                kinds=kinds_for(cid, entry),
                thread_dumps={},
            )

    def _get(self, channel: str) -> Channel | None:
        raw = channel.lstrip("#")
        hit = self._channels.get(raw)
        if hit is not None:
            return hit
        with self._lock:
            by_name = self._channels_by_name
            if by_name is None:
                by_name = {}
                for ch in self._channels.values():
                    by_name.setdefault(ch.name, ch)
                self._channels_by_name = by_name
            return by_name.get(raw)

    def _in_scope_channels(self, in_toks: list[str]) -> list[Channel]:
        if not in_toks:
            return list(self._channels.values())
        in_me = any(norm_from(t).lower() == "me" for t in in_toks)
        named = [t for t in in_toks if norm_from(t).lower() != "me"]
        named_chs = (
            [ch for ch in self._channels.values() if channel_in_scope(ch, named)] if named else []
        )
        if not in_me:
            return named_chs
        auth = self._workspace_auth()
        me = {x.lower() for x in expand_me(["me"], auth) if x and x != "\0"}
        dms: list[Channel] = []
        for ch in self._channels.values():
            self._channel_obj(ch)  # refine kinds from channel.json / catalog
            if ch.kinds & {"im", "mpim"} and me & {u.lower() for u in self._roster(ch)}:
                dms.append(ch)
        if not named:
            return dms
        seen = {ch.id: ch for ch in named_chs}
        for ch in dms:
            seen[ch.id] = ch
        return list(seen.values())

    def _roster(self, ch: Channel) -> list[str]:
        with self._lock:
            if ch.roster is not None:
                return ch.roster
        raw = self._channel_sidecar(ch, "members.json")
        if isinstance(raw, list):
            roster = [str(x) for x in raw]
            with self._lock:
                if ch.roster is None:
                    ch.roster = roster
                return ch.roster
        other = str(self._channel_obj(ch).get("user") or "")
        if other:
            ids = [other]
            me = str(self._workspace_auth().get("user_id") or "")
            if me and me not in ids:
                ids.append(me)
            with self._lock:
                if ch.roster is None:
                    ch.roster = ids
                return ch.roster
        roster = self._load(ch).member_ids
        with self._lock:
            if ch.roster is None:
                ch.roster = roster
            return ch.roster

    def _channel_sidecar(self, ch: Channel, name: str) -> Any:
        with self._lock:
            store = ch.sidecars
            if store is not None and name in store:
                return store[name]
        path = ch.path / name
        if path.is_file():
            try:
                raw = read_json(path)
            except (OSError, ValueError):
                raw = None
        else:
            raw = None
        with self._lock:
            store = ch.sidecars
            if store is None:
                store = {}
                ch.sidecars = store
            if name in store:
                return store[name]
            store[name] = raw
            return raw

    def _with_ok(
        self,
        ch: Channel,
        with_toks: list[str],
        profiles: dict[str, dict[str, Any]],
    ) -> bool:
        self._channel_obj(ch)  # refine kinds before im/mpim gate
        extra = ch.loaded.users_extra if ch.loaded is not None else {}
        return channel_with_ok(ch, self._roster(ch), with_toks, profiles, extra)

    def _load(
        self,
        ch: Channel,
        *,
        merge_files: bool = True,
        parallel_days: bool = True,
    ) -> Loaded:
        if ch.loaded is not None:
            return ch.loaded
        if not ch.path.is_dir():
            loaded = empty_loaded()
            with self._lock:
                if ch.loaded is not None:
                    return ch.loaded
                ch.loaded = loaded
            return loaded
        messages, loose = split_parents(read_channel_messages(ch.path, parallel=parallel_days))
        by_ts: dict[str, dict[str, Any]] = {}
        all_by_ts: dict[str, dict[str, Any]] = {}
        thread_root: dict[str, str] = {}
        files: dict[str, dict[str, Any]] = {}
        users_extra: dict[str, dict[str, Any]] = {}
        members: set[str] = set()

        def index(msg: dict[str, Any], root_ts: str | None) -> None:
            ts = msg.get("ts")
            if ts:
                key = str(ts)
                all_by_ts[key] = msg
                if root_ts:
                    thread_root[key] = root_ts
            ingest(msg, files, users_extra, members, ch.id)

        for msg in messages:
            ts = msg.get("ts")
            if ts:
                by_ts[str(ts)] = msg
            index(msg, None)
            root = str(ts) if ts else ""
            for reply in msg.get("thread") or []:
                index(reply, root or None)
        thread_only: dict[str, list[dict[str, Any]]] = {k: list(v) for k, v in loose.items()}
        for ts, replies in list(thread_only.items()):
            for reply in replies:
                index(reply, ts)
        # Flat export / messages.json rows with thread_ts ≠ ts land in loose.
        # When the parent is already present, fold them in (same as thread_dumps)
        # so threads_list / threads_info are not duplicated with an empty latest_reply.
        for ts, replies in list(thread_only.items()):
            if ts not in by_ts:
                continue
            existing = by_ts[ts]
            base_thread = [
                r for r in (existing.get("thread") or []) if isinstance(r, dict) and r.get("ts")
            ]
            existing["thread"] = merge_by_ts(base_thread, replies)
            reconcile_thread_meta(existing)
            for reply in existing["thread"]:
                index(reply, ts)
            del thread_only[ts]
        for ts, tpath in ch.thread_dumps.items():
            if tpath.is_file():
                try:
                    raw = read_json(tpath)
                except (OSError, ValueError) as exc:
                    print(f"  skip unreadable {tpath}: {exc}", file=sys.stderr, flush=True)
                    raw = []
            else:
                raw = []
            rows = [m for m in (raw if isinstance(raw, list) else []) if isinstance(m, dict)]
            dump_parent: dict[str, Any] | None = None
            rest: list[dict[str, Any]] = []
            for reply in rows:
                if dump_parent is None and str(reply.get("ts") or "") == ts:
                    dump_parent = reply
                else:
                    rest.append(reply)
            if ts in by_ts:
                # Parent already in messages.json: fold the standalone dump in
                # rather than ignoring replies that history never captured.
                existing = by_ts[ts]
                base_thread = [
                    r for r in (existing.get("thread") or []) if isinstance(r, dict) and r.get("ts")
                ]
                existing["thread"] = merge_by_ts(base_thread, rest)
                reconcile_thread_meta(existing)
                for reply in existing["thread"]:
                    index(reply, ts)
                continue
            if dump_parent is not None:
                dump_parent = {**dump_parent, "thread": rest}
                reconcile_thread_meta(dump_parent)
                messages.append(dump_parent)
                by_ts[ts] = dump_parent
                index(dump_parent, None)
                for reply in rest:
                    index(reply, ts)
            else:
                thread_only[ts] = rest
                for reply in rest:
                    index(reply, ts)
        member_ids = sorted(members)
        members_path = ch.path / "members.json"
        if members_path.is_file():
            try:
                roster = read_json(members_path)
            except (OSError, ValueError):
                roster = None
            if isinstance(roster, list):
                member_ids = [str(x) for x in roster]
        loaded = Loaded(
            messages,
            by_ts,
            all_by_ts,
            thread_root,
            thread_only,
            files,
            users_extra,
            None,
            member_ids,
            [],
            {},
        )
        with self._lock:
            if ch.loaded is not None:
                return ch.loaded
            # merge_files=False when _load_all parallelizes: files are merged
            # afterward in stable channel order so scheduling cannot pick a winner.
            if merge_files:
                self._files.update(files)
            ch.loaded = loaded
        return ch.loaded

    def _ensure_search(self, loaded: Loaded) -> Loaded:
        # Double-checked under _lock: Loaded is published by parallel _load
        # workers, then search indexes are filled lazily. Without the lock,
        # concurrent readers can observe docs/words/search_ready torn.
        if loaded.search_ready:
            return loaded
        docs: list[tuple[str, dict[str, Any]]] = []
        words: dict[str, list[int]] = {}
        for msg in iter_msgs(loaded):
            i = len(docs)
            text_l = doc_text(msg)
            docs.append((text_l, msg))
            seen_w: set[str] = set()
            for w in WORD_RE.findall(text_l):
                if w in seen_w:
                    continue
                seen_w.add(w)
                words.setdefault(w, []).append(i)
        bigrams = build_word_bigrams(words)
        with self._lock:
            if loaded.search_ready:
                return loaded
            loaded.docs = docs
            loaded.words = words
            loaded.word_bigrams = bigrams
            loaded.search_ready = True
        return loaded

    def _ensure_history(self, loaded: Loaded) -> list[dict[str, Any]]:
        items = loaded.history_newest
        if items is not None:
            return items
        built = [_history_item(m) for m in reversed(loaded.messages) if m.get("ts")]
        with self._lock:
            if loaded.history_newest is None:
                loaded.history_newest = built
            return loaded.history_newest

    def _channel_meta(self, ch: Channel) -> dict[str, Any]:
        with self._lock:
            if ch.meta_checked:
                return ch.meta or {}
        path = ch.path / "channel.json"
        meta: dict[str, Any] = {}
        if path.is_file():
            try:
                raw = read_json(path)
            except (OSError, ValueError):
                raw = None
            if isinstance(raw, dict):
                meta = raw
        with self._lock:
            if ch.meta_checked:
                return ch.meta or {}
            ch.meta = meta
            ch.meta_checked = True
            return meta

    def _load_all(self) -> None:
        # Serialize concurrent callers: only one thread runs the channel pool.
        # Waiters block on the condition until load completes (or fails).
        with self._load_all_cv:
            while self._load_all_in_progress:
                self._load_all_cv.wait()
            if self._all_loaded:
                return
            self._load_all_in_progress = True
        try:
            channels = [ch for ch in self._channels.values() if ch.path.is_dir()]
            if len(channels) <= 1:
                for ch in channels:
                    self._load(ch)
            else:
                workers = min(LOAD_WORKERS, len(channels))
                # parallel_days=False: avoid nested ThreadPoolExecutors (channel pool
                # x day-file pool). merge_files=False: workers must not race on
                # self._files; merge in discovery order after the pool joins.
                with ThreadPoolExecutor(max_workers=workers) as pool:
                    list(
                        pool.map(
                            partial(self._load, merge_files=False, parallel_days=False),
                            channels,
                        )
                    )
                with self._lock:
                    for ch in channels:
                        loaded = ch.loaded
                        if loaded is not None:
                            self._files.update(loaded.files)
            with self._load_all_cv:
                self._all_loaded = True
                self._load_all_in_progress = False
                self._load_all_cv.notify_all()
        except BaseException:
            with self._load_all_cv:
                self._load_all_in_progress = False
                self._load_all_cv.notify_all()
            raise

    def _ensure_profiles(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._profiles is not None:
                return self._profiles
        profiles: dict[str, dict[str, Any]] = {}
        seen: set[Path] = set()
        # Build a deduplicated candidate list. ch.path.parent (workspace dir) is
        # shared across all channels, so add it once via seen. Thread-level
        # users.json files are written by persist_thread_dump but always contain
        # a subset of the channel-level data (same api.get_user_profiles() call).
        # Skip thread dirs when the channel users.json already exists so a
        # workspace with C channels x T thread dumps doesn't stat C*T extra paths.
        candidates: list[Path] = []
        pending_seen: set[Path] = set()

        def _add(p: Path) -> None:
            if p not in pending_seen:
                pending_seen.add(p)
                candidates.append(p)

        _add(self.root / "users.json")
        for ch in self._channels.values():
            _add(ch.path.parent / "users.json")
            _add(ch.path / "users.json")
            # ponytail: skip thread dirs when channel users.json exists; they hold
            # identical data. Fall back to thread dirs only for standalone thread
            # dumps where no channel-level file has been written yet.
            if not (ch.path / "users.json").is_file():
                for p in ch.thread_dumps.values():
                    _add(p.parent / "users.json")
        for path in candidates:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            try:
                raw = read_json(path)
            except (OSError, ValueError):
                continue
            ingest_users(raw, profiles)
        with self._lock:
            if self._profiles is not None:
                return self._profiles
            self._profiles = profiles
            return profiles

    def _all_users(self, *, extras: bool = True) -> dict[str, dict[str, Any]]:
        if extras:
            with self._lock:
                if self._users_merged is not None:
                    return self._users_merged
        users = dict(self._ensure_profiles())
        if not extras:
            return users
        for ch in self._channels.values():
            raw = self._channel_sidecar(ch, "members.json")
            ids: list[str] = []
            if isinstance(raw, list):
                ids = [str(x) for x in raw if x]
            else:
                meta = {**(self._catalog.get(ch.id) or {}), **self._channel_meta(ch)}
                other = str(meta.get("user") or "")
                if other:
                    ids = [other]
                elif ch.path.is_dir():
                    loaded = self._load(ch)
                    for uid, profile in loaded.users_extra.items():
                        if uid not in users:
                            users[uid] = profile
                    continue
            for uid in ids:
                if uid and uid not in users:
                    users[uid] = {"id": uid, "handle": uid, "display_name": uid}
        with self._lock:
            if self._users_merged is not None:
                return self._users_merged
            self._users_merged = users
            return users

    def _channel_obj(self, ch: Channel) -> dict[str, Any]:
        with self._lock:
            if ch.obj_cache is not None:
                return ch.obj_cache
        prefix = ch.id[:1]
        meta = {**(self._catalog.get(ch.id) or {}), **self._channel_meta(ch)}
        kinds = kinds_for(ch.id, meta)
        # Type flags follow kinds (same source as conversations.list filters).
        # Prefix-only defaults used to set is_mpim for every G id, so a catalog
        # / channel.json that classified a G as private_channel without an
        # explicit is_mpim:false left the object flag stuck true and rtm_start
        # / is: filters disagreed with ch.kinds.
        obj: dict[str, Any] = {
            "id": ch.id,
            "name": ch.name,
            "is_im": "im" in kinds,
            "is_mpim": "mpim" in kinds,
            "is_channel": "public_channel" in kinds
            or ("private_channel" in kinds and prefix == "C"),
            "is_group": "mpim" in kinds or ("private_channel" in kinds and prefix == "G"),
            "is_private": "public_channel" not in kinds,
            "is_archived": False,
            "workspace": ch.workspace,
        }
        if meta:
            for key in _CATALOG_KEYS:
                if key in meta:
                    obj[key] = meta[key]
            for field_name in ("topic", "purpose"):
                if field_name in meta:
                    value = meta[field_name]
                    obj[field_name] = {"value": value} if isinstance(value, str) else value
                creator = meta.get(f"{field_name}_creator")
                last_set = meta.get(f"{field_name}_last_set")
                if creator is None and last_set is None:
                    continue
                blob = obj.get(field_name)
                if not isinstance(blob, dict):
                    blob = {"value": blob or ""}
                    obj[field_name] = blob
                if creator is not None:
                    blob["creator"] = creator
                if last_set is not None:
                    blob["last_set"] = last_set
        if "num_members" not in obj:
            raw = self._channel_sidecar(ch, "members.json")
            if isinstance(raw, list):
                obj["num_members"] = len(raw)
        with self._lock:
            if ch.obj_cache is not None:
                return ch.obj_cache
            # Keep conversations.list type filters aligned with channel flags.
            ch.kinds = kinds
            ch.obj_cache = obj
            return obj

    def _channel_matches_is(self, ch: Channel, is_kinds: set[str]) -> bool:
        obj = self._channel_obj(ch)  # refine ch.kinds from channel.json / catalog
        prefix = ch.id[:1]
        for kind in is_kinds:
            if kind in MSG_IS:
                continue
            if kind in {"dm", "im"}:
                if prefix != "D":
                    return False
            elif kind == "mpim":
                if "mpim" not in ch.kinds:
                    return False
            elif kind in {"channel", "channels"}:
                if prefix != "C":
                    return False
            elif kind in {"group", "groups"}:
                if prefix != "G":
                    return False
            elif kind == "public":
                if prefix != "C" or obj.get("is_private"):
                    return False
            elif kind in {"unreads", "unread"}:
                n = int(obj.get("unread_count") or 0)
                d = int(obj.get("unread_count_display") or 0)
                if n <= 0 and d <= 0:
                    return False
            else:
                flag = channel_flag_is_ok(kind, ch, obj)
                if flag is None or not flag:
                    return False
        return True

    def auth_test(self) -> dict[str, Any]:
        stored = self._workspace_auth()
        workspaces = list(
            dict.fromkeys(ch.workspace for ch in self._channels.values() if ch.workspace)
        )
        team = stored.get("team") or (workspaces[0] if workspaces else self.root.name)
        url = stored.get("url") or f"https://{team}.slack.com/"
        return _ok(
            url=url,
            team=team,
            team_id=stored.get("team_id") or "",
            user=stored.get("user") or "",
            user_id=stored.get("user_id") or "",
            enterprise_id=stored.get("enterprise_id") or "",
            is_enterprise_install=bool(stored.get("is_enterprise_install")),
        )

    def api_test(self, **kwargs: Any) -> dict[str, Any]:
        return _ok(**kwargs)

    def auth_teams_list(
        self,
        *,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        raw = self._first_json("teams.json")
        if isinstance(raw, list):
            teams = [row for row in raw if isinstance(row, dict)]
        elif isinstance(raw, dict) and isinstance(raw.get("teams"), list):
            teams = [row for row in raw["teams"] if isinstance(row, dict)]
        else:
            teams = []
        if not teams:
            auth = self.auth_test()
            teams = [{"id": auth.get("team_id") or "", "name": auth.get("team") or ""}]
        return _paged(teams, count=count, page=page, limit=limit, cursor=cursor, key="teams")

    def auth_teams_search(
        self,
        *,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.auth_teams_list().get("teams"),
            query,
            key="teams",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def rtm_connect(self, **kwargs: Any) -> dict[str, Any]:
        del kwargs
        auth = self.auth_test()
        team = self.team_info().get("team") or {}
        return _ok(
            url="",
            self={"id": auth.get("user_id") or "", "name": auth.get("user") or ""},
            team={
                "id": team.get("id") or auth.get("team_id") or "",
                "name": team.get("name") or auth.get("team") or "",
                "domain": team.get("domain") or auth.get("team") or "",
            },
        )

    def rtm_start(self, **kwargs: Any) -> dict[str, Any]:
        resp = self.rtm_connect(**kwargs)
        resp["users"] = list(self.users_list(include_message_users=False).get("members") or [])
        channels: list[dict[str, Any]] = []
        groups: list[dict[str, Any]] = []
        ims: list[dict[str, Any]] = []
        mpims: list[dict[str, Any]] = []
        for c in self.conversations_list().get("channels") or []:
            cid = str(c.get("id") or "")
            prefix = cid[:1]
            if c.get("is_im") or prefix == "D":
                ims.append(c)
            elif c.get("is_mpim") and not c.get("is_channel"):
                mpims.append(c)
            elif c.get("is_private") or c.get("is_group") or prefix == "G":
                groups.append(c)
            else:
                channels.append(c)
        resp["channels"] = channels
        resp["groups"] = groups
        resp["ims"] = ims
        resp["mpims"] = mpims
        resp["bots"] = list(self.bots_list().get("bots") or [])
        best = ""
        best_key: int | None = None
        for row in self.iter_cursors():
            ts = str(row.get("ts") or "")
            if not ts:
                continue
            try:
                val = ts_key(ts)
            except ValueError:
                continue
            if best_key is None or val > best_key:
                best_key = val
                best = ts
        resp["cache_ts"] = best
        return resp

    def api_call(
        self,
        api_method: str,
        *,
        http_verb: str = "POST",
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
        **kwargs: Any,
    ) -> dict[str, Any]:
        del http_verb
        name = api_method.replace(".", "_").strip()
        fn = getattr(self, name, None)
        if not callable(fn) or name.startswith("_"):
            return _err("unknown_method")
        payload: dict[str, Any] = {}
        if params:
            payload.update(params)
        if json:
            payload.update(json)
        payload.update(kwargs)
        sig = inspect.signature(fn)
        if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()):
            return cast(dict[str, Any], fn(**payload))
        allowed = {k: v for k, v in payload.items() if k in sig.parameters}
        return cast(dict[str, Any], fn(**allowed))

    def conversations_list(
        self,
        *,
        types: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        exclude_archived: bool = False,
    ) -> dict[str, Any]:
        wanted = {t.strip() for t in (types or ALL_CONV_TYPES).split(",") if t.strip()}
        channels: list[dict[str, Any]] = []
        for ch in self._channels.values():
            obj = self._channel_obj(ch)
            if ch.kinds & wanted:
                channels.append(obj)
        if exclude_archived:
            channels = [c for c in channels if not c.get("is_archived")]
        return _paged(channels, limit=limit, cursor=cursor, key="channels")

    def conversations_search(
        self,
        *,
        query: str,
        types: str | None = None,
        exclude_archived: bool = False,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().lstrip("#").lower()
        if not needle:
            return _err("invalid_arguments")
        wanted = {t.strip() for t in (types or ALL_CONV_TYPES).split(",") if t.strip()}
        hits: list[dict[str, Any]] = []
        for ch in self._channels.values():
            obj = self._channel_obj(ch)
            if not (ch.kinds & wanted):
                continue
            if exclude_archived and obj.get("is_archived"):
                continue
            blob = " ".join(
                [ch.id, ch.name, str(obj.get("name") or ""), str(obj.get("name_normalized") or "")]
            ).lower()
            if needle in blob:
                hits.append(obj)
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="channels")

    def conversations_info(self, *, channel: str) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        return _ok(channel=self._channel_obj(ch))

    def get_cursor(self, *, channel: str) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        marker = ch.path / ".cursor"
        ts = ""
        if marker.is_file():
            try:
                ts = marker.read_text(encoding="utf-8").strip()
            except OSError:
                ts = ""
        return _ok(channel=ch.id, ts=ts)

    def channels_list(
        self,
        *,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        exclude_archived: bool = False,
    ) -> dict[str, Any]:
        return self.conversations_list(
            types="public_channel",
            limit=limit,
            cursor=cursor,
            exclude_archived=exclude_archived,
        )

    def groups_list(self, *, limit: int = _MAX_LIMIT, cursor: str | None = None) -> dict[str, Any]:
        return self.conversations_list(types="private_channel", limit=limit, cursor=cursor)

    def im_list(self, *, limit: int = _MAX_LIMIT, cursor: str | None = None) -> dict[str, Any]:
        return self.conversations_list(types="im", limit=limit, cursor=cursor)

    def mpim_list(self, *, limit: int = _MAX_LIMIT, cursor: str | None = None) -> dict[str, Any]:
        return self.conversations_list(types="mpim", limit=limit, cursor=cursor)

    def conversations_history(
        self,
        *,
        channel: str,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        inclusive: bool = False,
    ) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        loaded = self._load(ch)
        items = self._ensure_history(loaded)
        items = _filter_ts_range(items, oldest, latest, inclusive)
        return _paged(items, limit=limit, cursor=cursor, key="messages", include_has_more=True)

    def conversations_history_search(
        self,
        *,
        channel: str,
        query: str,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        inclusive: bool = False,
    ) -> dict[str, Any]:
        if not query.strip():
            return _err("invalid_arguments")
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        items = _filter_ts_range(self._ensure_history(self._load(ch)), oldest, latest, inclusive)
        return _search_rows(
            items, query, key="messages", limit=limit, cursor=cursor, include_has_more=True
        )

    def conversations_replies(
        self,
        *,
        channel: str,
        ts: str,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        inclusive: bool = False,
    ) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        loaded = self._load(ch)
        parent_ts = loaded.thread_root.get(ts, ts)
        parent = loaded.by_ts.get(parent_ts)
        items: list[dict[str, Any]] = []
        seen: set[str] = set()

        def add(msg: dict[str, Any], thread_ts: str) -> None:
            key = str(msg.get("ts") or "")
            if key and key in seen:
                return
            if key:
                seen.add(key)
            items.append(_reply_item({**msg, "thread_ts": thread_ts}))

        if parent is not None:
            add({**parent, "thread_ts": parent["ts"]}, str(parent["ts"]))
            for reply in parent.get("thread") or []:
                add(reply, str(parent["ts"]))
            for reply in loaded.thread_only.get(parent_ts, []):
                add(reply, parent_ts)
        elif parent_ts in loaded.thread_only:
            for reply in loaded.thread_only[parent_ts]:
                add(reply, parent_ts)
        else:
            return _err("thread_not_found")
        filtered = _filter_ts_range(
            [m for m in items if isinstance(m, dict) and m.get("ts")],
            oldest,
            latest,
            inclusive,
        )
        return _paged(filtered, limit=limit, cursor=cursor, key="messages", include_has_more=True)

    # Legacy Slack Web API names (channels.*/groups.*/im.*/mpim.*) → conversations.*
    channels_info = conversations_info
    groups_info = conversations_info
    im_info = conversations_info
    mpim_info = conversations_info
    channels_history = conversations_history
    groups_history = conversations_history
    im_history = conversations_history
    mpim_history = conversations_history
    channels_replies = conversations_replies
    groups_replies = conversations_replies
    im_replies = conversations_replies
    mpim_replies = conversations_replies

    def conversations_replies_search(
        self,
        *,
        channel: str,
        ts: str,
        query: str,
        oldest: str | None = None,
        latest: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        inclusive: bool = False,
    ) -> dict[str, Any]:
        if not query.strip():
            return _err("invalid_arguments")
        listed = self.conversations_replies(
            channel=channel,
            ts=ts,
            oldest=oldest,
            latest=latest,
            inclusive=inclusive,
            limit=_MAX_LIMIT,
        )
        if not listed.get("ok"):
            return listed
        return _search_rows(
            listed.get("messages"),
            query,
            key="messages",
            limit=limit,
            cursor=cursor,
            include_has_more=True,
        )

    def users_list(
        self,
        *,
        include_deleted: bool = True,
        include_bots: bool = True,
        include_message_users: bool = True,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        members = [_slack_user(p) for p in self._all_users(extras=include_message_users).values()]
        if not include_deleted:
            members = [u for u in members if not u.get("deleted")]
        if not include_bots:
            members = [u for u in members if not u.get("is_bot")]
        members.sort(key=lambda u: (u.get("name") or "", u.get("id") or ""))
        for member in members:
            presence = self._presence_of(str(member.get("id") or ""))
            if presence:
                member["presence"] = presence
        return _paged(members, limit=limit, cursor=cursor, key="members")

    def _profile_for(self, user: str) -> dict[str, Any] | None:
        want = user.strip().lstrip("@")
        profiles = self._ensure_profiles()
        hit = profiles.get(want)
        if hit is not None:
            return hit
        low = want.lower()
        for profile in profiles.values():
            uid = str(profile.get("id") or "")
            handle = str(profile.get("handle") or "").lower()
            display = str(profile.get("display_name") or "").lower()
            if uid == want or handle == low or display == low:
                return profile
        return None

    def users_info(self, *, user: str) -> dict[str, Any]:
        profile = self._profile_for(user)
        if profile is None:
            want = user.strip().lstrip("@")
            self._load_all()
            for ch in self._channels.values():
                loaded = ch.loaded
                if loaded and want in loaded.users_extra:
                    profile = loaded.users_extra[want]
                    break
                if loaded is None:
                    continue
                for extra in loaded.users_extra.values():
                    handle = str(extra.get("handle") or "").lower()
                    if handle == want.lower():
                        profile = extra
                        break
                if profile is not None:
                    break
        if profile is None:
            return _err("user_not_found")
        user_obj = _slack_user(profile)
        presence = self._presence_of(user_obj["id"])
        if presence:
            user_obj["presence"] = presence
        return _ok(user=user_obj)

    def users_search(
        self,
        *,
        query: str,
        include_deleted: bool = True,
        include_bots: bool = True,
        include_message_users: bool = True,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().lstrip("@").lower()
        if not needle:
            return _err("invalid_arguments")
        # Same user set and filters as users_list so --search matches list results.
        members = [_slack_user(p) for p in self._all_users(extras=include_message_users).values()]
        if not include_deleted:
            members = [u for u in members if not u.get("deleted")]
        if not include_bots:
            members = [u for u in members if not u.get("is_bot")]
        hits: list[dict[str, Any]] = []
        for user in members:
            profile = user.get("profile")
            profile = profile if isinstance(profile, dict) else {}
            blob = " ".join(
                [
                    str(user.get("id") or ""),
                    str(user.get("name") or ""),
                    str(profile.get("display_name") or ""),
                    str(profile.get("real_name") or ""),
                    str(profile.get("email") or ""),
                ]
            ).lower()
            if needle in blob:
                hits.append(user)
        hits.sort(key=lambda u: (u.get("name") or "", u.get("id") or ""))
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="members")

    def _presence_of(self, uid: str) -> str | None:
        if not uid:
            return None
        raw = self._first_json("presence.json")
        info: Any = raw.get(uid) if isinstance(raw, dict) else None
        if isinstance(info, str):
            return info
        if isinstance(info, dict) and info.get("presence"):
            return str(info["presence"])
        return None

    def files_info(self, *, file: str) -> dict[str, Any]:
        self._ensure_workspace_files()
        stored = self._files_get(file)
        if stored is not None:
            return _ok(file=stored)
        want = file.strip().lower()
        for row in self._files_snapshot():
            if str(row.get("name") or "").lower() == want:
                return _ok(file=row)
        self._fill_files()
        stored = self._files_get(file)
        if stored:
            return _ok(file=stored)
        for row in self._files_snapshot():
            if str(row.get("name") or "").lower() == want:
                return _ok(file=row)
        return _err("file_not_found")

    def files_remote_list(
        self,
        *,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        self._fill_files()
        files = [f for f in self._files_snapshot() if is_remote_file(f)]
        files.sort(key=lambda f: str(f.get("id") or ""))
        return _paged(files, count=count, page=page, limit=limit, cursor=cursor, key="files")

    def files_remote_info(
        self, *, file: str | None = None, external_id: str | None = None
    ) -> dict[str, Any]:
        self._ensure_workspace_files()
        if file:
            fobj = self._files_get(file)
            if fobj is not None:
                if is_remote_file(fobj):
                    return _ok(file=fobj)
                return _err("file_not_found")
        self._fill_files()
        if file:
            fobj = self._files_get(file)
            if fobj and is_remote_file(fobj):
                return _ok(file=fobj)
            return _err("file_not_found")
        if external_id:
            for fobj in self._files_snapshot():
                if str(fobj.get("external_id") or "") == external_id and is_remote_file(fobj):
                    return _ok(file=fobj)
            return _err("file_not_found")
        return _err("file_not_found")

    def files_remote_search(
        self,
        *,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.files_remote_list().get("files"),
            query,
            key="files",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def files_list(
        self,
        *,
        channel: str | None = None,
        user: str | None = None,
        ts_from: float | int | str | None = None,
        ts_to: float | int | str | None = None,
        types: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        count: int | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        files = self._listed_files(
            channel=channel, user=user, ts_from=ts_from, ts_to=ts_to, types=types
        )
        if isinstance(files, dict):
            return files
        return _paged(files, count=count, page=page, limit=limit, cursor=cursor, key="files")

    def files_list_search(
        self,
        *,
        query: str,
        channel: str | None = None,
        user: str | None = None,
        ts_from: float | int | str | None = None,
        ts_to: float | int | str | None = None,
        types: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        files = self._listed_files(
            channel=channel, user=user, ts_from=ts_from, ts_to=ts_to, types=types
        )
        if isinstance(files, dict):
            return files
        return _search_rows(
            files, query, key="files", count=count, page=page, limit=limit, cursor=cursor
        )

    def files_comments(
        self,
        *,
        file: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        info = self.files_info(file=file)
        if not info.get("ok"):
            return info
        fobj = info.get("file") or {}
        comments = fobj.get("comments")
        if comments is None:
            initial = fobj.get("initial_comment")
            comments = [initial] if isinstance(initial, dict) else []
        elif isinstance(comments, dict):
            comments = list(comments.values())
        elif not isinstance(comments, list):
            comments = []
        paged = _paged(comments, count=count, page=page, limit=limit, cursor=cursor, key="comments")
        if not paged.get("ok"):
            return paged
        paged["comments_count"] = fobj.get("comments_count") or len(comments)
        return paged

    def files_comments_search(
        self,
        *,
        file: str,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        listed = self.files_comments(file=file)
        if not listed.get("ok"):
            return listed
        return _search_rows(
            listed.get("comments"),
            query,
            key="comments",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def conversations_members(
        self,
        *,
        channel: str,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        return _paged(self._roster(ch), limit=limit, cursor=cursor, key="members")

    def conversations_members_search(
        self,
        *,
        channel: str,
        query: str,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        profiles = self._ensure_profiles()
        hits: list[str] = []
        for uid in self._roster(ch):
            if _uid_hit(uid, profiles.get(uid), needle):
                hits.append(uid)
        return _paged(hits, limit=limit, cursor=cursor, key="members")

    def users_lookupByEmail(self, *, email: str) -> dict[str, Any]:
        want = email.strip().lower()
        if not want:
            return _err("users_not_found")
        for profile in self._ensure_profiles().values():
            if (profile.get("email") or "").strip().lower() == want:
                return _ok(user=_slack_user(profile))
        return _err("users_not_found")

    def pins_list(
        self,
        *,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if channel:
            ch = self._get(channel)
            if ch is None:
                return _err("channel_not_found")
            chans = [ch]
        else:
            chans = list(self._channels.values())
        items: list[dict[str, Any]] = []
        for ch in chans:
            items.extend(self._pins_items(ch))
        return _paged(items, count=count, page=page, limit=limit, cursor=cursor, key="items")

    def _pins_items(self, ch: Channel) -> list[dict[str, Any]]:
        raw = self._channel_sidecar(ch, "pins.json")
        if isinstance(raw, list):
            return raw
        loaded = self._load(ch)
        items: list[dict[str, Any]] = []
        for msg in iter_msgs(loaded):
            if not (msg.get("pinned_to") or msg.get("pinned_info")):
                continue
            info = msg.get("pinned_info") or {}
            items.append(
                {
                    "type": "message",
                    "created": info.get("pinned_ts") or 0,
                    "created_by": info.get("pinned_by") or msg.get("user") or "",
                    "channel": ch.id,
                    "message": _history_item(msg),
                }
            )
        return items

    def pins_info(self, *, channel: str, ts: str) -> dict[str, Any]:
        listed = self.pins_list(channel=channel)
        if not listed.get("ok"):
            return listed
        return _item_by_ts(listed.get("items"), ts)

    def pins_search(
        self,
        *,
        query: str,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not query.strip():
            return _err("invalid_arguments")
        listed = self.pins_list(channel=channel)
        if not listed.get("ok"):
            return listed
        return _search_rows(
            listed.get("items"),
            query,
            key="items",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def team_info(self) -> dict[str, Any]:
        raw = self._first_json("team.json")
        if isinstance(raw, dict) and raw:
            team = raw.get("team") if isinstance(raw.get("team"), dict) else raw
            return _ok(team=team)
        auth = self.auth_test()
        team = auth["team"]
        return _ok(team={"id": auth.get("team_id") or "", "name": team, "domain": team})

    def chat_getPermalink(self, *, channel: str, message_ts: str) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        loaded = self._load(ch)
        if message_ts not in loaded.all_by_ts:
            return _err("message_not_found")
        stamp = message_ts.replace(".", "")
        permalink = f"https://{ch.workspace}.slack.com/archives/{ch.id}/p{stamp}"
        return _ok(channel=ch.id, permalink=permalink)

    def search_messages(
        self, *, query: str, count: int = 20, page: int | None = None, sort_dir: str = "desc"
    ) -> dict[str, Any]:
        text, mods = parse_search(query)
        text, nots = split_negation(text)
        text_l_query = text.lower()
        empty = _ok(query=query, messages={"total": 0, "matches": []})
        if not text_l_query and not any(mods.values()) and not nots:
            return empty
        scoped = self._in_scope_channels(mods["in"])
        if mods["in"] and not scoped:
            return empty
        auth = self._workspace_auth()
        from_toks = expand_me(mods["from"], auth)
        to_toks = expand_me(mods["to"], auth)
        with_toks = expand_me(mods["with"], auth)
        profiles = self._ensure_profiles()
        if with_toks:
            scoped = [ch for ch in scoped if self._with_ok(ch, with_toks, profiles)]
            if not scoped:
                return empty
        is_kinds = {t.lower() for t in mods["is"]}
        has_toks: list[str] = []
        for tok in mods["has"]:
            if tok.lower() in {"star", "stars", "starred"}:
                is_kinds.add("starred")
            else:
                has_toks.append(tok)
        channel_is = is_kinds - MSG_IS
        if channel_is:
            scoped = [ch for ch in scoped if self._channel_matches_is(ch, is_kinds)]
            if not scoped:
                return empty
        if mods["in"] or with_toks or channel_is:
            for ch in scoped:
                self._load(ch)
        else:
            self._load_all()
            scoped = list(self._channels.values())
        starred: set[tuple[str, str]] = set()
        if is_kinds & {"starred", "saved"}:
            starred = self._star_keys()
        me: set[str] = set()
        if is_kinds & {"me"}:
            me = {x.lower() for x in expand_me(["me"], auth) if x and x != "\0"}
        bounds = compile_time(mods)
        # Bound the in-memory hit buffer to the requested page window so a broad
        # query over a large dump does not allocate one tuple per match. Still
        # scan everything for an accurate total.
        count = min(max(count, 1), _MAX_LIMIT)
        start = 0 if page is None else (max(int(page), 1) - 1) * count
        need = start + count
        if need > _MAX_SEARCH_NEED:
            need = _MAX_SEARCH_NEED
        descending = str(sort_dir or "desc").lower() != "asc"
        # Min-heap of size ``need``. Desc: store (ts, seq, …) and keep largest.
        # Asc: store (-ts, -seq, …) so the heap top is the largest among the
        # smallest ``need`` keys (Python only offers a min-heap).
        heap: list[tuple[Any, ...]] = []
        total = 0
        seq = 0
        is_toks = list(is_kinds)
        for ch in scoped:
            loaded = ch.loaded
            if loaded is None:
                continue
            extras = loaded.users_extra
            ch_obj = self._channel_obj(ch)
            for text_l, msg in docs_for_query(self._ensure_search(loaded), text_l_query):
                uid = msg.get("user") or ""
                profile = profiles.get(uid) or extras.get(uid) or {}
                if not msg_from_ok(msg, from_toks, profile):
                    continue
                if not msg_to_ok(msg, to_toks):
                    continue
                if not msg_has_ok(msg, has_toks):
                    continue
                if not msg_is_ok(msg, is_toks, ch, loaded, ch_obj, starred, me, profile):
                    continue
                if not msg_time_ok(msg, bounds):
                    continue
                if text_l_query and text_l_query not in text_l:
                    continue
                if any(n in text_l for n in nots):
                    continue
                total += 1
                if need == 0:
                    continue
                ts = msg_ts_key(msg.get("ts"))
                seq += 1
                if descending:
                    entry: tuple[Any, ...] = (ts, seq, ch, msg)
                    if len(heap) < need:
                        heapq.heappush(heap, entry)
                    elif entry > heap[0]:
                        heapq.heapreplace(heap, entry)
                else:
                    entry = (-ts, -seq, ch, msg)
                    if len(heap) < need:
                        heapq.heappush(heap, entry)
                    elif entry > heap[0]:
                        heapq.heapreplace(heap, entry)
        if start >= _MAX_SEARCH_NEED:
            sliced: list[tuple[Any, ...]] = []
        elif descending:
            ranked = sorted(heap, key=lambda row: (row[0], row[1]), reverse=True)
            sliced = ranked[start : start + count]
        else:
            ranked = sorted(heap, key=lambda row: (-row[0], -row[1]))
            sliced = ranked[start : start + count]
        matches = [
            {
                "type": "message",
                "ts": msg.get("ts", ""),
                "user": msg.get("user") or "",
                "username": msg.get("user_name") or "",
                "text": msg.get("text") or "",
                "channel": {"id": ch.id, "name": ch.name},
            }
            for _key_a, _key_b, ch, msg in sliced
        ]
        return _ok(
            query=query,
            messages={"total": total, "matches": matches},
        )

    def reactions_get(self, *, channel: str, timestamp: str) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        loaded = self._load(ch)
        msg = loaded.all_by_ts.get(timestamp)
        if msg is None:
            return _err("message_not_found")
        return _ok(type="message", channel=ch.id, message=_history_item(msg))

    def reactions_list(
        self,
        *,
        user: str | None = None,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if channel:
            ch = self._get(channel)
            if ch is None:
                return _err("channel_not_found")
            chans = [ch]
        else:
            chans = list(self._channels.values())
        items: list[dict[str, Any]] = []
        missing: list[Channel] = []
        for ch in chans:
            rows = self._reaction_sidecar(ch, user)
            if rows is not None:
                items.extend(rows)
            else:
                missing.append(ch)
        if missing:
            if channel is None:
                self._load_all()
            for ch in missing:
                loaded = self._load(ch)
                for msg in iter_msgs(loaded):
                    items.extend(_reaction_items(ch.id, msg, user))
        return _paged(items, count=count, page=page, limit=limit, cursor=cursor, key="items")

    def reactions_search(
        self,
        *,
        query: str,
        user: str | None = None,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not query.strip():
            return _err("invalid_arguments")
        listed = self.reactions_list(user=user, channel=channel)
        if not listed.get("ok"):
            return listed
        return _search_rows(
            listed.get("items"),
            query,
            key="items",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def _reaction_sidecar(self, ch: Channel, user: str | None) -> list[dict[str, Any]] | None:
        raw = self._channel_sidecar(ch, "reactions.json")
        if not isinstance(raw, list):
            return None
        rows = [row for row in raw if isinstance(row, dict)]
        if user:
            rows = [row for row in rows if row.get("user") == user]
        return rows

    def users_conversations(
        self,
        *,
        user: str,
        types: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        exclude_archived: bool = False,
    ) -> dict[str, Any]:
        wanted = {t.strip() for t in (types or ALL_CONV_TYPES).split(",") if t.strip()}
        channels: list[dict[str, Any]] = []
        for ch in self._channels.values():
            obj = self._channel_obj(ch)
            if ch.kinds & wanted and user in self._roster(ch):
                channels.append(obj)
        if exclude_archived:
            channels = [c for c in channels if not c.get("is_archived")]
        return _paged(channels, limit=limit, cursor=cursor, key="channels")

    def users_conversations_search(
        self,
        *,
        user: str,
        query: str,
        types: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        exclude_archived: bool = False,
    ) -> dict[str, Any]:
        if not query.strip():
            return _err("invalid_arguments")
        wanted = {t.strip() for t in (types or ALL_CONV_TYPES).split(",") if t.strip()}
        channels: list[dict[str, Any]] = []
        for ch in self._channels.values():
            obj = self._channel_obj(ch)
            if ch.kinds & wanted and user in self._roster(ch):
                channels.append(obj)
        if exclude_archived:
            channels = [c for c in channels if not c.get("is_archived")]
        return _search_rows(channels, query, key="channels", limit=limit, cursor=cursor)

    def search_files(
        self, *, query: str, count: int = 20, page: int | None = None
    ) -> dict[str, Any]:
        text, mods = parse_search(query)
        text, nots = split_negation(text)
        q = text.lower().strip()
        empty = _ok(query=query, files={"total": 0, "matches": []})
        if not q and not any(mods.values()) and not nots:
            return empty
        scoped = self._in_scope_channels(mods["in"])
        if mods["in"] and not scoped:
            return empty
        if mods["in"]:
            for ch in scoped:
                self._load(ch)
            files: list[dict[str, Any]] = []
            for ch in scoped:
                loaded = ch.loaded
                if loaded:
                    files.extend(loaded.files.values())
        else:
            self._fill_files()
            files = self._files_snapshot()
        profiles = self._ensure_profiles()
        from_toks = expand_me(mods["from"], self._workspace_auth())
        bounds = compile_time(mods)
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for f in files:
            fid = str(f.get("id") or "")
            if fid and fid in seen:
                continue
            uid = f.get("user") or ""
            profile = profiles.get(uid) or {}
            raw_file_ts = next(
                (
                    str(f.get(k))
                    for k in ("created", "timestamp", "updated")
                    if f.get(k) is not None
                ),
                "",
            )
            fake_msg = {
                "user": uid,
                "user_name": profile.get("handle") or "",
                "ts": raw_file_ts,
            }
            if not msg_from_ok(fake_msg, from_toks, profile):
                continue
            if not msg_time_ok(fake_msg, bounds):
                continue
            if not file_has_ok(f, mods["has"]):
                continue
            blob = " ".join(
                str(f.get(k) or "")
                for k in ("name", "title", "filetype", "mimetype", "pretty_type")
            ).lower()
            if q and q not in blob:
                continue
            if any(n in blob for n in nots):
                continue
            if fid:
                seen.add(fid)
            matches.append(f)
        matches.sort(key=lambda f: str(f.get("id") or ""))
        count = min(max(count, 1), _MAX_LIMIT)
        start = 0 if page is None else (max(int(page), 1) - 1) * count
        return _ok(
            query=query, files={"total": len(matches), "matches": matches[start : start + count]}
        )

    def emoji_list(self) -> dict[str, Any]:
        with self._lock:
            cached = self._emoji
        if cached is None:
            catalog: dict[str, str] = {}
            raw = self._first_json("emoji.json")
            if isinstance(raw, dict):
                catalog.update({str(k): str(v) for k, v in raw.items()})
            if not catalog:
                self._load_all()
                for ch in self._channels.values():
                    loaded = ch.loaded
                    if loaded is None:
                        continue
                    for msg in iter_msgs(loaded):
                        for rx in msg.get("reactions") or []:
                            name = rx.get("name")
                            if name and name not in catalog:
                                catalog[str(name)] = ""
            with self._lock:
                if self._emoji is None:
                    self._emoji = catalog
                cached = self._emoji
        cats = self._first_json("emoji_categories.json")
        if isinstance(cats, list):
            return _ok(emoji=cached, categories=cats)
        return _ok(emoji=cached)

    def emoji_get(self, *, name: str) -> dict[str, Any]:
        want = name.strip().strip(":")
        catalog = self.emoji_list().get("emoji") or {}
        if want not in catalog:
            return _err("emoji_not_found")
        return _ok(emoji={want: catalog[want]})

    def emoji_search(
        self,
        *,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().strip(":").lower()
        if not needle:
            return _err("invalid_arguments")
        catalog = self.emoji_list().get("emoji") or {}
        hits = [
            {"name": str(name), "url": str(url)}
            for name, url in catalog.items()
            if needle in str(name).lower()
        ]
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="emoji")

    def emoji_categories_search(
        self,
        *,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        cats = self.emoji_list().get("categories") or []
        return _search_rows(
            cats, query, key="categories", count=count, page=page, limit=limit, cursor=cursor
        )

    def iter_messages(
        self, channel: str | None = None, *, include_replies: bool = True
    ) -> Iterator[dict[str, Any]]:
        if channel:
            ch = self._get(channel)
            chans = [ch] if ch is not None else []
        else:
            self._load_all()
            chans = list(self._channels.values())
        for ch in chans:
            loaded = self._load(ch)
            for msg in loaded.messages:
                item = _history_item(msg)
                item["channel"] = ch.id
                yield item
                if include_replies:
                    for reply in msg.get("thread") or []:
                        ritem = _reply_item({**reply, "thread_ts": msg.get("ts")})
                        ritem["channel"] = ch.id
                        yield ritem
            if include_replies:
                for replies in loaded.thread_only.values():
                    for reply in replies:
                        ritem = _reply_item(reply)
                        ritem["channel"] = ch.id
                        yield ritem

    def iter_files(self, channel: str | None = None) -> Iterator[dict[str, Any]]:
        if channel:
            ch = self._get(channel)
            if ch is None:
                return
            yield from self._channel_files(ch)
            return
        self._fill_files()
        yield from self._files_snapshot()

    def iter_remote_files(self) -> Iterator[dict[str, Any]]:
        yield from self.files_remote_list().get("files") or []

    def iter_threads(self, channel: str | None = None) -> Iterator[dict[str, Any]]:
        if channel:
            ch = self._get(channel)
            chans = [ch] if ch is not None else []
        else:
            chans = list(self._channels.values())
        missing: list[Channel] = []
        for ch in chans:
            rows = self._thread_sidecar(ch)
            if rows is not None:
                yield from rows
            else:
                missing.append(ch)
        if missing:
            if channel is None:
                self._load_all()
            for ch in missing:
                loaded = self._load(ch)
                yield from threads_from_loaded(ch, loaded)

    def _thread_sidecar(self, ch: Channel) -> list[dict[str, Any]] | None:
        raw = self._channel_sidecar(ch, "threads.json")
        if not isinstance(raw, list):
            return None
        rows: list[dict[str, Any]] = []
        for row in raw:
            if not isinstance(row, dict):
                continue
            if row.get("channel"):
                rows.append(row)
            else:
                rows.append({**row, "channel": ch.id})
        return rows

    def threads_list(
        self,
        *,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        items = list(self.iter_threads(channel))
        return _paged(items, count=count, page=page, limit=limit, cursor=cursor, key="threads")

    def threads_info(
        self,
        *,
        channel: str,
        ts: str | None = None,
        thread_ts: str | None = None,
    ) -> dict[str, Any]:
        want = (ts or thread_ts or "").strip()
        if self._get(channel) is None:
            return _err("channel_not_found")
        if not want:
            return _err("thread_not_found")
        for row in self.iter_threads(channel):
            if str(row.get("thread_ts") or "") == want:
                return _ok(thread=row)
        return _err("thread_not_found")

    def threads_search(
        self,
        *,
        query: str,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.threads_list(channel=channel).get("threads"),
            query,
            key="threads",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def search_all(
        self,
        *,
        query: str,
        count: int = 20,
        page: int | None = None,
        sort_dir: str = "desc",
    ) -> dict[str, Any]:
        messages = self.search_messages(query=query, count=count, page=page, sort_dir=sort_dir)
        files = self.search_files(query=query, count=count, page=page)
        return _ok(query=query, messages=messages["messages"], files=files["files"])

    def bookmarks_list(
        self,
        *,
        channel: str | None = None,
        channel_id: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        cid = channel or channel_id
        if cid:
            ch = self._get(cid)
            if ch is None:
                return _err("channel_not_found")
            chans = [ch]
        else:
            chans = list(self._channels.values())
        bookmarks: list[Any] = []
        for ch in chans:
            raw = self._channel_sidecar(ch, "bookmarks.json")
            if not isinstance(raw, list):
                continue
            for item in raw:
                if isinstance(item, dict) and not item.get("channel_id"):
                    bookmarks.append({**item, "channel_id": ch.id})
                else:
                    bookmarks.append(item)
        return _paged(
            bookmarks, count=count, page=page, limit=limit, cursor=cursor, key="bookmarks"
        )

    def bookmarks_info(
        self, *, bookmark: str | None = None, id: str | None = None
    ) -> dict[str, Any]:
        want = (bookmark or id or "").strip()
        if not want:
            return _err("not_found")
        for row in self.bookmarks_list().get("bookmarks") or []:
            if isinstance(row, dict) and str(row.get("id") or "") == want:
                return _ok(bookmark=row)
        return _err("not_found")

    def bookmarks_search(
        self,
        *,
        query: str,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if not query.strip():
            return _err("invalid_arguments")
        listed = self.bookmarks_list(channel=channel)
        if not listed.get("ok"):
            return listed
        return _search_rows(
            listed.get("bookmarks"),
            query,
            key="bookmarks",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def usergroups_list(
        self,
        *,
        include_count: bool = False,
        include_users: bool = True,
        include_disabled: bool = False,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        raw = self._first_json("usergroups.json")
        groups = raw if isinstance(raw, list) else []
        if not include_disabled:
            groups = [g for g in groups if isinstance(g, dict) and not _usergroup_disabled(g)]
        if include_count:
            groups = [
                {**g, "user_count": len(g.get("users") or [])} if isinstance(g, dict) else g
                for g in groups
            ]
        if not include_users:
            groups = [
                {k: v for k, v in g.items() if k != "users"} if isinstance(g, dict) else g
                for g in groups
            ]
        return _paged(groups, count=count, page=page, limit=limit, cursor=cursor, key="usergroups")

    def usergroups_info(self, *, usergroup: str) -> dict[str, Any]:
        want = usergroup.strip().lstrip("@")
        groups = self.usergroups_list(include_disabled=True).get("usergroups") or []
        for group in groups:
            if not isinstance(group, dict):
                continue
            if group.get("id") == want or group.get("handle") == want:
                return _ok(usergroup=group)
        return _err("usergroup_not_found")

    def usergroups_search(
        self,
        *,
        query: str,
        include_disabled: bool = False,
        include_count: bool = False,
        include_users: bool = True,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().lstrip("@").lower()
        if not needle:
            return _err("invalid_arguments")
        groups = (
            self.usergroups_list(
                include_disabled=include_disabled,
                include_count=include_count,
                include_users=include_users,
            ).get("usergroups")
            or []
        )
        hits: list[dict[str, Any]] = []
        for group in groups:
            if not isinstance(group, dict):
                continue
            blob = " ".join(
                [
                    str(group.get("id") or ""),
                    str(group.get("handle") or ""),
                    str(group.get("name") or ""),
                ]
            ).lower()
            if needle in blob:
                hits.append(group)
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="usergroups")

    def stats(self) -> dict[str, Any]:
        n_msg = 0
        n_replies = 0
        n_files = 0
        missing = False
        for ch in self._channels.values():
            raw = self._channel_sidecar(ch, "stats.json")
            if isinstance(raw, dict):
                n_msg += int(raw.get("messages") or 0)
                n_replies += int(raw.get("replies") or 0)
                n_files += int(raw.get("files") or 0)
                continue
            missing = True
            loaded = self._load(ch)
            n_msg += len(loaded.messages)
            n_replies += sum(len(m.get("thread") or []) for m in loaded.messages)
            n_replies += sum(len(r) for r in loaded.thread_only.values())
            n_files += len(loaded.files)
        if missing:
            users = len(self._all_users())
            files = self._files_len()
        else:
            self._ensure_workspace_files()
            n_known = self._files_len()
            files = n_known if n_known else n_files
            users = len(self._ensure_profiles())
        return _ok(
            channels=len(self._channels),
            messages=n_msg,
            replies=n_replies,
            files=files,
            users=users,
        )

    def get_message(self, *, channel: str, ts: str) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        msg = self._load(ch).all_by_ts.get(ts)
        if msg is None:
            return _err("message_not_found")
        return _ok(message=_history_item(msg))

    def iter_members(self, *, channel: str) -> Iterator[str]:
        yield from self.conversations_members(channel=channel).get("members") or []

    def iter_billable(self, *, user: str | None = None) -> Iterator[dict[str, Any]]:
        info = self.team_billableInfo(user=user).get("billable_info") or {}
        for uid, row in info.items():
            if isinstance(row, dict):
                yield {"user_id": uid, **row}
            else:
                yield {"user_id": uid, "billing_active": row}

    def iter_external_teams(self) -> Iterator[dict[str, Any]]:
        yield from self.team_externalTeams_list().get("teams") or []

    def iter_teams(self) -> Iterator[dict[str, Any]]:
        yield from self.auth_teams_list().get("teams") or []

    def iter_presence(self) -> Iterator[dict[str, Any]]:
        raw = self._first_json("presence.json")
        if not isinstance(raw, dict):
            return
        for uid, info in raw.items():
            if isinstance(info, dict):
                yield {"user_id": str(uid), **info}
            else:
                yield {"user_id": str(uid), "presence": info}

    def iter_users(self) -> Iterator[dict[str, Any]]:
        for profile in self._all_users().values():
            yield _slack_user(profile)

    def iter_pins(self, channel: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self.pins_list(channel=channel).get("items") or []

    def iter_bookmarks(self, channel: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self.bookmarks_list(channel=channel).get("bookmarks") or []

    def iter_bots(self) -> Iterator[dict[str, Any]]:
        yield from self.bots_list().get("bots") or []

    def iter_stars(self, *, channel: str | None = None) -> Iterator[dict[str, Any]]:
        yield from self.stars_list(channel=channel).get("items") or []

    def iter_reminders(self, *, include_complete: bool = True) -> Iterator[dict[str, Any]]:
        yield from self.reminders_list(include_complete=include_complete).get("reminders") or []

    def iter_usergroups(self, *, include_disabled: bool = False) -> Iterator[dict[str, Any]]:
        yield from self.usergroups_list(include_disabled=include_disabled).get("usergroups") or []

    def iter_scheduled(
        self,
        *,
        channel: str | None = None,
        oldest: str | float | int | None = None,
        latest: str | float | int | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from (
            self.chat_scheduledMessages_list(channel=channel, oldest=oldest, latest=latest).get(
                "scheduled_messages"
            )
            or []
        )

    def iter_calls(self) -> Iterator[dict[str, Any]]:
        yield from self.calls_list().get("calls") or []

    def iter_reactions(
        self, channel: str | None = None, user: str | None = None
    ) -> Iterator[dict[str, Any]]:
        yield from self.reactions_list(channel=channel, user=user).get("items") or []

    def iter_emoji(self) -> Iterator[dict[str, str]]:
        emoji = self.emoji_list().get("emoji") or {}
        for name, url in emoji.items():
            yield {"name": str(name), "url": str(url)}

    def iter_emoji_categories(self) -> Iterator[dict[str, Any]]:
        cats = self.emoji_list().get("categories") or []
        for cat in cats:
            if isinstance(cat, dict):
                yield cat

    def iter_cursors(self) -> Iterator[dict[str, str]]:
        for ch in self._channels.values():
            resp = self.get_cursor(channel=ch.id)
            if resp.get("ok"):
                yield {"channel": ch.id, "ts": str(resp.get("ts") or "")}

    def cursors_list(
        self,
        *,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        rows = sorted(self.iter_cursors(), key=lambda r: r["channel"])
        return _paged(rows, count=count, page=page, limit=limit, cursor=cursor, key="cursors")

    def cursors_search(
        self,
        *,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        rows = sorted(self.iter_cursors(), key=lambda r: r["channel"])
        return _search_rows(
            rows, query, key="cursors", count=count, page=page, limit=limit, cursor=cursor
        )

    def iter_access_logs(
        self,
        *,
        user: str | None = None,
        after: float | int | str | None = None,
        before: float | int | str | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from self.team_accessLogs(user=user, after=after, before=before).get("logins") or []

    def iter_integration_logs(
        self,
        *,
        user: str | None = None,
        service_id: str | None = None,
        change_type: str | None = None,
        app_id: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        yield from (
            self.team_integrationLogs(
                user=user, service_id=service_id, change_type=change_type, app_id=app_id
            ).get("logs")
            or []
        )

    def usergroups_users(
        self,
        *,
        usergroup: str,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        count: int | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        want = usergroup.strip().lstrip("@")
        for group in self.usergroups_list(include_disabled=True).get("usergroups") or []:
            if group.get("id") == want or group.get("handle") == want:
                users = list(group.get("users") or [])
                return _paged(
                    users, count=count, page=page, limit=limit, cursor=cursor, key="users"
                )
        return _err("usergroup_not_found")

    def usergroups_users_search(
        self,
        *,
        usergroup: str,
        query: str,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        count: int | None = None,
        page: int | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        want = usergroup.strip().lstrip("@")
        users: list[str] | None = None
        for group in self.usergroups_list(include_disabled=True).get("usergroups") or []:
            if group.get("id") == want or group.get("handle") == want:
                users = [str(uid) for uid in (group.get("users") or [])]
                break
        if users is None:
            return _err("usergroup_not_found")
        profiles = self._ensure_profiles()
        hits = [uid for uid in users if _uid_hit(uid, profiles.get(uid), needle)]
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="users")

    usergroups_users_list = usergroups_users

    def stars_list(
        self,
        *,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        raw = self._first_json("stars.json")
        items = raw if isinstance(raw, list) else []
        if channel:
            want = channel.strip()
            items = [
                row
                for row in items
                if isinstance(row, dict)
                and str(row.get("channel") or row.get("channel_id") or "") == want
            ]
        return _paged(items, count=count, page=page, limit=limit, cursor=cursor, key="items")

    def _star_keys(self) -> set[tuple[str, str]]:
        with self._lock:
            if self._starred is not None:
                return self._starred
        keys: set[tuple[str, str]] = set()
        for item in self.stars_list().get("items") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("channel") or item.get("channel_id") or "")
            msg = item.get("message")
            msg = msg if isinstance(msg, dict) else {}
            ts = str(msg.get("ts") or item.get("ts") or "")
            if cid and ts:
                keys.add((cid, ts))
        with self._lock:
            if self._starred is not None:
                return self._starred
            self._starred = keys
            return keys

    def stars_info(self, *, channel: str, ts: str) -> dict[str, Any]:
        return _item_by_ts(self.stars_list(channel=channel).get("items"), ts)

    def stars_search(
        self,
        *,
        query: str,
        channel: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.stars_list(channel=channel).get("items"),
            query,
            key="items",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def reminders_list(
        self,
        *,
        include_complete: bool = True,
        user: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        raw = self._first_json("reminders.json")
        items = raw if isinstance(raw, list) else []
        if user:
            want = user.strip()
            items = [row for row in items if isinstance(row, dict) and row.get("user") == want]
        if not include_complete:
            items = [row for row in items if isinstance(row, dict) and not _reminder_done(row)]
        return _paged(items, count=count, page=page, limit=limit, cursor=cursor, key="reminders")

    def reminders_info(self, *, reminder: str) -> dict[str, Any]:
        want = reminder.strip()
        for item in self.reminders_list().get("reminders") or []:
            if isinstance(item, dict) and item.get("id") == want:
                return _ok(reminder=item)
        return _err("not_found")

    def reminders_search(
        self,
        *,
        query: str,
        include_complete: bool = True,
        user: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.reminders_list(include_complete=include_complete, user=user).get("reminders"),
            query,
            key="reminders",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def dnd_teamInfo(self, *, users: str | None = None) -> dict[str, Any]:
        raw = self._first_json("dnd.json")
        if isinstance(raw, dict) and isinstance(raw.get("users"), dict):
            info = raw["users"]
        elif isinstance(raw, dict):
            info = raw
        else:
            info = {}
        if users:
            wanted = {u.strip() for u in users.split(",") if u.strip()}
            info = {k: v for k, v in info.items() if k in wanted}
        return _ok(users=info)

    def dnd_info(self, *, user: str | None = None) -> dict[str, Any]:
        users = self.dnd_teamInfo().get("users") or {}
        uid = user or (self._workspace_auth().get("user_id") or "")
        if not uid:
            return _ok(dnd_enabled=False)
        info = users.get(uid)
        if not isinstance(info, dict):
            return _err("user_not_found")
        return _ok(**info)

    def dnd_search(self, *, query: str, users: str | None = None) -> dict[str, Any]:
        return _search_map(self.dnd_teamInfo(users=users).get("users"), query, key="users")

    def team_integrationLogs(
        self,
        *,
        user: str | None = None,
        service_id: str | None = None,
        change_type: str | None = None,
        app_id: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        raw = self._first_json("integration_logs.json")
        items = raw if isinstance(raw, list) else []
        if user:
            items = [row for row in items if isinstance(row, dict) and row.get("user_id") == user]
        if service_id:
            items = [
                row
                for row in items
                if isinstance(row, dict) and str(row.get("service_id") or "") == str(service_id)
            ]
        if change_type:
            want = change_type.strip().lower()
            items = [
                row
                for row in items
                if isinstance(row, dict) and str(row.get("change_type") or "").lower() == want
            ]
        if app_id:
            want_app = str(app_id).strip()
            items = [
                row
                for row in items
                if isinstance(row, dict) and str(row.get("app_id") or "") == want_app
            ]
        return _paged(items, count=count, page=page, limit=limit, cursor=cursor, key="logs")

    def team_integrationLogs_search(
        self,
        *,
        query: str,
        user: str | None = None,
        service_id: str | None = None,
        change_type: str | None = None,
        app_id: str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        listed = self.team_integrationLogs(
            user=user, service_id=service_id, change_type=change_type, app_id=app_id
        )
        return _search_rows(
            listed.get("logs"),
            query,
            key="logs",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def team_accessLogs(
        self,
        *,
        user: str | None = None,
        before: float | int | str | None = None,
        after: float | int | str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        raw = self._first_json("access_logs.json")
        if isinstance(raw, dict) and isinstance(raw.get("logins"), list):
            items = raw["logins"]
        elif isinstance(raw, list):
            items = raw
        else:
            items = []
        if user:
            items = [row for row in items if isinstance(row, dict) and row.get("user_id") == user]
        if after is not None:
            start = parse_bound(str(after))
            if start is not None:
                items = [
                    row
                    for row in items
                    if isinstance(row, dict) and _first_ts(row, "date_last", "date_first") >= start
                ]
        if before is not None:
            end = parse_bound(str(before))
            if end is not None:
                items = [
                    row
                    for row in items
                    if isinstance(row, dict) and _first_ts(row, "date_last", "date_first") <= end
                ]
        return _paged(items, count=count, page=page, limit=limit, cursor=cursor, key="logins")

    def team_accessLogs_search(
        self,
        *,
        query: str,
        user: str | None = None,
        before: float | int | str | None = None,
        after: float | int | str | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        listed = self.team_accessLogs(user=user, before=before, after=after)
        return _search_rows(
            listed.get("logins"),
            query,
            key="logins",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def team_billableInfo(self, *, user: str | None = None) -> dict[str, Any]:
        raw = self._first_json("billable_info.json")
        if isinstance(raw, dict) and isinstance(raw.get("billable_info"), dict):
            info = raw["billable_info"]
        elif isinstance(raw, dict):
            info = raw
        else:
            info = {}
        if user:
            uid = user.strip()
            info = {uid: info[uid]} if uid in info else {}
        return _ok(billable_info=info)

    def team_billableInfo_search(self, *, query: str, user: str | None = None) -> dict[str, Any]:
        return _search_map(
            self.team_billableInfo(user=user).get("billable_info"), query, key="billable_info"
        )

    def team_profile_get(self) -> dict[str, Any]:
        raw = self._first_json("team_profile.json")
        if isinstance(raw, dict):
            profile = raw.get("profile") if isinstance(raw.get("profile"), dict) else raw
            return _ok(profile=profile)
        return _ok(profile={"fields": []})

    def team_profile_search(self, *, query: str) -> dict[str, Any]:
        if not query.strip():
            return _err("invalid_arguments")
        fields = (self.team_profile_get().get("profile") or {}).get("fields") or []
        resp = _search_rows(fields, query, key="fields", limit=_MAX_LIMIT)
        if not resp.get("ok"):
            return resp
        return _ok(profile={"fields": resp.get("fields") or []})

    def team_preferences_list(self) -> dict[str, Any]:
        raw = self._first_json("team_preferences.json")
        if isinstance(raw, dict) and isinstance(raw.get("prefs"), dict):
            prefs = raw["prefs"]
        elif isinstance(raw, dict):
            prefs = {k: v for k, v in raw.items() if k != "ok"}
        else:
            prefs = {}
        return _ok(prefs=prefs)

    def team_preferences_search(self, *, query: str) -> dict[str, Any]:
        return _search_map(self.team_preferences_list().get("prefs"), query, key="prefs")

    def team_externalTeams_list(
        self,
        *,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        raw = self._first_json("external_teams.json")
        if isinstance(raw, list):
            teams = raw
        elif isinstance(raw, dict) and isinstance(raw.get("teams"), list):
            teams = raw["teams"]
        else:
            teams = []
        return _paged(teams, count=count, page=page, limit=limit, cursor=cursor, key="teams")

    def team_externalTeams_search(
        self,
        *,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.team_externalTeams_list().get("teams"),
            query,
            key="teams",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def users_identity(self) -> dict[str, Any]:
        auth = self._workspace_auth()
        if not auth:
            return _err("not_authed")
        return _ok(
            user={"id": auth.get("user_id") or "", "name": auth.get("user") or ""},
            team={"id": auth.get("team_id") or "", "name": auth.get("team") or ""},
        )

    openid_connect_userInfo = users_identity

    def bots_info(self, *, bot: str) -> dict[str, Any]:
        want = bot.strip()
        if not want:
            return _err("bot_not_found")
        catalog = self._bots_catalog()
        if want in catalog:
            return _ok(bot=_bot_obj(catalog[want], want))
        self._load_all()
        for ch in self._channels.values():
            loaded = ch.loaded
            if loaded is None:
                continue
            for msg in iter_msgs(loaded):
                if msg.get("bot_id") != want:
                    continue
                return _ok(bot=_bot_obj(msg, want))
        return _err("bot_not_found")

    def _bots_catalog(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._bots is not None:
                return self._bots
        raw = self._first_json("bots.json")
        out: dict[str, dict[str, Any]] = {}
        if isinstance(raw, dict):
            for key, val in raw.items():
                if isinstance(val, dict):
                    out[str(val.get("id") or key)] = val
                else:
                    out[str(key)] = {"id": str(key), "name": str(val)}
        elif isinstance(raw, list):
            for item in raw:
                if isinstance(item, dict) and item.get("id"):
                    out[str(item["id"])] = item
        with self._lock:
            if self._bots is not None:
                return self._bots
            self._bots = out
            return out

    def bots_list(
        self,
        *,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        catalog = dict(self._bots_catalog())
        if not catalog:
            self._load_all()
            for ch in self._channels.values():
                loaded = ch.loaded
                if loaded is None:
                    continue
                for msg in iter_msgs(loaded):
                    bid = msg.get("bot_id")
                    if bid and str(bid) not in catalog:
                        catalog[str(bid)] = msg
            with self._lock:
                if not self._bots:
                    self._bots = catalog
                else:
                    catalog = self._bots
        bots = [_bot_obj(val, key) for key, val in catalog.items()]
        bots.sort(key=lambda b: (str(b.get("name") or ""), str(b.get("id") or "")))
        return _paged(bots, count=count, page=page, limit=limit, cursor=cursor, key="bots")

    def bots_search(
        self,
        *,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.bots_list().get("bots"),
            query,
            key="bots",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def chat_scheduledMessages_list(
        self,
        *,
        channel: str | None = None,
        oldest: str | float | int | None = None,
        latest: str | float | int | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        raw = self._first_json("scheduled_messages.json")
        items = raw if isinstance(raw, list) else []
        if channel:
            ch = self._get(channel)
            cid = ch.id if ch is not None else channel
            items = [
                m
                for m in items
                if isinstance(m, dict) and (m.get("channel_id") or m.get("channel")) == cid
            ]
        if oldest is not None:
            start = msg_ts_key(oldest)
            items = [
                m
                for m in items
                if isinstance(m, dict) and _first_ts(m, "post_at", "date_created") >= start
            ]
        if latest is not None:
            end = msg_ts_key(latest)
            items = [
                m
                for m in items
                if isinstance(m, dict) and _first_ts(m, "post_at", "date_created") <= end
            ]
        return _paged(
            items, count=count, page=page, limit=limit, cursor=cursor, key="scheduled_messages"
        )

    def chat_scheduledMessages_info(self, *, id: str) -> dict[str, Any]:
        want = id.strip()
        if not want:
            return _err("not_found")
        for row in self.chat_scheduledMessages_list().get("scheduled_messages") or []:
            if isinstance(row, dict) and str(row.get("id") or "") == want:
                return _ok(scheduled_message=row)
        return _err("not_found")

    def chat_scheduledMessages_search(
        self,
        *,
        query: str,
        channel: str | None = None,
        oldest: str | float | int | None = None,
        latest: str | float | int | None = None,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.chat_scheduledMessages_list(channel=channel, oldest=oldest, latest=latest).get(
                "scheduled_messages"
            ),
            query,
            key="scheduled_messages",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def export_jsonl(self, path: str | Path, channel: str | None = None) -> int:
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with target.open("wb") as fh:
            for msg in self.iter_messages(channel):
                fh.write(dumps_bytes(msg) + b"\n")
                n += 1
        return n

    def users_profile_get(self, *, user: str) -> dict[str, Any]:
        resp = self.users_info(user=user)
        if not resp.get("ok"):
            return resp
        return _ok(profile=(resp.get("user") or {}).get("profile") or {})

    def migration_exchange(self, *, users: str | list[str]) -> dict[str, Any]:
        if isinstance(users, str):
            ids = [u.strip() for u in users.split(",") if u.strip()]
        else:
            ids = [str(u).strip() for u in users if str(u).strip()]
        known = self._ensure_profiles()
        mapping: dict[str, str] = {}
        invalid: list[str] = []
        for uid in ids:
            if uid in known:
                mapping[uid] = uid
            else:
                invalid.append(uid)
        auth = self.auth_test()
        return _ok(
            team_id=auth.get("team_id") or "",
            enterprise_id=auth.get("enterprise_id") or "",
            user_id_map=mapping,
            invalid_user_ids=invalid,
        )

    def calls_info(self, *, id: str) -> dict[str, Any]:
        want = id.strip()
        if not want:
            return _err("not_found")
        call = self._calls_catalog().get(want)
        if call is None:
            return _err("not_found")
        return _ok(call=call)

    def calls_list(
        self,
        *,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        calls = list(self._calls_catalog().values())
        calls.sort(key=lambda c: str(c.get("id") or ""))
        return _paged(calls, count=count, page=page, limit=limit, cursor=cursor, key="calls")

    def calls_search(
        self,
        *,
        query: str,
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        return _search_rows(
            self.calls_list().get("calls"),
            query,
            key="calls",
            count=count,
            page=page,
            limit=limit,
            cursor=cursor,
        )

    def _calls_catalog(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            if self._calls is not None:
                return self._calls
        out: dict[str, dict[str, Any]] = {}
        missing: list[Channel] = []
        for ch in self._channels.values():
            raw = self._channel_sidecar(ch, "calls.json")
            if isinstance(raw, list):
                for item in raw:
                    if not isinstance(item, dict):
                        continue
                    cid = str(item.get("id") or "")
                    if cid and cid not in out:
                        out[cid] = item
            else:
                missing.append(ch)
        if missing:
            for ch in missing:
                loaded = self._load(ch)
                for msg in iter_msgs(loaded):
                    for key in ("room", "call"):
                        obj = msg.get(key)
                        if not isinstance(obj, dict):
                            continue
                        cid = str(obj.get("id") or "")
                        if cid and cid not in out:
                            out[cid] = obj
        with self._lock:
            if self._calls is not None:
                return self._calls
            self._calls = out
            return out

    def calls_participants(self, *, id: str) -> dict[str, Any]:
        info = self.calls_info(id=id)
        if not info.get("ok"):
            return info
        call = info.get("call") or {}
        raw = call.get("users") or call.get("participants") or call.get("participant_history") or []
        people: list[dict[str, Any]] = []
        for item in raw:
            if isinstance(item, str):
                people.append({"slack_id": item})
            elif isinstance(item, dict):
                people.append(item)
        return _ok(participants=people)

    def calls_participants_search(self, *, id: str, query: str) -> dict[str, Any]:
        listed = self.calls_participants(id=id)
        if not listed.get("ok"):
            return listed
        return _search_rows(listed.get("participants"), query, key="participants")

    def users_getPresence(self, *, user: str | None = None) -> dict[str, Any]:
        uid = user or (self._workspace_auth().get("user_id") or "")
        if not uid:
            return _err("user_not_found")
        raw = self._first_json("presence.json")
        info: Any = raw.get(uid) if isinstance(raw, dict) else None
        if isinstance(info, str):
            info = {"presence": info}
        if not isinstance(info, dict):
            return _err("user_not_found")
        if "presence" not in info:
            info = {"presence": "away", **info}
        return _ok(**info)

    def presence_search(self, *, query: str) -> dict[str, Any]:
        return _search_map(self._first_json("presence.json"), query, key="users")
