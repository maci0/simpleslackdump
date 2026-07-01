import re
import time
from typing import Any
from urllib.parse import quote, urlparse

import click
from slack_sdk import WebClient

_ID_RE = re.compile(r"^[CDG][A-Z0-9a-z]+$")
_MENTION_RE = re.compile(r"<@([A-Z0-9a-z]+)>")


def _url_encode_cookie(cookie: str) -> str:
    """URL-encode the xoxd- cookie value for use in a Cookie header.
    Slack stores the cookie URL-encoded (/ -> %2F, + -> %2B).
    """
    return quote(cookie, safe="")


class SlackAPI:
    def __init__(self, token: str, delay: float = 1.0, cookie: str | None = None):
        # xoxc- tokens require the d cookie sent alongside; xoxd-/xoxb- work standalone
        headers = {"Cookie": f"d={_url_encode_cookie(cookie)}"} if cookie else {}
        self.client = WebClient(token=token, headers=headers)
        self.delay = delay
        self._user_cache: dict[str, str] = {}
        self._profile_cache: dict[str, dict[str, Any]] = {}

    def get_workspace(self) -> str:
        resp = self.client.auth_test()
        # Enterprise Grid workspaces omit team_domain; extract from url instead
        domain = resp.get("team_domain")
        if not domain:
            url = resp.get("url", "")
            # https://acme.enterprise.slack.com/ -> acme.enterprise
            host = urlparse(url).hostname or ""
            domain = host.replace(".slack.com", "") if host.endswith(".slack.com") else host
        if not domain:
            raise RuntimeError(
                "Could not determine workspace domain from auth.test response. "
                "Check that the token is valid."
            )
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

    def _paginate(
        self,
        sdk_method: Any,
        base_kwargs: dict[str, Any],
        oldest: str | None = None,
        max_retries: int = 3,
    ) -> list[dict[str, Any]]:
        items = []
        cursor = None
        while True:
            kwargs = dict(base_kwargs)
            if oldest is not None:
                kwargs["oldest"] = oldest
            if cursor:
                kwargs["cursor"] = cursor
            # Retry loop for transient network/rate-limit errors
            resp = None
            for attempt in range(max_retries):
                try:
                    resp = sdk_method(**kwargs)
                    break
                except Exception as exc:
                    err = getattr(getattr(exc, "response", None), "get", lambda k, d=None: d)(
                        "error"
                    )
                    if err == "ratelimited" or isinstance(exc, (TimeoutError, OSError)):
                        wait = self.delay * (2**attempt)
                        click.echo(
                            f"  [retry {attempt + 1}/{max_retries}] {exc.__class__.__name__}"
                            f" — waiting {wait:.1f}s",
                            err=True,
                        )
                        time.sleep(wait)
                    else:
                        raise
            if resp is None:
                raise RuntimeError("Slack API request failed after retries")
            page = resp.get("messages")
            if page is None:
                break  # unexpected response shape — stop paginating rather than silently dropping
            items.extend(page)
            if not resp.get("has_more"):
                break
            cursor = resp.get("response_metadata", {}).get("next_cursor", "")
            if not cursor:
                break
            time.sleep(self.delay)
        return items

    def get_messages(self, channel_id: str, oldest: str | None = None) -> list[dict[str, Any]]:
        return self._paginate(
            self.client.conversations_history,
            {"channel": channel_id, "limit": 200},
            oldest=oldest,
        )

    def get_replies(
        self, channel_id: str, thread_ts: str, oldest: str | None = None
    ) -> list[dict[str, Any]]:
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
            user_obj = self.client.users_info(user=user_id)["user"]
            p = user_obj.get("profile", {})
            name = p.get("display_name_normalized") or p.get("real_name") or user_id
            self._profile_cache[user_id] = {
                "id": user_id,
                "handle": user_obj.get("name", ""),
                "real_name": p.get("real_name_normalized") or p.get("real_name", ""),
                "display_name": p.get("display_name_normalized") or p.get("display_name", ""),
                "title": p.get("title", ""),
                "email": p.get("email", ""),
                "phone": p.get("phone", ""),
                "status_text": p.get("status_text", ""),
                "status_emoji": p.get("status_emoji", ""),
                "timezone": user_obj.get("tz", ""),
                "timezone_label": user_obj.get("tz_label", ""),
                "is_bot": user_obj.get("is_bot", False),
                "image": p.get("image_192") or p.get("image_72", ""),
            }
        except Exception:
            name = user_id
        self._user_cache[user_id] = name
        return name

    def get_user_profiles(self) -> dict[str, dict[str, Any]]:
        """Return all user profiles fetched so far, keyed by user ID."""
        return dict(self._profile_cache)

    def enrich_reply(self, r: dict[str, Any]) -> dict[str, Any]:
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

    def enrich(self, channel_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for msg in messages:
            enriched = {**self.enrich_reply(msg), "thread": []}
            # reply_count can be null from the API (deleted thread) — guard with or 0
            if (msg.get("reply_count") or 0) > 0:
                raw_replies = self.get_replies(channel_id, msg["ts"])
                enriched["thread"] = [self.enrich_reply(r) for r in raw_replies]
            result.append(enriched)
        return result
