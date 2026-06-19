import re
from dataclasses import dataclass
from typing import Optional


@dataclass
class SlackTarget:
    channel_id: Optional[str] = None
    channel_name: Optional[str] = None
    workspace: Optional[str] = None
    thread_ts: Optional[str] = None


def parse_target(target: str) -> SlackTarget:
    # Thread URL: .../archives/<CID>/p<10digits><6digits>
    m = re.match(
        r"https?://([^/]+?)\.slack\.com/archives/([A-Z0-9]+)/p(\d{10})(\d{6})",
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
        r"https?://([^/]+?)\.slack\.com/archives/([A-Z0-9]+)",
        target,
    )
    if m:
        workspace, channel_id = m.groups()
        return SlackTarget(channel_id=channel_id, workspace=workspace)

    # Bare Slack ID (C, D, G prefix)
    if re.match(r"^[CDG][A-Z0-9]+$", target):
        return SlackTarget(channel_id=target)

    # Channel name (#general or general)
    return SlackTarget(channel_name=target.lstrip("#"))
