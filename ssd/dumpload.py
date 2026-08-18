"""Dump directory discovery and in-memory channel load cache.

Leaf used by ``dumpapi.DumpClient``: walks ssd/export layouts, reads
``messages.json`` / day files / thread dumps, and builds ``Loaded`` caches.
No Slack network I/O and no Web API response shaping.
"""

from __future__ import annotations

import os
import re
import sys
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ssd.dumpsearch import WORD_RE, msg_ts_key
from ssd.output import max_ts, read_json, thread_reply_meta
from ssd.parser import CHANNEL_DIR_RE, dir_rank, ts_from_thread_dir

_DATE_JSON_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
LOAD_WORKERS = min(32, (os.cpu_count() or 4) * 4)


def doc_text(msg: dict[str, Any]) -> str:
    parts = [msg.get("text") or "", msg.get("text_raw") or ""]
    for f in msg.get("files") or []:
        parts.append(str(f.get("name") or ""))
        parts.append(str(f.get("title") or ""))
    return " ".join(parts).lower()


def build_word_bigrams(words: dict[str, list[int]]) -> dict[str, set[str]]:
    """Map each 2-char gram to vocabulary words that contain it.

    Lets ``_token_hits`` avoid a full vocabulary scan on every query token.
    """
    out: dict[str, set[str]] = {}
    for w in words:
        if len(w) < 2:
            continue
        seen: set[str] = set()
        for i in range(len(w) - 1):
            bg = w[i : i + 2]
            if bg in seen:
                continue
            seen.add(bg)
            out.setdefault(bg, set()).add(w)
    return out


def _token_hits(
    words: dict[str, list[int]],
    tok: str,
    bigrams: dict[str, set[str]] | None = None,
) -> set[int]:
    """Doc indexes whose inverted-index keys contain ``tok`` as a substring."""
    hits: set[int] = set()
    exact = words.get(tok)
    if exact:
        hits.update(exact)
    n = len(tok)
    if n < 2:
        return hits
    if bigrams:
        # Intersect bigram postings, then verify ``tok in word`` (necessary filter).
        cands: set[str] | None = None
        for i in range(n - 1):
            bucket = bigrams.get(tok[i : i + 2])
            if not bucket:
                return hits
            cands = set(bucket) if cands is None else cands & bucket
            if not cands:
                return hits
        assert cands is not None  # loop runs ≥ once since n ≥ 2
        for word in cands:
            if len(word) > n and tok in word:
                hits.update(words[word])
        return hits
    for word, idxs in words.items():
        if len(word) > n and tok in word:
            hits.update(idxs)
    return hits


def docs_for_query(loaded: Loaded, query: str) -> list[tuple[str, dict[str, Any]]]:
    """Narrow search candidates via the per-channel word index.

    Single- and multi-token queries use inverted-index intersection so phrase
    checks later only scan docs that contain every token (substring match on
    word keys, same as the old single-token path). Empty / short-token-only
    queries still return the full doc list.
    """
    if not query:
        return loaded.docs
    tokens = [t for t in WORD_RE.findall(query) if len(t) >= 2]
    if not tokens:
        return loaded.docs
    candidate: set[int] | None = None
    bigrams = loaded.word_bigrams
    for tok in tokens:
        hits = _token_hits(loaded.words, tok, bigrams)
        candidate = hits if candidate is None else candidate & hits
        if not candidate:
            return []
    assert candidate is not None
    # Order does not matter: search.messages re-sorts hits by timestamp.
    return [loaded.docs[i] for i in candidate]


def kinds_for(channel_id: str, meta: dict[str, Any] | None = None) -> frozenset[str]:
    """Map a conversation id (+ optional Slack flags) to conversations.list types.

    When ``meta`` carries classifying flags (``is_im`` / ``is_mpim`` /
    ``is_channel`` / ``is_group`` / ``is_private``), those win. Without meta,
    prefix heuristics match DumpClient channel-object defaults: D→im, G→mpim,
    C→public_channel. Private C-ids and non-mpim G-ids need channel.json or a
    conversations catalog entry to land in ``private_channel``.
    """
    meta = meta or {}
    if meta.get("is_im"):
        return frozenset({"im"})
    if meta.get("is_mpim"):
        return frozenset({"mpim"})
    if meta.get("is_channel") is True and not meta.get("is_private"):
        return frozenset({"public_channel"})
    if meta.get("is_private") or (meta.get("is_group") and not meta.get("is_mpim")):
        return frozenset({"private_channel"})
    prefix = channel_id[:1] if channel_id else ""
    if prefix == "D":
        return frozenset({"im"})
    if prefix == "G":
        return frozenset({"mpim"})
    return frozenset({"public_channel"})


def ingest(
    msg: dict[str, Any],
    files: dict[str, dict[str, Any]],
    users: dict[str, dict[str, Any]],
    members: set[str],
    channel_id: str,
) -> None:
    uid = msg.get("user") or ""
    if uid:
        members.add(uid)
    if uid and uid not in users:
        name = msg.get("user_name") or uid
        users[uid] = {
            "id": uid,
            "handle": name,
            "display_name": name,
            "real_name": name,
            "title": "",
            "email": "",
            "phone": "",
            "status_text": "",
            "status_emoji": "",
            "timezone": "",
            "timezone_label": "",
            "is_bot": False,
            "image": "",
        }
    for rx in msg.get("reactions") or []:
        for user_id in rx.get("users") or []:
            if user_id:
                members.add(user_id)
    for f in msg.get("files") or []:
        fid = f.get("id")
        if not fid:
            continue
        stored = dict(f)
        chans = list(stored.get("channels") or [])
        if channel_id and channel_id not in chans:
            chans.append(channel_id)
            stored["channels"] = chans
        if uid and not stored.get("user"):
            stored["user"] = uid
        files[fid] = stored
        file_user = stored.get("user") or ""
        if file_user:
            members.add(file_user)


def _profile_from_any_user(user: dict[str, Any]) -> dict[str, Any]:
    if "handle" in user:
        return user
    p = user.get("profile")
    p = p if isinstance(p, dict) else {}
    return {
        "id": user.get("id") or "",
        "handle": user.get("name") or p.get("display_name") or "",
        "display_name": p.get("display_name") or user.get("name") or "",
        "real_name": p.get("real_name") or user.get("real_name") or "",
        "title": p.get("title") or "",
        "email": p.get("email") or "",
        "phone": p.get("phone") or "",
        "status_text": p.get("status_text") or "",
        "status_emoji": p.get("status_emoji") or "",
        "status_text_canonical": user.get("status_text_canonical")
        or p.get("status_text_canonical")
        or "",
        "timezone": user.get("tz") or "",
        "timezone_label": user.get("tz_label") or "",
        "is_bot": bool(user.get("is_bot")),
        "deleted": bool(user.get("deleted")),
        "is_admin": bool(user.get("is_admin")),
        "is_owner": bool(user.get("is_owner")),
        "is_restricted": bool(user.get("is_restricted")),
        "is_ultra_restricted": bool(user.get("is_ultra_restricted")),
        "is_app_user": bool(user.get("is_app_user")),
        "is_stranger": bool(user.get("is_stranger")),
        "is_invited_user": bool(user.get("is_invited_user")),
        "is_primary_owner": bool(user.get("is_primary_owner")),
        "always_active": bool(user.get("always_active")),
        "is_email_confirmed": bool(user.get("is_email_confirmed")),
        "huddle_state": user.get("huddle_state") or "",
        "huddle_state_expiration_ts": user.get("huddle_state_expiration_ts") or 0,
        "who_can_share_contact_card": user.get("who_can_share_contact_card") or "",
        "team_id": user.get("team_id") or "",
        "is_forgotten": bool(user.get("is_forgotten")),
        "is_workflow_bot": bool(user.get("is_workflow_bot")),
        "has_2fa": bool(user.get("has_2fa")),
        "two_factor_type": user.get("two_factor_type") or "",
        "guest_invited_by": user.get("guest_invited_by") or "",
        "is_connector": bool(user.get("is_connector")),
        "enterprise_user": user.get("enterprise_user") or {},
        "locale": user.get("locale") or "",
        "color": user.get("color") or "",
        "updated": user.get("updated") or 0,
        "tz_offset": user.get("tz_offset") or 0,
        "image": p.get("image_192") or p.get("image_72") or "",
        "image_192": user.get("image_192") or p.get("image_192") or "",
        "first_name": user.get("first_name") or p.get("first_name") or "",
        "last_name": user.get("last_name") or p.get("last_name") or "",
        "skype": user.get("skype") or p.get("skype") or "",
        "status_expiration": user.get("status_expiration") or p.get("status_expiration") or 0,
        "avatar_hash": user.get("avatar_hash") or p.get("avatar_hash") or "",
        "pronouns": user.get("pronouns") or p.get("pronouns") or "",
        "start_date": user.get("start_date") or p.get("start_date") or "",
        "status_emoji_display_info": user.get("status_emoji_display_info")
        or p.get("status_emoji_display_info")
        or [],
        "image_72": user.get("image_72") or p.get("image_72") or "",
        "image_512": user.get("image_512") or p.get("image_512") or "",
        "image_original": user.get("image_original") or p.get("image_original") or "",
        "image_24": user.get("image_24") or p.get("image_24") or "",
        "image_32": user.get("image_32") or p.get("image_32") or "",
        "image_48": user.get("image_48") or p.get("image_48") or "",
        "image_1024": user.get("image_1024") or p.get("image_1024") or "",
        "is_custom_image": bool(user.get("is_custom_image") or p.get("is_custom_image")),
        "fields": user.get("fields") or p.get("fields") or {},
        "display_name_normalized": user.get("display_name_normalized")
        or p.get("display_name_normalized")
        or p.get("display_name")
        or "",
        "real_name_normalized": user.get("real_name_normalized")
        or p.get("real_name_normalized")
        or p.get("real_name")
        or user.get("real_name")
        or "",
        "guest_expiration_ts": user.get("guest_expiration_ts") or p.get("guest_expiration_ts") or 0,
        "bot_id": user.get("bot_id") or p.get("bot_id") or "",
        "api_app_id": user.get("api_app_id") or p.get("api_app_id") or "",
        "team": user.get("team") or p.get("team") or "",
    }


def ingest_users(raw: Any, profiles: dict[str, dict[str, Any]]) -> None:
    if isinstance(raw, list):
        for user in raw:
            if isinstance(user, dict) and user.get("id"):
                profiles[str(user["id"])] = _profile_from_any_user(user)
        return
    if isinstance(raw, dict):
        for key, user in raw.items():
            if isinstance(user, dict):
                uid = str(user.get("id") or key)
                profiles[uid] = _profile_from_any_user(user)


def _reply_user_ids(msg: dict[str, Any], replies: list[Any]) -> list[str]:
    users = [str(u) for u in (msg.get("reply_users") or []) if u]
    if users:
        return users
    seen: set[str] = set()
    out: list[str] = []
    for reply in replies:
        if not isinstance(reply, dict):
            continue
        uid = str(reply.get("user") or "")
        if uid and uid not in seen:
            seen.add(uid)
            out.append(uid)
    return out


def threads_from_loaded(ch: Channel, loaded: Loaded) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for msg in loaded.messages:
        meta = thread_reply_meta(msg)
        if meta is None:
            continue
        count, latest = meta
        thread = [r for r in (msg.get("thread") or []) if isinstance(r, dict) and r.get("ts")]
        users = _reply_user_ids(msg, thread)
        rows.append(
            {
                "channel": ch.id,
                "thread_ts": msg.get("ts", ""),
                "reply_count": count,
                "latest_reply": latest,
                "reply_users": users,
                "reply_users_count": int(msg.get("reply_users_count") or len(users)),
            }
        )
    for ts, replies in loaded.thread_only.items():
        users = _reply_user_ids({}, replies)
        reply_ts = [str(r["ts"]) for r in replies if isinstance(r, dict) and r.get("ts")]
        rows.append(
            {
                "channel": ch.id,
                "thread_ts": ts,
                "reply_count": len(reply_ts),
                "latest_reply": max_ts(reply_ts) if reply_ts else "",
                "reply_users": users,
                "reply_users_count": len(users),
            }
        )
    return rows


def iter_msgs(loaded: Loaded) -> Iterator[dict[str, Any]]:
    """Yield parents, nested replies, then standalone thread-only replies."""
    for msg in loaded.messages:
        yield msg
        thread = msg.get("thread")
        if thread:
            yield from thread
    for replies in loaded.thread_only.values():
        yield from replies


@dataclass
class Loaded:
    """In-memory index for one channel's messages.

    ``by_ts`` holds top-level (parent) messages only.
    ``all_by_ts`` holds every message including thread replies. Use this for
    point lookups by ts when the caller does not know whether the ts is a parent
    or a reply.
    ``thread_root`` maps a reply ts to its parent ts.
    ``thread_only`` holds replies whose parent is not present in ``messages``
    (e.g. standalone thread dumps with no matching channel history entry).
    """

    messages: list[dict[str, Any]]
    by_ts: dict[str, dict[str, Any]]
    all_by_ts: dict[str, dict[str, Any]]
    thread_root: dict[str, str]
    thread_only: dict[str, list[dict[str, Any]]]
    files: dict[str, dict[str, Any]]
    users_extra: dict[str, dict[str, Any]]
    history_newest: list[dict[str, Any]] | None
    member_ids: list[str]
    docs: list[tuple[str, dict[str, Any]]]
    words: dict[str, list[int]]
    word_bigrams: dict[str, set[str]] = field(default_factory=dict)
    search_ready: bool = False


def empty_loaded() -> Loaded:
    return Loaded([], {}, {}, {}, {}, {}, {}, None, [], [], {})


@dataclass
class Channel:
    id: str
    name: str
    workspace: str
    path: Path
    kinds: frozenset[str]
    thread_dumps: dict[str, Path]
    loaded: Loaded | None = field(default=None, repr=False)
    meta: dict[str, Any] | None = field(default=None, repr=False)
    meta_checked: bool = field(default=False, repr=False)
    obj_cache: dict[str, Any] | None = field(default=None, repr=False)
    roster: list[str] | None = field(default=None, repr=False)
    sidecars: dict[str, Any] | None = field(default=None, repr=False)


def _date_jsons(path: Path) -> list[Path]:
    try:
        kids = path.iterdir()
    except OSError:
        return []
    return sorted(p for p in kids if p.is_file() and _DATE_JSON_RE.match(p.name))


def read_channel_messages(path: Path, *, parallel: bool = True) -> list[dict[str, Any]]:
    """Read channel messages from messages.json or dated day files.

    ``parallel`` enables a ThreadPoolExecutor over day files. Callers that are
    already inside another pool (DumpClient._load_all) must pass parallel=False
    to avoid nested executor thread explosion.
    """
    msg_path = path / "messages.json"
    if msg_path.is_file():
        try:
            raw = read_json(msg_path)
        except (OSError, ValueError) as exc:
            print(f"  skip unreadable {msg_path}: {exc}", file=sys.stderr, flush=True)
            return []
        if not isinstance(raw, list):
            print(
                f"  skip {msg_path}: expected list, got {type(raw).__name__}",
                file=sys.stderr,
                flush=True,
            )
            return []
        return raw
    files = _date_jsons(path)
    if not files:
        return []

    def _safe_read(f: Path) -> list[Any]:
        try:
            raw = read_json(f)
        except (OSError, ValueError) as exc:
            print(f"  skip unreadable {f}: {exc}", file=sys.stderr, flush=True)
            return []
        return raw if isinstance(raw, list) else []

    if len(files) == 1 or not parallel:
        chunks = [_safe_read(f) for f in files]
    else:
        with ThreadPoolExecutor(max_workers=min(LOAD_WORKERS, len(files))) as pool:
            chunks = list(pool.map(_safe_read, files))
    out: list[dict[str, Any]] = [m for chunk in chunks for m in chunk]
    out.sort(key=lambda m: msg_ts_key(m.get("ts") if isinstance(m, dict) else None))
    return out


def split_parents(
    messages: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    parents: list[dict[str, Any]] = []
    loose: dict[str, list[dict[str, Any]]] = {}
    for msg in messages:
        ts = str(msg.get("ts") or "")
        thread_ts = str(msg.get("thread_ts") or "")
        if thread_ts and ts and thread_ts != ts:
            loose.setdefault(thread_ts, []).append(msg)
        else:
            parents.append(msg)
    return parents, loose


def _scan_channel(path: Path) -> tuple[bool, dict[str, Path]]:
    """One directory listing: whether ``path`` is a channel dump, plus thread dumps.

    Returns both results together so callers that need both (e.g. ``discover``)
    only walk the directory once.
    """
    if not path.is_dir():
        return False, {}
    name_ok = bool(CHANNEL_DIR_RE.match(path.name))
    thread_dumps: dict[str, Path] = {}
    has_messages = False
    has_date = False
    try:
        kids = path.iterdir()
    except OSError:
        return name_ok, {}
    for entry in kids:
        if entry.is_file():
            name = entry.name
            if name == "messages.json":
                has_messages = True
            elif _DATE_JSON_RE.match(name):
                has_date = True
            continue
        if not entry.is_dir() or not entry.name.startswith("thread_"):
            continue
        tf = entry / "thread.json"
        if not tf.is_file():
            continue
        ts = ts_from_thread_dir(entry.name)
        if ts:
            thread_dumps[ts] = tf
    is_channel = name_ok or has_messages or has_date or bool(thread_dumps)
    return is_channel, thread_dumps


def is_channel_dir(path: Path) -> bool:
    # Named ``{name}_{id}`` dirs count even when empty (legacy layout); see _scan_channel.
    is_channel, _ = _scan_channel(path)
    return is_channel


def _make_channel(path: Path, workspace: str, *, thread_dumps: dict[str, Path]) -> Channel:
    m = CHANNEL_DIR_RE.match(path.name)
    if m:
        name, cid = m.group(1), m.group(2)
    else:
        name, cid = path.name, path.name
    return Channel(
        id=cid,
        name=name,
        workspace=workspace,
        path=path,
        kinds=kinds_for(cid),
        thread_dumps=thread_dumps,
    )


def discover(root: Path) -> dict[str, Channel]:
    found: dict[str, Channel] = {}

    def add(path: Path, workspace: str, dumps: dict[str, Path]) -> None:
        ch = _make_channel(path, workspace, thread_dumps=dumps)
        prev = found.get(ch.id)
        if prev is None or dir_rank(ch.path) > dir_rank(prev.path):
            found[ch.id] = ch

    is_root, root_dumps = _scan_channel(root)
    if is_root:
        add(root, root.parent.name, root_dumps)
        return found

    try:
        children = sorted(root.iterdir())
    except OSError:
        return found
    for child in children:
        if not child.is_dir():
            continue
        is_ch, dumps = _scan_channel(child)
        if is_ch:
            add(child, root.name, dumps)
            continue
        try:
            grandchildren = sorted(child.iterdir())
        except OSError:
            continue
        for gc in grandchildren:
            if not gc.is_dir():
                continue
            is_gc, gc_dumps = _scan_channel(gc)
            if is_gc:
                add(gc, child.name, gc_dumps)
    return found
