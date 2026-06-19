from pathlib import Path
from typing import Optional

import click

from ssd.api import SlackAPI
from ssd.sync import run_sync


@click.group()
@click.option("--token", envvar="SSD_TOKEN", default=None, help="Slack token override")
@click.option("--output", default="./output", show_default=True, help="Output directory")
@click.option("--config", "config_path", default="./ssd.toml", show_default=True, help="Path to config file")
@click.option("--attachments/--no-attachments", default=None)
@click.option("--delay", default=1.0, show_default=True, help="Seconds between API calls")
@click.pass_context
def main(ctx, token, output, config_path, attachments, delay):
    ctx.ensure_object(dict)
    ctx.obj["token"] = token
    ctx.obj["output"] = output
    ctx.obj["config_path"] = config_path
    ctx.obj["attachments"] = attachments
    ctx.obj["delay"] = delay


@main.command()
@click.pass_context
def token(ctx):
    """Extract Slack token from macOS desktop app."""
    from ssd.token import extract_token, extract_cookie

    tok = extract_token()
    click.echo(tok)
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
        click.echo("Warning: could not extract d cookie (Chrome not found or not logged in). "
                   "API calls may fail for newer Slack versions.", err=True)


def _get_token(ctx_obj: dict) -> str:
    from ssd.token import extract_token
    tok = ctx_obj.get("token")
    if tok:
        return tok
    token_path = Path(ctx_obj["output"]) / ".token"
    if token_path.exists():
        return token_path.read_text().strip()
    return extract_token()


def _get_cookie(ctx_obj: dict) -> Optional[str]:
    cookie_path = Path(ctx_obj["output"]) / ".cookie"
    if cookie_path.exists():
        return cookie_path.read_text().strip()
    return None


@main.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--delay", default=None, show_default=True, type=float)
@click.pass_context
def dump(ctx, targets, delay):
    """Full history dump of channel(s)."""
    from ssd.dump import run_dump
    from ssd.config import load_config

    delay = delay if delay is not None else ctx.obj.get("delay", 1.0)
    token = _get_token(ctx.obj)
    cookie = _get_cookie(ctx.obj)
    api = SlackAPI(token, delay=delay, cookie=cookie)
    workspace = api.get_workspace()
    cfg = load_config(Path(ctx.obj["config_path"]))
    attach = ctx.obj["attachments"]
    if attach is None:
        attach = cfg.settings.attachments
    for target in targets:
        click.echo(f"Dumping {target}...")
        run_dump(api, workspace, target, ctx.obj["output"], token=token, attachments_enabled=attach)


@main.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--since", default=None, help="YYYY-MM-DD or Unix timestamp")
@click.option("--delay", default=None, show_default=True, type=float)
@click.pass_context
def sync(ctx, targets, since, delay):
    """Incremental sync of channel(s)."""
    from ssd.config import load_config

    delay = delay if delay is not None else ctx.obj.get("delay", 1.0)
    token = _get_token(ctx.obj)
    cookie = _get_cookie(ctx.obj)
    api = SlackAPI(token, delay=delay, cookie=cookie)
    workspace = api.get_workspace()
    cfg = load_config(Path(ctx.obj["config_path"]))
    attach = ctx.obj["attachments"]
    if attach is None:
        attach = cfg.settings.attachments
    for target in targets:
        click.echo(f"Syncing {target}...")
        run_sync(api, workspace, target, ctx.obj["output"], since=since, token=token, attachments_enabled=attach)


@main.command()
@click.argument("target")
@click.pass_context
def add(ctx, target):
    """Add channel/thread to ssd.toml."""
    from ssd.parser import parse_target
    from ssd.config import add_channel, add_thread

    parsed = parse_target(target)
    api = SlackAPI(_get_token(ctx.obj), cookie=_get_cookie(ctx.obj))
    workspace = api.get_workspace()
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
def remove(ctx, target):
    """Remove channel/thread from ssd.toml."""
    from ssd.parser import parse_target
    from ssd.config import remove_entry

    parsed = parse_target(target)
    channel_id = parsed.channel_id or parsed.channel_name
    removed = remove_entry(Path(ctx.obj["config_path"]), channel_id, thread_ts=parsed.thread_ts)
    if removed:
        click.echo(f"Removed {channel_id}")
    else:
        click.echo(f"Not found: {channel_id}", err=True)


@main.command("list")
@click.pass_context
def list_cmd(ctx):
    """Show tracked channels and last sync time."""
    from ssd.config import load_config
    from ssd.output import read_cursor

    cfg = load_config(Path(ctx.obj["config_path"]))
    if not cfg.channels and not cfg.threads:
        click.echo("No channels tracked. Use: ssd add <url>")
        return
    for ch in cfg.channels:
        matches = list(Path(ctx.obj["output"]).glob(f"*/{ch.name}_{ch.id}"))
        cursor = read_cursor(matches[0]) if matches else None
        click.echo(f"  #{ch.name} ({ch.id})  last={cursor or 'never'}")
    for th in cfg.threads:
        click.echo(f"  thread {th.thread_ts} in {th.channel_id}")


@main.command()
@click.option("--delay", default=None, show_default=True, type=float)
@click.pass_context
def update(ctx, delay):
    """Sync all channels in ssd.toml."""
    from ssd.config import load_config

    delay = delay if delay is not None else ctx.obj.get("delay", 1.0)
    cfg = load_config(Path(ctx.obj["config_path"]))
    if not cfg.channels and not cfg.threads:
        click.echo("Nothing tracked. Use: ssd add <url>")
        return
    token = _get_token(ctx.obj)
    cookie = _get_cookie(ctx.obj)
    api = SlackAPI(token, delay=delay, cookie=cookie)
    workspace = api.get_workspace()
    attach = ctx.obj["attachments"]
    if attach is None:
        attach = cfg.settings.attachments
    for ch in cfg.channels:
        click.echo(f"Syncing #{ch.name}...")
        run_sync(api, workspace, ch.id, ctx.obj["output"], since=ch.since, token=token, attachments_enabled=attach)
    for th in cfg.threads:
        click.echo(f"Syncing thread {th.thread_ts}...")
        run_sync(api, workspace, th.url, ctx.obj["output"], since=None, token=token, attachments_enabled=attach)
