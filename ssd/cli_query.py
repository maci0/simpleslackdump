"""Click 'query' subcommand group: read local dump data without network.

Kept separate from ``ssd.cli`` so the main entrypoint module is not dominated
by 40+ query subcommands. Register into the main group with::

    from ssd.cli_query import query_cmd
    main.add_command(query_cmd)
"""

from pathlib import Path
from typing import Any

import click

from ssd.cli_format import (
    print_bookmarks,
    print_bots,
    print_calls,
    print_channels,
    print_comments,
    print_cursors,
    print_emoji,
    print_files,
    print_history,
    print_items,
    print_json,
    print_logins,
    print_logs,
    print_members,
    print_presence,
    print_reactions,
    print_reminders,
    print_scheduled,
    print_search,
    print_teams,
    print_threads,
    print_usergroups,
    print_users,
)


def _client(ctx: click.Context) -> Any:
    from ssd.dumpapi import DumpClient

    path = Path(ctx.obj["output"])
    try:
        client = DumpClient(path)
    except FileNotFoundError as exc:
        raise click.UsageError(f"No dump at {path}. Run ssd dump, or pass --output.") from exc
    if not client.has_channels:
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


@click.group("query", cls=QueryGroup)
@click.option("--json", "query_json", is_flag=True, help="Print JSON even on a TTY")
@click.pass_context
def query_cmd(ctx: click.Context, query_json: bool) -> None:
    """Read local dump data. No Slack network."""
    ctx.ensure_object(dict)
    ctx.obj["query_json"] = query_json


@query_cmd.command("stats", help="Channel and message counts")
@click.pass_context
def query_stats(ctx: click.Context) -> None:
    print_json(_client(ctx).stats())


@query_cmd.command("search", help="Search messages and files (from:/in:/has:/is:)")
@click.argument("q")
@click.option("--count", default=20, type=int)
@click.option("--page", default=None, type=int)
@click.option("--sort-dir", default="desc")
@click.pass_context
def query_search(ctx: click.Context, q: str, count: int, page: int | None, sort_dir: str) -> None:
    print_search(
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
        print_history(ctx, client.conversations_history_search(query=search, **kwargs), channel)
        return
    print_history(ctx, client.conversations_history(**kwargs), channel)


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
        print_json(client.get_cursor(channel=channel))
        return
    if search:
        result = client.cursors_search(query=search, count=count, page=page, cursor=cursor)
        print_cursors(ctx, result)
        return
    print_cursors(ctx, client.cursors_list(count=count, page=page, cursor=cursor))


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
        print_json(client.conversations_info(channel=channel))
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
        print_channels(ctx, client.conversations_search(**kwargs))
        return
    kwargs = {"exclude_archived": exclude_archived, "types": types}
    if limit is not None:
        kwargs["limit"] = limit
    if cursor is not None:
        kwargs["cursor"] = cursor
    print_channels(ctx, client.conversations_list(**kwargs))


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
        print_json(client.users_info(user=user))
        return
    if search:
        kwargs: dict[str, Any] = {
            "query": search,
            "include_message_users": message_users,
            "include_bots": bots,
            "include_deleted": deleted,
        }
        if limit is not None:
            kwargs["limit"] = limit
        if cursor is not None:
            kwargs["cursor"] = cursor
        print_users(ctx, client.users_search(**kwargs))
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
    print_users(ctx, client.users_list(**kwargs))


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
        print_json(client.files_info(file=file))
        return
    kwargs: dict[str, Any] = {
        "channel": channel,
        "user": user,
        "types": types,
        "ts_from": ts_from,
        "ts_to": ts_to,
        "count": count,
        "page": page,
        "cursor": cursor,
    }
    if search:
        print_files(ctx, client.files_list_search(query=search, **kwargs))
        return
    print_files(ctx, client.files_list(**kwargs))


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
    if file_id:
        print_json(client.files_remote_info(file=file_id))
        return
    if search:
        print_files(
            ctx,
            client.files_remote_search(query=search, count=count, page=page, cursor=cursor),
        )
        return
    print_files(ctx, client.files_remote_list(count=count, page=page, cursor=cursor))


@query_cmd.command("message", help="One message by channel and ts")
@click.argument("channel")
@click.argument("ts")
@click.pass_context
def query_message(ctx: click.Context, channel: str, ts: str) -> None:
    print_json(_client(ctx).get_message(channel=channel, ts=ts))


@query_cmd.command("export", help="Write messages as JSONL")
@click.argument("path", type=click.Path())
@click.option("--channel", default=None)
@click.pass_context
def query_export(ctx: click.Context, path: str, channel: str | None) -> None:
    n = _client(ctx).export_jsonl(path, channel=channel)
    click.echo(str(n))


@query_cmd.command("emoji", help="emoji.list / get; --search filters")
@click.argument("name", required=False)
@click.option("--search", default=None)
@click.pass_context
def query_emoji(ctx: click.Context, name: str | None, search: str | None) -> None:
    client = _client(ctx)
    if name:
        print_json(client.emoji_get(name=name))
        return
    if search:
        print_emoji(ctx, client.emoji_search(query=search))
        return
    print_emoji(ctx, client.emoji_list())


@query_cmd.command("identity", help="users.identity from auth.json")
@click.pass_context
def query_identity(ctx: click.Context) -> None:
    print_json(_client(ctx).users_identity())


@query_cmd.command("auth", help="auth.test from auth.json")
@click.pass_context
def query_auth(ctx: click.Context) -> None:
    print_json(_client(ctx).auth_test())


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
        result = client.auth_teams_search(query=search, count=count, page=page, cursor=cursor)
        print_teams(ctx, result)
        return
    print_teams(ctx, client.auth_teams_list(count=count, page=page, cursor=cursor))


@query_cmd.command("rtm", help="rtm.connect snapshot; --start for rtm.start")
@click.option("--start", is_flag=True)
@click.pass_context
def query_rtm(ctx: click.Context, start: bool) -> None:
    client = _client(ctx)
    if start:
        print_json(client.rtm_start())
        return
    print_json(client.rtm_connect())


@query_cmd.command("team-profile", help="team.profile.get; --search filters")
@click.option("--search", default=None)
@click.pass_context
def query_team_profile(ctx: click.Context, search: str | None) -> None:
    client = _client(ctx)
    if search:
        print_json(client.team_profile_search(query=search))
        return
    print_json(client.team_profile_get())


@query_cmd.command("prefs", help="team.preferences; --search filters")
@click.option("--search", default=None)
@click.pass_context
def query_prefs(ctx: click.Context, search: str | None) -> None:
    client = _client(ctx)
    if search:
        print_json(client.team_preferences_search(query=search))
        return
    print_json(client.team_preferences_list())


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
        result = client.team_externalTeams_search(
            query=search, count=count, page=page, cursor=cursor
        )
        print_teams(ctx, result)
        return
    print_teams(ctx, client.team_externalTeams_list(count=count, page=page, cursor=cursor))


@query_cmd.command("profile", help="users.profile.get")
@click.argument("user")
@click.pass_context
def query_profile(ctx: click.Context, user: str) -> None:
    print_json(_client(ctx).users_profile_get(user=user))


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
        print_json(client.pins_info(channel=channel, ts=ts))
        return
    if search:
        print_items(
            ctx,
            client.pins_search(
                query=search, channel=channel, count=count, page=page, cursor=cursor
            ),
        )
        return
    print_items(ctx, client.pins_list(channel=channel, count=count, page=page, cursor=cursor))


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
        print_json(client.chat_scheduledMessages_info(id=scheduled_id))
        return
    if search:
        print_scheduled(
            ctx,
            client.chat_scheduledMessages_search(
                query=search,
                channel=channel,
                oldest=oldest,
                latest=latest,
                count=count,
                page=page,
                cursor=cursor,
            ),
        )
        return
    print_scheduled(
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
        print_json(client.threads_info(channel=channel, ts=ts))
        return
    if search:
        print_threads(
            ctx,
            client.threads_search(
                query=search, channel=channel, count=count, page=page, cursor=cursor
            ),
        )
        return
    print_threads(ctx, client.threads_list(channel=channel, count=count, page=page, cursor=cursor))


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
        print_history(ctx, client.conversations_replies_search(query=search, **kwargs), channel)
        return
    print_history(ctx, client.conversations_replies(**kwargs), channel)


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
        print_json(client.stars_info(channel=channel, ts=ts))
        return
    if search:
        print_items(
            ctx,
            client.stars_search(
                query=search, channel=channel_opt or channel, count=count, page=page, cursor=cursor
            ),
        )
        return
    print_items(
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
        print_json(client.reminders_info(reminder=reminder))
        return
    if search:
        print_reminders(
            ctx,
            client.reminders_search(
                query=search,
                include_complete=complete,
                user=user,
                count=count,
                page=page,
                cursor=cursor,
            ),
        )
        return
    print_reminders(
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
        print_json(client.usergroups_info(usergroup=usergroup))
        return
    if search:
        kwargs: dict[str, Any] = {
            "query": search,
            "include_disabled": include_disabled,
            "include_count": include_count,
            "include_users": not no_users,
            "count": count,
            "page": page,
            "cursor": cursor,
        }
        print_usergroups(ctx, client.usergroups_search(**kwargs))
        return
    print_usergroups(
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
    if user:
        print_json(client.users_getPresence(user=user))
        return
    if search:
        print_presence(ctx, client.presence_search(query=search))
        return
    if all_users:
        print_presence(ctx, {"ok": True, "users": list(client.iter_presence())})
        return
    # No args: presence for the auth user from auth.json
    print_json(client.users_getPresence())


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
            "count": count,
            "page": page,
            "cursor": cursor,
        }
        print_logins(ctx, client.team_accessLogs_search(**kwargs))
        return
    print_logins(
        ctx,
        client.team_accessLogs(
            user=user, after=after, before=before, count=count, page=page, cursor=cursor
        ),
    )


@query_cmd.command("team", help="team.info")
@click.pass_context
def query_team(ctx: click.Context) -> None:
    print_json(_client(ctx).team_info())


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
    if bot:
        print_json(client.bots_info(bot=bot))
        return
    if search:
        print_bots(
            ctx,
            client.bots_search(query=search, count=count, page=page, cursor=cursor),
        )
        return
    print_bots(ctx, client.bots_list(count=count, page=page, cursor=cursor))


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
        print_members(ctx, client, client.conversations_members_search(query=search, **kwargs))
        return
    print_members(ctx, client, client.conversations_members(**kwargs))


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
        print_channels(ctx, client.users_conversations_search(query=search, **kwargs))
        return
    print_channels(ctx, client.users_conversations(**kwargs))


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
        print_json(client.reactions_get(channel=channel, timestamp=ts))
        return
    if search:
        print_reactions(
            ctx,
            client.reactions_search(
                query=search, channel=channel, user=user, count=count, page=page, cursor=cursor
            ),
        )
        return
    print_reactions(
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
        print_json(client.dnd_search(query=search, users=users or user))
        return
    if users is not None:
        print_json(client.dnd_teamInfo(users=users or None))
        return
    print_json(client.dnd_info(user=user))


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
        print_comments(
            ctx,
            client.files_comments_search(
                file=file_id, query=search, count=count, page=page, cursor=cursor
            ),
        )
        return
    print_comments(ctx, client.files_comments(file=file_id, count=count, page=page, cursor=cursor))


@query_cmd.command("email", help="users.lookupByEmail")
@click.argument("addr")
@click.pass_context
def query_email(ctx: click.Context, addr: str) -> None:
    print_json(_client(ctx).users_lookupByEmail(email=addr))


@query_cmd.command("files-info", help="files.info")
@click.argument("file_id")
@click.pass_context
def query_files_info(ctx: click.Context, file_id: str) -> None:
    print_json(_client(ctx).files_info(file=file_id))


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
        print_members(ctx, client, client.usergroups_users_search(query=search, **kwargs), "users")
        return
    print_members(ctx, client, client.usergroups_users(**kwargs), "users")


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
    if call_id:
        print_json(client.calls_info(id=call_id))
        return
    if search:
        print_calls(
            ctx,
            client.calls_search(query=search, count=count, page=page, cursor=cursor),
        )
        return
    print_calls(ctx, client.calls_list(count=count, page=page, cursor=cursor))


@query_cmd.command("participants", help="calls.participants; --search filters")
@click.argument("call_id")
@click.option("--search", default=None)
@click.pass_context
def query_participants(ctx: click.Context, call_id: str, search: str | None) -> None:
    client = _client(ctx)
    if search:
        print_members(
            ctx, client, client.calls_participants_search(id=call_id, query=search), "participants"
        )
        return
    print_members(ctx, client, client.calls_participants(id=call_id), "participants")


@query_cmd.command("billable", help="team.billableInfo; --search filters")
@click.option("--search", default=None)
@click.option("--user", default=None)
@click.pass_context
def query_billable(ctx: click.Context, search: str | None, user: str | None) -> None:
    client = _client(ctx)
    if search:
        print_json(client.team_billableInfo_search(query=search, user=user))
        return
    print_json(client.team_billableInfo(user=user))


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
        print_logs(
            ctx,
            client.team_integrationLogs_search(
                query=search,
                user=user,
                change_type=change_type,
                app_id=app_id,
                count=count,
                page=page,
                cursor=cursor,
            ),
        )
        return
    print_logs(
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
        print_json(client.bookmarks_info(bookmark=channel))
        return
    if search:
        print_bookmarks(
            ctx,
            client.bookmarks_search(
                query=search, channel=channel, count=count, page=page, cursor=cursor
            ),
        )
        return
    print_bookmarks(
        ctx, client.bookmarks_list(channel=channel, count=count, page=page, cursor=cursor)
    )


@query_cmd.command("permalink", help="chat.getPermalink")
@click.argument("channel")
@click.argument("ts")
@click.pass_context
def query_permalink(ctx: click.Context, channel: str, ts: str) -> None:
    print_json(_client(ctx).chat_getPermalink(channel=channel, message_ts=ts))


def _coerce_api_arg(val: str) -> Any:
    """Map CLI key=value strings to bool/int/None; leave ids and Slack ts as str."""
    low = val.lower()
    if low in {"true", "yes"}:
        return True
    if low in {"false", "no"}:
        return False
    if low in {"null", "none"}:
        return None
    if val.isdigit() or (val.startswith("-") and val[1:].isdigit()):
        return int(val)
    return val


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
        payload[key] = _coerce_api_arg(val)
    print_json(_client(ctx).api_call(method, params=payload))


@query_cmd.command("migration", help="migration.exchange")
@click.argument("users")
@click.pass_context
def query_migration(ctx: click.Context, users: str) -> None:
    print_json(_client(ctx).migration_exchange(users=users))
