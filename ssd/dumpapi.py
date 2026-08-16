"""Read-only Slack Web API facade over ssd dump directories. No network.

Construct from an output root, a workspace dir, or a single channel dir::

    from ssd.dumpapi import DumpClient
    client = DumpClient("output")
    client.conversations_list()
    client.conversations_history(channel="C123")

Fast path: ``DumpClient`` scans directory names once at open and does not parse
JSON until a channel is first read. Each ``messages.json`` / ``thread.json`` is
loaded at most once and reused for later history, replies, search, and file
lookups. ``conversations.list`` is served from the in-memory channel index.

Gaps vs live Slack:
- write methods are not implemented
- search.messages supports substring plus ``from:``, ``in:``, and ``has:``
  (file/reaction/pin); not full Slack search syntax
- C-prefix channels cannot be told apart as public vs private
- team_id and the authed user are unknown
- message text already has ``<@U...>`` replaced with ``@display_name``
- standalone thread dumps may omit the parent message
- files.info needs a Slack file id (kept on dump; older ``--attachments``
  runs used to strip ids)
- conversations.members is everyone who posted, replied, or reacted in the
  dump, not Slack's live membership roster
- pins.list is empty unless messages include ``pinned_to`` / ``pinned_info``
- emoji.list is not available (dumps have reaction names on messages, not
  a custom-emoji catalog)
"""

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

_CHANNEL_DIR_RE = re.compile(r"^(.*)_([CDG][A-Za-z0-9]+)$")
_ALL_TYPES = "public_channel,private_channel,mpim,im"
_MAX_LIMIT = 10_000  # ponytail: no Slack 999 cap; still bound so a typo cannot allocate forever
_SEARCH_MOD_RE = re.compile(r'(?i)\b(from|in|has):(?:"([^"]+)"|(\S+))')
_SKIP_COPY = frozenset({"thread"})


def _parse_search(query: str) -> tuple[str, dict[str, list[str]]]:
    mods: dict[str, list[str]] = {"from": [], "in": [], "has": []}

    def repl(m: re.Match[str]) -> str:
        val = m.group(2) if m.group(2) is not None else m.group(3)
        mods[m.group(1).lower()].append(val)
        return " "

    text = " ".join(_SEARCH_MOD_RE.sub(repl, query).split())
    return text, mods


def _norm_from(tok: str) -> str:
    return tok.strip().strip("<>").lstrip("@")


def _norm_in(tok: str) -> str:
    return tok.strip().lstrip("#")


def _channel_in_scope(ch: "_Channel", in_toks: list[str]) -> bool:
    if not in_toks:
        return True
    names = {ch.id.lower(), ch.name.lower()}
    return any(_norm_in(t).lower() in names for t in in_toks)


def _msg_from_ok(msg: dict[str, Any], from_toks: list[str], profile: dict[str, Any]) -> bool:
    if not from_toks:
        return True
    candidates = {
        (msg.get("user") or "").lower(),
        (msg.get("user_name") or "").lower(),
        (profile.get("handle") or "").lower(),
        (profile.get("display_name") or "").lower(),
        (profile.get("id") or "").lower(),
    }
    candidates.discard("")
    return any(_norm_from(tok).lower() in candidates for tok in from_toks)


def _msg_has_ok(msg: dict[str, Any], has_toks: list[str]) -> bool:
    for tok in has_toks:
        kind = tok.lower()
        if kind in {"file", "files"}:
            if not msg.get("files"):
                return False
        elif kind in {"reaction", "reactions", "emoji"}:
            if not msg.get("reactions"):
                return False
        elif kind in {"pin", "pinned"}:
            if not (msg.get("pinned_to") or msg.get("pinned_info")):
                return False
        else:
            return False
    return True


def _ok(**kwargs: Any) -> dict[str, Any]:
    return {"ok": True, **kwargs}


def _err(error: str) -> dict[str, Any]:
    return {"ok": False, "error": error}


def _read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def _ts_from_thread_dir(name: str) -> str | None:
    if not name.startswith("thread_"):
        return None
    rest = name[len("thread_") :]
    if "_" not in rest:
        return None
    return rest.replace("_", ".", 1)


def _kinds_for(channel_id: str) -> frozenset[str]:
    prefix = channel_id[:1] if channel_id else ""
    if prefix == "D":
        return frozenset({"im"})
    if prefix == "G":
        return frozenset({"mpim", "private_channel"})
    return frozenset({"public_channel", "private_channel"})


def _page(
    items: list[Any], limit: int, cursor: str | None
) -> tuple[list[Any], bool, str] | dict[str, Any]:
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
    return chunk, has_more, str(next_off) if has_more else ""


def _in_range(ts: str, oldest: str | None, latest: str | None, inclusive: bool) -> bool:
    t = float(ts)
    if oldest is not None:
        o = float(oldest)
        if t < o if inclusive else t <= o:
            return False
    if latest is not None:
        end = float(latest)
        if t > end if inclusive else t >= end:
            return False
    return True


def _ingest(
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


def _slack_user(profile: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": profile.get("id", ""),
        "name": profile.get("handle") or profile.get("display_name") or profile.get("id", ""),
        "is_bot": bool(profile.get("is_bot")),
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
            "image_192": profile.get("image") or "",
        },
    }


def _copy_extras(msg: dict[str, Any], item: dict[str, Any]) -> None:
    for key, value in msg.items():
        if key not in item and key not in _SKIP_COPY:
            item[key] = value


def _history_item(msg: dict[str, Any]) -> dict[str, Any]:
    thread = msg.get("thread") or []
    item = {
        "type": "message",
        "ts": msg["ts"],
        "user": msg.get("user") or "",
        "text": msg.get("text") or "",
        "user_name": msg.get("user_name") or "",
        "reactions": msg.get("reactions") or [],
        "files": msg.get("files") or [],
    }
    _copy_extras(msg, item)
    if thread:
        item["reply_count"] = len(thread)
        item["thread_ts"] = msg["ts"]
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


def _iter_msgs(loaded: "_Loaded") -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for msg in loaded.messages:
        out.append(msg)
        out.extend(msg.get("thread") or [])
    for replies in loaded.thread_only.values():
        out.extend(replies)
    return out


@dataclass
class _Loaded:
    messages: list[dict[str, Any]]
    by_ts: dict[str, dict[str, Any]]
    thread_only: dict[str, list[dict[str, Any]]]
    files: dict[str, dict[str, Any]]
    users_extra: dict[str, dict[str, Any]]
    history_newest: list[dict[str, Any]]
    member_ids: list[str]


@dataclass
class _Channel:
    id: str
    name: str
    workspace: str
    path: Path
    kinds: frozenset[str]
    thread_dumps: dict[str, Path]
    loaded: _Loaded | None = field(default=None, repr=False)


def _thread_dumps(path: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    try:
        subs = path.iterdir()
    except OSError:
        return out
    for sub in subs:
        if not sub.is_dir() or not sub.name.startswith("thread_"):
            continue
        tf = sub / "thread.json"
        if not tf.is_file():
            continue
        ts = _ts_from_thread_dir(sub.name)
        if ts:
            out[ts] = tf
    return out


def _is_channel_dir(path: Path) -> bool:
    if not path.is_dir():
        return False
    if _CHANNEL_DIR_RE.match(path.name):
        return True
    if (path / "messages.json").is_file():
        return True
    return bool(_thread_dumps(path))


def _make_channel(path: Path, workspace: str) -> _Channel:
    m = _CHANNEL_DIR_RE.match(path.name)
    if m:
        name, cid = m.group(1), m.group(2)
    else:
        name, cid = path.name, path.name
    return _Channel(
        id=cid,
        name=name,
        workspace=workspace,
        path=path,
        kinds=_kinds_for(cid),
        thread_dumps=_thread_dumps(path),
    )


def _discover(root: Path) -> dict[str, _Channel]:
    found: dict[str, _Channel] = {}

    def add(path: Path, workspace: str) -> None:
        ch = _make_channel(path, workspace)
        found[ch.id] = ch

    if _is_channel_dir(root):
        add(root, root.parent.name)
        return found

    for child in sorted(root.iterdir()):
        if not child.is_dir():
            continue
        if _is_channel_dir(child):
            add(child, root.name)
            continue
        try:
            grandchildren = sorted(child.iterdir())
        except OSError:
            continue
        for gc in grandchildren:
            if gc.is_dir() and _is_channel_dir(gc):
                add(gc, child.name)
    return found


class DumpClient:
    """Read-only Slack Web API over a local ssd dump. No tokens, no network."""

    def __init__(self, path: str | Path):
        self.root = Path(path)
        if not self.root.exists():
            raise FileNotFoundError(self.root)
        self._channels = _discover(self.root)
        self._profiles: dict[str, dict[str, Any]] | None = None
        self._files: dict[str, dict[str, Any]] = {}
        self._all_loaded = False

    def _get(self, channel: str) -> _Channel | None:
        raw = channel.lstrip("#")
        if raw in self._channels:
            return self._channels[raw]
        for ch in self._channels.values():
            if ch.name == raw:
                return ch
        return None

    def _load(self, ch: _Channel) -> _Loaded:
        if ch.loaded is not None:
            return ch.loaded
        messages: list[dict[str, Any]] = []
        msg_path = ch.path / "messages.json"
        if msg_path.is_file():
            raw = _read_json(msg_path)
            if isinstance(raw, list):
                messages = raw
        by_ts: dict[str, dict[str, Any]] = {}
        files: dict[str, dict[str, Any]] = {}
        users_extra: dict[str, dict[str, Any]] = {}
        members: set[str] = set()
        for msg in messages:
            ts = msg.get("ts")
            if ts:
                by_ts[str(ts)] = msg
            _ingest(msg, files, users_extra, members, ch.id)
            for reply in msg.get("thread") or []:
                _ingest(reply, files, users_extra, members, ch.id)
        thread_only: dict[str, list[dict[str, Any]]] = {}
        for ts, tpath in ch.thread_dumps.items():
            if ts in by_ts:
                continue
            raw = _read_json(tpath) if tpath.is_file() else []
            replies = raw if isinstance(raw, list) else []
            thread_only[ts] = replies
            for reply in replies:
                _ingest(reply, files, users_extra, members, ch.id)
        history_newest = [_history_item(m) for m in reversed(messages) if m.get("ts")]
        ch.loaded = _Loaded(
            messages,
            by_ts,
            thread_only,
            files,
            users_extra,
            history_newest,
            sorted(members),
        )
        self._files.update(files)
        return ch.loaded

    def _load_all(self) -> None:
        if self._all_loaded:
            return
        for ch in self._channels.values():
            self._load(ch)
        self._all_loaded = True

    def _ensure_profiles(self) -> dict[str, dict[str, Any]]:
        if self._profiles is not None:
            return self._profiles
        profiles: dict[str, dict[str, Any]] = {}
        seen: set[Path] = set()
        for ch in self._channels.values():
            candidates = [ch.path / "users.json"]
            candidates.extend(p.parent / "users.json" for p in ch.thread_dumps.values())
            for path in candidates:
                if path in seen or not path.is_file():
                    continue
                seen.add(path)
                raw = _read_json(path)
                if isinstance(raw, dict):
                    profiles.update(raw)
        self._profiles = profiles
        return profiles

    def _all_users(self) -> dict[str, dict[str, Any]]:
        users = dict(self._ensure_profiles())
        self._load_all()
        for ch in self._channels.values():
            loaded = ch.loaded
            if loaded is None:
                continue
            for uid, profile in loaded.users_extra.items():
                if uid not in users:
                    users[uid] = profile
        return users

    def _channel_obj(self, ch: _Channel) -> dict[str, Any]:
        prefix = ch.id[:1]
        return {
            "id": ch.id,
            "name": ch.name,
            "is_channel": prefix == "C",
            "is_group": prefix == "G",
            "is_im": prefix == "D",
            "is_mpim": prefix == "G",
            "is_private": prefix in {"D", "G"},
            "is_archived": False,
            "workspace": ch.workspace,
        }

    def auth_test(self) -> dict[str, Any]:
        workspaces = list(
            dict.fromkeys(ch.workspace for ch in self._channels.values() if ch.workspace)
        )
        team = workspaces[0] if workspaces else self.root.name
        return _ok(
            url=f"https://{team}.slack.com/",
            team=team,
            team_id="",
            user="",
            user_id="",
        )

    def conversations_list(
        self,
        *,
        types: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        wanted = {t.strip() for t in (types or _ALL_TYPES).split(",") if t.strip()}
        channels = [self._channel_obj(ch) for ch in self._channels.values() if ch.kinds & wanted]
        paged = _page(channels, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(
            channels=chunk,
            response_metadata={"next_cursor": next_cursor},
        )

    def conversations_info(self, *, channel: str) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        return _ok(channel=self._channel_obj(ch))

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
        items = loaded.history_newest
        if oldest is not None or latest is not None:
            items = [m for m in items if _in_range(str(m["ts"]), oldest, latest, inclusive)]
        paged = _page(items, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, has_more, next_cursor = paged
        return _ok(
            messages=chunk,
            has_more=has_more,
            response_metadata={"next_cursor": next_cursor},
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
        parent = loaded.by_ts.get(ts)
        if parent is not None:
            items = [_reply_item({**parent, "thread_ts": parent["ts"]})]
            for reply in parent.get("thread") or []:
                items.append(_reply_item({**reply, "thread_ts": parent["ts"]}))
        elif ts in loaded.thread_only:
            items = [_reply_item(r) for r in loaded.thread_only[ts]]
        else:
            return _err("thread_not_found")
        filtered = [
            m for m in items if m.get("ts") and _in_range(str(m["ts"]), oldest, latest, inclusive)
        ]
        paged = _page(filtered, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, has_more, next_cursor = paged
        return _ok(
            messages=chunk,
            has_more=has_more,
            response_metadata={"next_cursor": next_cursor},
        )

    def users_list(self, *, limit: int = _MAX_LIMIT, cursor: str | None = None) -> dict[str, Any]:
        members = [_slack_user(p) for p in self._all_users().values()]
        members.sort(key=lambda u: (u.get("name") or "", u.get("id") or ""))
        paged = _page(members, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(members=chunk, response_metadata={"next_cursor": next_cursor})

    def users_info(self, *, user: str) -> dict[str, Any]:
        profiles = self._ensure_profiles()
        profile = profiles.get(user)
        if profile is None:
            self._load_all()
            for ch in self._channels.values():
                loaded = ch.loaded
                if loaded and user in loaded.users_extra:
                    profile = loaded.users_extra[user]
                    break
        if profile is None:
            return _err("user_not_found")
        return _ok(user=_slack_user(profile))

    def files_info(self, *, file: str) -> dict[str, Any]:
        if file in self._files:
            return _ok(file=self._files[file])
        for ch in self._channels.values():
            loaded = self._load(ch)
            if file in loaded.files:
                return _ok(file=loaded.files[file])
        return _err("file_not_found")

    def files_list(
        self,
        *,
        channel: str | None = None,
        user: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        if channel:
            ch = self._get(channel)
            if ch is None:
                return _err("channel_not_found")
            files = list(self._load(ch).files.values())
        else:
            self._load_all()
            files = list(self._files.values())
        if user:
            files = [f for f in files if f.get("user") == user]
        files.sort(key=lambda f: str(f.get("id") or ""))
        paged = _page(files, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(files=chunk, response_metadata={"next_cursor": next_cursor})

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
        loaded = self._load(ch)
        paged = _page(loaded.member_ids, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(members=chunk, response_metadata={"next_cursor": next_cursor})

    def users_lookupByEmail(self, *, email: str) -> dict[str, Any]:
        want = email.strip().lower()
        if not want:
            return _err("users_not_found")
        for profile in self._ensure_profiles().values():
            if (profile.get("email") or "").strip().lower() == want:
                return _ok(user=_slack_user(profile))
        return _err("users_not_found")

    def pins_list(self, *, channel: str) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        loaded = self._load(ch)
        items: list[dict[str, Any]] = []
        for msg in _iter_msgs(loaded):
            if not (msg.get("pinned_to") or msg.get("pinned_info")):
                continue
            info = msg.get("pinned_info") or {}
            items.append(
                {
                    "type": "message",
                    "created": info.get("pinned_ts") or 0,
                    "created_by": info.get("pinned_by") or msg.get("user") or "",
                    "channel": ch.id,
                    "message": _history_item(msg) if "ts" in msg else msg,
                }
            )
        return _ok(items=items)

    def team_info(self) -> dict[str, Any]:
        auth = self.auth_test()
        team = auth["team"]
        return _ok(team={"id": auth.get("team_id") or "", "name": team, "domain": team})

    def chat_getPermalink(self, *, channel: str, message_ts: str) -> dict[str, Any]:
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        loaded = self._load(ch)
        if not any(m.get("ts") == message_ts for m in _iter_msgs(loaded)):
            return _err("message_not_found")
        stamp = message_ts.replace(".", "")
        permalink = f"https://{ch.workspace}.slack.com/archives/{ch.id}/p{stamp}"
        return _ok(channel=ch.id, permalink=permalink)

    def search_messages(self, *, query: str, count: int = 20) -> dict[str, Any]:
        text, mods = _parse_search(query)
        text_l = text.lower()
        empty = _ok(query=query, messages={"total": 0, "matches": []})
        if not text_l and not any(mods.values()):
            return empty
        scoped = [ch for ch in self._channels.values() if _channel_in_scope(ch, mods["in"])]
        if mods["in"] and not scoped:
            return empty
        if mods["in"]:
            for ch in scoped:
                self._load(ch)
        else:
            self._load_all()
            scoped = list(self._channels.values())
        profiles = self._ensure_profiles()
        matches: list[dict[str, Any]] = []
        for ch in scoped:
            loaded = ch.loaded
            if loaded is None:
                continue
            extras = loaded.users_extra
            for msg in _iter_msgs(loaded):
                uid = msg.get("user") or ""
                profile = profiles.get(uid) or extras.get(uid) or {}
                if not _msg_from_ok(msg, mods["from"], profile):
                    continue
                if not _msg_has_ok(msg, mods["has"]):
                    continue
                body = msg.get("text") or ""
                if text_l and text_l not in body.lower():
                    continue
                matches.append(
                    {
                        "type": "message",
                        "ts": msg.get("ts", ""),
                        "user": uid,
                        "username": msg.get("user_name") or "",
                        "text": body,
                        "channel": {"id": ch.id, "name": ch.name},
                    }
                )
        matches.sort(key=lambda m: float(m["ts"] or 0), reverse=True)
        count = min(max(count, 1), _MAX_LIMIT)
        sliced = matches[:count]
        return _ok(
            query=query,
            messages={"total": len(matches), "matches": sliced},
        )
