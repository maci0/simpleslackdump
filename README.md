# ssd — simpleslackdump

Dump Slack channels and threads to JSON and Markdown. macOS only, no Slack app credentials required — extracts auth from the running desktop app.

## Features

- Full channel dump or incremental sync (cursor-based)
- Threads and replies included
- `@user` mentions resolved to display names
- JSON (structured) + Markdown (readable) output per channel
- Optional file attachment download
- Config file (`ssd.toml`) for tracking channels to keep in sync
- Enterprise Grid workspaces supported

## Install

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

**Install as a tool (recommended):**

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

> **Note:** If you need LevelDB-based token extraction (fallback method), install `brew install leveldb` first, then add `--extra leveldb` to the install command.

## Quick start

```bash
# 1. Extract token from the running Slack desktop app (also reads d cookie from Chrome)
uv run ssd token

# 2. Dump a channel
uv run ssd dump https://yourworkspace.enterprise.slack.com/archives/C0XXXXXXXXX

# 3. Or by channel name / ID
uv run ssd dump #general
uv run ssd dump C0XXXXXXXXX
```

Output lands in `./output/<workspace>/<channel_name>_<channel_id>/`:
```
messages.json   # structured, all metadata
messages.md     # human-readable
.cursor         # tracks last synced timestamp
```

## Incremental sync

```bash
# Sync since last run (uses stored cursor)
uv run ssd sync https://yourworkspace.slack.com/archives/C0XXXXXXXXX

# Sync from a specific date
uv run ssd sync #general --since 2024-06-01
```

## Config-driven sync

```bash
# Track channels
uv run ssd add https://yourworkspace.slack.com/archives/C0XXXXXXXXX
uv run ssd add #engineering

# Show tracked channels and last sync time
uv run ssd list

# Sync all tracked channels
uv run ssd update
```

`ssd.toml` is created/updated automatically:

```toml
[settings]
output_dir = "./output"
attachments = false

[[channels]]
id = "C0XXXXXXXXX"
name = "general"
url = "https://yourworkspace.slack.com/archives/C0XXXXXXXXX"
```

## Attachments

```bash
# Download file attachments alongside messages
uv run ssd dump #general --attachments

# Or enable by default in ssd.toml
[settings]
attachments = true
```

Files saved to `<channel_dir>/attachments/<ts>_<filename>`. Markdown links updated to local paths.

## Thread-only dump

Pass a thread URL to dump just that thread:

```bash
uv run ssd dump "https://yourworkspace.slack.com/archives/C0XXXXXXXXX/p1234567890123456"
```

Output goes to `<channel_dir>/thread_<ts>/thread.json` and `thread.md`.

## Global options

```
--token TOKEN       Override auto-extracted token (or set SSD_TOKEN env var)
--output DIR        Output directory (default: ./output)
--config FILE       Config file (default: ./ssd.toml)
--delay FLOAT       Seconds between paginated API calls (default: 1.0)
--attachments / --no-attachments
```

## How auth works

`ssd token` does the following:

1. Reads `xoxc-` client token from Slack's LevelDB storage (`~/Library/Application Support/Slack/Local Storage/leveldb/`)
2. Decrypts the `d` cookie from Chrome's cookie database using the Chrome Safe Storage key from macOS Keychain
3. Saves token to `output/.token` and cookie to `output/.cookie` (both `chmod 600`)

Newer Slack (Electron) requires both the `xoxc-` bearer token and the `d` cookie for API calls. Chrome must be installed and logged into the same Slack workspace.

## Output stats

```
#general (C0XXXXXXXXX) -> output/acme/general_C0XXXXXXXXX
fetched 879 messages in 7.3s (120 msg/s)
879 messages | 175 threads | 1446 replies | 102.5s total (9 msg/s)
```

## Development

```bash
uv sync --group dev
uv run pytest
```
