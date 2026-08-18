"""Click entrypoint (`ssd` console script) and command wiring."""

import os
import sys
import webbrowser
from pathlib import Path
from typing import Any

import click

from ssd.api import SlackAPI
from ssd.cli_format import print_watch_line
from ssd.cli_query import query_cmd
from ssd.dump import run_dump
from ssd.sync import run_sync


def _write_secret(path: Path, content: str) -> None:
    """Write credentials with mode 0600 from creation (no world-readable window)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write(content)
    # Re-assert in case the path already existed with looser permissions.
    os.chmod(path, 0o600)


def _token_path(output: Path, token_file: str | None) -> Path:
    """Resolve token path under output; reject absolute/parent/multi-segment names.

    ``settings.token_file`` is a filename inside output_dir. Absolute paths and
    ``..`` segments would otherwise let a crafted ssd.toml write or read
    credentials outside the dump tree (``Path(output) / "/etc/..."`` replaces
    the base entirely).
    """
    name = str(token_file or ".token")
    part = Path(name)
    if part.is_absolute() or len(part.parts) != 1 or part.parts[0] in (".", "..") or "\x00" in name:
        raise click.ClickException(
            f"settings.token_file must be a single filename inside output_dir (got {name!r})"
        )
    return output / part


@click.group()
@click.option("--token", envvar="SSD_TOKEN", default=None, help="Slack token override")
@click.option(
    "--output",
    default=None,
    help="Output directory (default: ./output or settings.output_dir)",
)
@click.option(
    "--config", "config_path", default="./ssd.toml", show_default=True, help="Path to config file"
)
@click.option("--attachments/--no-attachments", default=None)
@click.option(
    "--delay",
    default=1.0,
    show_default=True,
    help="Seconds between paginated fetches and sync per-thread polls",
)
@click.pass_context
def main(
    ctx: click.Context,
    token: str | None,
    output: str | None,
    config_path: str,
    attachments: bool | None,
    delay: float,
) -> None:
    """Dump Slack channels to JSON/Markdown. Query dumps locally with no Slack network."""
    from ssd.config import load_config

    ctx.ensure_object(dict)
    cfg = load_config(Path(config_path))
    ctx.obj["token"] = token
    ctx.obj["output"] = output if output is not None else cfg.settings.output_dir
    ctx.obj["token_file"] = cfg.settings.token_file
    ctx.obj["config_path"] = config_path
    ctx.obj["attachments"] = attachments
    ctx.obj["delay"] = delay
    ctx.obj["config"] = cfg


@main.command()
@click.pass_context
def token(ctx: click.Context) -> None:
    """Extract Slack token and cookie from the macOS desktop app (or browser)."""
    from ssd.token import extract_cookie_with_validation, extract_token

    tok = extract_token()
    click.echo(tok, err=True)
    out = Path(ctx.obj["output"])
    out.mkdir(parents=True, exist_ok=True)
    token_path = _token_path(out, ctx.obj.get("token_file"))
    _write_secret(token_path, tok)
    click.echo(f"Token saved to {token_path}", err=True)

    click.echo("Validating cookie (Chrome may lag disk; retrying up to 3x)...", err=True)
    cookie = extract_cookie_with_validation(tok)
    if cookie:
        cookie_path = out / ".cookie"
        _write_secret(cookie_path, cookie)
        click.echo(f"Cookie saved to {cookie_path}", err=True)
    else:
        from ssd.token import missing_optional_extras

        hint = ""
        missing = missing_optional_extras()
        if "chrome" in missing:
            hint = " Chrome cookie decryption needs the chrome extra (uv sync --extra chrome)."
        click.echo(
            "Warning: could not extract a valid d cookie from any source. "
            "Make sure Chrome or Firefox is open and signed into Slack, "
            f"then re-run ssd token.{hint}",
            err=True,
        )


def _get_token(ctx_obj: dict[str, Any]) -> str:
    from ssd.token import extract_token

    tok = ctx_obj.get("token")
    if isinstance(tok, str) and tok:
        return tok
    token_path = _token_path(Path(ctx_obj["output"]), ctx_obj.get("token_file"))
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
        _write_secret(out / ".cookie", fresh_cookie)
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
            print_watch_line(msg, as_json)
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
        assert parsed.channel_id is not None  # thread URLs always carry a channel id
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
            assert parsed.channel_name is not None  # parse_target always sets id or name
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
        # Resolve name to ID from the config (avoids API call)
        cfg = load_config(Path(ctx.obj["config_path"]))
        name = parsed.channel_name.lstrip("#")
        for ch in cfg.channels:
            if ch.name == name:
                channel_id = ch.id
                break
        if not channel_id:
            click.echo(f"Not found: {parsed.channel_name}", err=True)
            return
    assert channel_id is not None  # guaranteed: either parsed or found in config above
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
    from ssd.dumpload import discover
    from ssd.output import read_cursor

    cfg = load_config(Path(ctx.obj["config_path"]))
    if not cfg.channels and not cfg.threads:
        click.echo("No channels tracked. Use: ssd add <url>")
        return
    # Match by channel id so a Slack rename that left {old-name}_{id} still shows last=.
    dumps = discover(Path(ctx.obj["output"]))
    for ch in cfg.channels:
        found = dumps.get(ch.id)
        cursor = read_cursor(found.path) if found is not None else None
        click.echo(f"  #{ch.name} ({ch.id})  last={cursor or 'never'}")
    for th in cfg.threads:
        found = dumps.get(th.channel_id)
        cursor = None
        if found is not None:
            thread_dir = found.path / f"thread_{th.thread_ts.replace('.', '_')}"
            cursor = read_cursor(thread_dir)
        click.echo(f"  thread {th.thread_ts} in {th.channel_id}  last={cursor or 'never'}")


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
    from ssd.dumpload import discover
    from ssd.graph import build_graph, render_html

    dirs = [Path(d) for d in channel_dirs]
    if not dirs:
        # discover includes thread-only channel dirs (no messages.json yet).
        dirs = [ch.path for ch in discover(Path(ctx.obj["output"])).values()]
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
        webbrowser.open(f"file://{Path(output).resolve()!s}")


main.add_command(query_cmd)
