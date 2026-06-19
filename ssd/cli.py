import click


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
    from ssd.token import extract_token
    from pathlib import Path

    tok = extract_token()
    click.echo(tok)
    token_path = Path(ctx.obj["output"]) / ".token"
    token_path.parent.mkdir(parents=True, exist_ok=True)
    token_path.write_text(tok)
    click.echo(f"Token saved to {token_path}", err=True)


@main.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--delay", default=1.0, show_default=True)
@click.pass_context
def dump(ctx, targets, delay):
    """Full history dump of channel(s)."""
    from ssd.token import extract_token
    from ssd.api import SlackAPI
    from ssd.dump import run_dump

    token = ctx.obj["token"] or _load_token(ctx.obj["output"])
    if not token:
        token = extract_token()
    api = SlackAPI(token, delay=delay)
    workspace = api.get_workspace()
    for target in targets:
        click.echo(f"Dumping {target}...")
        run_dump(api, workspace, target, ctx.obj["output"])


def _load_token(output_root: str) -> str | None:
    from pathlib import Path
    p = Path(output_root) / ".token"
    if p.exists():
        return p.read_text().strip()
    return None


@main.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--since", default=None, help="YYYY-MM-DD or Unix timestamp")
@click.option("--delay", default=1.0, show_default=True)
@click.pass_context
def sync(ctx, targets, since, delay):
    """Incremental sync of channel(s)."""
    from ssd.token import extract_token
    from ssd.api import SlackAPI
    from ssd.sync import run_sync

    token = ctx.obj["token"] or _load_token(ctx.obj["output"])
    if not token:
        token = extract_token()
    api = SlackAPI(token, delay=delay)
    workspace = api.get_workspace()
    for target in targets:
        click.echo(f"Syncing {target}...")
        run_sync(api, workspace, target, ctx.obj["output"], since=since)


@main.command()
@click.argument("target")
@click.pass_context
def add(ctx, target):
    """Add channel/thread to ssd.toml."""
    click.echo("add: not yet implemented")


@main.command()
@click.argument("target")
@click.pass_context
def remove(ctx, target):
    """Remove channel/thread from ssd.toml."""
    click.echo("remove: not yet implemented")


@main.command("list")
@click.pass_context
def list_cmd(ctx):
    """Show tracked channels and last sync time."""
    click.echo("list: not yet implemented")


@main.command()
@click.pass_context
def update(ctx):
    """Sync all channels in ssd.toml."""
    click.echo("update: not yet implemented")
