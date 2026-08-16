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
sh, workflow, star.

``is:`` on messages: thread, bot, starred/saved, edited, unthreaded, broadcast,
locked, tombstone/deleted, app, file_share, me, hidden, join, leave, topic,
purpose, parent, archive, unarchive, rename, subscribed, pinned, workflow,
call/huddle, ephemeral, creator, delayed, scheduled, guest, admin, owner,
app_user, me_message, stranger, invited, primary_owner, ultra_restricted,
canvas, forgotten, enterprise, moved, connector, workflow_bot.

``is:`` on channels (unrelated channels skipped before parse): dm/im, mpim,
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
- without ``conversations.json``, a C-prefix id is both public and private
- dumped text already has ``<@U...>`` replaced with ``@display_name``
- standalone thread dumps may omit the parent message
- ``conversations.members`` prefers ``members.json``; else IM ``user`` plus
  auth user; else people who posted, replied, or reacted
- ``files.comments`` reads comments stored on dumped file objects
- presence dump is the authenticated user unless ``presence.json`` has more
"""

import inspect
import json
import os
import re
import threading
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

try:
    import orjson as _fastjson
except ImportError:
    _fastjson = None

_CHANNEL_DIR_RE = re.compile(r"^(.*)_([CDG][A-Za-z0-9]+)$")
_DATE_JSON_RE = re.compile(r"^\d{4}-\d{2}-\d{2}\.json$")
_ALL_TYPES = "public_channel,private_channel,mpim,im"
_MAX_LIMIT = 10_000  # ponytail: no Slack 999 cap; still bound so a typo cannot allocate forever
_LOAD_WORKERS = min(32, (os.cpu_count() or 4) * 4)
_SEARCH_MOD_RE = re.compile(
    r'(?i)\b(from|in|has|before|after|to|with|is|around|on|during):(?:"([^"]+)"|(\S+))'
)
_SKIP_COPY = frozenset({"thread"})
_MSG_IS = frozenset(
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
_LINK_MARKERS = ("http://", "https://")
_WORD_RE = re.compile(r"[a-z0-9_]+")
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


def _parse_bound(tok: str) -> float | None:
    raw = tok.strip()
    try:
        return float(raw)
    except ValueError:
        pass
    try:
        return datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
    except ValueError:
        pass
    return _day_start(raw)


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


def _parse_search(query: str) -> tuple[str, dict[str, list[str]]]:
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


def _split_negation(text: str) -> tuple[str, list[str]]:
    keep: list[str] = []
    drop: list[str] = []
    for tok in text.split():
        if tok.startswith("-") and len(tok) > 1:
            drop.append(tok[1:].lower())
        else:
            keep.append(tok)
    return " ".join(keep), drop


def _expand_me(toks: list[str], auth: dict[str, Any]) -> list[str]:
    me = [x for x in (auth.get("user_id") or "", auth.get("user") or "") if x]
    out: list[str] = []
    for tok in toks:
        if _norm_from(tok).lower() == "me":
            out.extend(me or ["\0"])
        else:
            out.append(tok)
    return out


def _around_window(tok: str) -> tuple[float, float] | None:
    raw = tok.strip()
    try:
        start = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
        return start, start + 86400.0
    except ValueError:
        pass
    try:
        mid = float(raw)
    except ValueError:
        return None
    return mid - 86400.0, mid + 86400.0


def _on_window(tok: str) -> tuple[float, float] | None:
    raw = tok.strip()
    try:
        start = datetime.strptime(raw, "%Y-%m-%d").replace(tzinfo=UTC).timestamp()
        return start, start + 86400.0
    except ValueError:
        pass
    try:
        mid = float(raw)
    except ValueError:
        return None
    day = datetime.fromtimestamp(mid, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    start = day.timestamp()
    return start, start + 86400.0


def _during_window(tok: str) -> tuple[float, float] | None:
    key = tok.strip().lower()
    start = _day_start(tok)
    if start is not None:
        if key in {"week", "thisweek", "lastweek"}:
            return start, start + 7 * 86400.0
        if key in {"month", "thismonth", "lastmonth", "year", "thisyear", "lastyear"}:
            dt = datetime.fromtimestamp(start, UTC)
            if key in {"year", "thisyear", "lastyear"}:
                nxt = dt.replace(year=dt.year + 1)
            elif dt.month == 12:
                nxt = dt.replace(year=dt.year + 1, month=1)
            else:
                nxt = dt.replace(month=dt.month + 1)
            return start, nxt.timestamp()
        return start, start + 86400.0
    return _on_window(tok)


def _norm_from(tok: str) -> str:
    return tok.strip().strip("<>").lstrip("@")


def _norm_in(tok: str) -> str:
    return tok.strip().lstrip("#")


def _channel_in_scope(ch: "_Channel", in_toks: list[str]) -> bool:
    if not in_toks:
        return True
    names = {ch.id.lower(), ch.name.lower()}
    return any(_norm_in(t).lower() in names for t in in_toks)


def _channel_with_ok(
    ch: "_Channel",
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
    return any(_norm_from(tok).lower() in names for tok in with_toks)


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
        elif kind in {"link", "links", "url"}:
            body = (msg.get("text") or "").lower()
            if not any(m in body for m in _LINK_MARKERS):
                return False
        elif kind in {"canvas", "canvases"}:
            files = msg.get("files") or []
            if not any(str(f.get("filetype") or "").lower() == "canvas" for f in files):
                return False
        elif kind in {"image", "images"}:
            if not any(_file_is_image(f) for f in msg.get("files") or []):
                return False
        elif kind in {"video", "videos"}:
            if not any(_file_is_video(f) for f in msg.get("files") or []):
                return False
        elif kind in {"audio"}:
            if not any(_file_is_audio(f) for f in msg.get("files") or []):
                return False
        elif kind in {"snippet", "snippets", "code"}:
            if not any(_file_is_snippet(f) for f in msg.get("files") or []):
                return False
        elif kind in {"attachment", "attachments"}:
            if not msg.get("attachments"):
                return False
        elif kind in {"mention", "mentions"}:
            text = msg.get("text") or ""
            if not _MENTION_RE.search(text):
                return False
        elif kind in {"space", "spaces", "post", "posts"}:
            if not any(_file_is_post(f) for f in msg.get("files") or []):
                return False
        elif kind in {"block", "blocks"}:
            if not msg.get("blocks"):
                return False
        elif kind in {"email", "emails", "eml"}:
            if not any(_file_is_email(f) for f in msg.get("files") or []):
                return False
        elif kind in {"call", "calls", "huddle", "huddles"}:
            if not _msg_has_call(msg):
                return False
        elif kind in {"x_file", "x_files"}:
            if not msg.get("x_files"):
                return False
        elif kind == "pdf":
            if not any(_file_is_pdf(f) for f in msg.get("files") or []):
                return False
        elif kind in {"reply", "replies"}:
            if not (msg.get("thread") or msg.get("reply_count")):
                return False
        elif kind in {"spreadsheet", "spreadsheets", "sheet", "sheets", "excel"}:
            if not any(_file_is_spreadsheet(f) for f in msg.get("files") or []):
                return False
        elif kind in {"metadata", "meta"}:
            meta = msg.get("metadata")
            if not (isinstance(meta, dict) and meta):
                return False
        elif kind in {"remote", "external"}:
            if not any(_is_remote_file(f) for f in msg.get("files") or []):
                return False
        elif kind in {"zip", "archive", "archives"}:
            if not any(_file_is_zip(f) for f in msg.get("files") or []):
                return False
        elif kind in {"presentation", "presentations", "slides", "pptx", "ppt"}:
            if not any(_file_is_presentation(f) for f in msg.get("files") or []):
                return False
        elif kind in {"list", "lists"}:
            if not any(_file_is_list(f) for f in msg.get("files") or []):
                return False
        elif kind in {"doc", "docs", "document", "documents"}:
            if not any(_file_is_doc(f) for f in msg.get("files") or []):
                return False
        elif kind in {"txt", "text", "plaintext"}:
            if not any(_file_is_txt(f) for f in msg.get("files") or []):
                return False
        elif kind in {"button", "buttons"}:
            if not _msg_has_button(msg):
                return False
        elif kind in {"gif", "gifs"}:
            if not any(_file_is_gif(f) for f in msg.get("files") or []):
                return False
        elif kind == "json":
            if not any(_file_is_json(f) for f in msg.get("files") or []):
                return False
        elif kind == "csv":
            if not any(_file_is_csv(f) for f in msg.get("files") or []):
                return False
        elif kind == "xml":
            if not any(_file_is_xml(f) for f in msg.get("files") or []):
                return False
        elif kind in {"md", "markdown"}:
            if not any(_file_is_md(f) for f in msg.get("files") or []):
                return False
        elif kind in {"yaml", "yml"}:
            if not any(_file_is_yaml(f) for f in msg.get("files") or []):
                return False
        elif kind == "toml":
            if not any(_file_is_toml(f) for f in msg.get("files") or []):
                return False
        elif kind in {"html", "htm"}:
            if not any(_file_is_html(f) for f in msg.get("files") or []):
                return False
        elif kind == "svg":
            if not any(_file_is_svg(f) for f in msg.get("files") or []):
                return False
        elif kind in {"python", "py"}:
            if not any(_file_is_python(f) for f in msg.get("files") or []):
                return False
        elif kind in {"js", "javascript"}:
            if not any(_file_is_js(f) for f in msg.get("files") or []):
                return False
        elif kind in {"ts", "typescript"}:
            if not any(_file_is_ts(f) for f in msg.get("files") or []):
                return False
        elif kind in {"go", "golang"}:
            if not any(_file_is_go(f) for f in msg.get("files") or []):
                return False
        elif kind in {"rust", "rs"}:
            if not any(_file_is_rust(f) for f in msg.get("files") or []):
                return False
        elif kind == "sql":
            if not any(_file_is_sql(f) for f in msg.get("files") or []):
                return False
        elif kind == "css":
            if not any(_file_is_css(f) for f in msg.get("files") or []):
                return False
        elif kind in {"sh", "shell", "bash"}:
            if not any(_file_is_sh(f) for f in msg.get("files") or []):
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


def _file_kind(f: dict[str, Any], prefix: str, types: frozenset[str]) -> bool:
    mime = str(f.get("mimetype") or "").lower()
    if mime.startswith(prefix):
        return True
    return str(f.get("filetype") or "").lower() in types


def _file_is_image(f: dict[str, Any]) -> bool:
    return _file_kind(f, "image/", _IMAGE_FT)


def _file_is_video(f: dict[str, Any]) -> bool:
    return _file_kind(f, "video/", _VIDEO_FT)


def _file_is_audio(f: dict[str, Any]) -> bool:
    return _file_kind(f, "audio/", _AUDIO_FT)


def _file_is_snippet(f: dict[str, Any]) -> bool:
    mode = str(f.get("mode") or "").lower()
    ft = str(f.get("filetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    return mode == "snippet" or ft == "snippet" or pretty == "snippet"


def _file_is_post(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    return ft in {"space", "post"} or pretty in {"space", "post"}


def _file_is_email(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    return (
        ft in {"email", "eml", "msg"}
        or pretty in {"email", "eml"}
        or mime in {"message/rfc822", "application/vnd.ms-outlook"}
    )


def _file_is_pdf(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft == "pdf" or pretty == "pdf" or mime == "application/pdf" or name.endswith(".pdf")


def _file_is_spreadsheet(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    if ft in _SHEET_FT or pretty in _SHEET_FT:
        return True
    if "spreadsheet" in mime or "excel" in mime:
        return True
    return name.endswith((".xlsx", ".xls", ".csv", ".ods", ".numbers"))


def _file_is_zip(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    if ft in _ZIP_FT or pretty in _ZIP_FT:
        return True
    if any(part in mime for part in ("zip", "tar", "gzip", "x-rar", "x-7z", "x-gtar")):
        return True
    return name.endswith((".zip", ".tar", ".gz", ".tgz", ".rar", ".7z"))


def _file_is_presentation(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    if ft in _SLIDE_FT or pretty in _SLIDE_FT:
        return True
    if any(part in mime for part in ("powerpoint", "presentation", "keynote")):
        return True
    return name.endswith((".ppt", ".pptx", ".key", ".odp"))


def _file_is_list(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    return ft in _LIST_FT or pretty in _LIST_FT


def _file_is_doc(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    if ft in _DOC_FT or pretty in _DOC_FT:
        return True
    if "wordprocessing" in mime or "msword" in mime:
        return True
    return name.endswith((".doc", ".docx", ".odt", ".rtf"))


def _file_is_txt(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    if ft in _TXT_FT or pretty in _TXT_FT:
        return True
    if mime in {"text/plain", "text/txt"}:
        return True
    return name.endswith(".txt")


def _file_is_gif(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft == "gif" or pretty == "gif" or mime.endswith("/gif") or name.endswith(".gif")


def _file_is_json(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft == "json" or pretty == "json" or mime.endswith("/json") or name.endswith(".json")


def _file_is_csv(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft == "csv" or pretty == "csv" or "csv" in mime or name.endswith(".csv")


def _file_is_xml(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft == "xml" or pretty == "xml" or "xml" in mime or name.endswith(".xml")


def _file_is_md(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return (
        ft in {"md", "markdown"}
        or pretty in {"md", "markdown"}
        or "markdown" in mime
        or name.endswith((".md", ".markdown"))
    )


def _file_is_yaml(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return (
        ft in {"yaml", "yml"}
        or pretty in {"yaml", "yml"}
        or "yaml" in mime
        or name.endswith((".yaml", ".yml"))
    )


def _file_is_toml(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return (
        ft == "toml"
        or pretty == "toml"
        or "toml" in mime
        or name.endswith(".toml")
    )


def _file_is_html(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return (
        ft in {"html", "htm"}
        or pretty in {"html", "htm"}
        or "html" in mime
        or name.endswith((".html", ".htm"))
    )


def _file_is_svg(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft == "svg" or pretty == "svg" or "svg" in mime or name.endswith(".svg")


def _file_is_python(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return (
        ft in {"python", "py"}
        or pretty in {"python", "py"}
        or "python" in mime
        or name.endswith(".py")
    )


def _file_is_js(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return (
        ft in {"javascript", "js"}
        or pretty in {"javascript", "js"}
        or "javascript" in mime
        or name.endswith(".js")
        or name.endswith(".mjs")
        or name.endswith(".cjs")
    )


def _file_is_ts(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    mime = str(f.get("mimetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return (
        ft in {"typescript", "ts"}
        or pretty in {"typescript", "ts"}
        or "typescript" in mime
        or name.endswith(".ts")
        or name.endswith(".tsx")
        or name.endswith(".mts")
        or name.endswith(".cts")
    )


def _file_is_go(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft in {"go", "golang"} or pretty in {"go", "golang"} or name.endswith(".go")


def _file_is_rust(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft in {"rust", "rs"} or pretty in {"rust", "rs"} or name.endswith(".rs")


def _file_is_sql(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft == "sql" or pretty == "sql" or name.endswith(".sql")


def _file_is_css(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return ft == "css" or pretty == "css" or name.endswith(".css")


def _file_is_sh(f: dict[str, Any]) -> bool:
    ft = str(f.get("filetype") or "").lower()
    pretty = str(f.get("pretty_type") or "").lower()
    name = str(f.get("name") or "").lower()
    return (
        ft in {"shell", "sh", "bash", "zsh"}
        or pretty in {"shell", "sh", "bash", "zsh"}
        or name.endswith(".sh")
        or name.endswith(".bash")
        or name.endswith(".zsh")
    )


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


def _file_has_ok(f: dict[str, Any], has_toks: list[str]) -> bool:
    for tok in has_toks:
        kind = tok.lower()
        if kind in {"file", "files"}:
            continue
        if kind in {"image", "images"}:
            if not _file_is_image(f):
                return False
        elif kind in {"video", "videos"}:
            if not _file_is_video(f):
                return False
        elif kind in {"audio"}:
            if not _file_is_audio(f):
                return False
        elif kind in {"canvas", "canvases"}:
            if str(f.get("filetype") or "").lower() != "canvas":
                return False
        elif kind in {"snippet", "snippets", "code"}:
            if not _file_is_snippet(f):
                return False
        elif kind in {"space", "spaces", "post", "posts"}:
            if not _file_is_post(f):
                return False
        elif kind in {"email", "emails", "eml"}:
            if not _file_is_email(f):
                return False
        elif kind == "pdf":
            if not _file_is_pdf(f):
                return False
        elif kind in {"spreadsheet", "spreadsheets", "sheet", "sheets", "excel"}:
            if not _file_is_spreadsheet(f):
                return False
        elif kind in {"remote", "external"}:
            if not _is_remote_file(f):
                return False
        elif kind in {"zip", "archive", "archives"}:
            if not _file_is_zip(f):
                return False
        elif kind in {"presentation", "presentations", "slides", "pptx", "ppt"}:
            if not _file_is_presentation(f):
                return False
        elif kind in {"list", "lists"}:
            if not _file_is_list(f):
                return False
        elif kind in {"doc", "docs", "document", "documents"}:
            if not _file_is_doc(f):
                return False
        elif kind in {"txt", "text", "plaintext"}:
            if not _file_is_txt(f):
                return False
        elif kind in {"gif", "gifs"}:
            if not _file_is_gif(f):
                return False
        elif kind == "json":
            if not _file_is_json(f):
                return False
        elif kind == "csv":
            if not _file_is_csv(f):
                return False
        elif kind == "xml":
            if not _file_is_xml(f):
                return False
        elif kind in {"md", "markdown"}:
            if not _file_is_md(f):
                return False
        elif kind in {"yaml", "yml"}:
            if not _file_is_yaml(f):
                return False
        elif kind == "toml":
            if not _file_is_toml(f):
                return False
        elif kind in {"html", "htm"}:
            if not _file_is_html(f):
                return False
        elif kind == "svg":
            if not _file_is_svg(f):
                return False
        elif kind in {"python", "py"}:
            if not _file_is_python(f):
                return False
        elif kind in {"js", "javascript"}:
            if not _file_is_js(f):
                return False
        elif kind in {"ts", "typescript"}:
            if not _file_is_ts(f):
                return False
        elif kind in {"go", "golang"}:
            if not _file_is_go(f):
                return False
        elif kind in {"rust", "rs"}:
            if not _file_is_rust(f):
                return False
        elif kind == "sql":
            if not _file_is_sql(f):
                return False
        elif kind == "css":
            if not _file_is_css(f):
                return False
        elif kind in {"sh", "shell", "bash"}:
            if not _file_is_sh(f):
                return False
        else:
            return False
    return True


def _msg_to_ok(msg: dict[str, Any], to_toks: list[str]) -> bool:
    if not to_toks:
        return True
    text = (msg.get("text") or "").lower()
    for tok in to_toks:
        want = _norm_from(tok).lower()
        if want and (f"@{want}" in text or f"<@{want}>" in text):
            return True
    return False


def _msg_is_ok(
    msg: dict[str, Any],
    is_toks: list[str],
    ch: "_Channel",
    loaded: "_Loaded",
    ch_obj: dict[str, Any],
    starred: set[tuple[str, str]],
    me: set[str],
    profile: dict[str, Any] | None = None,
) -> bool:
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
    for tok in is_toks:
        kind = tok.lower()
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
        elif kind == "private":
            if not ch_obj.get("is_private"):
                return False
        elif kind == "public":
            if ch_obj.get("is_private") or not ch_obj.get("is_channel"):
                return False
        elif kind == "shared":
            if not (
                ch_obj.get("is_shared")
                or ch_obj.get("is_ext_shared")
                or ch_obj.get("is_org_shared")
            ):
                return False
        elif kind in {"ext_shared", "extshared"}:
            if not ch_obj.get("is_ext_shared"):
                return False
        elif kind in {"org_shared", "orgshared"}:
            if not ch_obj.get("is_org_shared"):
                return False
        elif kind in {"general", "random"}:
            name = str(ch.name or ch_obj.get("name") or "").lower()
            if kind == "general":
                if name != "general" and not ch_obj.get("is_general"):
                    return False
            elif name != kind:
                return False
        elif kind == "archived":
            if not ch_obj.get("is_archived"):
                return False
        elif kind == "frozen":
            if not ch_obj.get("is_frozen"):
                return False
        elif kind == "open":
            if not ch_obj.get("is_open"):
                return False
        elif kind in {"org_default", "orgdefault"}:
            if not ch_obj.get("is_org_default"):
                return False
        elif kind in {"global_shared", "globalshared"}:
            if not ch_obj.get("is_global_shared"):
                return False
        elif kind in {"org_mandatory", "orgmandatory"}:
            if not ch_obj.get("is_org_mandatory"):
                return False
        elif kind == "member":
            if not ch_obj.get("is_member"):
                return False
        elif kind in {"pending_ext_shared", "pendingextshared"}:
            if not ch_obj.get("is_pending_ext_shared"):
                return False
        elif kind in {"read_only", "readonly"}:
            if not ch_obj.get("is_read_only"):
                return False
        elif kind in {"thread_only", "threadonly"}:
            if not ch_obj.get("is_thread_only"):
                return False
        elif kind in {"non_threadable", "nonthreadable"}:
            if not ch_obj.get("is_non_threadable"):
                return False
        elif kind in {"user_deleted", "userdeleted"}:
            if not ch_obj.get("is_user_deleted"):
                return False
        elif kind in {"muted", "mute"}:
            if not ch_obj.get("is_muted"):
                return False
        elif kind in {"unreads", "unread"}:
            n = int(ch_obj.get("unread_count") or 0)
            d = int(ch_obj.get("unread_count_display") or 0)
            if n <= 0 and d <= 0:
                return False
            last = ch_obj.get("last_read")
            if last:
                try:
                    if float(msg.get("ts") or 0) <= float(last):
                        return False
                except (TypeError, ValueError):
                    return False
        elif kind in {"pending_shared", "pendingshared"}:
            if not ch_obj.get("is_pending_shared"):
                return False
        elif kind in {"has_canvas", "hascanvas"}:
            if not ch_obj.get("has_canvas"):
                return False
        elif kind in {"im_blocked", "imblocked"}:
            if not ch_obj.get("is_im_blocked"):
                return False
        elif kind == "connected":
            if not (ch_obj.get("connected_team_ids") or []):
                return False
        elif kind == "unlinked":
            if not ch_obj.get("unlinked"):
                return False
        elif kind == "internal":
            if not (ch_obj.get("internal_team_ids") or []):
                return False
        elif kind == "host":
            if not ch_obj.get("conversation_host_id"):
                return False
        elif kind in {"connected_limited", "connectedlimited"}:
            if not (ch_obj.get("connected_limited_team_ids") or []):
                return False
        elif kind == "creator":
            creator = str(ch_obj.get("creator") or "")
            if not creator or str(msg.get("user") or "") != creator:
                return False
        elif kind == "delayed":
            if not msg.get("is_delayed_message"):
                return False
        elif kind in {"scheduled", "sched"}:
            if not msg.get("scheduled_message_id"):
                return False
        elif kind in {"guest", "restricted"}:
            p = profile or {}
            if not (p.get("is_restricted") or p.get("is_ultra_restricted")):
                return False
        elif kind == "admin":
            if not (profile or {}).get("is_admin"):
                return False
        elif kind == "owner":
            if not (profile or {}).get("is_owner"):
                return False
        elif kind in {"app_user", "appuser"}:
            if not (profile or {}).get("is_app_user"):
                return False
        elif kind in {"me_message", "memessage"}:
            if msg.get("subtype") != "me_message":
                return False
        elif kind == "stranger":
            if not (profile or {}).get("is_stranger"):
                return False
        elif kind in {"invited", "invited_user"}:
            if not (profile or {}).get("is_invited_user"):
                return False
        elif kind in {"primary_owner", "primaryowner"}:
            if not (profile or {}).get("is_primary_owner"):
                return False
        elif kind in {"ultra_restricted", "ultrarestricted"}:
            if not (profile or {}).get("is_ultra_restricted"):
                return False
        elif kind in {"canvas", "canvases"}:
            files = msg.get("files") or []
            if msg.get("subtype") != "canvas_share" and not any(
                str(f.get("filetype") or "").lower() == "canvas" for f in files
            ):
                return False
        elif kind == "forgotten":
            if not (profile or {}).get("is_forgotten"):
                return False
        elif kind == "connector":
            if not (profile or {}).get("is_connector"):
                return False
        elif kind in {"workflow_bot", "workflowbot"}:
            if not (profile or {}).get("is_workflow_bot"):
                return False
        elif kind == "enterprise":
            if not (profile or {}).get("enterprise_user"):
                return False
        elif kind == "moved":
            if not msg.get("is_moved"):
                return False
        elif kind == "bot":
            if not (msg.get("bot_id") or msg.get("subtype") == "bot_message"):
                return False
        elif kind in {"starred", "saved"}:
            if (ch.id, ts) not in starred:
                return False
        elif kind == "edited":
            if not msg.get("edited"):
                return False
        elif kind in {"unthreaded", "unthread"}:
            if in_thread:
                return False
        elif kind in {"broadcast", "thread_broadcast"}:
            if not (msg.get("subtype") == "thread_broadcast" or msg.get("reply_broadcast")):
                return False
        elif kind == "locked":
            if not msg.get("is_locked"):
                return False
        elif kind in {"tombstone", "deleted"}:
            if msg.get("subtype") != "tombstone" and not msg.get("hidden"):
                return False
        elif kind == "app":
            if not msg.get("app_id"):
                return False
        elif kind in {"file_share", "fileshare"}:
            if msg.get("subtype") != "file_share":
                return False
        elif kind == "me":
            uid = (msg.get("user") or "").lower()
            handle = (msg.get("user_name") or "").lower()
            if not me or (uid not in me and handle not in me):
                return False
        elif kind == "hidden":
            if not msg.get("hidden"):
                return False
        elif kind in {"join", "channel_join"}:
            if msg.get("subtype") != "channel_join":
                return False
        elif kind in {"leave", "channel_leave"}:
            if msg.get("subtype") != "channel_leave":
                return False
        elif kind in {"topic", "channel_topic"}:
            if msg.get("subtype") != "channel_topic":
                return False
        elif kind in {"purpose", "channel_purpose"}:
            if msg.get("subtype") != "channel_purpose":
                return False
        elif kind == "parent":
            is_reply = bool((thread_ts and ts and thread_ts != ts) or (root and root != ts))
            has_kids = bool(msg.get("thread") or msg.get("reply_count"))
            if is_reply or not has_kids:
                return False
        elif kind in {"archive", "channel_archive"}:
            if msg.get("subtype") != "channel_archive":
                return False
        elif kind in {"unarchive", "channel_unarchive"}:
            if msg.get("subtype") != "channel_unarchive":
                return False
        elif kind in {"rename", "channel_name"}:
            if msg.get("subtype") != "channel_name":
                return False
        elif kind == "subscribed":
            if not msg.get("subscribed"):
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
        elif kind == "ephemeral":
            if not msg.get("is_ephemeral"):
                return False
        else:
            return False
    return True


def _compile_time(mods: dict[str, list[str]]) -> dict[str, list[Any]]:
    return {
        "after": [_parse_bound(t) for t in mods["after"]],
        "before": [_parse_bound(t) for t in mods["before"]],
        "around": [_around_window(t) for t in mods.get("around") or []],
        "on": [_on_window(t) for t in mods.get("on") or []],
        "during": [_during_window(t) for t in mods.get("during") or []],
    }


def _msg_time_ok(msg: dict[str, Any], bounds: dict[str, list[Any]]) -> bool:
    timed = any(bounds[k] for k in bounds)
    ts_raw = msg.get("ts")
    if ts_raw is None or ts_raw == "":
        return not timed
    t = float(ts_raw)
    for bound in bounds["after"]:
        if bound is not None and t <= bound:
            return False
    for bound in bounds["before"]:
        if bound is not None and t >= bound:
            return False
    for key in ("around", "on", "during"):
        for window in bounds[key]:
            if window is None:
                return False
            lo, hi = window
            if t < lo or t >= hi:
                return False
    return True


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
) -> dict[str, Any]:
    needle = query.strip().lower()
    if not needle:
        return _err("invalid_arguments")
    hits = [row for row in (rows or []) if isinstance(row, dict) and _sidecar_hit(row, needle)]
    return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key=key)


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


def _read_json(path: Path) -> Any:
    data = path.read_bytes()
    if _fastjson is not None:
        return _fastjson.loads(data)
    return json.loads(data)


def _file_ts(f: dict[str, Any]) -> float:
    v = f.get("created")
    if v is None:
        v = f.get("timestamp")
    if v is None:
        v = f.get("updated")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _login_ts(row: dict[str, Any]) -> float:
    v = row.get("date_last")
    if v is None:
        v = row.get("date_first")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _sched_ts(row: dict[str, Any]) -> float:
    v = row.get("post_at")
    if v is None:
        v = row.get("date_created")
    try:
        return float(v or 0)
    except (TypeError, ValueError):
        return 0.0


def _is_remote_file(f: dict[str, Any]) -> bool:
    return bool(f.get("is_external") or f.get("is_remote") or f.get("mode") == "remote")


def _doc_text(msg: dict[str, Any]) -> str:
    parts = [msg.get("text") or ""]
    for f in msg.get("files") or []:
        parts.append(str(f.get("name") or ""))
        parts.append(str(f.get("title") or ""))
    return " ".join(parts).lower()


def _docs_for_query(loaded: "_Loaded", query: str) -> list[tuple[str, dict[str, Any]]]:
    if not query or " " in query or len(query) < 2:
        return loaded.docs
    seen: set[int] = set()
    out: list[tuple[str, dict[str, Any]]] = []
    for word, idxs in loaded.words.items():
        if query not in word:
            continue
        for i in idxs:
            if i in seen:
                continue
            seen.add(i)
            out.append(loaded.docs[i])
    return out


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


def _paged(
    items: list[Any],
    *,
    count: int | None,
    page: int | None,
    limit: int,
    cursor: str | None,
    key: str,
) -> dict[str, Any]:
    if count is not None:
        limit = int(count)
    if page is not None and not cursor:
        cursor = str((max(int(page), 1) - 1) * min(max(limit, 1), _MAX_LIMIT))
    paged = _page(items, limit, cursor)
    if isinstance(paged, dict):
        return paged
    chunk, _, next_cursor = paged
    return _ok(**{key: chunk}, response_metadata={"next_cursor": next_cursor})


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


def _bot_obj(src: dict[str, Any], want: str) -> dict[str, Any]:
    profile = src.get("bot_profile") if isinstance(src.get("bot_profile"), dict) else {}
    return {
        "id": src.get("id") or src.get("bot_id") or want,
        "app_id": src.get("app_id") or profile.get("app_id") or "",
        "name": src.get("name") or src.get("username") or src.get("user_name") or want,
        "deleted": bool(src.get("deleted") or profile.get("deleted")),
        "icons": src.get("icons") or profile.get("icons") or {},
        "team_id": src.get("team_id") or profile.get("team_id") or src.get("team") or "",
        "updated": src.get("updated") or profile.get("updated") or 0,
        "is_workflow_bot": bool(src.get("is_workflow_bot") or profile.get("is_workflow_bot")),
    }


def _reminder_done(item: dict[str, Any]) -> bool:
    if item.get("complete") is True:
        return True
    try:
        return float(item.get("complete_ts") or 0) != 0
    except (TypeError, ValueError):
        return bool(item.get("complete_ts"))


def _profile_from_any_user(user: dict[str, Any]) -> dict[str, Any]:
    if "handle" in user:
        return user
    p = user.get("profile") if isinstance(user.get("profile"), dict) else {}
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
        "guest_expiration_ts": user.get("guest_expiration_ts")
        or p.get("guest_expiration_ts")
        or 0,
        "bot_id": user.get("bot_id") or p.get("bot_id") or "",
        "api_app_id": user.get("api_app_id") or p.get("api_app_id") or "",
        "team": user.get("team") or p.get("team") or "",
    }


def _ingest_users(raw: Any, profiles: dict[str, dict[str, Any]]) -> None:
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


def _reaction_items(channel_id: str, msg: dict[str, Any], user: str | None) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for rx in msg.get("reactions") or []:
        name = rx.get("name") or ""
        users = [u for u in (rx.get("users") or []) if u]
        if user:
            if user not in users:
                continue
            users = [user]
        for uid in users:
            items.append(
                {
                    "type": "message",
                    "channel": channel_id,
                    "reaction": name,
                    "user": uid,
                    "message": _history_item(msg),
                }
            )
    return items


def _item_ts(item: dict[str, Any]) -> str:
    msg = item.get("message") if isinstance(item.get("message"), dict) else {}
    return str(msg.get("ts") or item.get("ts") or "")


def _sidecar_hit(item: dict[str, Any], needle: str) -> bool:
    chunks: list[str] = []
    for key in (
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
    ):
        val = item.get(key)
        if val:
            chunks.append(str(val))
    msg = item.get("message")
    if isinstance(msg, dict):
        for key in ("text", "ts", "user"):
            val = msg.get(key)
            if val:
                chunks.append(str(val))
    profile = item.get("profile")
    if isinstance(profile, dict):
        for key in ("email", "display_name", "real_name", "title"):
            val = profile.get(key)
            if val:
                chunks.append(str(val))
    return needle in " ".join(chunks).lower()


def _uid_hit(uid: str, profile: dict[str, Any] | None, needle: str) -> bool:
    if needle in str(uid).lower():
        return True
    if not isinstance(profile, dict):
        return False
    return _sidecar_hit(profile, needle) or _sidecar_hit(_slack_user(profile), needle)


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


def _threads_from_loaded(ch: "_Channel", loaded: "_Loaded") -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for msg in loaded.messages:
        thread = msg.get("thread") or []
        if not thread:
            continue
        users = _reply_user_ids(msg, thread)
        rows.append(
            {
                "channel": ch.id,
                "thread_ts": msg.get("ts", ""),
                "reply_count": len(thread),
                "latest_reply": thread[-1].get("ts", ""),
                "reply_users": users,
                "reply_users_count": int(msg.get("reply_users_count") or len(users)),
            }
        )
    for ts, replies in loaded.thread_only.items():
        users = _reply_user_ids({}, replies)
        rows.append(
            {
                "channel": ch.id,
                "thread_ts": ts,
                "reply_count": len(replies),
                "latest_reply": replies[-1].get("ts", "") if replies else "",
                "reply_users": users,
                "reply_users_count": len(users),
            }
        )
    return rows


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
    all_by_ts: dict[str, dict[str, Any]]
    thread_root: dict[str, str]
    thread_only: dict[str, list[dict[str, Any]]]
    files: dict[str, dict[str, Any]]
    users_extra: dict[str, dict[str, Any]]
    history_newest: list[dict[str, Any]] | None
    member_ids: list[str]
    docs: list[tuple[str, dict[str, Any]]]
    words: dict[str, list[int]]
    search_ready: bool = False


def _empty_loaded() -> _Loaded:
    return _Loaded([], {}, {}, {}, {}, {}, {}, None, [], [], {})


@dataclass
class _Channel:
    id: str
    name: str
    workspace: str
    path: Path
    kinds: frozenset[str]
    thread_dumps: dict[str, Path]
    loaded: _Loaded | None = field(default=None, repr=False)
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


def _read_channel_messages(path: Path) -> list[dict[str, Any]]:
    msg_path = path / "messages.json"
    if msg_path.is_file():
        raw = _read_json(msg_path)
        return raw if isinstance(raw, list) else []
    files = _date_jsons(path)
    if not files:
        return []
    if len(files) == 1:
        raw = _read_json(files[0])
        return raw if isinstance(raw, list) else []
    with ThreadPoolExecutor(max_workers=min(_LOAD_WORKERS, len(files))) as pool:
        chunks = list(pool.map(_read_json, files))
    out: list[dict[str, Any]] = []
    for raw in chunks:
        if isinstance(raw, list):
            out.extend(raw)
    out.sort(key=lambda m: float(m["ts"]) if isinstance(m, dict) and m.get("ts") else 0.0)
    return out


def _split_parents(
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
    if _date_jsons(path):
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
    """Read-only Slack Web API over a local ssd dump or Slack export. No tokens, no network."""

    def __init__(self, path: str | Path):
        self.root = Path(path)
        if not self.root.exists():
            raise FileNotFoundError(self.root)
        self._channels = _discover(self.root)
        self._profiles: dict[str, dict[str, Any]] | None = None
        self._files: dict[str, dict[str, Any]] = {}
        self._all_loaded = False
        self._emoji: dict[str, str] | None = None
        self._auth: dict[str, Any] | None = None
        self._catalog: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._ws_files_loaded = False
        self._files_complete = False
        self._users_merged: dict[str, dict[str, Any]] | None = None
        self._starred: set[tuple[str, str]] | None = None
        self._bots: dict[str, dict[str, Any]] | None = None
        self._json_cache: dict[str, Any] = {}
        self._calls: dict[str, dict[str, Any]] | None = None
        self._apply_catalog()

    def _workspace_auth(self) -> dict[str, Any]:
        if self._auth is None:
            raw = self._first_json("auth.json")
            self._auth = raw if isinstance(raw, dict) else {}
        return self._auth

    def _first_json(self, name: str) -> Any:
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
            found = _read_json(path)
            break
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

    def _ensure_workspace_files(self) -> None:
        if self._ws_files_loaded:
            return
        self._ws_files_loaded = True
        self._ingest_file_rows(self._first_json("files.json"))
        self._ingest_file_rows(self._first_json("remote_files.json"))
        if self._files:
            self._files_complete = True
            return
        missing = False
        for ch in self._channels.values():
            raw = self._channel_sidecar(ch, "files.json")
            if isinstance(raw, list):
                self._ingest_file_rows(raw)
            else:
                missing = True
        self._files_complete = not missing

    def _fill_files(self) -> None:
        self._ensure_workspace_files()
        if not self._files_complete:
            self._load_all()
            self._files_complete = True

    def _channel_files(self, ch: _Channel) -> list[dict[str, Any]]:
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
            files = list(self._files.values())
        if user:
            files = [f for f in files if f.get("user") == user]
        if ts_from is not None:
            start = float(ts_from)
            files = [f for f in files if _file_ts(f) >= start]
        if ts_to is not None:
            end = float(ts_to)
            files = [f for f in files if _file_ts(f) <= end]
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
            raw = _read_json(path)
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
        remap: list[tuple[str, str]] = []
        for old_id, ch in self._channels.items():
            if old_id in self._catalog:
                continue
            for cid, entry in self._catalog.items():
                if str(entry.get("name") or "") == ch.name:
                    remap.append((old_id, cid))
                    break
        for old_id, new_id in remap:
            if new_id in self._channels:
                continue
            ch = self._channels.pop(old_id)
            ch.id = new_id
            ch.kinds = _kinds_for(new_id)
            self._channels[new_id] = ch
        sample = next(iter(self._channels.values()), None)
        workspace = sample.workspace if sample else self.root.name
        parent = sample.path.parent if sample else self.root
        for cid, entry in self._catalog.items():
            if cid in self._channels:
                continue
            name = str(entry.get("name") or cid)
            self._channels[cid] = _Channel(
                id=cid,
                name=name,
                workspace=workspace,
                path=parent / f"{name}_{cid}",
                kinds=_kinds_for(cid),
                thread_dumps={},
            )

    def _get(self, channel: str) -> _Channel | None:
        raw = channel.lstrip("#")
        if raw in self._channels:
            return self._channels[raw]
        for ch in self._channels.values():
            if ch.name == raw:
                return ch
        return None

    def _in_scope_channels(self, in_toks: list[str]) -> list[_Channel]:
        if not in_toks:
            return list(self._channels.values())
        in_me = any(_norm_from(t).lower() == "me" for t in in_toks)
        named = [t for t in in_toks if _norm_from(t).lower() != "me"]
        named_chs = (
            [ch for ch in self._channels.values() if _channel_in_scope(ch, named)] if named else []
        )
        if not in_me:
            return named_chs
        auth = self._workspace_auth()
        me = {x.lower() for x in _expand_me(["me"], auth) if x and x != "\0"}
        dms = [
            ch
            for ch in self._channels.values()
            if ch.kinds & {"im", "mpim"} and me & {u.lower() for u in self._roster(ch)}
        ]
        if not named:
            return dms
        seen = {ch.id: ch for ch in named_chs}
        for ch in dms:
            seen[ch.id] = ch
        return list(seen.values())

    def _roster(self, ch: _Channel) -> list[str]:
        if ch.roster is not None:
            return ch.roster
        raw = self._channel_sidecar(ch, "members.json")
        if isinstance(raw, list):
            ch.roster = [str(x) for x in raw]
            return ch.roster
        other = str(self._channel_obj(ch).get("user") or "")
        if other:
            ids = [other]
            me = str(self._workspace_auth().get("user_id") or "")
            if me and me not in ids:
                ids.append(me)
            ch.roster = ids
            return ch.roster
        ch.roster = self._load(ch).member_ids
        return ch.roster

    def _channel_sidecar(self, ch: _Channel, name: str) -> Any:
        store = ch.sidecars
        if store is None:
            store = {}
            ch.sidecars = store
        if name in store:
            return store[name]
        path = ch.path / name
        raw = _read_json(path) if path.is_file() else None
        store[name] = raw
        return raw

    def _with_ok(
        self,
        ch: _Channel,
        with_toks: list[str],
        profiles: dict[str, dict[str, Any]],
    ) -> bool:
        extra = ch.loaded.users_extra if ch.loaded is not None else {}
        return _channel_with_ok(ch, self._roster(ch), with_toks, profiles, extra)

    def _load(self, ch: _Channel) -> _Loaded:
        if ch.loaded is not None:
            return ch.loaded
        if not ch.path.is_dir():
            loaded = _empty_loaded()
            with self._lock:
                if ch.loaded is not None:
                    return ch.loaded
                ch.loaded = loaded
            return loaded
        messages, loose = _split_parents(_read_channel_messages(ch.path))
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
            _ingest(msg, files, users_extra, members, ch.id)

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
        for ts, tpath in ch.thread_dumps.items():
            if ts in by_ts:
                continue
            raw = _read_json(tpath) if tpath.is_file() else []
            replies = raw if isinstance(raw, list) else []
            thread_only[ts] = replies
            for reply in replies:
                index(reply, ts)
        member_ids = sorted(members)
        members_path = ch.path / "members.json"
        if members_path.is_file():
            roster = _read_json(members_path)
            if isinstance(roster, list):
                member_ids = [str(x) for x in roster]
        loaded = _Loaded(
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
            ch.loaded = loaded
            self._files.update(files)
        return ch.loaded

    def _ensure_search(self, loaded: _Loaded) -> _Loaded:
        if loaded.search_ready:
            return loaded
        docs: list[tuple[str, dict[str, Any]]] = []
        words: dict[str, list[int]] = {}
        for msg in _iter_msgs(loaded):
            i = len(docs)
            text_l = _doc_text(msg)
            docs.append((text_l, msg))
            seen_w: set[str] = set()
            for w in _WORD_RE.findall(text_l):
                if w in seen_w:
                    continue
                seen_w.add(w)
                words.setdefault(w, []).append(i)
        loaded.docs = docs
        loaded.words = words
        loaded.search_ready = True
        return loaded

    def _ensure_history(self, loaded: _Loaded) -> list[dict[str, Any]]:
        items = loaded.history_newest
        if items is None:
            items = [_history_item(m) for m in reversed(loaded.messages) if m.get("ts")]
            loaded.history_newest = items
        return items

    def _channel_meta(self, ch: _Channel) -> dict[str, Any]:
        if ch.meta_checked:
            return ch.meta or {}
        ch.meta_checked = True
        path = ch.path / "channel.json"
        if path.is_file():
            raw = _read_json(path)
            if isinstance(raw, dict):
                ch.meta = raw
                return raw
        ch.meta = {}
        return {}

    def _load_all(self) -> None:
        if self._all_loaded:
            return
        channels = [ch for ch in self._channels.values() if ch.path.is_dir()]
        if len(channels) <= 1:
            for ch in channels:
                self._load(ch)
        else:
            workers = min(_LOAD_WORKERS, len(channels))
            with ThreadPoolExecutor(max_workers=workers) as pool:
                list(pool.map(self._load, channels))
        self._all_loaded = True

    def _ensure_profiles(self) -> dict[str, dict[str, Any]]:
        if self._profiles is not None:
            return self._profiles
        profiles: dict[str, dict[str, Any]] = {}
        seen: set[Path] = set()
        candidates: list[Path] = [self.root / "users.json"]
        for ch in self._channels.values():
            candidates.append(ch.path.parent / "users.json")
            candidates.append(ch.path / "users.json")
            candidates.extend(p.parent / "users.json" for p in ch.thread_dumps.values())
        for path in candidates:
            if path in seen or not path.is_file():
                continue
            seen.add(path)
            raw = _read_json(path)
            _ingest_users(raw, profiles)
        self._profiles = profiles
        return profiles

    def _all_users(self, *, extras: bool = True) -> dict[str, dict[str, Any]]:
        if extras and self._users_merged is not None:
            return self._users_merged
        users = dict(self._ensure_profiles())
        if not extras:
            return users
        self._load_all()
        for ch in self._channels.values():
            loaded = ch.loaded
            if loaded is None:
                continue
            for uid, profile in loaded.users_extra.items():
                if uid not in users:
                    users[uid] = profile
        self._users_merged = users
        return users

    def _channel_obj(self, ch: _Channel) -> dict[str, Any]:
        if ch.obj_cache is not None:
            return ch.obj_cache
        prefix = ch.id[:1]
        obj: dict[str, Any] = {
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
        meta = {**(self._catalog.get(ch.id) or {}), **self._channel_meta(ch)}
        if meta:
            for key in _CATALOG_KEYS:
                if key in meta:
                    obj[key] = meta[key]
            for field_name in ("topic", "purpose"):
                if field_name not in meta:
                    continue
                value = meta[field_name]
                obj[field_name] = {"value": value} if isinstance(value, str) else value
            for field_name in ("topic", "purpose"):
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
        ch.obj_cache = obj
        return obj

    def _channel_matches_is(self, ch: _Channel, is_kinds: set[str]) -> bool:
        obj = None

        def flags() -> dict[str, Any]:
            nonlocal obj
            if obj is None:
                obj = self._channel_obj(ch)
            return obj

        prefix = ch.id[:1]
        for kind in is_kinds:
            if kind in _MSG_IS:
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
            elif kind == "private":
                if not flags().get("is_private"):
                    return False
            elif kind == "public":
                if prefix != "C" or flags().get("is_private"):
                    return False
            elif kind in {"shared", "ext_shared", "extshared"}:
                if kind in {"ext_shared", "extshared"}:
                    if not flags().get("is_ext_shared"):
                        return False
                elif not (
                    flags().get("is_shared")
                    or flags().get("is_ext_shared")
                    or flags().get("is_org_shared")
                ):
                    return False
            elif kind in {"org_shared", "orgshared"}:
                if not flags().get("is_org_shared"):
                    return False
            elif kind in {"general", "random"}:
                name = str(ch.name or flags().get("name") or "").lower()
                if kind == "general":
                    if name != "general" and not flags().get("is_general"):
                        return False
                elif name != kind:
                    return False
            elif kind == "archived":
                if not flags().get("is_archived"):
                    return False
            elif kind == "frozen":
                if not flags().get("is_frozen"):
                    return False
            elif kind == "open":
                if not flags().get("is_open"):
                    return False
            elif kind in {"org_default", "orgdefault"}:
                if not flags().get("is_org_default"):
                    return False
            elif kind in {"global_shared", "globalshared"}:
                if not flags().get("is_global_shared"):
                    return False
            elif kind in {"org_mandatory", "orgmandatory"}:
                if not flags().get("is_org_mandatory"):
                    return False
            elif kind == "member":
                if not flags().get("is_member"):
                    return False
            elif kind in {"pending_ext_shared", "pendingextshared"}:
                if not flags().get("is_pending_ext_shared"):
                    return False
            elif kind in {"read_only", "readonly"}:
                if not flags().get("is_read_only"):
                    return False
            elif kind in {"thread_only", "threadonly"}:
                if not flags().get("is_thread_only"):
                    return False
            elif kind in {"non_threadable", "nonthreadable"}:
                if not flags().get("is_non_threadable"):
                    return False
            elif kind in {"user_deleted", "userdeleted"}:
                if not flags().get("is_user_deleted"):
                    return False
            elif kind in {"muted", "mute"}:
                if not flags().get("is_muted"):
                    return False
            elif kind in {"unreads", "unread"}:
                n = int(flags().get("unread_count") or 0)
                d = int(flags().get("unread_count_display") or 0)
                if n <= 0 and d <= 0:
                    return False
            elif kind in {"pending_shared", "pendingshared"}:
                if not flags().get("is_pending_shared"):
                    return False
            elif kind in {"has_canvas", "hascanvas"}:
                if not flags().get("has_canvas"):
                    return False
            elif kind in {"im_blocked", "imblocked"}:
                if not flags().get("is_im_blocked"):
                    return False
            elif kind == "connected":
                if not (flags().get("connected_team_ids") or []):
                    return False
            elif kind == "unlinked":
                if not flags().get("unlinked"):
                    return False
            elif kind == "internal":
                if not (flags().get("internal_team_ids") or []):
                    return False
            elif kind == "host":
                if not flags().get("conversation_host_id"):
                    return False
            elif kind in {"connected_limited", "connectedlimited"}:
                if not (flags().get("connected_limited_team_ids") or []):
                    return False
            else:
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
        best_f: float | None = None
        for row in self.iter_cursors():
            ts = str(row.get("ts") or "")
            if not ts:
                continue
            try:
                val = float(ts)
            except ValueError:
                continue
            if best_f is None or val > best_f:
                best_f = val
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
            return fn(**payload)
        allowed = {k: v for k, v in payload.items() if k in sig.parameters}
        return fn(**allowed)

    def conversations_list(
        self,
        *,
        types: str | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
        exclude_archived: bool = False,
    ) -> dict[str, Any]:
        wanted = {t.strip() for t in (types or _ALL_TYPES).split(",") if t.strip()}
        channels = [self._channel_obj(ch) for ch in self._channels.values() if ch.kinds & wanted]
        if exclude_archived:
            channels = [c for c in channels if not c.get("is_archived")]
        paged = _page(channels, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(
            channels=chunk,
            response_metadata={"next_cursor": next_cursor},
        )

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
        wanted = {t.strip() for t in (types or _ALL_TYPES).split(",") if t.strip()}
        hits: list[dict[str, Any]] = []
        for ch in self._channels.values():
            if not (ch.kinds & wanted):
                continue
            obj = self._channel_obj(ch)
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

    def groups_list(
        self, *, limit: int = _MAX_LIMIT, cursor: str | None = None
    ) -> dict[str, Any]:
        return self.conversations_list(types="private_channel", limit=limit, cursor=cursor)

    def im_list(self, *, limit: int = _MAX_LIMIT, cursor: str | None = None) -> dict[str, Any]:
        return self.conversations_list(types="im", limit=limit, cursor=cursor)

    def mpim_list(self, *, limit: int = _MAX_LIMIT, cursor: str | None = None) -> dict[str, Any]:
        return self.conversations_list(types="mpim", limit=limit, cursor=cursor)

    def channels_info(self, *, channel: str) -> dict[str, Any]:
        return self.conversations_info(channel=channel)

    def groups_info(self, *, channel: str) -> dict[str, Any]:
        return self.conversations_info(channel=channel)

    def im_info(self, *, channel: str) -> dict[str, Any]:
        return self.conversations_info(channel=channel)

    def mpim_info(self, *, channel: str) -> dict[str, Any]:
        return self.conversations_info(channel=channel)

    def channels_history(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversations_history(**kwargs)

    def groups_history(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversations_history(**kwargs)

    def im_history(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversations_history(**kwargs)

    def mpim_history(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversations_history(**kwargs)

    def channels_replies(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversations_replies(**kwargs)

    def groups_replies(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversations_replies(**kwargs)

    def im_replies(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversations_replies(**kwargs)

    def mpim_replies(self, **kwargs: Any) -> dict[str, Any]:
        return self.conversations_replies(**kwargs)

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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        ch = self._get(channel)
        if ch is None:
            return _err("channel_not_found")
        items = self._ensure_history(self._load(ch))
        if oldest is not None or latest is not None:
            items = [m for m in items if _in_range(str(m["ts"]), oldest, latest, inclusive)]
        hits = [row for row in items if isinstance(row, dict) and _sidecar_hit(row, needle)]
        paged = _page(hits, limit, cursor)
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
        needle = query.strip().lower()
        if not needle:
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
        hits = [
            row
            for row in listed.get("messages") or []
            if isinstance(row, dict) and _sidecar_hit(row, needle)
        ]
        paged = _page(hits, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, has_more, next_cursor = paged
        return _ok(
            messages=chunk,
            has_more=has_more,
            response_metadata={"next_cursor": next_cursor},
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
        members = [
            _slack_user(p) for p in self._all_users(extras=include_message_users).values()
        ]
        if not include_deleted:
            members = [u for u in members if not u.get("deleted")]
        if not include_bots:
            members = [u for u in members if not u.get("is_bot")]
        members.sort(key=lambda u: (u.get("name") or "", u.get("id") or ""))
        for member in members:
            presence = self._presence_of(str(member.get("id") or ""))
            if presence:
                member["presence"] = presence
        paged = _page(members, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(members=chunk, response_metadata={"next_cursor": next_cursor})

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
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().lstrip("@").lower()
        if not needle:
            return _err("invalid_arguments")
        hits: list[dict[str, Any]] = []
        for profile in self._ensure_profiles().values():
            blob = " ".join(
                [
                    str(profile.get("id") or ""),
                    str(profile.get("handle") or ""),
                    str(profile.get("display_name") or ""),
                    str(profile.get("real_name") or ""),
                    str(profile.get("email") or ""),
                ]
            ).lower()
            if needle in blob:
                hits.append(_slack_user(profile))
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
        if file in self._files:
            return _ok(file=self._files[file])
        want = file.strip().lower()
        for stored in self._files.values():
            if str(stored.get("name") or "").lower() == want:
                return _ok(file=stored)
        self._fill_files()
        stored = self._files.get(file)
        if stored:
            return _ok(file=stored)
        for stored in self._files.values():
            if str(stored.get("name") or "").lower() == want:
                return _ok(file=stored)
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
        files = [f for f in self._files.values() if _is_remote_file(f)]
        files.sort(key=lambda f: str(f.get("id") or ""))
        return _paged(files, count=count, page=page, limit=limit, cursor=cursor, key="files")

    def files_remote_info(
        self, *, file: str | None = None, external_id: str | None = None
    ) -> dict[str, Any]:
        self._ensure_workspace_files()
        if file and file in self._files:
            fobj = self._files[file]
            if _is_remote_file(fobj):
                return _ok(file=fobj)
            return _err("file_not_found")
        self._fill_files()
        if file:
            fobj = self._files.get(file)
            if fobj and _is_remote_file(fobj):
                return _ok(file=fobj)
            return _err("file_not_found")
        if external_id:
            for fobj in self._files.values():
                if str(fobj.get("external_id") or "") == external_id and _is_remote_file(fobj):
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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        hits = [
            row
            for row in self.files_remote_list().get("files") or []
            if isinstance(row, dict) and _sidecar_hit(row, needle)
        ]
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="files")

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
        if count is not None:
            limit = int(count)
        if page is not None and not cursor:
            cursor = str((max(int(page), 1) - 1) * min(max(limit, 1), _MAX_LIMIT))
        files = self._listed_files(
            channel=channel, user=user, ts_from=ts_from, ts_to=ts_to, types=types
        )
        if isinstance(files, dict):
            return files
        paged = _page(files, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(files=chunk, response_metadata={"next_cursor": next_cursor})

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
        paged = _paged(
            comments, count=count, page=page, limit=limit, cursor=cursor, key="comments"
        )
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
        paged = _page(self._roster(ch), limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(members=chunk, response_metadata={"next_cursor": next_cursor})

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
        paged = _page(hits, limit, cursor)
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

    def _pins_items(self, ch: _Channel) -> list[dict[str, Any]]:
        raw = self._channel_sidecar(ch, "pins.json")
        if isinstance(raw, list):
            return raw
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
        return items

    def pins_info(self, *, channel: str, ts: str) -> dict[str, Any]:
        listed = self.pins_list(channel=channel)
        if not listed.get("ok"):
            return listed
        want = ts.strip()
        for item in listed.get("items") or []:
            if isinstance(item, dict) and _item_ts(item) == want:
                return _ok(item=item)
        return _err("not_found")

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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        listed = self.pins_list(channel=channel)
        if not listed.get("ok"):
            return listed
        hits = [
            row
            for row in listed.get("items") or []
            if isinstance(row, dict) and _sidecar_hit(row, needle)
        ]
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="items")

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
        text, mods = _parse_search(query)
        text, nots = _split_negation(text)
        text_l_query = text.lower()
        empty = _ok(query=query, messages={"total": 0, "matches": []})
        if not text_l_query and not any(mods.values()) and not nots:
            return empty
        scoped = self._in_scope_channels(mods["in"])
        if mods["in"] and not scoped:
            return empty
        auth = self._workspace_auth()
        from_toks = _expand_me(mods["from"], auth)
        to_toks = _expand_me(mods["to"], auth)
        with_toks = _expand_me(mods["with"], auth)
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
        channel_is = is_kinds - _MSG_IS
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
            me = {x.lower() for x in _expand_me(["me"], auth) if x and x != "\0"}
        bounds = _compile_time(mods)
        matches: list[dict[str, Any]] = []
        for ch in scoped:
            loaded = ch.loaded
            if loaded is None:
                continue
            extras = loaded.users_extra
            ch_obj = self._channel_obj(ch)
            for text_l, msg in _docs_for_query(self._ensure_search(loaded), text_l_query):
                uid = msg.get("user") or ""
                profile = profiles.get(uid) or extras.get(uid) or {}
                if not _msg_from_ok(msg, from_toks, profile):
                    continue
                if not _msg_to_ok(msg, to_toks):
                    continue
                if not _msg_has_ok(msg, has_toks):
                    continue
                if not _msg_is_ok(
                    msg, list(is_kinds), ch, loaded, ch_obj, starred, me, profile
                ):
                    continue
                if not _msg_time_ok(msg, bounds):
                    continue
                if text_l_query and text_l_query not in text_l:
                    continue
                if any(n in text_l for n in nots):
                    continue
                matches.append(
                    {
                        "type": "message",
                        "ts": msg.get("ts", ""),
                        "user": uid,
                        "username": msg.get("user_name") or "",
                        "text": msg.get("text") or "",
                        "channel": {"id": ch.id, "name": ch.name},
                    }
                )
        matches.sort(
            key=lambda m: float(m["ts"] or 0),
            reverse=str(sort_dir or "desc").lower() != "asc",
        )
        count = min(max(count, 1), _MAX_LIMIT)
        start = 0 if page is None else (max(int(page), 1) - 1) * count
        sliced = matches[start : start + count]
        return _ok(
            query=query,
            messages={"total": len(matches), "matches": sliced},
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
        missing: list[_Channel] = []
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
                for msg in _iter_msgs(loaded):
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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        listed = self.reactions_list(user=user, channel=channel)
        if not listed.get("ok"):
            return listed
        hits = [
            row
            for row in listed.get("items") or []
            if isinstance(row, dict) and _sidecar_hit(row, needle)
        ]
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="items")

    def _reaction_sidecar(
        self, ch: _Channel, user: str | None
    ) -> list[dict[str, Any]] | None:
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
        wanted = {t.strip() for t in (types or _ALL_TYPES).split(",") if t.strip()}
        channels = [
            self._channel_obj(ch)
            for ch in self._channels.values()
            if ch.kinds & wanted and user in self._roster(ch)
        ]
        if exclude_archived:
            channels = [c for c in channels if not c.get("is_archived")]
        paged = _page(channels, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(channels=chunk, response_metadata={"next_cursor": next_cursor})

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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        wanted = {t.strip() for t in (types or _ALL_TYPES).split(",") if t.strip()}
        channels = [
            self._channel_obj(ch)
            for ch in self._channels.values()
            if ch.kinds & wanted and user in self._roster(ch)
        ]
        if exclude_archived:
            channels = [c for c in channels if not c.get("is_archived")]
        hits = [c for c in channels if _sidecar_hit(c, needle)]
        paged = _page(hits, limit, cursor)
        if isinstance(paged, dict):
            return paged
        chunk, _, next_cursor = paged
        return _ok(channels=chunk, response_metadata={"next_cursor": next_cursor})

    def search_files(
        self, *, query: str, count: int = 20, page: int | None = None
    ) -> dict[str, Any]:
        text, mods = _parse_search(query)
        q = text.lower().strip()
        empty = _ok(query=query, files={"total": 0, "matches": []})
        if not q and not any(mods.values()):
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
            files = list(self._files.values())
        profiles = self._ensure_profiles()
        from_toks = _expand_me(mods["from"], self._workspace_auth())
        bounds = _compile_time(mods)
        matches: list[dict[str, Any]] = []
        seen: set[str] = set()
        for f in files:
            fid = str(f.get("id") or "")
            if fid and fid in seen:
                continue
            uid = f.get("user") or ""
            profile = profiles.get(uid) or {}
            fake_msg = {
                "user": uid,
                "user_name": profile.get("handle") or "",
                "ts": str(_file_ts(f)),
            }
            if not _msg_from_ok(fake_msg, from_toks, profile):
                continue
            if not _msg_time_ok(fake_msg, bounds):
                continue
            if not _file_has_ok(f, mods["has"]):
                continue
            blob = " ".join(
                str(f.get(k) or "")
                for k in ("name", "title", "filetype", "mimetype", "pretty_type")
            ).lower()
            if q and q not in blob:
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
        if self._emoji is None:
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
                    for msg in _iter_msgs(loaded):
                        for rx in msg.get("reactions") or []:
                            name = rx.get("name")
                            if name and name not in catalog:
                                catalog[str(name)] = ""
            self._emoji = catalog
        cats = self._first_json("emoji_categories.json")
        if isinstance(cats, list):
            return _ok(emoji=self._emoji, categories=cats)
        return _ok(emoji=self._emoji)

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
        yield from self._files.values()

    def iter_remote_files(self) -> Iterator[dict[str, Any]]:
        yield from self.files_remote_list().get("files") or []

    def iter_threads(self, channel: str | None = None) -> Iterator[dict[str, Any]]:
        if channel:
            ch = self._get(channel)
            chans = [ch] if ch is not None else []
        else:
            chans = list(self._channels.values())
        missing: list[_Channel] = []
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
                yield from _threads_from_loaded(ch, loaded)

    def _thread_sidecar(self, ch: _Channel) -> list[dict[str, Any]] | None:
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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        hits = [
            row
            for row in self.threads_list(channel=channel).get("threads") or []
            if isinstance(row, dict) and _sidecar_hit(row, needle)
        ]
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="threads")

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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        listed = self.bookmarks_list(channel=channel)
        if not listed.get("ok"):
            return listed
        hits = [
            row
            for row in listed.get("bookmarks") or []
            if isinstance(row, dict) and _sidecar_hit(row, needle)
        ]
        return _paged(
            hits, count=count, page=page, limit=limit, cursor=cursor, key="bookmarks"
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
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().lstrip("@").lower()
        if not needle:
            return _err("invalid_arguments")
        groups = self.usergroups_list(include_disabled=include_disabled).get("usergroups") or []
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
            files = len(self._files)
        else:
            self._ensure_workspace_files()
            files = len(self._files) if self._files else n_files
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
        yield from self.chat_scheduledMessages_list(
            channel=channel, oldest=oldest, latest=latest
        ).get("scheduled_messages") or []

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
        yield from self.team_integrationLogs(
            user=user, service_id=service_id, change_type=change_type, app_id=app_id
        ).get("logs") or []

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

    def usergroups_users_list(self, *, usergroup: str, **kwargs: Any) -> dict[str, Any]:
        return self.usergroups_users(usergroup=usergroup, **kwargs)

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
        if self._starred is not None:
            return self._starred
        keys: set[tuple[str, str]] = set()
        for item in self.stars_list().get("items") or []:
            if not isinstance(item, dict):
                continue
            cid = str(item.get("channel") or item.get("channel_id") or "")
            msg = item.get("message") if isinstance(item.get("message"), dict) else {}
            ts = str(msg.get("ts") or item.get("ts") or "")
            if cid and ts:
                keys.add((cid, ts))
        self._starred = keys
        return keys

    def stars_info(self, *, channel: str, ts: str) -> dict[str, Any]:
        listed = self.stars_list(channel=channel)
        if not listed.get("ok"):
            return listed
        want = ts.strip()
        for item in listed.get("items") or []:
            if isinstance(item, dict) and _item_ts(item) == want:
                return _ok(item=item)
        return _err("not_found")

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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        hits = [
            row
            for row in self.stars_list(channel=channel).get("items") or []
            if isinstance(row, dict) and _sidecar_hit(row, needle)
        ]
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="items")

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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        hits: list[dict[str, Any]] = []
        listed = self.reminders_list(
            include_complete=include_complete, user=user
        ).get("reminders") or []
        for item in listed:
            if not isinstance(item, dict):
                continue
            blob = " ".join(
                [
                    str(item.get("id") or ""),
                    str(item.get("text") or ""),
                    str(item.get("user") or ""),
                ]
            ).lower()
            if needle in blob:
                hits.append(item)
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="reminders")

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
            start = _parse_bound(str(after))
            if start is not None:
                items = [row for row in items if isinstance(row, dict) and _login_ts(row) >= start]
        if before is not None:
            end = _parse_bound(str(before))
            if end is not None:
                items = [row for row in items if isinstance(row, dict) and _login_ts(row) <= end]
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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        fields = (self.team_profile_get().get("profile") or {}).get("fields") or []
        hits = [row for row in fields if isinstance(row, dict) and _sidecar_hit(row, needle)]
        return _ok(profile={"fields": hits})

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

    def openid_connect_userInfo(self) -> dict[str, Any]:
        return self.users_identity()

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
            for msg in _iter_msgs(loaded):
                if msg.get("bot_id") != want:
                    continue
                return _ok(bot=_bot_obj(msg, want))
        return _err("bot_not_found")

    def _bots_catalog(self) -> dict[str, dict[str, Any]]:
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
                for msg in _iter_msgs(loaded):
                    bid = msg.get("bot_id")
                    if bid and str(bid) not in catalog:
                        catalog[str(bid)] = msg
            self._bots = catalog
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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        hits: list[dict[str, Any]] = []
        for bot in self.bots_list().get("bots") or []:
            if not isinstance(bot, dict):
                continue
            blob = " ".join(
                [str(bot.get("id") or ""), str(bot.get("name") or ""), str(bot.get("app_id") or "")]
            ).lower()
            if needle in blob:
                hits.append(bot)
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="bots")

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
            start = float(oldest)
            items = [m for m in items if isinstance(m, dict) and _sched_ts(m) >= start]
        if latest is not None:
            end = float(latest)
            items = [m for m in items if isinstance(m, dict) and _sched_ts(m) <= end]
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
        count: int | None = None,
        page: int | None = None,
        limit: int = _MAX_LIMIT,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        hits = [
            row
            for row in self.chat_scheduledMessages_list(channel=channel).get(
                "scheduled_messages"
            )
            or []
            if isinstance(row, dict) and _sidecar_hit(row, needle)
        ]
        return _paged(
            hits, count=count, page=page, limit=limit, cursor=cursor, key="scheduled_messages"
        )

    def export_jsonl(self, path: str | Path, channel: str | None = None) -> int:
        dest = Path(path)
        dest.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with dest.open("wb") as fh:
            for msg in self.iter_messages(channel):
                if _fastjson is not None:
                    fh.write(_fastjson.dumps(msg))
                    fh.write(b"\n")
                else:
                    fh.write((json.dumps(msg, ensure_ascii=False) + "\n").encode())
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
        needle = query.strip().lower()
        if not needle:
            return _err("invalid_arguments")
        hits: list[dict[str, Any]] = []
        for call in self.calls_list().get("calls") or []:
            if not isinstance(call, dict):
                continue
            blob = " ".join(
                [str(call.get("id") or ""), str(call.get("name") or "")]
            ).lower()
            if needle in blob:
                hits.append(call)
        return _paged(hits, count=count, page=page, limit=limit, cursor=cursor, key="calls")

    def _calls_catalog(self) -> dict[str, dict[str, Any]]:
        if self._calls is not None:
            return self._calls
        out: dict[str, dict[str, Any]] = {}
        missing: list[_Channel] = []
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
                for msg in _iter_msgs(loaded):
                    for key in ("room", "call"):
                        obj = msg.get(key)
                        if not isinstance(obj, dict):
                            continue
                        cid = str(obj.get("id") or "")
                        if cid and cid not in out:
                            out[cid] = obj
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
