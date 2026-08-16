# ssd: simpleslackdump

Dump Slack channels and threads to JSON and Markdown. No OAuth setup. No bot token. No Slack app to register. Extracts credentials directly from the running Slack desktop app.

## What it does

- Full channel dump or incremental sync (cursor-based, deduplicating)
- Dump every visible conversation (`--all`) or only DMs/MPIMs (`--dms`)
- Threads and replies captured in full, merged correctly on re-sync
- `@user` mentions resolved to display names
- JSON (all metadata) + Markdown (readable) output per channel
- Workspace and per-channel sidecar JSON (users, emoji, pins, files, …)
- File attachment download with skip-if-already-downloaded logic
- `ssd.toml` config to track multiple channels and threads, synced with one command
- Query dumps locally: `ssd query` and `DumpClient` (Slack-shaped, no network)
- Communication graph export (HTML, opens in browser)
- Works with Enterprise Grid workspaces

## Requirements

| Requirement | Why |
|---|---|
| macOS | `ssd token` / live dump read Slack's local app data |
| [Slack desktop app](https://slack.com/downloads/mac), signed in | Source of the `xoxc-` API token (dump only) |
| Python 3.11+ | Runtime |
| [uv](https://docs.astral.sh/uv/) | Dependency/tool management |

`ssd query` and `DumpClient` run on any OS that can read the dump (or an official Slack export). No token needed for query.

Chrome or Firefox is needed for cookie extraction on newer Slack. Older Slack versions store the cookie in plaintext in Slack's own Cookies file, so no browser is required in that case.

## Install

```bash
uv tool install git+https://github.com/maci0/simpleslackdump
ssd --help
```

Or to hack on it:

```bash
git clone https://github.com/maci0/simpleslackdump
cd simpleslackdump
uv sync
uv run ssd --help
```

## Quick start

```bash
# Step 1: extract credentials (run once, re-run if you get invalid_auth)
ssd token

# Step 2: dump a channel (paste the Slack URL directly)
ssd dump https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# Also works with channel name or bare ID
ssd dump "#general"
ssd dump C0XXXXXXXXX

# Multiple targets in one call
ssd dump "#general" "#random" C0XXXXXXXXX

# Every conversation the token can see (channels, groups, DMs, MPIMs)
ssd dump --all

# Direct messages and MPIMs only
ssd dump --dms
```

Output in `./output/<workspace>/<channel_name>_<channel_id>/`:

```
messages.json     # structured: messages, threads, reactions, file metadata
messages.md       # readable: @mentions resolved to names, timestamps in UTC
.cursor           # last synced timestamp, used by ssd sync
channel.json      # conversations.info snapshot
members.json      # roster
pins.json bookmarks.json reactions.json files.json calls.json threads.json stats.json
```

Workspace sidecars land next to the channel dirs. Written once per workspace, reused by `ssd query`:

```
auth.json users.json conversations.json emoji.json emoji_categories.json
usergroups.json stars.json reminders.json dnd.json team.json team_profile.json
team_preferences.json scheduled_messages.json files.json remote_files.json
presence.json billable_info.json integration_logs.json access_logs.json
external_teams.json teams.json bots.json
```

Progress output:

```
  #general (C0XXXXXXXXX) -> output/myworkspace/general_C0XXXXXXXXX
  fetched 879 messages in 7.3s (120 msg/s)
  879 messages | 175 threads | 1446 replies | 102.5s total (9 msg/s)
```

## Incremental sync

```bash
# Fetch only messages since last run
ssd sync https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# Fetch from a specific date
ssd sync "#general" --since 2024-06-01

# Unix timestamp also works
ssd sync "#general" --since 1717200000

# Multiple targets
ssd sync "#general" "#random"
```

`--since` acts as a floor: messages older than this date are never re-fetched, but the cursor still advances normally as new messages arrive. If both a cursor and `--since` are set, the later of the two is used.

New messages merge into existing `messages.json`: no duplicates, no overwrites. New replies to older messages are also picked up (each known thread is polled for replies newer than the last stored reply). Note: `ssd sync` on a channel also polls all known threads for new replies, which may make syncs slower on channels with many active threads.

## Track channels with ssd.toml

```bash
# Add a channel
ssd add https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# Add a thread (syncs only replies in that thread)
ssd add "https://yourworkspace.slack.com/archives/C0XXXXXXXXX/p1234567890123456"

# Show tracked channels and when they were last synced
ssd list

# Sync all tracked channels and threads in one shot
ssd update
```

`ssd.toml` (auto-managed by `ssd add` / `ssd remove`):

```toml
[settings]
attachments = false     # set to true to download files by default
output_dir = "./output" # where channel dirs are written
token_file = ".token"   # token filename inside output_dir

[[channels]]
id = "C0XXXXXXXXX"
name = "general"
url = "https://yourworkspace.slack.com/archives/C0XXXXXXXXX"

[[channels]]
id = "C0YYYYYYYYY"
name = "engineering"
url = "https://yourworkspace.slack.com/archives/C0YYYYYYYYY"
since = "2024-01-01"   # never fetch messages older than this

[[threads]]
channel_id = "C0XXXXXXXXX"
thread_ts = "1234567890.123456"
url = "https://yourworkspace.slack.com/archives/C0XXXXXXXXX/p1234567890123456"
```

Remove a channel or thread:

```bash
ssd remove C0XXXXXXXXX
ssd remove "#general"
ssd remove https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# Remove a specific tracked thread
ssd remove "https://yourworkspace.slack.com/archives/C0XXXXXXXXX/p1234567890123456"
```

## Attachments

Files are not downloaded by default. Enable with `--attachments` (before the subcommand) or in `ssd.toml`:

```bash
ssd --attachments dump "#general"
ssd --attachments sync "#engineering"
ssd --attachments update
```

Files land in `<channel_dir>/attachments/`. This includes files attached to thread replies, not just top-level messages. Files are skipped on re-run when the size is known and the local file already matches. If Slack omits the size field, the file is re-downloaded to avoid keeping a partial file from an interrupted run. If a download fails, the Markdown link falls back to the original Slack URL.

Per-channel override in `ssd.toml`:

```toml
[[channels]]
id = "C0XXXXXXXXX"
name = "general"
attachments = false    # disable for this channel even if global is true
```

## Thread dump

Paste a thread URL to dump only that thread's replies:

```bash
ssd dump "https://yourworkspace.slack.com/archives/C0XXXXXXXXX/p1234567890123456"
```

Output in `<channel_dir>/thread_1234567890_123456/thread.json` and `thread.md`. Note the thread timestamp uses underscores in the directory name (`1234567890.123456` becomes `thread_1234567890_123456/`). `ssd sync` on a thread URL fetches only new replies and merges them.

## Query local dumps

Reads `./output` (or `--output`). No `api.slack.com`. JSON shape matches slack_sdk Web API methods. Opens an ssd dump root, a workspace dir, a single channel dir, or an official Slack export (daily `YYYY-MM-DD.json` files).

```bash
ssd query search "from:alice has:file after:yesterday"
ssd query history C0XXXXXXXXX --limit 20
ssd query history C0XXXXXXXXX --search "deploy"
ssd query channels --search eng
ssd query users --search alice
ssd query files --search report.pdf
ssd query message C0XXXXXXXXX 1717200000.000100
ssd query export messages.jsonl
ssd query api conversations.history channel=C0XXXXXXXXX
```

A positional id looks up one object. `--search TERM` filters the list. Info wins if both are present. `ssd query --help` lists every subcommand.

On a TTY, query prints indented JSON. Pipes stay one line. A Slack-shaped `{"ok": false}` payload still prints, then exits 1. A missing `--output` path is a usage error. An empty dump prints JSON and a stderr hint.

| Group | Commands |
|---|---|
| Messages | `search` `history` `replies` `message` `threads` `export` |
| People | `users` `profile` `email` `identity` `members` `convos` `usergroups` `usergroup-users` `presence` `migration` |
| Channels | `channels` `cursor` `stats` |
| Files | `files` `files-info` `remote-files` `comments` |
| Channel extras | `emoji` `pins` `stars` `bookmarks` `reactions` `calls` `participants` `scheduled` `reminders` `bots` |
| Team | `team` `team-profile` `prefs` `auth` `teams` `external-teams` `access-logs` `integration-logs` `billable` `dnd` `rtm` |
| Raw | `api` `permalink` |

Search (`ssd query search`, `search.messages`, `search.files`, `search.all`): substring plus `from:` `in:` `to:` `with:` `has:` `is:` `before:` `after:` `around:` `on:` `during:`, `from:me` / `to:me` / `with:me` / `in:me`, and `-term` exclusion. Relative dates on after/before/during: `today` `yesterday` `week` `month` `year` `lastweek` `lastmonth` `lastyear`. `has::emoji:` matches a named reaction.

`has:` file, reaction, pin, link, canvas, image, video, audio, snippet, attachment, mention, space, block, email, call, x_files, pdf, replies, spreadsheet, metadata, remote, zip, presentation, list, doc, txt, button, gif, json, csv, xml, md, yaml, toml, html, svg, python, js, ts, go, rust, sql, css, sh, workflow, star.

`is:` on messages: thread, bot, starred/saved, edited, unthreaded, broadcast, locked, tombstone/deleted, app, file_share, me, hidden, join, leave, topic, purpose, parent, archive, unarchive, rename, subscribed, pinned, workflow, call/huddle, ephemeral, creator, delayed, scheduled, guest, admin, owner, app_user, me_message, stranger, invited, primary_owner, ultra_restricted, canvas, forgotten, enterprise, moved, connector, workflow_bot.

`is:` on channels (other channels skipped before parse): dm/im, mpim, channel, group, private, public, shared, ext_shared, org_shared, general, pending_ext_shared, member, open, org_default, frozen, global_shared, org_mandatory, read_only, thread_only, non_threadable, user_deleted, muted, unreads, pending_shared, has_canvas, im_blocked, connected, unlinked, internal, host, connected_limited, archived.

Pairs that are not aliases: `is:me` (authed user) vs `is:me_message` (`/me` subtype); `is:archive` (subtype) vs `is:archived` (channel flag); `is:app` vs `is:app_user`; `is:canvas` vs `is:has_canvas`; `is:pending_shared` vs `is:pending_ext_shared`; `is:workflow` vs `is:workflow_bot`; `is:connected` vs `is:connected_limited`. `is:starred` is a starred message, not channel `is_starred`.

Python:

```python
from ssd import DumpClient

client = DumpClient("output")
client.conversations_history(channel="C0XXXXXXXXX")
client.search_messages(query="from:alice has:file after:yesterday")
client.api_call("conversations.history", params={"channel": "C0XXXXXXXXX"})
```

`api_call` maps `conversations.history` to `conversations_history`. Unknown methods return `{"ok": false, "error": "unknown_method"}`. Write methods are not implemented.

## Communication graph

Generate an HTML graph showing who talks to whom across one or more channel dumps:

```bash
ssd graph output/myworkspace/general_C0XXXXXXXXX
ssd graph output/myworkspace/general_C0XXXXXXXXX output/myworkspace/engineering_C0YYYYYYYYY
ssd graph output/myworkspace/general_C0XXXXXXXXX --output graph.html

# Auto-discover all channel dirs under the output directory
ssd graph
```

Without arguments, discovers all channel directories under `--output` (default: `./output`).

Opens in the browser. Nodes are users; edges represent message replies and mentions. Useful for mapping active communication patterns across a workspace.

Note: edges (connections between users) are derived from channel message threads and @mentions. Standalone thread dumps (`thread_*/thread.json`) contribute to user activity counts. Reply-to-author edges are not recorded (the original thread author is not stored in the thread file), but `@mentions` found in those replies do create edges.

## All options

Options go **before** the subcommand:

```
ssd [OPTIONS] COMMAND [ARGS]

Options:
  --token TEXT                  Override auto-extracted token (or SSD_TOKEN env var)
  --output DIR                  Output directory (default: ./output)
  --config FILE                 Config file (default: ./ssd.toml)
  --delay FLOAT                 Seconds between paginated batch fetches (not applied to individual API calls such as user lookups or per-thread reply fetches) (default: 1.0)
  --attachments / --no-attachments

Commands:
  token     Extract credentials from Slack desktop app and browser
  dump      Full history dump of one or more channels/threads (`--all`, `--dms`)
  sync      Incremental sync: fetch only new messages since last run
  add       Add a channel or thread to ssd.toml
  remove    Remove a channel or thread from ssd.toml
  list      Show tracked channels and last sync time
  update    Sync all channels and threads tracked in ssd.toml
  query     Read a local dump (no Slack network)
  graph     Generate a communication graph HTML file from channel dumps
```

## How auth works

`ssd token` runs once to save credentials locally:

1. Finds the `xoxc-` token in Slack's LevelDB (`~/Library/Application Support/Slack/Local Storage/leveldb/`)
2. Extracts the `d` session cookie, trying in order: Slack's own Cookies file (older Slack, plaintext), Firefox `cookies.sqlite` (plaintext), Chrome's SQLite store (AES-decrypted via macOS Keychain). (Only the Default Chrome profile at `~/Library/Application Support/Google/Chrome/Default/` is searched; Beta, Canary, and custom profiles are not tried.)
3. Saves both to `output/.token` and `output/.cookie` (permissions `600`)

Every API call sends `Authorization: Bearer xoxc-...` and `Cookie: d=xoxd-...`. This is how the Slack Electron desktop app itself authenticates. No API keys needed.

Re-run `ssd token` if commands return `invalid_auth` (e.g. after signing out and back in).

## Known limitations

- **macOS only** for `ssd token` / live dump. Token and cookie extraction reads macOS-specific paths (`~/Library/Application Support/Slack/`, Chrome, and Firefox profile dirs). `ssd query` and `DumpClient` run anywhere you have a dump.
- **DumpClient is read-only.** No chat.postMessage, no reactions.add, no websocket. `rtm.connect` / `rtm.start` return snapshot dicts from sidecars.

## Troubleshooting

**`invalid_auth` on every command:**
Re-run `ssd token`. The session may have expired or the browser may not have been open when credentials were extracted.

**`ssd token` prints a warning about cookie extraction failing:**
The actual warning indicates cookie extraction failed from all sources: Slack's own Cookies file, Firefox, and Chrome. Make sure at least one browser is open and signed into the same Slack workspace, then re-run `ssd token`.

**Channel not found when using `ssd dump #name`:**
Use the channel URL or bare ID instead. Name-based lookup pages through `conversations.list` which may time out on large workspaces.

**Attachments show as URL links instead of local file links in Markdown:**
The download failed (likely a permissions issue or the file was deleted from Slack). Re-run with `--attachments` to retry.

**`ssd query` shows no channels:**
`--output` must point at the dump root (default `./output`), a workspace directory, a channel directory, or an official Slack export. Query never calls Slack, so an empty dir stays empty.

## Development

```bash
git clone https://github.com/maci0/simpleslackdump
cd simpleslackdump
uv sync --group dev
uv run pytest
uv run ssd --help
```
