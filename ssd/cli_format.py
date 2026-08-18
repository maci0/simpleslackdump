"""TTY/table/JSON presentation helpers for the ssd CLI.

Kept separate from Click command wiring in ``ssd.cli`` so query formatting
does not bloat the entrypoint module.
"""

from __future__ import annotations

import sys
from typing import Any

import click
import orjson


def _json_pretty() -> bool:
    return sys.stdout.isatty()


def print_watch_line(msg: dict[str, Any], as_json: bool) -> None:
    if as_json or not _json_pretty():
        click.echo(orjson.dumps(msg).decode())
        return
    user = msg.get("user_name") or msg.get("username") or msg.get("user") or ""
    ts = msg.get("ts") or ""
    text = " ".join(str(msg.get("text") or "").split())
    click.echo(f"{user}  {ts}  {text}")


def print_json(data: Any) -> None:
    pretty = _json_pretty()
    opts = orjson.OPT_INDENT_2 if pretty else 0
    click.echo(orjson.dumps(data, option=opts).decode())
    if isinstance(data, dict) and data.get("ok") is False:
        ctx = click.get_current_context(silent=True)
        if ctx is not None:
            ctx.exit(1)
        raise SystemExit(1)


def _want_table(ctx: click.Context) -> bool:
    return _json_pretty() and not ctx.obj.get("query_json")


def _cell(val: Any, width: int) -> str:
    text = " ".join(str(val or "").split())
    if len(text) > width:
        text = text[: width - 3] + "..."
    return text.ljust(width)


def _print_table(rows: list[dict[str, str]], headers: tuple[str, ...]) -> None:
    widths = {h: len(h) for h in headers}
    for row in rows:
        for key in headers:
            cap = (
                48
                if key in {"text", "real_name", "name", "title", "handle", "url", "comment"}
                else 24
            )
            widths[key] = max(widths[key], min(len(row.get(key) or ""), cap))
    click.echo("  ".join(_cell(h, widths[h]) for h in headers).rstrip())
    for row in rows:
        click.echo("  ".join(_cell(row.get(h) or "", widths[h]) for h in headers).rstrip())


def _print_list(
    ctx: click.Context,
    data: dict[str, Any],
    key: str,
    headers: tuple[str, ...],
    row_fn: Any,
) -> None:
    if not _want_table(ctx):
        print_json(data)
        return
    rows: list[dict[str, str]] = []
    for item in data.get(key) or []:
        if isinstance(item, dict):
            rows.append(row_fn(item))
    _print_table(rows, headers)
    if data.get("ok") is False:
        ctx.exit(1)


def _print_id_name(ctx: click.Context, data: dict[str, Any], key: str) -> None:
    _print_list(
        ctx,
        data,
        key,
        ("id", "name"),
        lambda row: {"id": str(row.get("id") or ""), "name": str(row.get("name") or "")},
    )


def _channel_cell(item: dict[str, Any], fallback: str = "") -> str:
    ch = item.get("channel")
    if isinstance(ch, dict):
        return str(ch.get("name") or ch.get("id") or fallback)
    if ch:
        return str(ch)
    return str(item.get("channel_id") or fallback)


def print_search(ctx: click.Context, data: dict[str, Any]) -> None:
    if not _want_table(ctx):
        print_json(data)
        return
    rows: list[dict[str, str]] = []
    messages = data.get("messages") or {}
    for item in messages.get("matches") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "channel": _channel_cell(item),
                "user": str(
                    item.get("username") or item.get("user_name") or item.get("user") or ""
                ),
                "ts": str(item.get("ts") or ""),
                "text": str(item.get("text") or ""),
            }
        )
    files = data.get("files") or {}
    for item in files.get("matches") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "channel": _channel_cell(item),
                "user": str(item.get("user") or ""),
                "ts": str(item.get("timestamp") or item.get("created") or item.get("ts") or ""),
                "text": str(item.get("name") or item.get("title") or ""),
            }
        )
    _print_table(rows, ("channel", "user", "ts", "text"))
    if data.get("ok") is False:
        ctx.exit(1)


def print_history(ctx: click.Context, data: dict[str, Any], channel: str) -> None:
    _print_list(
        ctx,
        data,
        "messages",
        ("channel", "user", "ts", "text"),
        lambda item: {
            "channel": _channel_cell(item, channel),
            "user": str(item.get("user_name") or item.get("username") or item.get("user") or ""),
            "ts": str(item.get("ts") or ""),
            "text": str(item.get("text") or ""),
        },
    )


def print_members(
    ctx: click.Context, client: Any, data: dict[str, Any], key: str = "members"
) -> None:
    if not _want_table(ctx):
        print_json(data)
        return
    names: dict[str, str] = {}
    for user in client.users_list().get("members") or []:
        if isinstance(user, dict) and user.get("id"):
            names[str(user["id"])] = str(user.get("name") or user.get("real_name") or "")
    rows: list[dict[str, str]] = []
    for uid in data.get(key) or []:
        sid = (
            str(uid)
            if not isinstance(uid, dict)
            else str(uid.get("id") or uid.get("slack_id") or "")
        )
        rows.append({"id": sid, "name": names.get(sid, "")})
    _print_table(rows, ("id", "name"))
    if data.get("ok") is False:
        ctx.exit(1)


def print_threads(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "threads",
        ("channel", "thread_ts", "replies"),
        lambda t: {
            "channel": _channel_cell(t),
            "thread_ts": str(t.get("thread_ts") or ""),
            "replies": str(t.get("reply_count") if t.get("reply_count") is not None else ""),
        },
    )


def _channel_row(ch: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(ch.get("id") or ""),
        "name": str(ch.get("name") or ""),
        "private": "yes" if ch.get("is_private") else "",
    }


def _user_row(user: dict[str, Any]) -> dict[str, str]:
    profile = user.get("profile")
    profile = profile if isinstance(profile, dict) else {}
    return {
        "id": str(user.get("id") or ""),
        "name": str(user.get("name") or ""),
        "real_name": str(user.get("real_name") or profile.get("real_name") or ""),
    }


def _file_row(file: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(file.get("id") or ""),
        "name": str(file.get("name") or file.get("title") or ""),
        "user": str(file.get("user") or ""),
    }


def _item_row(item: dict[str, Any]) -> dict[str, str]:
    msg = item.get("message")
    msg = msg if isinstance(msg, dict) else {}
    return {
        "channel": _channel_cell(item),
        "user": str(
            msg.get("user_name") or msg.get("username") or msg.get("user") or item.get("user") or ""
        ),
        "ts": str(msg.get("ts") or item.get("ts") or ""),
        "text": str(msg.get("text") or item.get("text") or item.get("type") or ""),
    }


def _reaction_row(item: dict[str, Any]) -> dict[str, str]:
    msg = item.get("message")
    msg = msg if isinstance(msg, dict) else {}
    return {
        "channel": _channel_cell(item),
        "user": str(item.get("user") or ""),
        "reaction": str(item.get("reaction") or ""),
        "text": str(msg.get("text") or ""),
    }


def print_channels(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "channels", ("id", "name", "private"), _channel_row)


def print_users(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "members", ("id", "name", "real_name"), _user_row)


def print_files(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "files", ("id", "name", "user"), _file_row)


def print_items(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "items", ("channel", "user", "ts", "text"), _item_row)


def print_emoji(ctx: click.Context, data: dict[str, Any]) -> None:
    if not _want_table(ctx):
        print_json(data)
        return
    raw = data.get("emoji") or {}
    rows: list[dict[str, str]] = []
    if isinstance(raw, dict):
        for name, url in raw.items():
            rows.append({"name": str(name), "url": str(url)})
    else:
        for item in raw:
            if isinstance(item, dict):
                rows.append(
                    {"name": str(item.get("name") or ""), "url": str(item.get("url") or "")}
                )
    _print_table(rows, ("name", "url"))
    if data.get("ok") is False:
        ctx.exit(1)


def print_bookmarks(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "bookmarks",
        ("id", "title", "channel"),
        lambda b: {
            "id": str(b.get("id") or ""),
            "title": str(b.get("title") or ""),
            "channel": str(b.get("channel_id") or b.get("channel") or ""),
        },
    )


def print_usergroups(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "usergroups",
        ("id", "handle", "name"),
        lambda g: {
            "id": str(g.get("id") or ""),
            "handle": str(g.get("handle") or ""),
            "name": str(g.get("name") or ""),
        },
    )


def print_bots(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_id_name(ctx, data, "bots")


def print_reactions(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "items", ("channel", "user", "reaction", "text"), _reaction_row)


def print_scheduled(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "scheduled_messages",
        ("id", "channel", "text"),
        lambda m: {
            "id": str(m.get("id") or ""),
            "channel": _channel_cell(m),
            "text": str(m.get("text") or ""),
        },
    )


def print_reminders(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "reminders",
        ("id", "text"),
        lambda r: {"id": str(r.get("id") or ""), "text": str(r.get("text") or "")},
    )


def print_comments(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "comments",
        ("id", "user", "comment"),
        lambda c: {
            "id": str(c.get("id") or ""),
            "user": str(c.get("user") or ""),
            "comment": str(c.get("comment") or c.get("text") or ""),
        },
    )


def print_calls(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_id_name(ctx, data, "calls")


def print_logins(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "logins",
        ("user_id", "ip"),
        lambda r: {"user_id": str(r.get("user_id") or ""), "ip": str(r.get("ip") or "")},
    )


def print_logs(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "logs",
        ("user_id", "service_id"),
        lambda r: {
            "user_id": str(r.get("user_id") or ""),
            "service_id": str(r.get("service_id") or ""),
        },
    )


def print_cursors(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "cursors",
        ("channel", "ts"),
        lambda r: {"channel": str(r.get("channel") or ""), "ts": str(r.get("ts") or "")},
    )


def print_teams(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_id_name(ctx, data, "teams")


def print_presence(ctx: click.Context, data: dict[str, Any]) -> None:
    if not _want_table(ctx):
        print_json(data)
        return
    raw = data.get("users")
    rows: list[dict[str, str]] = []
    if isinstance(raw, dict):
        for uid, val in raw.items():
            presence = val.get("presence") if isinstance(val, dict) else val
            rows.append({"user": str(uid), "presence": str(presence or "")})
    else:
        for item in raw or []:
            if isinstance(item, dict):
                rows.append(
                    {
                        "user": str(item.get("user_id") or item.get("user") or ""),
                        "presence": str(item.get("presence") or ""),
                    }
                )
    _print_table(rows, ("user", "presence"))
    if data.get("ok") is False:
        ctx.exit(1)
