# ssd — simpleslackdump

Dump Slack channels and threads to JSON and Markdown. macOS only. Extracts auth tokens directly from the running Slack desktop app — no OAuth setup, no bot token, no Slack app to configure.

## Features

- Full channel dump or incremental sync with cursor-based deduplication
- Threads and replies fully captured and merged
- `@user` mentions resolved to display names
- JSON (structured, all metadata) + Markdown (human-readable) output per channel
- Optional file attachment download with skip-existing
- Config file (`ssd.toml`) for tracking channels across `ssd update` runs
- Enterprise Grid workspaces supported

## Requirements

- macOS (token extraction reads macOS Slack desktop app + Chrome cookie store)
- [Slack desktop app](https://slack.com/downloads/mac) installed and signed in
- [Google Chrome](https://www.google.com/chrome/) installed and signed into the same Slack workspace
- Python 3.11+ and [uv](https://docs.astral.sh/uv/)

## Install

**As a tool (recommended):**

```bash
uv tool install git+https://github.com/maci0/simpleslackdump
ssd --help
```

**Or clone and run locally:**

```bash
git clone https://github.com/maci0/simpleslackdump
cd simpleslackdump
uv sync
uv run ssd --help
```

## Quick start

```bash
# 1. Extract credentials from running Slack desktop app + Chrome cookies
ssd token

# 2. Dump a channel — paste the URL directly from Slack
ssd dump https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# 3. Or use channel name or bare ID
ssd dump "#general"
ssd dump C0XXXXXXXXX
```

Output lands in `./output/<workspace>/<channel_name>_<channel_id>/`:

```
messages.json     # all messages, threads, metadata — sorted by timestamp
messages.md       # human-readable, @mentions resolved
.cursor           # tracks last synced timestamp for incremental sync
```

Example output:

```
#general (C0XXXXXXXXX) -> output/myworkspace/general_C0XXXXXXXXX
fetched 879 messages in 7.3s (120 msg/s)
879 messages | 175 threads | 1446 replies | 102.5s total (9 msg/s)
```

## Incremental sync

```bash
# Sync new messages since last run (reads .cursor file)
ssd sync https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# Sync from a specific date forward
ssd sync "#general" --since 2024-06-01

# Also accepts a Unix timestamp
ssd sync "#general" --since 1717200000
```

New messages are merged into the existing `messages.json` — no duplicates, no data loss.

## Config-driven sync

Track channels in `ssd.toml` and sync them all with one command:

```bash
# Add channels to track
ssd add https://yourworkspace.slack.com/archives/C0XXXXXXXXX
ssd add "#engineering"

# Add a single thread (dumps only that thread's replies)
ssd add "https://yourworkspace.slack.com/archives/C0XXXXXXXXX/p1234567890123456"

# Show tracked channels and last sync time
ssd list

# Sync everything in ssd.toml
ssd update
```

`ssd.toml` (auto-created and updated by `ssd add`):

```toml
[settings]
output_dir = "./output"
attachments = false

[[channels]]
id = "C0XXXXXXXXX"
name = "general"
url = "https://yourworkspace.slack.com/archives/C0XXXXXXXXX"

[[channels]]
id = "C0YYYYYYYYY"
name = "engineering"
url = "https://yourworkspace.slack.com/archives/C0YYYYYYYYY"
since = "2024-01-01"   # only sync messages after this date
```

## Attachments

```bash
# Download file attachments (pass flag before the subcommand)
ssd --attachments dump "#general"
ssd --attachments update

# Enable by default in ssd.toml
[settings]
attachments = true
```

Files saved to `<channel_dir>/attachments/<timestamp>_<filename>`. Already-downloaded files are skipped. Markdown output links to local paths.

Per-channel override:

```toml
[[channels]]
id = "C0XXXXXXXXX"
name = "general"
attachments = false   # override global setting for this channel
```

## Thread-only dump

Pass a thread URL to dump just that thread's replies:

```bash
ssd dump "https://yourworkspace.slack.com/archives/C0XXXXXXXXX/p1234567890123456"
```

Output goes to `<channel_dir>/thread_<ts>/thread.json` and `thread.md`. Incremental sync works the same way — only new replies are fetched and merged.

## Global options

All options must appear before the subcommand:

```
ssd [OPTIONS] COMMAND

Options:
  --token TEXT          Override auto-extracted token (or set SSD_TOKEN env var)
  --output DIR          Output directory (default: ./output)
  --config FILE         Config file (default: ./ssd.toml)
  --delay FLOAT         Seconds between paginated API calls (default: 1.0)
  --attachments / --no-attachments
```

## How authentication works

`ssd token` extracts credentials from your local machine:

1. **`xoxc-` token** — read from Slack's LevelDB local storage at `~/Library/Application Support/Slack/Local Storage/leveldb/`
2. **`d` cookie** — decrypted from Chrome's SQLite cookie database using the Chrome Safe Storage key from macOS Keychain

Both are required for the Slack Web API. Newer Slack (Electron-based) sends `Authorization: Bearer xoxc-...` and `Cookie: d=xoxd-...` on every request.

Credentials are saved to `output/.token` and `output/.cookie` (mode `600`). Re-run `ssd token` if API calls start returning `invalid_auth`.

## Removing a tracked channel

```bash
ssd remove C0XXXXXXXXX
# or
ssd remove "https://yourworkspace.slack.com/archives/C0XXXXXXXXX"
```

## Development

```bash
git clone https://github.com/maci0/simpleslackdump
cd simpleslackdump
uv sync --group dev
uv run pytest
```
