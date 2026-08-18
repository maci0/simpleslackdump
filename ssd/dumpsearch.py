"""Offline Slack search query parsing and message/file/channel filters.

Leaf module used by ``ssd.dumpapi.DumpClient``. No filesystem I/O beyond
what callers pass in; no network.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol

from ssd.parser import ts_key

__all__ = [
    "MSG_IS",
    "WORD_RE",
    "channel_flag_is_ok",
    "channel_in_scope",
    "channel_with_ok",
    "compile_time",
    "expand_me",
    "file_has_ok",
    "is_remote_file",
    "msg_from_ok",
    "msg_has_ok",
    "msg_is_ok",
    "msg_time_ok",
    "msg_to_ok",
    "msg_ts_key",
    "norm_from",
    "parse_bound",
    "parse_search",
    "split_negation",
]


class ChannelScope(Protocol):
    """Minimal channel shape needed by ``in:`` / ``with:`` / ``is:`` filters."""

    id: str
    name: str
    kinds: frozenset[str]


class LoadedScope(Protocol):
    """Minimal loaded-channel shape for thread-aware ``is:`` filters."""

    thread_root: dict[str, str]


_SEARCH_MOD_RE = re.compile(
    r'(?i)\b(from|in|has|before|after|to|with|is|around|on|during):(?:"([^"]+)"|(\S+))'
)


MSG_IS = frozenset(
    {
        "thread",
        "threads",
        "bot",
        "starred",
        "saved",
        "edited",
        "unthreaded",
        "unthread",
        "broadcast",
        "thread_broadcast",
        "locked",
        "tombstone",
        "deleted",
        "app",
        "file_share",
        "fileshare",
        "me",
        "hidden",
        "join",
        "channel_join",
        "leave",
        "channel_leave",
        "topic",
        "channel_topic",
        "purpose",
        "channel_purpose",
        "parent",
        "archive",
        "channel_archive",
        "unarchive",
        "channel_unarchive",
        "rename",
        "channel_name",
        "subscribed",
        "pinned",
        "workflow",
        "workflows",
        "call",
        "calls",
        "huddle",
        "huddles",
        "ephemeral",
        "creator",
        "delayed",
        "scheduled",
        "sched",
        "guest",
        "restricted",
        "admin",
        "owner",
        "app_user",
        "appuser",
        "me_message",
        "memessage",
        "stranger",
        "invited",
        "invited_user",
        "primary_owner",
        "primaryowner",
        "ultra_restricted",
        "ultrarestricted",
        "canvas",
        "canvases",
        "forgotten",
        "enterprise",
        "moved",
        "connector",
        "workflow_bot",
        "workflowbot",
    }
)


WORD_RE = re.compile(r"[a-z0-9_]+")


_MENTION_RE = re.compile(r"@\w|<@")


_IMAGE_FT = frozenset({"png", "jpg", "jpeg", "gif", "webp", "heic", "bmp", "svg"})


_VIDEO_FT = frozenset(
    {"mp4", "mov", "webm", "m4v", "mpeg", "mpg", "avi", "wmv", "flv", "mkv", "3gp"}
)


_AUDIO_FT = frozenset({"mp3", "m4a", "wav", "ogg", "flac", "aac", "wma", "aiff", "opus"})


_SHEET_FT = frozenset({"xlsx", "xls", "csv", "gsheet", "ods", "numbers"})


_ZIP_FT = frozenset({"zip", "tar", "gz", "tgz", "rar", "7z", "gzip"})


_SLIDE_FT = frozenset({"ppt", "pptx", "key", "gslide", "odp"})


_LIST_FT = frozenset({"list", "slack_list"})


_DOC_FT = frozenset({"doc", "docx", "gdoc", "odt", "rtf"})


_TXT_FT = frozenset({"text", "txt", "plain"})


def _parse_ymd(raw: str) -> int | None:
    try:
        return int(datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).timestamp() * 1_000_000)
    except ValueError:
        return None


def parse_bound(tok: str) -> int | None:
    raw = tok.strip()
    try:
        return ts_key(raw)
    except ValueError:
        pass
    d = _parse_ymd(raw)
    if d is not None:
        return d
    day = _day_start(raw)
    return int(day * 1_000_000) if day is not None else None


def _day_start(raw: str) -> float | None:
    key = raw.strip().lower()
    today = datetime.now(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if key == "today":
        return today.timestamp()
    if key == "yesterday":
        return (today - timedelta(days=1)).timestamp()
    if key in {"week", "thisweek"}:
        return (today - timedelta(days=today.weekday())).timestamp()
    if key in {"month", "thismonth"}:
        return today.replace(day=1).timestamp()
    if key in {"year", "thisyear"}:
        return today.replace(month=1, day=1).timestamp()
    if key == "lastyear":
        return today.replace(year=today.year - 1, month=1, day=1).timestamp()
    if key == "lastweek":
        this_monday = today - timedelta(days=today.weekday())
        return (this_monday - timedelta(days=7)).timestamp()
    if key == "lastmonth":
        first = today.replace(day=1)
        if first.month == 1:
            prev = first.replace(year=first.year - 1, month=12)
        else:
            prev = first.replace(month=first.month - 1)
        return prev.timestamp()
    return None


def parse_search(query: str) -> tuple[str, dict[str, list[str]]]:
    mods: dict[str, list[str]] = {
        "from": [],
        "in": [],
        "has": [],
        "before": [],
        "after": [],
        "to": [],
        "with": [],
        "is": [],
        "around": [],
        "on": [],
        "during": [],
    }

    def repl(m: re.Match[str]) -> str:
        val = m.group(2) if m.group(2) is not None else m.group(3)
        mods[m.group(1).lower()].append(val)
        return " "

    text = " ".join(_SEARCH_MOD_RE.sub(repl, query).split())
    return text, mods


def split_negation(text: str) -> tuple[str, list[str]]:
    keep: list[str] = []
    drop: list[str] = []
    for tok in text.split():
        if tok.startswith("-") and len(tok) > 1:
            drop.append(tok[1:].lower())
        else:
            keep.append(tok)
    return " ".join(keep), drop


def expand_me(toks: list[str], auth: dict[str, Any]) -> list[str]:
    """Replace "me" tokens with the authenticated user's id and name.

    When auth carries no user info, "me" expands to the sentinel ``"\0"`` so
    callers can detect the unknown-user case (filter with ``x != "\0"``).
    """
    me = [x for x in (auth.get("user_id") or "", auth.get("user") or "") if x]
    out: list[str] = []
    for tok in toks:
        if norm_from(tok).lower() == "me":
            out.extend(me or ["\0"])
        else:
            out.append(tok)
    return out


_USEC = 1_000_000


def _around_window(tok: str) -> tuple[int, int] | None:
    raw = tok.strip()
    day = 86400 * _USEC
    start = _parse_ymd(raw)
    if start is not None:
        return start, start + day
    try:
        mid = ts_key(raw)
    except ValueError:
        return None
    return mid - day, mid + day


def _on_window(tok: str) -> tuple[int, int] | None:
    raw = tok.strip()
    day = 86400 * _USEC
    start = _parse_ymd(raw)
    if start is not None:
        return start, start + day
    try:
        mid = ts_key(raw)
    except ValueError:
        return None
    # Day bucket only needs second resolution; float is fine here.
    start_dt = datetime.fromtimestamp(mid / _USEC, UTC).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    start = int(start_dt.timestamp() * _USEC)
    return start, start + day


def _during_window(tok: str) -> tuple[int, int] | None:
    key = tok.strip().lower()
    start_f = _day_start(tok)
    if start_f is not None:
        start = int(start_f * _USEC)
        if key in {"week", "thisweek", "lastweek"}:
            return start, start + 7 * 86400 * _USEC
        if key in {"month", "thismonth", "lastmonth", "year", "thisyear", "lastyear"}:
            dt = datetime.fromtimestamp(start_f, UTC)
            if key in {"year", "thisyear", "lastyear"}:
                nxt = dt.replace(year=dt.year + 1)
            elif dt.month == 12:
                nxt = dt.replace(year=dt.year + 1, month=1)
            else:
                nxt = dt.replace(month=dt.month + 1)
            return start, int(nxt.timestamp() * _USEC)
        return start, start + 86400 * _USEC
    return _on_window(tok)


def norm_from(tok: str) -> str:
    return tok.strip().strip("<>").lstrip("@")


def channel_in_scope(ch: ChannelScope, in_toks: list[str]) -> bool:
    if not in_toks:
        return True
    names = {ch.id.lower(), ch.name.lower()}
    return any(t.strip().lstrip("#").lower() in names for t in in_toks)


def channel_with_ok(
    ch: ChannelScope,
    member_ids: list[str],
    with_toks: list[str],
    profiles: dict[str, dict[str, Any]],
    extra: dict[str, dict[str, Any]] | None = None,
) -> bool:
    if not with_toks:
        return True
    if not (ch.kinds & {"im", "mpim"}):
        return False
    extra = extra or {}
    names: set[str] = set()
    for uid in member_ids:
        names.add(uid.lower())
        profile = profiles.get(uid) or extra.get(uid) or {}
        names.add((profile.get("handle") or "").lower())
        names.add((profile.get("display_name") or "").lower())
        names.add((profile.get("id") or "").lower())
    names.discard("")
    return any(norm_from(tok).lower() in names for tok in with_toks)


def msg_from_ok(msg: dict[str, Any], from_toks: list[str], profile: dict[str, Any]) -> bool:
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
    return any(norm_from(tok).lower() in candidates for tok in from_toks)


def _file_attrs(f: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(f.get("filetype") or "").lower(),
        str(f.get("mimetype") or "").lower(),
        str(f.get("pretty_type") or "").lower(),
        str(f.get("name") or "").lower(),
    )


def _file_kind(f: dict[str, Any], prefix: str, types: frozenset[str]) -> bool:
    ft, mime, _, _ = _file_attrs(f)
    if mime.startswith(prefix):
        return True
    return ft in types


def _file_match(
    f: dict[str, Any],
    *,
    types: frozenset[str] | set[str] | None = None,
    mime_exact: frozenset[str] | set[str] | None = None,
    mime_contains: tuple[str, ...] = (),
    mime_endswith: tuple[str, ...] = (),
    suffixes: tuple[str, ...] = (),
) -> bool:
    ft, mime, pretty, name = _file_attrs(f)
    if types is not None and (ft in types or pretty in types):
        return True
    if mime_exact is not None and mime in mime_exact:
        return True
    if mime_contains and any(part in mime for part in mime_contains):
        return True
    if mime_endswith and any(mime.endswith(suffix) for suffix in mime_endswith):
        return True
    return bool(suffixes and name.endswith(suffixes))


def _matcher(**kwargs: Any) -> Callable[[dict[str, Any]], bool]:
    return lambda f: _file_match(f, **kwargs)


def _file_is_snippet(f: dict[str, Any]) -> bool:
    ft, _, pretty, _ = _file_attrs(f)
    mode = str(f.get("mode") or "").lower()
    return mode == "snippet" or ft == "snippet" or pretty == "snippet"


def _file_is_post(f: dict[str, Any]) -> bool:
    ft, _, pretty, _ = _file_attrs(f)
    return ft in {"space", "post"} or pretty in {"space", "post"}


def _file_is_email(f: dict[str, Any]) -> bool:
    ft, mime, pretty, _ = _file_attrs(f)
    return (
        ft in {"email", "eml", "msg"}
        or pretty in {"email", "eml"}
        or mime in {"message/rfc822", "application/vnd.ms-outlook"}
    )


def _file_is_canvas(f: dict[str, Any]) -> bool:
    return _file_attrs(f)[0] == "canvas"


def _msg_has_button(msg: dict[str, Any]) -> bool:
    for block in msg.get("blocks") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "button":
            return True
        for el in block.get("elements") or []:
            if isinstance(el, dict) and el.get("type") == "button":
                return True
    return False


def _msg_is_workflow(msg: dict[str, Any]) -> bool:
    bp = msg.get("bot_profile")
    if isinstance(bp, dict) and bp.get("is_workflow_bot"):
        return True
    meta = msg.get("metadata")
    if isinstance(meta, dict) and "workflow" in str(meta.get("event_type") or "").lower():
        return True
    return str(msg.get("subtype") or "").startswith("workflow")


def _msg_has_call(msg: dict[str, Any]) -> bool:
    return isinstance(msg.get("room"), dict) or isinstance(msg.get("call"), dict)


def is_remote_file(f: dict[str, Any]) -> bool:
    return bool(f.get("is_external") or f.get("is_remote") or f.get("mode") == "remote")


# Shared has: file-kind predicates. Alias keys must stay in sync for messages and files.
_FILE_HAS_SPECS: tuple[tuple[frozenset[str], Callable[[dict[str, Any]], bool]], ...] = (
    (frozenset({"image", "images"}), lambda f: _file_kind(f, "image/", _IMAGE_FT)),
    (frozenset({"video", "videos"}), lambda f: _file_kind(f, "video/", _VIDEO_FT)),
    (frozenset({"audio"}), lambda f: _file_kind(f, "audio/", _AUDIO_FT)),
    (frozenset({"canvas", "canvases"}), _file_is_canvas),
    (frozenset({"snippet", "snippets", "code"}), _file_is_snippet),
    (frozenset({"space", "spaces", "post", "posts"}), _file_is_post),
    (frozenset({"email", "emails", "eml"}), _file_is_email),
    (
        frozenset({"pdf"}),
        _matcher(types={"pdf"}, mime_exact={"application/pdf"}, suffixes=(".pdf",)),
    ),
    (
        frozenset({"spreadsheet", "spreadsheets", "sheet", "sheets", "excel"}),
        _matcher(
            types=_SHEET_FT,
            mime_contains=("spreadsheet", "excel"),
            suffixes=(".xlsx", ".xls", ".csv", ".ods", ".numbers"),
        ),
    ),
    (frozenset({"remote", "external"}), is_remote_file),
    (
        frozenset({"zip", "archive", "archives"}),
        _matcher(
            types=_ZIP_FT,
            mime_contains=("zip", "tar", "gzip", "x-rar", "x-7z", "x-gtar"),
            suffixes=(".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"),
        ),
    ),
    (
        frozenset({"presentation", "presentations", "slides", "pptx", "ppt"}),
        _matcher(
            types=_SLIDE_FT,
            mime_contains=("powerpoint", "presentation", "keynote"),
            suffixes=(".ppt", ".pptx", ".key", ".odp"),
        ),
    ),
    (frozenset({"list", "lists"}), _matcher(types=_LIST_FT)),
    (
        frozenset({"doc", "docs", "document", "documents"}),
        _matcher(
            types=_DOC_FT,
            mime_contains=("wordprocessing", "msword"),
            suffixes=(".doc", ".docx", ".odt", ".rtf"),
        ),
    ),
    (
        frozenset({"txt", "text", "plaintext"}),
        _matcher(types=_TXT_FT, mime_exact={"text/plain", "text/txt"}, suffixes=(".txt",)),
    ),
    (
        frozenset({"gif", "gifs"}),
        _matcher(types={"gif"}, mime_endswith=("/gif",), suffixes=(".gif",)),
    ),
    (frozenset({"json"}), _matcher(types={"json"}, mime_endswith=("/json",), suffixes=(".json",))),
    (frozenset({"csv"}), _matcher(types={"csv"}, mime_contains=("csv",), suffixes=(".csv",))),
    (frozenset({"xml"}), _matcher(types={"xml"}, mime_contains=("xml",), suffixes=(".xml",))),
    (
        frozenset({"md", "markdown"}),
        _matcher(
            types={"md", "markdown"},
            mime_contains=("markdown",),
            suffixes=(".md", ".markdown"),
        ),
    ),
    (
        frozenset({"yaml", "yml"}),
        _matcher(
            types={"yaml", "yml"},
            mime_contains=("yaml",),
            suffixes=(".yaml", ".yml"),
        ),
    ),
    (frozenset({"toml"}), _matcher(types={"toml"}, mime_contains=("toml",), suffixes=(".toml",))),
    (
        frozenset({"html", "htm"}),
        _matcher(
            types={"html", "htm"},
            mime_contains=("html",),
            suffixes=(".html", ".htm"),
        ),
    ),
    (frozenset({"svg"}), _matcher(types={"svg"}, mime_contains=("svg",), suffixes=(".svg",))),
    (
        frozenset({"python", "py"}),
        _matcher(types={"python", "py"}, mime_contains=("python",), suffixes=(".py",)),
    ),
    (
        frozenset({"js", "javascript"}),
        _matcher(
            types={"javascript", "js"},
            mime_contains=("javascript",),
            suffixes=(".js", ".mjs", ".cjs"),
        ),
    ),
    (
        frozenset({"ts", "typescript"}),
        _matcher(
            types={"typescript", "ts"},
            mime_contains=("typescript",),
            suffixes=(".ts", ".tsx", ".mts", ".cts"),
        ),
    ),
    (frozenset({"go", "golang"}), _matcher(types={"go", "golang"}, suffixes=(".go",))),
    (frozenset({"rust", "rs"}), _matcher(types={"rust", "rs"}, suffixes=(".rs",))),
    (frozenset({"sql"}), _matcher(types={"sql"}, suffixes=(".sql",))),
    (frozenset({"css"}), _matcher(types={"css"}, suffixes=(".css",))),
    (
        frozenset({"sh", "shell", "bash"}),
        _matcher(types={"shell", "sh", "bash", "zsh"}, suffixes=(".sh", ".bash", ".zsh")),
    ),
)


_FILE_HAS_PREDICATE: dict[str, Callable[[dict[str, Any]], bool]] = {
    alias: pred for aliases, pred in _FILE_HAS_SPECS for alias in aliases
}


def file_has_ok(f: dict[str, Any], has_toks: list[str]) -> bool:
    for tok in has_toks:
        kind = tok.lower()
        if kind in {"file", "files"}:
            continue
        pred = _FILE_HAS_PREDICATE.get(kind)
        if pred is None:
            return False
        if not pred(f):
            return False
    return True


def msg_has_ok(msg: dict[str, Any], has_toks: list[str]) -> bool:
    for tok in has_toks:
        kind = tok.lower()
        pred = _FILE_HAS_PREDICATE.get(kind)
        if pred is not None:
            if not any(pred(f) for f in msg.get("files") or []):
                return False
            continue
        if kind in {"file", "files"}:
            if not msg.get("files"):
                return False
        elif kind in {"reaction", "reactions", "emoji"}:
            if not msg.get("reactions"):
                return False
        elif kind in {"pin", "pinned"}:
            if not (msg.get("pinned_to") or msg.get("pinned_info")):
                return False
        elif kind in {"link", "links", "url"}:
            body = (msg.get("text") or "").lower()
            if "http://" not in body and "https://" not in body:
                return False
        elif kind in {"attachment", "attachments"}:
            if not msg.get("attachments"):
                return False
        elif kind in {"mention", "mentions"}:
            text = msg.get("text") or ""
            if not _MENTION_RE.search(text):
                return False
        elif kind in {"block", "blocks"}:
            if not msg.get("blocks"):
                return False
        elif kind in {"call", "calls", "huddle", "huddles"}:
            if not _msg_has_call(msg):
                return False
        elif kind in {"x_file", "x_files"}:
            if not msg.get("x_files"):
                return False
        elif kind in {"reply", "replies"}:
            if not (msg.get("thread") or msg.get("reply_count")):
                return False
        elif kind in {"metadata", "meta"}:
            meta = msg.get("metadata")
            if not (isinstance(meta, dict) and meta):
                return False
        elif kind in {"button", "buttons"}:
            if not _msg_has_button(msg):
                return False
        elif kind in {"workflow", "workflows"}:
            if not _msg_is_workflow(msg):
                return False
        elif kind.startswith(":") and kind.endswith(":") and len(kind) > 2:
            name = kind.strip(":")
            names = {str(rx.get("name") or "") for rx in msg.get("reactions") or []}
            if name not in names:
                return False
        else:
            return False
    return True


def msg_to_ok(msg: dict[str, Any], to_toks: list[str]) -> bool:
    if not to_toks:
        return True
    text = (msg.get("text") or "").lower()
    # text_raw preserves original <@U123> mention syntax; text has resolved display names.
    # Check both so to:userid and to:displayname both work.
    text_raw = (msg.get("text_raw") or "").lower()
    for tok in to_toks:
        want = norm_from(tok).lower()
        if not want:
            continue
        if f"@{want}" in text or f"<@{want}>" in text:
            return True
        if text_raw and f"<@{want}>" in text_raw:
            return True
    return False


# Channel ``is:`` tokens that map to a single truthy field on the channel object.
_CHANNEL_IS_FIELD: dict[str, str] = {
    "private": "is_private",
    "ext_shared": "is_ext_shared",
    "extshared": "is_ext_shared",
    "org_shared": "is_org_shared",
    "orgshared": "is_org_shared",
    "archived": "is_archived",
    "frozen": "is_frozen",
    "open": "is_open",
    "org_default": "is_org_default",
    "orgdefault": "is_org_default",
    "global_shared": "is_global_shared",
    "globalshared": "is_global_shared",
    "org_mandatory": "is_org_mandatory",
    "orgmandatory": "is_org_mandatory",
    "member": "is_member",
    "pending_ext_shared": "is_pending_ext_shared",
    "pendingextshared": "is_pending_ext_shared",
    "read_only": "is_read_only",
    "readonly": "is_read_only",
    "thread_only": "is_thread_only",
    "threadonly": "is_thread_only",
    "non_threadable": "is_non_threadable",
    "nonthreadable": "is_non_threadable",
    "user_deleted": "is_user_deleted",
    "userdeleted": "is_user_deleted",
    "muted": "is_muted",
    "mute": "is_muted",
    "pending_shared": "is_pending_shared",
    "pendingshared": "is_pending_shared",
    "has_canvas": "has_canvas",
    "hascanvas": "has_canvas",
    "im_blocked": "is_im_blocked",
    "imblocked": "is_im_blocked",
    "unlinked": "unlinked",
    "host": "conversation_host_id",
}

# Channel ``is:`` tokens that map to a non-empty list/collection field.
_CHANNEL_IS_NONEMPTY: dict[str, str] = {
    "connected": "connected_team_ids",
    "internal": "internal_team_ids",
    "connected_limited": "connected_limited_team_ids",
    "connectedlimited": "connected_limited_team_ids",
}


def channel_flag_is_ok(kind: str, ch: ChannelScope, ch_obj: dict[str, Any]) -> bool | None:
    """Shared channel-scoped ``is:`` checks used by message and channel filters.

    Returns None when ``kind`` is not handled here (caller keeps its own cases).
    Intentionally omits dm/im, mpim, channel, group, public, and unreads: those
    differ between message-level and channel-level matching.
    """
    field = _CHANNEL_IS_FIELD.get(kind)
    if field is not None:
        return bool(ch_obj.get(field))
    nonempty = _CHANNEL_IS_NONEMPTY.get(kind)
    if nonempty is not None:
        return bool(ch_obj.get(nonempty) or [])
    if kind == "shared":
        return bool(
            ch_obj.get("is_shared") or ch_obj.get("is_ext_shared") or ch_obj.get("is_org_shared")
        )
    if kind in {"general", "random"}:
        name = str(ch.name or ch_obj.get("name") or "").lower()
        if kind == "general":
            return name == "general" or bool(ch_obj.get("is_general"))
        return name == kind
    return None


# Simple ``is:`` tokens: truthy profile field, exact subtype, or truthy message field.
_MSG_IS_PROFILE: dict[str, str] = {
    "admin": "is_admin",
    "owner": "is_owner",
    "app_user": "is_app_user",
    "appuser": "is_app_user",
    "stranger": "is_stranger",
    "invited": "is_invited_user",
    "invited_user": "is_invited_user",
    "primary_owner": "is_primary_owner",
    "primaryowner": "is_primary_owner",
    "ultra_restricted": "is_ultra_restricted",
    "ultrarestricted": "is_ultra_restricted",
    "forgotten": "is_forgotten",
    "connector": "is_connector",
    "workflow_bot": "is_workflow_bot",
    "workflowbot": "is_workflow_bot",
    "enterprise": "enterprise_user",
}

_MSG_IS_SUBTYPE: dict[str, str] = {
    "me_message": "me_message",
    "memessage": "me_message",
    "file_share": "file_share",
    "fileshare": "file_share",
    "join": "channel_join",
    "channel_join": "channel_join",
    "leave": "channel_leave",
    "channel_leave": "channel_leave",
    "topic": "channel_topic",
    "channel_topic": "channel_topic",
    "purpose": "channel_purpose",
    "channel_purpose": "channel_purpose",
    "archive": "channel_archive",
    "channel_archive": "channel_archive",
    "unarchive": "channel_unarchive",
    "channel_unarchive": "channel_unarchive",
    "rename": "channel_name",
    "channel_name": "channel_name",
}

_MSG_IS_TRUTHY: dict[str, str] = {
    "delayed": "is_delayed_message",
    "edited": "edited",
    "locked": "is_locked",
    "app": "app_id",
    "hidden": "hidden",
    "moved": "is_moved",
    "subscribed": "subscribed",
    "ephemeral": "is_ephemeral",
}


def msg_is_ok(
    msg: dict[str, Any],
    is_toks: list[str],
    ch: ChannelScope,
    loaded: LoadedScope,
    ch_obj: dict[str, Any],
    starred: set[tuple[str, str]],
    me: set[str],
    profile: dict[str, Any] | None = None,
) -> bool:
    """Return True when ``msg`` satisfies all ``is:`` tokens.

    ``starred`` is a set of ``(channel_id, ts)`` pairs from ``stars.json``; used
    for ``is:starred`` / ``is:saved``. ``me`` is the lowercased set of the authed
    user's id and handle; used for ``is:me``. ``profile`` is the sender's stored
    profile dict (may be None if unknown). Returns True immediately when
    ``is_toks`` is empty.
    """
    if not is_toks:
        return True
    ts = str(msg.get("ts") or "")
    thread_ts = str(msg.get("thread_ts") or "")
    root = loaded.thread_root.get(ts, "")
    in_thread = bool(
        (msg.get("thread") or [])
        or (msg.get("reply_count") or 0)
        or (thread_ts and ts and thread_ts != ts)
        or (root and root != ts)
    )
    profile_row = profile or {}
    for tok in is_toks:
        kind = tok.lower()
        flag = channel_flag_is_ok(kind, ch, ch_obj)
        if flag is not None:
            if not flag:
                return False
            continue
        profile_field = _MSG_IS_PROFILE.get(kind)
        if profile_field is not None:
            if not profile_row.get(profile_field):
                return False
            continue
        subtype = _MSG_IS_SUBTYPE.get(kind)
        if subtype is not None:
            if msg.get("subtype") != subtype:
                return False
            continue
        truthy_field = _MSG_IS_TRUTHY.get(kind)
        if truthy_field is not None:
            if not msg.get(truthy_field):
                return False
            continue
        if kind in {"thread", "threads"}:
            if not in_thread:
                return False
        elif kind in {"dm", "im"}:
            if "im" not in ch.kinds:
                return False
        elif kind in {"mpim"}:
            if "mpim" not in ch.kinds:
                return False
        elif kind in {"channel", "channels"}:
            if not ch_obj.get("is_channel"):
                return False
        elif kind in {"group", "groups"}:
            if not ch_obj.get("is_group"):
                return False
        elif kind == "public":
            if ch_obj.get("is_private") or not ch_obj.get("is_channel"):
                return False
        elif kind in {"unreads", "unread"}:
            n = int(ch_obj.get("unread_count") or 0)
            d = int(ch_obj.get("unread_count_display") or 0)
            if n <= 0 and d <= 0:
                return False
            last = ch_obj.get("last_read")
            if last and msg_ts_key(ts) <= msg_ts_key(last):
                return False
        elif kind == "creator":
            creator = str(ch_obj.get("creator") or "")
            if not creator or str(msg.get("user") or "") != creator:
                return False
        elif kind in {"scheduled", "sched"}:
            if not msg.get("scheduled_message_id"):
                return False
        elif kind in {"guest", "restricted"}:
            if not (profile_row.get("is_restricted") or profile_row.get("is_ultra_restricted")):
                return False
        elif kind in {"canvas", "canvases"}:
            files = msg.get("files") or []
            if msg.get("subtype") != "canvas_share" and not any(_file_is_canvas(f) for f in files):
                return False
        elif kind == "bot":
            if not (msg.get("bot_id") or msg.get("subtype") == "bot_message"):
                return False
        elif kind in {"starred", "saved"}:
            if (ch.id, ts) not in starred:
                return False
        elif kind in {"unthreaded", "unthread"}:
            if in_thread:
                return False
        elif kind in {"broadcast", "thread_broadcast"}:
            if not (msg.get("subtype") == "thread_broadcast" or msg.get("reply_broadcast")):
                return False
        elif kind in {"tombstone", "deleted"}:
            if msg.get("subtype") != "tombstone" and not msg.get("hidden"):
                return False
        elif kind == "me":
            uid = (msg.get("user") or "").lower()
            handle = (msg.get("user_name") or "").lower()
            if not me or (uid not in me and handle not in me):
                return False
        elif kind == "parent":
            is_reply = bool((thread_ts and ts and thread_ts != ts) or (root and root != ts))
            has_kids = bool(msg.get("thread") or msg.get("reply_count"))
            if is_reply or not has_kids:
                return False
        elif kind == "pinned":
            if not (msg.get("pinned_to") or msg.get("pinned_info")):
                return False
        elif kind in {"workflow", "workflows"}:
            if not _msg_is_workflow(msg):
                return False
        elif kind in {"call", "calls", "huddle", "huddles"}:
            if not _msg_has_call(msg):
                return False
        else:
            return False
    return True


def compile_time(mods: dict[str, list[str]]) -> dict[str, list[Any]]:
    return {
        "after": [parse_bound(t) for t in mods["after"]],
        "before": [parse_bound(t) for t in mods["before"]],
        "around": [_around_window(t) for t in mods.get("around") or []],
        "on": [_on_window(t) for t in mods.get("on") or []],
        "during": [_during_window(t) for t in mods.get("during") or []],
    }


def msg_ts_key(ts: Any) -> int:
    """Like ``ts_key`` but returns 0 for None, empty, or unparseable values."""
    if ts is None or ts == "":
        return 0
    try:
        return ts_key(str(ts))
    except (TypeError, ValueError):
        return 0


def _bound_key(bound: float | int | str) -> int:
    if isinstance(bound, int):
        return bound
    try:
        return ts_key(str(bound))
    except (TypeError, ValueError):
        return int(float(bound) * _USEC)


def msg_time_ok(msg: dict[str, Any], bounds: dict[str, list[Any]]) -> bool:
    timed = any(bounds[k] for k in bounds)
    ts_raw = msg.get("ts")
    if ts_raw is None or ts_raw == "":
        return not timed
    t = msg_ts_key(ts_raw)
    # Unparseable after:/before: tokens compile to None. Fail closed (same as
    # invalid on:/around:/during: windows) so junk modifiers never widen matches.
    for bound in bounds["after"]:
        if bound is None or t <= _bound_key(bound):
            return False
    for bound in bounds["before"]:
        if bound is None or t >= _bound_key(bound):
            return False
    # around/on/during windows of the same kind are alternatives (OR).
    # AND across exclusive day windows matches nothing (on:Mon on:Tue).
    for key in ("around", "on", "during"):
        windows = bounds[key]
        if not windows:
            continue
        if any(w is None for w in windows):
            return False
        if not any(_bound_key(lo) <= t < _bound_key(hi) for lo, hi in windows):
            return False
    return True
