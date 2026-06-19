import re
import time
from typing import Optional
from urllib.parse import quote, urlparse
from slack_sdk import WebClient


_ID_RE = re.compile(r"^[CDG][A-Z0-9a-z]+$")
_MENTION_RE = re.compile(r"<@([A-Z0-9a-z]+)>")


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
            # https://acme.enterprise.slack.com/ -> acme.enterprise
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

    def _paginate(self, sdk_method, base_kwargs: dict, oldest: str | None = None) -> list[dict]:
        items = []
        cursor = None
        while True:
            kwargs = dict(base_kwargs)
            if oldest is not None:
                kwargs["oldest"] = oldest
            if cursor:
                kwargs["cursor"] = cursor
            resp = sdk_method(**kwargs)
            items.extend(resp["messages"])
            if not resp.get("has_more"):
                break
            cursor = resp["response_metadata"]["next_cursor"]
            time.sleep(self.delay)
        return items

    def get_messages(
        self, channel_id: str, oldest: Optional[str] = None
    ) -> list[dict]:
        return self._paginate(
            self.client.conversations_history,
            {"channel": channel_id, "limit": 200},
            oldest=oldest,
        )

    def get_replies(self, channel_id: str, thread_ts: str, oldest: Optional[str] = None) -> list[dict]:
        raw = self._paginate(
            self.client.conversations_replies,
            {"channel": channel_id, "ts": thread_ts, "limit": 200},
            oldest=oldest,
        )
        return [m for m in raw if m.get("ts") != thread_ts]

    def resolve_mentions(self, text: str) -> str:
        """Replace <@UXXXXXXX> with @display_name."""
        return _MENTION_RE.sub(lambda m: f"@{self.get_user_name(m.group(1))}", text)

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

    def enrich_reply(self, r: dict) -> dict:
        """Enrich a single reply dict — name resolution and mention substitution only,
        no recursive thread fetch (replies don't have sub-threads)."""
        user_id = r.get("user", "")
        return {
            "ts": r["ts"],
            "user": user_id,
            "user_name": self.get_user_name(user_id) if user_id else "unknown",
            "text": self.resolve_mentions(r.get("text", "")),
            "reactions": [
                {"name": rx["name"], "count": rx["count"], "users": rx.get("users", [])}
                for rx in r.get("reactions", [])
            ],
            "files": r.get("files", []),
        }

    def enrich(self, channel_id: str, messages: list[dict]) -> list[dict]:
        result = []
        for msg in messages:
            enriched = {**self.enrich_reply(msg), "thread": []}
            # reply_count can be null from the API (deleted thread) — guard with or 0
            if (msg.get("reply_count") or 0) > 0:
                raw_replies = self.get_replies(channel_id, msg["ts"])
                enriched["thread"] = [self.enrich_reply(r) for r in raw_replies]
            result.append(enriched)
        return result
