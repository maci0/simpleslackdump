import glob as _glob
import sys
import webbrowser
from pathlib import Path
from typing import Any

import click

from ssd.api import SlackAPI
from ssd.sync import run_sync


@click.group()
@click.option("--token", envvar="SSD_TOKEN", default=None, help="Slack token override")
@click.option("--output", default="./output", show_default=True, help="Output directory")
@click.option(
    "--config", "config_path", default="./ssd.toml", show_default=True, help="Path to config file"
)
@click.option("--attachments/--no-attachments", default=None)
@click.option("--delay", default=1.0, show_default=True, help="Seconds between API calls")
@click.pass_context
def main(
    ctx: click.Context,
    token: str | None,
    output: str,
    config_path: str,
    attachments: bool | None,
    delay: float,
) -> None:
    """Dump Slack channels to JSON/Markdown. Query dumps locally with no Slack network."""
    ctx.ensure_object(dict)
    ctx.obj["token"] = token
    ctx.obj["output"] = output
    ctx.obj["config_path"] = config_path
    ctx.obj["attachments"] = attachments
    ctx.obj["delay"] = delay


@main.command()
@click.pass_context
def token(ctx: click.Context) -> None:
    """Extract Slack token from macOS desktop app."""
    from ssd.token import extract_cookie_with_validation, extract_token

    tok = extract_token()
    click.echo(tok, err=True)
    out = Path(ctx.obj["output"])
    out.mkdir(parents=True, exist_ok=True)
    token_path = out / ".token"
    token_path.write_text(tok)
    token_path.chmod(0o600)
    click.echo(f"Token saved to {token_path}", err=True)

    click.echo("Validating cookie (Chrome may lag disk; retrying up to 3x)...", err=True)
    cookie = extract_cookie_with_validation(tok)
    if cookie:
        cookie_path = out / ".cookie"
        cookie_path.write_text(cookie)
        cookie_path.chmod(0o600)
        click.echo(f"Cookie saved to {cookie_path}", err=True)
    else:
        click.echo(
            "Warning: could not extract a valid d cookie from any source. "
            "Make sure Chrome or Firefox is open and signed into Slack, "
            "then re-run ssd token.",
            err=True,
        )


def _get_token(ctx_obj: dict[str, Any]) -> str:
    from ssd.token import extract_token

    tok = ctx_obj.get("token")
    if tok:
        return tok
    token_path = Path(ctx_obj["output"]) / ".token"
    if token_path.exists():
        return token_path.read_text().strip()
    return extract_token()


def _get_cookie(ctx_obj: dict[str, Any]) -> str | None:
    cookie_path = Path(ctx_obj["output"]) / ".cookie"
    if cookie_path.exists():
        return cookie_path.read_text().strip()
    return None


def _make_api(
    ctx_obj: dict[str, Any],
    delay: float,
    cfg: Any = None,
) -> tuple[SlackAPI, str, str, bool]:
    """Return (api, workspace, token, attach). Shared setup for dump/sync/update.

    If auth fails with a saved cookie (Slack may have rotated the session),
    re-extracts and validates the cookie automatically before raising.
    """
    from slack_sdk.errors import SlackApiError

    from ssd.config import load_config

    if cfg is None:
        cfg = load_config(Path(ctx_obj["config_path"]))
    token = _get_token(ctx_obj)
    cookie = _get_cookie(ctx_obj)
    api = SlackAPI(token, delay=delay, cookie=cookie)
    try:
        workspace = api.get_workspace()
    except SlackApiError as exc:
        if exc.response.get("error") != "invalid_auth":
            raise
        # Cookie likely rotated since last ssd token run. Re-extract and retry once.
        click.echo("Auth failed with saved cookie; re-extracting from browser...", err=True)
        from ssd.token import extract_cookie_with_validation

        fresh_cookie = extract_cookie_with_validation(token)
        if not fresh_cookie:
            raise RuntimeError(
                "invalid_auth: could not obtain a valid cookie. "
                "Run 'ssd token' with Chrome or Firefox open and signed into Slack."
            ) from exc
        # Persist so the next call is warm
        out = Path(ctx_obj["output"])
        out.mkdir(parents=True, exist_ok=True)
        cookie_path = out / ".cookie"
        cookie_path.write_text(fresh_cookie)
        cookie_path.chmod(0o600)
        api = SlackAPI(token, delay=delay, cookie=fresh_cookie)
        workspace = api.get_workspace()
        cookie = fresh_cookie
    attach = ctx_obj["attachments"]
    if attach is None:
        attach = cfg.settings.attachments
    return api, workspace, token, attach


def _should_confirm() -> bool:
    return sys.stdin.isatty()


@main.command()
@click.argument("targets", nargs=-1, required=False)
@click.option("--all", "dump_all", is_flag=True, help="Dump every visible conversation")
@click.option("--dms", "dump_dms", is_flag=True, help="Dump DMs and MPIMs")
@click.option("--yes", is_flag=True, help="Skip confirmation prompts")
@click.option("--delay", default=None, type=float, help="Override global --delay")
@click.pass_context
def dump(
    ctx: click.Context,
    targets: tuple[str, ...],
    dump_all: bool,
    dump_dms: bool,
    yes: bool,
    delay: float | None,
) -> None:
    """Full history dump of channel(s)."""
    from ssd.dump import run_dump

    delay = delay if delay is not None else ctx.obj.get("delay", 1.0)
    api, workspace, token, attach = _make_api(ctx.obj, delay)
    if dump_all or dump_dms:
        convos = [c for c in api.list_conversations() if c.get("id")]
        if dump_dms and not dump_all:
            convos = [c for c in convos if c.get("is_im") or c.get("is_mpim")]
        targets = tuple(str(c["id"]) for c in convos)
        if dump_all and not yes and _should_confirm():
            click.confirm(f"Dump {len(targets)} conversations?", abort=True)
        click.echo(f"Dumping {len(targets)} conversations...")
    elif not targets:
        raise click.UsageError("Provide channel targets, --all, or --dms")
    n = len(targets)
    for i, target in enumerate(targets, 1):
        if n > 1:
            click.echo(f"Dumping {i}/{n} {target}...")
        else:
            click.echo(f"Dumping {target}...")
        run_dump(
            api,
            workspace,
            target,
            ctx.obj["output"],
            token=token,
            attachments_enabled=attach,
            refresh_workspace=(i == 1),
        )


@main.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--since", default=None, help="YYYY-MM-DD or Unix timestamp")
@click.option("--delay", default=None, type=float, help="Override global --delay")
@click.pass_context
def sync(
    ctx: click.Context, targets: tuple[str, ...], since: str | None, delay: float | None
) -> None:
    """Incremental sync of channel(s)."""
    delay = delay if delay is not None else ctx.obj.get("delay", 1.0)
    api, workspace, token, attach = _make_api(ctx.obj, delay)
    for target in targets:
        click.echo(f"Syncing {target}...")
        run_sync(
            api,
            workspace,
            target,
            ctx.obj["output"],
            since=since,
            token=token,
            attachments_enabled=attach,
        )


@main.command()
@click.argument("target")
@click.option("--oldest", default=None, help="Unix ts; default is now (no replay)")
@click.option(
    "--interval",
    default=None,
    type=float,
    help="Seconds between polls (default: max(5, --delay))",
)
@click.option("--delay", default=None, type=float, help="Override global --delay")
@click.option("--json", "as_json", is_flag=True, help="JSON object per line (default when piped)")
@click.option("--from-cursor", "from_cursor", is_flag=True, help="Start from dump .cursor")
@click.pass_context
def watch(
    ctx: click.Context,
    target: str,
    oldest: str | None,
    interval: float | None,
    delay: float | None,
    as_json: bool,
    from_cursor: bool,
) -> None:
    """Poll a channel, DM, or thread for new messages."""
    from ssd.parser import parse_target

    delay = delay if delay is not None else ctx.obj.get("delay", 1.0)
    api, workspace, _token, _attach = _make_api(ctx.obj, delay)
    parsed = parse_target(target)
    ident = parsed.channel_id or parsed.channel_name
    if not ident:
        raise click.UsageError("Provide a channel id, #name, or Slack URL")
    if from_cursor and oldest is not None:
        raise click.UsageError("Use --from-cursor or --oldest, not both")
    if from_cursor:
        from ssd.output import channel_dir, read_cursor

        cid, name = api.resolve_channel(ident)
        ident = cid
        dump_dir = channel_dir(str(ctx.obj["output"]), workspace, name, cid)
        if parsed.thread_ts:
            dump_dir = dump_dir / f"thread_{parsed.thread_ts.replace('.', '_')}"
        oldest = read_cursor(dump_dir)
        if oldest is None:
            raise click.UsageError(f"No .cursor in {dump_dir}; dump or sync first")
    try:
        for msg in api.watch_messages(
            ident, oldest=oldest, interval=interval, thread_ts=parsed.thread_ts
        ):
            _print_watch_line(msg, as_json)
    except KeyboardInterrupt:
        return


@main.command()
@click.argument("target")
@click.pass_context
def add(ctx: click.Context, target: str) -> None:
    """Add channel/thread to ssd.toml."""
    from ssd.config import add_channel, add_thread
    from ssd.parser import parse_target

    parsed = parse_target(target)
    config_path = Path(ctx.obj["config_path"])

    if parsed.thread_ts:
        add_thread(
            config_path,
            channel_id=parsed.channel_id,
            thread_ts=parsed.thread_ts,
            url=target,
        )
        click.echo(f"Added thread {parsed.thread_ts} in {parsed.channel_id}")
    else:
        api = SlackAPI(_get_token(ctx.obj), cookie=_get_cookie(ctx.obj))
        if parsed.channel_id:
            cid, name = api.resolve_channel(parsed.channel_id)
        else:
            cid, name = api.resolve_channel(parsed.channel_name)
        add_channel(
            config_path,
            id=cid,
            name=name,
            url=target if target.startswith("http") else f"#{name}",
            since=None,
        )
        click.echo(f"Added #{name} ({cid})")


@main.command()
@click.argument("target")
@click.pass_context
def remove(ctx: click.Context, target: str) -> None:
    """Remove channel/thread from ssd.toml."""
    from ssd.config import load_config, remove_entry
    from ssd.parser import parse_target

    parsed = parse_target(target)
    channel_id = parsed.channel_id
    if not channel_id and parsed.channel_name:
        # Resolve name to ID from the config — avoids API call
        cfg = load_config(Path(ctx.obj["config_path"]))
        name = parsed.channel_name.lstrip("#")
        for ch in cfg.channels:
            if ch.name == name:
                channel_id = ch.id
                break
        if not channel_id:
            click.echo(f"Not found: {parsed.channel_name}", err=True)
            return
    removed = remove_entry(Path(ctx.obj["config_path"]), channel_id, thread_ts=parsed.thread_ts)
    if removed:
        click.echo(f"Removed {channel_id}")
    else:
        click.echo(f"Not found: {channel_id}", err=True)


@main.command("list")
@click.pass_context
def list_cmd(ctx: click.Context) -> None:
    """Show tracked channels and last sync time."""
    from ssd.config import load_config
    from ssd.output import read_cursor

    cfg = load_config(Path(ctx.obj["config_path"]))
    if not cfg.channels and not cfg.threads:
        click.echo("No channels tracked. Use: ssd add <url>")
        return
    for ch in cfg.channels:
        pattern = f"*/{_glob.escape(ch.name)}_{_glob.escape(ch.id)}"
        matches = list(Path(ctx.obj["output"]).glob(pattern))
        cursor = read_cursor(matches[0]) if matches else None
        click.echo(f"  #{ch.name} ({ch.id})  last={cursor or 'never'}")
    for th in cfg.threads:
        click.echo(f"  thread {th.thread_ts} in {th.channel_id}")


@main.command()
@click.option("--delay", default=None, type=float, help="Override global --delay")
@click.pass_context
def update(ctx: click.Context, delay: float | None) -> None:
    """Sync all channels in ssd.toml."""
    from ssd.config import load_config

    delay = delay if delay is not None else ctx.obj.get("delay", 1.0)
    cfg = load_config(Path(ctx.obj["config_path"]))
    if not cfg.channels and not cfg.threads:
        click.echo("Nothing tracked. Use: ssd add <url>")
        return
    api, workspace, token, attach = _make_api(ctx.obj, delay, cfg=cfg)
    for ch in cfg.channels:
        click.echo(f"Syncing #{ch.name}...")
        ch_attach = ch.attachments if ch.attachments is not None else attach
        run_sync(
            api,
            workspace,
            ch.id,
            ctx.obj["output"],
            since=ch.since,
            token=token,
            attachments_enabled=ch_attach,
        )
    for th in cfg.threads:
        click.echo(f"Syncing thread {th.thread_ts}...")
        run_sync(
            api,
            workspace,
            th.url,
            ctx.obj["output"],
            since=None,
            token=token,
            attachments_enabled=attach,
        )


@main.command()
@click.argument("channel_dirs", nargs=-1, type=click.Path(exists=True, file_okay=False))
@click.option("--output", default="graph.html", show_default=True, help="Output HTML file path")
@click.option("--open/--no-open", "open_browser", default=None, help="Open HTML in a browser")
@click.pass_context
def graph(
    ctx: click.Context,
    channel_dirs: tuple[str, ...],
    output: str,
    open_browser: bool | None,
) -> None:
    """Generate an interactive communication graph from dumped channels.

    Without arguments, uses all channel directories under the output dir.
    Opens the HTML in a browser on a TTY unless --no-open.
    """
    from ssd.graph import build_graph, render_html

    dirs = [Path(d) for d in channel_dirs]
    if not dirs:
        out = Path(ctx.obj["output"])
        dirs = [p.parent for p in out.rglob("messages.json")]
    if not dirs:
        click.echo("No channel data found. Run 'ssd dump' first.", err=True)
        return

    data = build_graph(dirs)
    if not data["nodes"]:
        click.echo("No users found in message data.", err=True)
        return

    html = render_html(data)
    Path(output).write_text(html, encoding="utf-8")
    click.echo(f"Graph: {output}: {len(data['nodes'])} users, {len(data['links'])} connections")
    if open_browser is None:
        open_browser = sys.stdout.isatty()
    if open_browser:
        webbrowser.open(f"file://{str(Path(output).resolve())}")


def _json_pretty() -> bool:
    return sys.stdout.isatty()


def _print_watch_line(msg: dict[str, Any], as_json: bool) -> None:
    if as_json or not _json_pretty():
        import json

        click.echo(json.dumps(msg, ensure_ascii=False))
        return
    user = msg.get("user_name") or msg.get("username") or msg.get("user") or ""
    ts = msg.get("ts") or ""
    text = " ".join(str(msg.get("text") or "").split())
    click.echo(f"{user}  {ts}  {text}")


def _print_json(data: Any) -> None:
    pretty = _json_pretty()
    try:
        import orjson

        opts = orjson.OPT_INDENT_2 if pretty else 0
        click.echo(orjson.dumps(data, option=opts).decode())
    except ImportError:
        import json

        click.echo(json.dumps(data, ensure_ascii=False, indent=2 if pretty else None))
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
        _print_json(data)
        return
    rows: list[dict[str, str]] = []
    for item in data.get(key) or []:
        if isinstance(item, dict):
            rows.append(row_fn(item))
    _print_table(rows, headers)
    if data.get("ok") is False:
        ctx.exit(1)


def _channel_cell(item: dict[str, Any], fallback: str = "") -> str:
    ch = item.get("channel")
    if isinstance(ch, dict):
        return str(ch.get("name") or ch.get("id") or fallback)
    if ch:
        return str(ch)
    return str(item.get("channel_id") or fallback)


def _print_search(ctx: click.Context, data: dict[str, Any]) -> None:
    if not _want_table(ctx):
        _print_json(data)
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


def _print_history(ctx: click.Context, data: dict[str, Any], channel: str) -> None:
    if not _want_table(ctx):
        _print_json(data)
        return
    rows: list[dict[str, str]] = []
    for item in data.get("messages") or []:
        if not isinstance(item, dict):
            continue
        rows.append(
            {
                "channel": _channel_cell(item, channel),
                "user": str(
                    item.get("user_name") or item.get("username") or item.get("user") or ""
                ),
                "ts": str(item.get("ts") or ""),
                "text": str(item.get("text") or ""),
            }
        )
    _print_table(rows, ("channel", "user", "ts", "text"))
    if data.get("ok") is False:
        ctx.exit(1)


def _print_members(
    ctx: click.Context, client: Any, data: dict[str, Any], key: str = "members"
) -> None:
    if not _want_table(ctx):
        _print_json(data)
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


def _print_threads(ctx: click.Context, data: dict[str, Any]) -> None:
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
    profile = user.get("profile") if isinstance(user.get("profile"), dict) else {}
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
    msg = item.get("message") if isinstance(item.get("message"), dict) else {}
    return {
        "channel": _channel_cell(item),
        "user": str(
            msg.get("user_name") or msg.get("username") or msg.get("user") or item.get("user") or ""
        ),
        "ts": str(msg.get("ts") or item.get("ts") or ""),
        "text": str(msg.get("text") or item.get("text") or item.get("type") or ""),
    }


def _reaction_row(item: dict[str, Any]) -> dict[str, str]:
    msg = item.get("message") if isinstance(item.get("message"), dict) else {}
    return {
        "channel": _channel_cell(item),
        "user": str(item.get("user") or ""),
        "reaction": str(item.get("reaction") or ""),
        "text": str(msg.get("text") or ""),
    }


def _print_channels(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "channels", ("id", "name", "private"), _channel_row)


def _print_users(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "members", ("id", "name", "real_name"), _user_row)


def _print_files(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "files", ("id", "name", "user"), _file_row)


def _print_items(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "items", ("channel", "user", "ts", "text"), _item_row)


def _print_emoji(ctx: click.Context, data: dict[str, Any]) -> None:
    if not _want_table(ctx):
        _print_json(data)
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


def _print_bookmarks(ctx: click.Context, data: dict[str, Any]) -> None:
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


def _print_usergroups(ctx: click.Context, data: dict[str, Any]) -> None:
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


def _print_bots(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "bots",
        ("id", "name"),
        lambda b: {"id": str(b.get("id") or ""), "name": str(b.get("name") or "")},
    )


def _print_reactions(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(ctx, data, "items", ("channel", "user", "reaction", "text"), _reaction_row)


def _print_scheduled(ctx: click.Context, data: dict[str, Any]) -> None:
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


def _print_reminders(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "reminders",
        ("id", "text"),
        lambda r: {"id": str(r.get("id") or ""), "text": str(r.get("text") or "")},
    )


def _print_comments(ctx: click.Context, data: dict[str, Any]) -> None:
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


def _print_calls(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "calls",
        ("id", "name"),
        lambda c: {"id": str(c.get("id") or ""), "name": str(c.get("name") or "")},
    )


def _print_logins(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "logins",
        ("user_id", "ip"),
        lambda r: {"user_id": str(r.get("user_id") or ""), "ip": str(r.get("ip") or "")},
    )


def _print_logs(ctx: click.Context, data: dict[str, Any]) -> None:
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


def _print_cursors(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "cursors",
        ("channel", "ts"),
        lambda r: {"channel": str(r.get("channel") or ""), "ts": str(r.get("ts") or "")},
    )


def _print_teams(ctx: click.Context, data: dict[str, Any]) -> None:
    _print_list(
        ctx,
        data,
        "teams",
        ("id", "name"),
        lambda t: {"id": str(t.get("id") or ""), "name": str(t.get("name") or "")},
    )


def _print_presence(ctx: click.Context, data: dict[str, Any]) -> None:
    if not _want_table(ctx):
        _print_json(data)
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


def _client(ctx: click.Context) -> Any:
    from ssd.dumpapi import DumpClient

    path = Path(ctx.obj["output"])
    try:
        client = DumpClient(path)
    except FileNotFoundError as exc:
        raise click.UsageError(f"No dump at {path}. Run ssd dump, or pass --output.") from exc
    if not client._channels:
        click.echo(
            f"No channels under {path}. Dump first, or point --output at a workspace or export.",
            err=True,
        )
    return client


_QUERY_SECTIONS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "Messages",
        (
            "search",
            "history",
            "message",
            "replies",
            "threads",
            "cursor",
            "export",
            "reactions",
            "pins",
            "stars",
            "scheduled",
            "permalink",
        ),
    ),
    (
        "People",
        (
            "users",
            "profile",
            "identity",
            "email",
            "presence",
            "dnd",
            "usergroups",
            "usergroup-users",
        ),
    ),
    ("Channels", ("channels", "members", "convos", "bookmarks")),
    ("Files", ("files", "files-info", "remote-files", "comments", "emoji")),
    (
        "Team",
        (
            "team",
            "teams",
            "team-profile",
            "prefs",
            "external-teams",
            "auth",
            "access-logs",
            "billable",
            "integration-logs",
            "bots",
            "rtm",
        ),
    ),
    ("Extras", ("stats", "calls", "participants", "reminders")),
    ("Raw", ("api", "migration")),
)


class QueryGroup(click.Group):
    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        listed: set[str] = set()
        extras_rows: list[tuple[str, str]] = []
        sections: list[tuple[str, list[tuple[str, str]]]] = []
        for title, names in _QUERY_SECTIONS:
            rows: list[tuple[str, str]] = []
            for name in names:
                cmd = self.get_command(ctx, name)
                if cmd is None or cmd.hidden:
                    continue
                listed.add(name)
                rows.append((name, cmd.get_short_help_str(limit=88)))
            if title == "Extras":
                extras_rows = rows
            elif rows:
                sections.append((title, rows))
        for name in self.list_commands(ctx):
            if name in listed:
                continue
            cmd = self.get_command(ctx, name)
            if cmd is None or cmd.hidden:
                continue
            extras_rows.append((name, cmd.get_short_help_str(limit=88)))
        extras_at = next(
            (i for i, (title, _) in enumerate(sections) if title == "Raw"),
            len(sections),
        )
        if extras_rows:
            sections.insert(extras_at, ("Extras", extras_rows))
        for title, rows in sections:
            with formatter.section(title):
                formatter.write_dl(rows)


@main.group("query", cls=QueryGroup)
@click.option("--json", "query_json", is_flag=True, help="Print JSON even on a TTY")
@click.pass_context
def query_cmd(ctx: click.Context, query_json: bool) -> None:
    """Read local dump data. No Slack network."""
    ctx.ensure_object(dict)
    ctx.obj["query_json"] = query_json


@query_cmd.command("stats", help="Channel and message counts")
@click.pass_context
def query_stats(ctx: click.Context) -> None:
    _print_json(_client(ctx).stats())


@query_cmd.command("search", help="Search messages and files (from:/in:/has:/is:)")
@click.argument("q")
@click.option("--count", default=20, type=int)
@click.option("--page", default=None, type=int)
@click.option("--sort-dir", default="desc")
@click.pass_context
def query_search(ctx: click.Context, q: str, count: int, page: int | None, sort_dir: str) -> None:
    _print_search(
        ctx,
        _client(ctx).search_all(query=q, count=count, page=page, sort_dir=sort_dir),
    )


@query_cmd.command("history", help="conversations.history; --search filters that channel")
@click.argument("channel")
@click.option("--search", default=None)
@click.option("--limit", default=100, type=int)
@click.option("--oldest", default=None)
@click.option("--latest", default=None)
@click.option("--inclusive", is_flag=True)
@click.option("--cursor", default=None)
@click.pass_context
def query_history(
    ctx: click.Context,
    channel: str,
    search: str | None,
    limit: int,
    oldest: str | None,
    latest: str | None,
    inclusive: bool,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    kwargs: dict[str, Any] = {
        "channel": channel,
        "limit": limit,
        "oldest": oldest,
        "latest": latest,
        "inclusive": inclusive,
        "cursor": cursor,
    }
    if search:
        _print_history(ctx, client.conversations_history_search(query=search, **kwargs), channel)
        return
    _print_history(ctx, client.conversations_history(**kwargs), channel)


@query_cmd.command("cursor", help="Sync cursor ts; omit channel to list all")
@click.argument("channel", required=False)
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_cursor(
    ctx: click.Context,
    channel: str | None,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if channel:
        _print_json(client.get_cursor(channel=channel))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_cursors(ctx, client.cursors_search(**kwargs))
        return
    _print_cursors(ctx, client.cursors_list(count=count, page=page, cursor=cursor))


@query_cmd.command("channels", help="conversations.list / info; --search filters")
@click.argument("channel", required=False)
@click.option("--search", default=None)
@click.option("--exclude-archived", is_flag=True)
@click.option("--types", default=None)
@click.option("--limit", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_channels(
    ctx: click.Context,
    channel: str | None,
    search: str | None,
    exclude_archived: bool,
    types: str | None,
    limit: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if channel:
        _print_json(client.conversations_info(channel=channel))
        return
    if search:
        kwargs: dict[str, Any] = {
            "query": search,
            "exclude_archived": exclude_archived,
            "types": types,
        }
        if limit is not None:
            kwargs["limit"] = limit
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_channels(ctx, client.conversations_search(**kwargs))
        return
    kwargs = {"exclude_archived": exclude_archived, "types": types}
    if limit is not None:
        kwargs["limit"] = limit
    if cursor is not None:
        kwargs["cursor"] = cursor
    _print_channels(ctx, client.conversations_list(**kwargs))


@query_cmd.command("users", help="users.list / info; --search filters")
@click.argument("user", required=False)
@click.option("--search", default=None)
@click.option("--message-users/--no-message-users", default=True)
@click.option("--bots/--no-bots", default=True)
@click.option("--deleted/--no-deleted", default=True)
@click.option("--limit", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_users(
    ctx: click.Context,
    user: str | None,
    search: str | None,
    message_users: bool,
    bots: bool,
    deleted: bool,
    limit: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if user:
        _print_json(client.users_info(user=user))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search}
        if limit is not None:
            kwargs["limit"] = limit
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_users(ctx, client.users_search(**kwargs))
        return
    kwargs = {
        "include_message_users": message_users,
        "include_bots": bots,
        "include_deleted": deleted,
    }
    if limit is not None:
        kwargs["limit"] = limit
    if cursor is not None:
        kwargs["cursor"] = cursor
    _print_users(ctx, client.users_list(**kwargs))


@query_cmd.command("files", help="files.list / info; --search filters")
@click.argument("file", required=False)
@click.option("--search", default=None)
@click.option("--channel", default=None)
@click.option("--user", default=None)
@click.option("--types", default=None, help="Comma-separated Slack filetypes")
@click.option("--ts-from", default=None)
@click.option("--ts-to", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_files(
    ctx: click.Context,
    file: str | None,
    search: str | None,
    channel: str | None,
    user: str | None,
    types: str | None,
    ts_from: str | None,
    ts_to: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if file:
        _print_json(client.files_info(file=file))
        return
    kwargs: dict[str, Any] = {
        "channel": channel,
        "user": user,
        "types": types,
        "ts_from": ts_from,
        "ts_to": ts_to,
    }
    if count is not None:
        kwargs["count"] = count
    if page is not None:
        kwargs["page"] = page
    if cursor is not None:
        kwargs["cursor"] = cursor
    if search:
        _print_files(ctx, client.files_list_search(query=search, **kwargs))
        return
    _print_files(ctx, client.files_list(**kwargs))


@query_cmd.command("remote-files", help="files.remote.list / info; --search filters")
@click.argument("file_id", required=False)
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_remote_files(
    ctx: click.Context,
    file_id: str | None,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if search:
        kwargs: dict[str, Any] = {"query": search}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_files(ctx, client.files_remote_search(**kwargs))
        return
    if file_id:
        _print_json(client.files_remote_info(file=file_id))
        return
    _print_files(ctx, client.files_remote_list(count=count, page=page, cursor=cursor))


@query_cmd.command("message", help="One message by channel and ts")
@click.argument("channel")
@click.argument("ts")
@click.pass_context
def query_message(ctx: click.Context, channel: str, ts: str) -> None:
    _print_json(_client(ctx).get_message(channel=channel, ts=ts))


@query_cmd.command("export", help="Write messages as JSONL")
@click.argument("dest", type=click.Path())
@click.option("--channel", default=None)
@click.pass_context
def query_export(ctx: click.Context, dest: str, channel: str | None) -> None:
    n = _client(ctx).export_jsonl(dest, channel=channel)
    click.echo(str(n))


@query_cmd.command("emoji", help="emoji.list / get; --search filters")
@click.argument("name", required=False)
@click.option("--search", default=None)
@click.pass_context
def query_emoji(ctx: click.Context, name: str | None, search: str | None) -> None:
    client = _client(ctx)
    if search:
        _print_emoji(ctx, client.emoji_search(query=search))
        return
    if name:
        _print_json(client.emoji_get(name=name))
        return
    _print_emoji(ctx, client.emoji_list())


@query_cmd.command("identity", help="users.identity from auth.json")
@click.pass_context
def query_identity(ctx: click.Context) -> None:
    _print_json(_client(ctx).users_identity())


@query_cmd.command("auth", help="auth.test from auth.json")
@click.pass_context
def query_auth(ctx: click.Context) -> None:
    _print_json(_client(ctx).auth_test())


@query_cmd.command("teams", help="auth.teams.list; --search filters")
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_teams(
    ctx: click.Context,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if search:
        kwargs: dict[str, Any] = {"query": search}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_teams(ctx, client.auth_teams_search(**kwargs))
        return
    _print_teams(ctx, client.auth_teams_list(count=count, page=page, cursor=cursor))


@query_cmd.command("rtm", help="rtm.connect snapshot; --start for rtm.start")
@click.option("--start", is_flag=True)
@click.pass_context
def query_rtm(ctx: click.Context, start: bool) -> None:
    client = _client(ctx)
    if start:
        _print_json(client.rtm_start())
        return
    _print_json(client.rtm_connect())


@query_cmd.command("team-profile", help="team.profile.get; --search filters")
@click.option("--search", default=None)
@click.pass_context
def query_team_profile(ctx: click.Context, search: str | None) -> None:
    client = _client(ctx)
    if search:
        _print_json(client.team_profile_search(query=search))
        return
    _print_json(client.team_profile_get())


@query_cmd.command("prefs", help="team.preferences; --search filters")
@click.option("--search", default=None)
@click.pass_context
def query_prefs(ctx: click.Context, search: str | None) -> None:
    client = _client(ctx)
    if search:
        _print_json(client.team_preferences_search(query=search))
        return
    _print_json(client.team_preferences_list())


@query_cmd.command("external-teams", help="team.externalTeams; --search filters")
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_external_teams(
    ctx: click.Context,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if search:
        kwargs: dict[str, Any] = {"query": search}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_teams(ctx, client.team_externalTeams_search(**kwargs))
        return
    _print_teams(ctx, client.team_externalTeams_list(count=count, page=page, cursor=cursor))


@query_cmd.command("profile", help="users.profile.get")
@click.argument("user")
@click.pass_context
def query_profile(ctx: click.Context, user: str) -> None:
    _print_json(_client(ctx).users_profile_get(user=user))


@query_cmd.command("pins", help="pins.list / info; --search filters")
@click.argument("channel", required=False)
@click.argument("ts", required=False)
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_pins(
    ctx: click.Context,
    channel: str | None,
    ts: str | None,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if channel and ts:
        _print_json(client.pins_info(channel=channel, ts=ts))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search, "channel": channel}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_items(ctx, client.pins_search(**kwargs))
        return
    _print_items(ctx, client.pins_list(channel=channel, count=count, page=page, cursor=cursor))


@query_cmd.command("scheduled", help="chat.scheduledMessages; --search filters")
@click.argument("scheduled_id", required=False)
@click.option("--search", default=None)
@click.option("--channel", default=None)
@click.option("--oldest", default=None)
@click.option("--latest", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_scheduled(
    ctx: click.Context,
    scheduled_id: str | None,
    search: str | None,
    channel: str | None,
    oldest: str | None,
    latest: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if scheduled_id:
        _print_json(client.chat_scheduledMessages_info(id=scheduled_id))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search, "channel": channel}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_scheduled(ctx, client.chat_scheduledMessages_search(**kwargs))
        return
    _print_scheduled(
        ctx,
        client.chat_scheduledMessages_list(
            channel=channel, oldest=oldest, latest=latest, count=count, page=page, cursor=cursor
        ),
    )


@query_cmd.command("threads", help="Thread index; --search filters")
@click.argument("channel", required=False)
@click.argument("ts", required=False)
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_threads(
    ctx: click.Context,
    channel: str | None,
    ts: str | None,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if channel and ts:
        _print_json(client.threads_info(channel=channel, ts=ts))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search, "channel": channel}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_threads(ctx, client.threads_search(**kwargs))
        return
    _print_threads(ctx, client.threads_list(channel=channel, count=count, page=page, cursor=cursor))


@query_cmd.command("replies", help="conversations.replies; --search filters")
@click.argument("channel")
@click.argument("ts")
@click.option("--search", default=None)
@click.option("--oldest", default=None)
@click.option("--latest", default=None)
@click.option("--inclusive", is_flag=True)
@click.option("--limit", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_replies(
    ctx: click.Context,
    channel: str,
    ts: str,
    search: str | None,
    oldest: str | None,
    latest: str | None,
    inclusive: bool,
    limit: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    kwargs: dict[str, Any] = {
        "channel": channel,
        "ts": ts,
        "oldest": oldest,
        "latest": latest,
        "inclusive": inclusive,
    }
    if limit is not None:
        kwargs["limit"] = limit
    if cursor is not None:
        kwargs["cursor"] = cursor
    if search:
        _print_history(ctx, client.conversations_replies_search(query=search, **kwargs), channel)
        return
    _print_history(ctx, client.conversations_replies(**kwargs), channel)


@query_cmd.command("stars", help="stars.list / info; --search filters")
@click.argument("channel", required=False)
@click.argument("ts", required=False)
@click.option("--channel", "channel_opt", default=None)
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_stars(
    ctx: click.Context,
    channel: str | None,
    ts: str | None,
    channel_opt: str | None,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if channel and ts:
        _print_json(client.stars_info(channel=channel, ts=ts))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search, "channel": channel_opt or channel}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_items(ctx, client.stars_search(**kwargs))
        return
    _print_items(
        ctx,
        client.stars_list(channel=channel_opt or channel, count=count, page=page, cursor=cursor),
    )


@query_cmd.command("reminders", help="reminders.list / info; --search filters")
@click.argument("reminder", required=False)
@click.option("--search", default=None)
@click.option("--complete/--no-complete", default=True)
@click.option("--user", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_reminders(
    ctx: click.Context,
    reminder: str | None,
    search: str | None,
    complete: bool,
    user: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if reminder:
        _print_json(client.reminders_info(reminder=reminder))
        return
    if search:
        kwargs: dict[str, Any] = {
            "query": search,
            "include_complete": complete,
            "user": user,
        }
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_reminders(ctx, client.reminders_search(**kwargs))
        return
    _print_reminders(
        ctx,
        client.reminders_list(
            include_complete=complete, user=user, count=count, page=page, cursor=cursor
        ),
    )


@query_cmd.command("usergroups", help="usergroups.list / info; --search filters")
@click.argument("usergroup", required=False)
@click.option("--search", default=None)
@click.option("--include-disabled", is_flag=True)
@click.option("--include-count", is_flag=True)
@click.option("--no-users", is_flag=True)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_usergroups(
    ctx: click.Context,
    usergroup: str | None,
    search: str | None,
    include_disabled: bool,
    include_count: bool,
    no_users: bool,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if usergroup:
        _print_json(client.usergroups_info(usergroup=usergroup))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search, "include_disabled": include_disabled}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_usergroups(ctx, client.usergroups_search(**kwargs))
        return
    _print_usergroups(
        ctx,
        client.usergroups_list(
            include_disabled=include_disabled,
            include_count=include_count,
            include_users=not no_users,
            count=count,
            page=page,
            cursor=cursor,
        ),
    )


@query_cmd.command("presence", help="users.getPresence; --search or --all")
@click.argument("user", required=False, default=None)
@click.option("--search", default=None)
@click.option("--all", "all_users", is_flag=True)
@click.pass_context
def query_presence(
    ctx: click.Context, user: str | None, search: str | None, all_users: bool
) -> None:
    client = _client(ctx)
    if search:
        _print_presence(ctx, client.presence_search(query=search))
        return
    if all_users:
        _print_presence(ctx, {"ok": True, "users": list(client.iter_presence())})
        return
    _print_json(client.users_getPresence(user=user))


@query_cmd.command("access-logs", help="team.accessLogs; --search filters")
@click.option("--search", default=None)
@click.option("--user", default=None)
@click.option("--after", default=None)
@click.option("--before", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_access_logs(
    ctx: click.Context,
    search: str | None,
    user: str | None,
    after: str | None,
    before: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if search:
        kwargs: dict[str, Any] = {
            "query": search,
            "user": user,
            "after": after,
            "before": before,
        }
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_logins(ctx, client.team_accessLogs_search(**kwargs))
        return
    _print_logins(
        ctx,
        client.team_accessLogs(
            user=user, after=after, before=before, count=count, page=page, cursor=cursor
        ),
    )


@query_cmd.command("team", help="team.info")
@click.pass_context
def query_team(ctx: click.Context) -> None:
    _print_json(_client(ctx).team_info())


@query_cmd.command("bots", help="bots.list / info; --search filters")
@click.argument("bot", required=False)
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_bots(
    ctx: click.Context,
    bot: str | None,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if search:
        kwargs: dict[str, Any] = {"query": search}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_bots(ctx, client.bots_search(**kwargs))
        return
    if bot:
        _print_json(client.bots_info(bot=bot))
        return
    _print_bots(ctx, client.bots_list(count=count, page=page, cursor=cursor))


@query_cmd.command("members", help="conversations.members; --search filters")
@click.argument("channel")
@click.option("--search", default=None)
@click.option("--limit", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_members(
    ctx: click.Context,
    channel: str,
    search: str | None,
    limit: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    kwargs: dict[str, Any] = {"channel": channel}
    if limit is not None:
        kwargs["limit"] = limit
    if cursor is not None:
        kwargs["cursor"] = cursor
    if search:
        _print_members(ctx, client, client.conversations_members_search(query=search, **kwargs))
        return
    _print_members(ctx, client, client.conversations_members(**kwargs))


@query_cmd.command("convos", help="users.conversations; --search filters")
@click.argument("user")
@click.option("--search", default=None)
@click.option("--types", default=None)
@click.option("--exclude-archived", is_flag=True)
@click.option("--limit", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_convos(
    ctx: click.Context,
    user: str,
    search: str | None,
    types: str | None,
    exclude_archived: bool,
    limit: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    kwargs: dict[str, Any] = {
        "user": user,
        "types": types,
        "exclude_archived": exclude_archived,
    }
    if limit is not None:
        kwargs["limit"] = limit
    if cursor is not None:
        kwargs["cursor"] = cursor
    if search:
        _print_channels(ctx, client.users_conversations_search(query=search, **kwargs))
        return
    _print_channels(ctx, client.users_conversations(**kwargs))


@query_cmd.command("reactions", help="reactions.get / list; --search filters")
@click.argument("channel", required=False)
@click.argument("ts", required=False)
@click.option("--search", default=None)
@click.option("--user", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_reactions(
    ctx: click.Context,
    channel: str | None,
    ts: str | None,
    search: str | None,
    user: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if channel and ts:
        _print_json(client.reactions_get(channel=channel, timestamp=ts))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search, "channel": channel, "user": user}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_reactions(ctx, client.reactions_search(**kwargs))
        return
    _print_reactions(
        ctx,
        client.reactions_list(channel=channel, user=user, count=count, page=page, cursor=cursor),
    )


@query_cmd.command("dnd", help="dnd.info / teamInfo; --search filters")
@click.argument("user", required=False, default=None)
@click.option("--search", default=None)
@click.option("--users", default=None, help="Comma-separated user ids for dnd.teamInfo")
@click.pass_context
def query_dnd(ctx: click.Context, user: str | None, search: str | None, users: str | None) -> None:
    client = _client(ctx)
    if search:
        _print_json(client.dnd_search(query=search, users=users or user))
        return
    if users is not None:
        _print_json(client.dnd_teamInfo(users=users or None))
        return
    _print_json(client.dnd_info(user=user))


@query_cmd.command("comments", help="files.comments; --search filters")
@click.argument("file_id")
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_comments(
    ctx: click.Context,
    file_id: str,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if search:
        kwargs: dict[str, Any] = {"file": file_id, "query": search}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_comments(ctx, client.files_comments_search(**kwargs))
        return
    _print_comments(ctx, client.files_comments(file=file_id, count=count, page=page, cursor=cursor))


@query_cmd.command("email", help="users.lookupByEmail")
@click.argument("addr")
@click.pass_context
def query_email(ctx: click.Context, addr: str) -> None:
    _print_json(_client(ctx).users_lookupByEmail(email=addr))


@query_cmd.command("files-info", help="files.info")
@click.argument("file_id")
@click.pass_context
def query_files_info(ctx: click.Context, file_id: str) -> None:
    _print_json(_client(ctx).files_info(file=file_id))


@query_cmd.command("usergroup-users", help="usergroups.users; --search filters")
@click.argument("handle")
@click.option("--search", default=None)
@click.option("--limit", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_usergroup_users(
    ctx: click.Context,
    handle: str,
    search: str | None,
    limit: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    kwargs: dict[str, Any] = {"usergroup": handle}
    if limit is not None:
        kwargs["limit"] = limit
    if cursor is not None:
        kwargs["cursor"] = cursor
    if search:
        _print_members(ctx, client, client.usergroups_users_search(query=search, **kwargs), "users")
        return
    _print_members(ctx, client, client.usergroups_users(**kwargs), "users")


@query_cmd.command("calls", help="calls.list / info; --search filters")
@click.argument("call_id", required=False)
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_calls(
    ctx: click.Context,
    call_id: str | None,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if search:
        kwargs: dict[str, Any] = {"query": search}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_calls(ctx, client.calls_search(**kwargs))
        return
    if call_id:
        _print_json(client.calls_info(id=call_id))
        return
    _print_calls(ctx, client.calls_list(count=count, page=page, cursor=cursor))


@query_cmd.command("participants", help="calls.participants; --search filters")
@click.argument("call_id")
@click.option("--search", default=None)
@click.pass_context
def query_participants(ctx: click.Context, call_id: str, search: str | None) -> None:
    client = _client(ctx)
    if search:
        _print_members(
            ctx, client, client.calls_participants_search(id=call_id, query=search), "participants"
        )
        return
    _print_members(ctx, client, client.calls_participants(id=call_id), "participants")


@query_cmd.command("billable", help="team.billableInfo; --search filters")
@click.option("--search", default=None)
@click.option("--user", default=None)
@click.pass_context
def query_billable(ctx: click.Context, search: str | None, user: str | None) -> None:
    client = _client(ctx)
    if search:
        _print_json(client.team_billableInfo_search(query=search, user=user))
        return
    _print_json(client.team_billableInfo(user=user))


@query_cmd.command("integration-logs", help="team.integrationLogs; --search filters")
@click.option("--search", default=None)
@click.option("--user", default=None)
@click.option("--change-type", default=None)
@click.option("--app-id", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_integration_logs(
    ctx: click.Context,
    search: str | None,
    user: str | None,
    change_type: str | None,
    app_id: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if search:
        kwargs: dict[str, Any] = {
            "query": search,
            "user": user,
            "change_type": change_type,
            "app_id": app_id,
        }
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_logs(ctx, client.team_integrationLogs_search(**kwargs))
        return
    _print_logs(
        ctx,
        client.team_integrationLogs(
            user=user,
            change_type=change_type,
            app_id=app_id,
            count=count,
            page=page,
            cursor=cursor,
        ),
    )


@query_cmd.command("bookmarks", help="bookmarks.list / info; --search filters")
@click.argument("channel", required=False)
@click.option("--search", default=None)
@click.option("--count", default=None, type=int)
@click.option("--page", default=None, type=int)
@click.option("--cursor", default=None)
@click.pass_context
def query_bookmarks(
    ctx: click.Context,
    channel: str | None,
    search: str | None,
    count: int | None,
    page: int | None,
    cursor: str | None,
) -> None:
    client = _client(ctx)
    if channel and channel.startswith("Bk"):
        _print_json(client.bookmarks_info(bookmark=channel))
        return
    if search:
        kwargs: dict[str, Any] = {"query": search, "channel": channel}
        if count is not None:
            kwargs["count"] = count
        if page is not None:
            kwargs["page"] = page
        if cursor is not None:
            kwargs["cursor"] = cursor
        _print_bookmarks(ctx, client.bookmarks_search(**kwargs))
        return
    _print_bookmarks(
        ctx, client.bookmarks_list(channel=channel, count=count, page=page, cursor=cursor)
    )


@query_cmd.command("permalink", help="chat.getPermalink")
@click.argument("channel")
@click.argument("ts")
@click.pass_context
def query_permalink(ctx: click.Context, channel: str, ts: str) -> None:
    _print_json(_client(ctx).chat_getPermalink(channel=channel, message_ts=ts))


@query_cmd.command("api", help="Call a DumpClient method by Slack name (key=value)")
@click.argument("method")
@click.argument("args", nargs=-1)
@click.pass_context
def query_api(ctx: click.Context, method: str, args: tuple[str, ...]) -> None:
    payload: dict[str, Any] = {}
    for item in args:
        key, sep, val = item.partition("=")
        if not sep:
            raise click.UsageError(f"expected key=value, got {item}")
        payload[key] = val
    _print_json(_client(ctx).api_call(method, params=payload))


@query_cmd.command("migration", help="migration.exchange")
@click.argument("users")
@click.pass_context
def query_migration(ctx: click.Context, users: str) -> None:
    _print_json(_client(ctx).migration_exchange(users=users))
