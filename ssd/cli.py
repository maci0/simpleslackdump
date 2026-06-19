import click


@click.group()
@click.option("--token", envvar="SSD_TOKEN", default=None, help="Slack token override")
@click.option("--output", default="./output", show_default=True, help="Output directory")
@click.option("--config", "config_path", default="./ssd.toml", show_default=True)
@click.option("--attachments/--no-attachments", default=None)
@click.pass_context
def main(ctx, token, output, config_path, attachments):
    ctx.ensure_object(dict)
    ctx.obj["token"] = token
    ctx.obj["output"] = output
    ctx.obj["config_path"] = config_path
    ctx.obj["attachments"] = attachments


@main.command()
@click.pass_context
def token(ctx):
    """Extract Slack token from macOS desktop app."""
    click.echo("token: not yet implemented")


@main.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--delay", default=1.0, show_default=True)
@click.pass_context
def dump(ctx, targets, delay):
    """Full history dump of channel(s)."""
    click.echo("dump: not yet implemented")


@main.command()
@click.argument("targets", nargs=-1, required=True)
@click.option("--since", default=None, help="YYYY-MM-DD or Unix timestamp")
@click.option("--delay", default=1.0, show_default=True)
@click.pass_context
def sync(ctx, targets, since, delay):
    """Incremental sync of channel(s)."""
    click.echo("sync: not yet implemented")


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
