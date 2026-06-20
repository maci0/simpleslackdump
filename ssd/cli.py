import glob as _glob
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
    from ssd.token import extract_cookie, extract_token

    tok = extract_token()
    click.echo(tok, err=True)
    out = Path(ctx.obj["output"])
    out.mkdir(parents=True, exist_ok=True)
    token_path = out / ".token"
    token_path.write_text(tok)
    token_path.chmod(0o600)
    click.echo(f"Token saved to {token_path}", err=True)

    cookie = extract_cookie()
    if cookie:
        cookie_path = out / ".cookie"
        cookie_path.write_text(cookie)
        cookie_path.chmod(0o600)
        click.echo(f"Cookie saved to {cookie_path}", err=True)
    else:
        click.echo(
            "Warning: could not extract d cookie from any source "
            "(Slack Cookies file, Firefox, Chrome). "
            "Make sure at least one browser is open and signed into Slack, "
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
    """Return (api, workspace, token, attach). Shared setup for dump/sync/update."""
    from ssd.config import load_config

    if cfg is None:
        cfg = load_config(Path(ctx_obj["config_path"]))
    token = _get_token(ctx_obj)
    cookie = _get_cookie(ctx_obj)
    api = SlackAPI(token, delay=delay, cookie=cookie)
    workspace = api.get_workspace()
    attach = ctx_obj["attachments"]
    if attach is None:
        attach = cfg.settings.attachments
    return api, workspace, token, attach


@main.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--delay", default=None, type=float, help="Override global --delay")
@click.pass_context
def dump(ctx: click.Context, targets: tuple[str, ...], delay: float | None) -> None:
    """Full history dump of channel(s)."""
    from ssd.dump import run_dump

    delay = delay if delay is not None else ctx.obj.get("delay", 1.0)
    api, workspace, token, attach = _make_api(ctx.obj, delay)
    for target in targets:
        click.echo(f"Dumping {target}...")
        run_dump(api, workspace, target, ctx.obj["output"], token=token, attachments_enabled=attach)


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
@click.pass_context
def graph(ctx: click.Context, channel_dirs: tuple[str, ...], output: str) -> None:
    """Generate an interactive communication graph from dumped channels.

    Without arguments, uses all channel directories under the output dir.
    Opens the resulting HTML file in a browser.
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
    click.echo(f"Graph: {output} — {len(data['nodes'])} users, {len(data['links'])} connections")
    webbrowser.open(f"file://{str(Path(output).resolve())}")
