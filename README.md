# ssd — simpleslackdump

Dump Slack channels and threads to JSON and Markdown. No OAuth setup. No bot token. No Slack app to register. Extracts credentials directly from the running Slack desktop app and Chrome browser.

## What it does

- Full channel dump or incremental sync (cursor-based, deduplicating)
- Threads and replies captured in full, merged correctly on re-sync
- `@user` mentions resolved to display names
- JSON (all metadata) + Markdown (readable) output per channel
- File attachment download with skip-already-downloaded
- `ssd.toml` config to track multiple channels and sync them with one command
- Works with Enterprise Grid workspaces

## Requirements

| Requirement | Why |
|---|---|
| macOS | Token extraction reads Slack's local app data |
| [Slack desktop app](https://slack.com/downloads/mac), signed in | Source of the `xoxc-` API token |
| Chrome or Firefox, signed into Slack | Source of the session cookie (`xoxd-`) |
| Python 3.11+ | Runtime |
| [uv](https://docs.astral.sh/uv/) | Dependency/tool management |

Chrome or Firefox must be signed into the **same workspace** as the desktop app.

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
# Step 1 — extract credentials (run once, re-run if you get invalid_auth)
ssd token

# Step 2 — dump a channel (paste the Slack URL directly)
ssd dump https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# Also works with channel name or bare ID
ssd dump "#general"
ssd dump C0XXXXXXXXX
```

Output in `./output/<workspace>/<channel_name>_<channel_id>/`:

```
messages.json     # structured — all messages, threads, reactions, file metadata
messages.md       # readable — @mentions resolved to names, timestamps in UTC
.cursor           # last synced timestamp — used by ssd sync
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
```

New messages merge into existing `messages.json` — no duplicates, no overwrites. New replies to older messages are also picked up (each known thread is polled for replies newer than the last stored reply).

## Track channels with ssd.toml

```bash
# Add a channel
ssd add https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# Add a thread (syncs only replies in that thread)
ssd add "https://yourworkspace.slack.com/archives/C0XXXXXXXXX/p1234567890123456"

# Show tracked channels and when they were last synced
ssd list

# Sync all tracked channels in one shot
ssd update
```

`ssd.toml` (auto-managed by `ssd add` / `ssd remove`):

```toml
[settings]
attachments = false     # set to true to download files by default

[[channels]]
id = "C0XXXXXXXXX"
name = "general"
url = "https://yourworkspace.slack.com/archives/C0XXXXXXXXX"

[[channels]]
id = "C0YYYYYYYYY"
name = "engineering"
url = "https://yourworkspace.slack.com/archives/C0YYYYYYYYY"
since = "2024-01-01"   # never fetch messages older than this
```

Remove a channel:

```bash
ssd remove C0XXXXXXXXX
ssd remove "#general"
ssd remove https://yourworkspace.slack.com/archives/C0XXXXXXXXX
```

## Attachments

Files are not downloaded by default. Enable with `--attachments` (before the subcommand) or in `ssd.toml`:

```bash
ssd --attachments dump "#general"
ssd --attachments sync "#engineering"
ssd --attachments update
```

Files land in `<channel_dir>/attachments/`. This includes files attached to thread replies, not just top-level messages. Files are skipped on re-run when the local file size matches what Slack reports. If a download fails, the Markdown link falls back to the original Slack URL.

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

Output in `<channel_dir>/thread_<ts>/thread.json` and `thread.md`. `ssd sync` on a thread URL fetches only new replies and merges them.

## All options

Options go **before** the subcommand:

```
ssd [OPTIONS] COMMAND [ARGS]

Options:
  --token TEXT                  Override auto-extracted token (or SSD_TOKEN env var)
  --output DIR                  Output directory (default: ./output)
  --config FILE                 Config file (default: ./ssd.toml)
  --delay FLOAT                 Seconds between paginated API calls (default: 1.0)
  --attachments / --no-attachments

Commands:
  token     Extract credentials from Slack desktop app and Chrome
  dump      Full history dump of one or more channels/threads
  sync      Incremental sync — fetch only new messages since last run
  add       Add a channel or thread to ssd.toml
  remove    Remove a channel or thread from ssd.toml
  list      Show tracked channels and last sync time
  update    Sync all channels tracked in ssd.toml
```

## How auth works

`ssd token` runs once to save credentials locally:

1. Finds the `xoxc-` token in Slack's LevelDB (`~/Library/Application Support/Slack/Local Storage/leveldb/`)
2. Extracts the `d` session cookie — tries in order: Slack's own Cookies file (older Slack), Firefox `cookies.sqlite` (plaintext), Chrome's SQLite store (AES-decrypted via macOS Keychain)
3. Saves both to `output/.token` and `output/.cookie` (permissions `600`)

Every API call sends `Authorization: Bearer xoxc-...` and `Cookie: d=xoxd-...`. This is how the Slack Electron desktop app itself authenticates — no API keys needed.

Re-run `ssd token` if commands return `invalid_auth` (e.g. after signing out and back in).

## Known limitations

- **macOS only.** Token and cookie extraction reads macOS-specific paths (`~/Library/Application Support/Slack/`, Chrome, and Firefox profile dirs).

## Troubleshooting

**`invalid_auth` on every command:**
Re-run `ssd token`. The session may have expired or Chrome may not have been open when credentials were extracted.

**`ssd token` prints "Warning: could not extract d cookie":**
Make sure Chrome or Firefox is open and signed into the same Slack workspace. Then re-run `ssd token`.

**Channel not found when using `ssd dump #name`:**
Use the channel URL or bare ID instead. Name-based lookup pages through `conversations.list` which may time out on large workspaces.

**Attachments show as URL links instead of local file links in Markdown:**
The download failed (likely a permissions issue or the file was deleted from Slack). Re-run with `--attachments` to retry.

## Development

```bash
git clone https://github.com/maci0/simpleslackdump
cd simpleslackdump
uv sync --group dev
uv run pytest
uv run ssd --help
```
