import re
import time
from typing import Optional
from urllib.parse import quote
from slack_sdk import WebClient


_ID_RE = re.compile(r"^[CDG][A-Z0-9a-z]+$")


def _url_encode_cookie(cookie: str) -> str:
    """URL-encode the xoxd- cookie value for use in a Cookie header.
    Slack stores the cookie URL-encoded (/ -> %2F, + -> %2B).
    """
    return quote(cookie, safe="-")


class SlackAPI:
    def __init__(self, token: str, delay: float = 1.0, cookie: Optional[str] = None):
        # xoxc- tokens require the d cookie sent alongside; xoxd-/xoxb- work standalone
        headers = {"Cookie": f"d={_url_encode_cookie(cookie)}"} if cookie else {}
        self.client = WebClient(token=token, headers=headers)
        self.delay = delay
        self._user_cache: dict[str, str] = {}

    def get_workspace(self) -> str:
        resp = self.client.auth_test()
        # Enterprise Grid workspaces omit team_domain; extract from url instead
        domain = resp.get("team_domain")
        if not domain:
            url = resp.get("url", "")
            # https://redhat.enterprise.slack.com/ -> redhat.enterprise
            from urllib.parse import urlparse
            host = urlparse(url).hostname or ""
            domain = host.replace(".slack.com", "") if host.endswith(".slack.com") else host
        return domain

    def resolve_channel(self, name_or_id: str) -> tuple[str, str]:
        if _ID_RE.match(name_or_id):
            info = self.client.conversations_info(channel=name_or_id)["channel"]
            return info["id"], info["name"]
        # search by name
        cursor = None
        while True:
            resp = self.client.conversations_list(
                limit=200, cursor=cursor, types="public_channel,private_channel"
            )
            for ch in resp["channels"]:
                if ch["name"] == name_or_id.lstrip("#"):
                    return ch["id"], ch["name"]
            cursor = resp.get("response_metadata", {}).get("next_cursor")
            if not cursor:
                break
        raise ValueError(f"Channel not found: {name_or_id}")

    def get_messages(
        self, channel_id: str, oldest: Optional[str] = None
    ) -> list[dict]:
        messages = []
        cursor = None
        while True:
            kwargs: dict = dict(channel=channel_id, limit=200)
            if oldest is not None:
                kwargs["oldest"] = oldest
            if cursor:
                kwargs["cursor"] = cursor
            resp = self.client.conversations_history(**kwargs)
            messages.extend(resp["messages"])
            if not resp.get("has_more"):
                break
            cursor = resp["response_metadata"]["next_cursor"]
            time.sleep(self.delay)
        return messages

    def get_replies(self, channel_id: str, thread_ts: str, oldest: Optional[str] = None) -> list[dict]:
        replies = []
        cursor = None
        while True:
            kwargs: dict = dict(channel=channel_id, ts=thread_ts, limit=200)
            if oldest is not None:
                kwargs["oldest"] = oldest
            if cursor:
                kwargs["cursor"] = cursor
            resp = self.client.conversations_replies(**kwargs)
            # first page: skip index 0 (root message); subsequent pages include all
            batch = resp["messages"]
            replies.extend(batch[1:] if cursor is None else batch)
            if not resp.get("has_more"):
                break
            cursor = resp["response_metadata"]["next_cursor"]
            time.sleep(self.delay)
        return replies

    def get_user_name(self, user_id: str) -> str:
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            profile = self.client.users_info(user=user_id)["user"]["profile"]
            name = profile.get("display_name_normalized") or profile.get("real_name", user_id)
        except Exception:
            name = user_id
        self._user_cache[user_id] = name
        return name

    def enrich(self, channel_id: str, messages: list[dict]) -> list[dict]:
        result = []
        for msg in messages:
            user_id = msg.get("user", "")
            enriched = {
                "ts": msg["ts"],
                "user": user_id,
                "user_name": self.get_user_name(user_id) if user_id else "unknown",
                "text": msg.get("text", ""),
                "reactions": [
                    {"name": r["name"], "count": r["count"], "users": r.get("users", [])}
                    for r in msg.get("reactions", [])
                ],
                "thread": [],
            }
            if msg.get("reply_count", 0) > 0:
                raw_replies = self.get_replies(channel_id, msg["ts"])
                enriched["thread"] = [
                    {
                        "ts": r["ts"],
                        "user": r.get("user", ""),
                        "user_name": (
                            self.get_user_name(r["user"]) if r.get("user") else "unknown"
                        ),
                        "text": r.get("text", ""),
                        "reactions": [
                            {"name": rx["name"], "count": rx["count"], "users": rx.get("users", [])}
                            for rx in r.get("reactions", [])
                        ],
                    }
                    for r in raw_replies
                ]
            result.append(enriched)
        return result
