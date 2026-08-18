"""Slack domain primitives: IDs, target strings, timestamp ordering, and dump path conventions."""

import re
from dataclasses import dataclass
from pathlib import Path

# Conversation IDs: public channel (C), direct message (D), private channel or group DM (G).
_ID_RE = re.compile(r"^[CDG][A-Z0-9a-z]+$")

# Dump directory names are ``{channel_name}_{channel_id}``.
# Channel id is the durable key; the name prefix is display-only.
CHANNEL_DIR_RE = re.compile(r"^(.*)_([CDG][A-Za-z0-9]+)$")

# Slack conversations.list types string used by live and dump-backed clients.
ALL_CONV_TYPES = "public_channel,private_channel,mpim,im"


def is_slack_id(s: str) -> bool:
    return bool(_ID_RE.match(s))


def ts_key(ts: str) -> int:
    """Order Slack timestamps without float precision loss.

    Returns microseconds-since-epoch as a plain int so comparisons and arithmetic
    use native integer math instead of the much slower ``Decimal`` type.
    """
    s = str(ts)
    try:
        if "." in s:
            sec_s, usec_s = s.split(".", 1)
            return int(sec_s) * 1_000_000 + int(usec_s.ljust(6, "0")[:6])
        return int(s) * 1_000_000
    except (ValueError, TypeError) as exc:
        raise ValueError(f"Invalid Slack ts: {ts!r}") from exc


@dataclass
class SlackTarget:
    channel_id: str | None = None
    channel_name: str | None = None
    workspace: str | None = None
    thread_ts: str | None = None


def parse_target(target: str) -> SlackTarget:
    # Thread URL: .../archives/<CID>/p<10digits><6digits>
    m = re.match(
        r"https?://([^/]+?)\.slack\.com/archives/([A-Z0-9a-z]+)/p(\d{10})(\d{6})",
        target,
    )
    if m:
        workspace, channel_id, ts_sec, ts_usec = m.groups()
        return SlackTarget(
            channel_id=channel_id,
            workspace=workspace,
            thread_ts=f"{ts_sec}.{ts_usec}",
        )

    # Channel URL: .../archives/<CID>
    m = re.match(
        r"https?://([^/]+?)\.slack\.com/archives/([A-Z0-9a-z]+)",
        target,
    )
    if m:
        workspace, channel_id = m.groups()
        return SlackTarget(channel_id=channel_id, workspace=workspace)

    # Bare Slack ID (C, D, G prefix)
    if is_slack_id(target):
        return SlackTarget(channel_id=target)

    # Channel name (#general or general)
    return SlackTarget(channel_name=target.lstrip("#"))


def ts_from_thread_dir(name: str) -> str | None:
    """Parse ``thread_<sec>_<usec>`` directory names back to a Slack ts string."""
    if not name.startswith("thread_"):
        return None
    rest = name[len("thread_") :]
    if "_" not in rest:
        return None
    return rest.replace("_", ".", 1)


def dir_rank(path: Path) -> tuple[int, float]:
    """Rank a channel dump directory for preference when multiple dirs match an id."""
    has_messages = 1 if (path / "messages.json").is_file() else 0
    try:
        mtime = path.stat().st_mtime
    except OSError:
        mtime = 0.0
    return (has_messages, mtime)
