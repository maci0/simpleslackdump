import re
from dataclasses import dataclass


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
    from ssd.api import _ID_RE

    if _ID_RE.match(target):
        return SlackTarget(channel_id=target)

    # Channel name (#general or general)
    return SlackTarget(channel_name=target.lstrip("#"))
