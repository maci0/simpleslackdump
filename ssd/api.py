import contextlib
import re
import time
from collections.abc import Iterator
from typing import Any
from urllib.parse import quote, urlparse

import click
from slack_sdk import WebClient

_ID_RE = re.compile(r"^[CDG][A-Z0-9a-z]+$")
_MENTION_RE = re.compile(r"<@([A-Z0-9a-z]+)>")
_ALL_CONV_TYPES = "public_channel,private_channel,mpim,im"
_WATCH_MIN_INTERVAL = 5.0
_PASSTHROUGH = (
    "subtype",
    "bot_id",
    "app_id",
    "username",
    "edited",
    "blocks",
    "attachments",
    "pinned_to",
    "pinned_info",
    "client_msg_id",
    "team",
    "parent_user_id",
    "reply_count",
    "reply_users",
    "reply_users_count",
    "latest_reply",
    "is_locked",
    "subscribed",
    "room",
    "call",
    "hidden",
    "reply_broadcast",
    "bot_profile",
    "metadata",
    "x_files",
    "root",
    "display_as_bot",
    "event_ts",
    "inviter",
    "upload",
    "source_team",
    "user_team",
    "topic",
    "purpose",
    "old_name",
    "name",
    "comment",
    "no_notifications",
    "is_starred",
    "bot_link",
    "icons",
    "file",
    "language",
    "is_intro",
    "assistant_app_thread",
    "connected_team_ids",
    "is_ephemeral",
    "pending_shared",
    "file_id",
    "is_moved",
    "parent_conversation",
    "is_delayed_message",
    "scheduled_message_id",
    "pending_connected_team_ids",
    "channel_type",
    "no_display",
    "is_thread_mention",
    "permalink_public",
    "skip_channel_mention_warning",
    "is_auto_split",
    "unfurl_links",
    "unfurl_media",
    "thread_broadcast",
    "is_limited",
    "item_type",
    "item",
    "replies",
    "deleted_ts",
    "hidden_by",
    "with_files",
    "signature",
    "preview",
    "last_read",
    "unread_count",
    "unread_count_display",
    "is_restricted",
    "preview_highlights",
    "plain_text",
    "is_unlocked",
    "preview_plain_text",
    "lines",
    "lines_more",
    "num_stars",
    "permalink",
    "user_profile",
    "is_tombstone",
    "members",
    "source_team_id",
    "user_team_id",
    "is_channel_mention",
    "parse",
    "mrkdwn",
    "item_user",
    "old_topic",
    "old_purpose",
    "invited_user",
    "deleted",
    "is_highlighted",
    "client_context_team_id",
    "saved",
    "local_files",
    "shares",
    "app_unfurl_url",
    "thread_ts",
    "is_share",
)


def _url_encode_cookie(cookie: str) -> str:
    """URL-encode the xoxd- cookie value for use in a Cookie header.
    Slack stores the cookie URL-encoded (/ -> %2F, + -> %2B).
    """
    return quote(cookie, safe="")


def _channel_record(ch: dict[str, Any]) -> dict[str, Any]:
    topic = ch.get("topic") or {}
    purpose = ch.get("purpose") or {}
    out: dict[str, Any] = {
        "id": ch.get("id") or "",
        "name": ch.get("name") or "",
        "created": ch.get("created") or 0,
        "creator": ch.get("creator") or "",
        "is_private": bool(ch.get("is_private")),
        "is_archived": bool(ch.get("is_archived")),
        "is_channel": bool(ch.get("is_channel")),
        "is_group": bool(ch.get("is_group")),
        "is_im": bool(ch.get("is_im")),
        "is_mpim": bool(ch.get("is_mpim")),
        "topic": topic.get("value") if isinstance(topic, dict) else str(topic or ""),
        "purpose": purpose.get("value") if isinstance(purpose, dict) else str(purpose or ""),
        "num_members": ch.get("num_members") or 0,
    }
    if isinstance(topic, dict):
        if "creator" in topic:
            out["topic_creator"] = topic["creator"]
        if "last_set" in topic:
            out["topic_last_set"] = topic["last_set"]
    if isinstance(purpose, dict):
        if "creator" in purpose:
            out["purpose_creator"] = purpose["creator"]
        if "last_set" in purpose:
            out["purpose_last_set"] = purpose["last_set"]
    for key in (
        "is_shared",
        "is_ext_shared",
        "is_org_shared",
        "is_general",
        "is_pending_ext_shared",
        "is_member",
        "is_open",
        "is_org_default",
        "is_frozen",
        "is_global_shared",
        "is_org_mandatory",
        "is_read_only",
        "is_thread_only",
        "is_non_threadable",
        "is_user_deleted",
        "is_muted",
        "is_starred",
        "is_pending_shared",
        "has_canvas",
        "is_im_blocked",
    ):
        if key in ch:
            out[key] = bool(ch[key])
    for key in (
        "locale",
        "updated",
        "previous_names",
        "unlinked",
        "conversation_host_id",
        "connected_team_ids",
        "internal_team_ids",
        "pending_shared",
        "parent_conversation",
        "context_team_id",
        "shared_team_ids",
        "pending_connected_team_ids",
        "connected_limited_team_ids",
        "properties",
        "priority",
        "name_normalized",
        "user",
        "is_moved",
        "use_case",
        "last_read",
        "unread_count",
        "unread_count_display",
        "latest",
        "enterprise_id",
        "file_id",
    ):
        if key in ch:
            out[key] = ch[key]
    return out


class SlackAPI:
    def __init__(self, token: str, delay: float = 1.0, cookie: str | None = None):
        # xoxc- tokens require the d cookie sent alongside; xoxd-/xoxb- work standalone
        headers = {"Cookie": f"d={_url_encode_cookie(cookie)}"} if cookie else {}
        self.client = WebClient(token=token, headers=headers)
        self.delay = delay
        self._user_cache: dict[str, str] = {}
        self._profile_cache: dict[str, dict[str, Any]] = {}
        self._emoji_cache: dict[str, str] | None = None
        self._emoji_categories: list[dict[str, Any]] | None = None
        self._auth: dict[str, Any] | None = None
        self._usergroups: list[dict[str, Any]] | None = None
        self._users_listed = False
        self._conversations: list[dict[str, Any]] | None = None
        self._stars: list[dict[str, Any]] | None = None
        self._reminders: list[dict[str, Any]] | None = None
        self._dnd: dict[str, Any] | None = None
        self._team_profile: dict[str, Any] | None = None
        self._scheduled: list[dict[str, Any]] | None = None
        self._team_info: dict[str, Any] | None = None
        self._files_list: list[dict[str, Any]] | None = None
        self._presence: dict[str, dict[str, Any]] = {}
        self._billable: dict[str, Any] | None = None
        self._integration_logs: list[dict[str, Any]] | None = None
        self._access_logs: list[dict[str, Any]] | None = None
        self._team_prefs: dict[str, Any] | None = None
        self._external_teams: list[dict[str, Any]] | None = None
        self._auth_teams: list[dict[str, Any]] | None = None
        self._remote_files: list[dict[str, Any]] | None = None

    def get_workspace(self) -> str:
        resp = self.auth_payload()
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

    def auth_payload(self) -> dict[str, Any]:
        if self._auth is None:
            self._auth = dict(self.client.auth_test())
        return self._auth

    def get_auth(self) -> dict[str, Any]:
        resp = self.auth_payload()
        return {
            "ok": True,
            "url": resp.get("url") or "",
            "team": resp.get("team") or "",
            "team_id": resp.get("team_id") or "",
            "user": resp.get("user") or "",
            "user_id": resp.get("user_id") or "",
            "enterprise_id": resp.get("enterprise_id") or "",
            "is_enterprise_install": bool(resp.get("is_enterprise_install")),
        }

    def get_auth_teams(self) -> list[dict[str, Any]]:
        if self._auth_teams is None:
            items: list[dict[str, Any]] = []
            cursor = None
            while True:
                kwargs: dict[str, Any] = {"limit": 100, "include_icon": True}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = self.client.auth_teams_list(**kwargs)
                items.extend(resp.get("teams") or [])
                cursor = (resp.get("response_metadata") or {}).get("next_cursor") or ""
                if not cursor:
                    break
                time.sleep(self.delay)
            self._auth_teams = items
        return self._auth_teams

    def resolve_channel(self, name_or_id: str) -> tuple[str, str]:
        if _ID_RE.match(name_or_id):
            info = self.client.conversations_info(channel=name_or_id)["channel"]
            return info["id"], info["name"]
        want = name_or_id.lstrip("#")
        for ch in self.list_conversations():
            if str(ch.get("name") or "") == want:
                return str(ch["id"]), str(ch.get("name") or "")
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
                            f" waiting {wait:.1f}s",
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
            {"channel": channel_id, "limit": 200, "include_all_metadata": True},
            oldest=oldest,
        )

    def watch_messages(
        self,
        channel: str,
        *,
        oldest: str | None = None,
        interval: float | None = None,
        thread_ts: str | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Yield new messages by polling conversations.history, or
        conversations.replies when ``thread_ts`` is set.

        Default ``oldest`` is now, so history is not replayed. Default interval
        is at least 5s (or ``delay`` if larger) to stay under history rate limits.
        Inclusive ``oldest`` hits are skipped. Channel watch does not poll old
        threads for new replies (one API call per interval).
        """
        channel_id, _name = self.resolve_channel(channel)
        cursor = oldest if oldest is not None else str(time.time())
        wait = (
            max(_WATCH_MIN_INTERVAL, float(self.delay or 0))
            if interval is None
            else float(interval)
        )
        with contextlib.suppress(Exception):
            self.fetch_workspace_users()
        while True:
            if thread_ts:
                raw = self.get_replies(channel_id, thread_ts, oldest=cursor, include_parent=True)
            else:
                raw = self.get_messages(channel_id, oldest=cursor)
            new = [m for m in raw if m.get("ts") and float(m["ts"]) > float(cursor)]
            new.sort(key=lambda m: float(m["ts"]))
            if new:
                if thread_ts:
                    yield from (self.enrich_reply(r, channel_id=channel_id) for r in new)
                else:
                    yield from self.enrich(channel_id, new)
                cursor = str(new[-1]["ts"])
            time.sleep(wait)

    def get_replies(
        self,
        channel_id: str,
        thread_ts: str,
        oldest: str | None = None,
        include_parent: bool = False,
    ) -> list[dict[str, Any]]:
        raw = self._paginate(
            self.client.conversations_replies,
            {"channel": channel_id, "ts": thread_ts, "limit": 200, "include_all_metadata": True},
            oldest=oldest,
        )
        if include_parent:
            return raw
        return [m for m in raw if m.get("ts") != thread_ts]

    def resolve_mentions(self, text: str) -> str:
        """Replace <@UXXXXXXX> with @display_name."""
        return _MENTION_RE.sub(lambda m: f"@{self.get_user_name(m.group(1))}", text)

    def get_user_name(self, user_id: str) -> str:
        if user_id in self._user_cache:
            return self._user_cache[user_id]
        try:
            user_obj = dict(self.client.users_info(user=user_id, include_locale=True)["user"])
            user_obj.setdefault("id", user_id)
            self._cache_user_obj(user_obj)
        except Exception:
            self._user_cache[user_id] = user_id
        return self._user_cache[user_id]

    def _cache_user_obj(self, user_obj: dict[str, Any]) -> None:
        user_id = user_obj.get("id") or ""
        if not user_id:
            return
        p = user_obj.get("profile", {})
        name = p.get("display_name_normalized") or p.get("real_name") or user_id
        self._user_cache[user_id] = name
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
            "status_text_canonical": p.get("status_text_canonical", ""),
            "timezone": user_obj.get("tz", ""),
            "timezone_label": user_obj.get("tz_label", ""),
            "is_bot": user_obj.get("is_bot", False),
            "deleted": bool(user_obj.get("deleted")),
            "is_admin": bool(user_obj.get("is_admin")),
            "is_owner": bool(user_obj.get("is_owner")),
            "is_restricted": bool(user_obj.get("is_restricted")),
            "is_ultra_restricted": bool(user_obj.get("is_ultra_restricted")),
            "is_app_user": bool(user_obj.get("is_app_user")),
            "is_stranger": bool(user_obj.get("is_stranger")),
            "is_invited_user": bool(user_obj.get("is_invited_user")),
            "is_primary_owner": bool(user_obj.get("is_primary_owner")),
            "always_active": bool(user_obj.get("always_active")),
            "is_email_confirmed": bool(user_obj.get("is_email_confirmed")),
            "huddle_state": user_obj.get("huddle_state") or "",
            "huddle_state_expiration_ts": user_obj.get("huddle_state_expiration_ts") or 0,
            "who_can_share_contact_card": user_obj.get("who_can_share_contact_card") or "",
            "team_id": user_obj.get("team_id") or "",
            "is_forgotten": bool(user_obj.get("is_forgotten")),
            "is_workflow_bot": bool(user_obj.get("is_workflow_bot")),
            "has_2fa": bool(user_obj.get("has_2fa")),
            "two_factor_type": user_obj.get("two_factor_type") or "",
            "guest_invited_by": user_obj.get("guest_invited_by") or "",
            "is_connector": bool(user_obj.get("is_connector")),
            "enterprise_user": user_obj.get("enterprise_user") or {},
            "locale": user_obj.get("locale") or "",
            "color": user_obj.get("color") or "",
            "updated": user_obj.get("updated") or 0,
            "tz_offset": user_obj.get("tz_offset") or 0,
            "image": p.get("image_192") or p.get("image_72", ""),
            "image_192": p.get("image_192") or "",
            "first_name": p.get("first_name") or "",
            "last_name": p.get("last_name") or "",
            "skype": p.get("skype") or "",
            "status_expiration": p.get("status_expiration") or 0,
            "avatar_hash": p.get("avatar_hash") or "",
            "pronouns": p.get("pronouns") or "",
            "start_date": p.get("start_date") or "",
            "status_emoji_display_info": p.get("status_emoji_display_info") or [],
            "image_72": p.get("image_72") or "",
            "image_512": p.get("image_512") or "",
            "image_original": p.get("image_original") or "",
            "image_24": p.get("image_24") or "",
            "image_32": p.get("image_32") or "",
            "image_48": p.get("image_48") or "",
            "image_1024": p.get("image_1024") or "",
            "is_custom_image": bool(p.get("is_custom_image")),
            "fields": p.get("fields") or {},
            "display_name_normalized": p.get("display_name_normalized")
            or p.get("display_name")
            or "",
            "real_name_normalized": p.get("real_name_normalized") or p.get("real_name") or "",
            "guest_expiration_ts": p.get("guest_expiration_ts") or 0,
            "bot_id": p.get("bot_id") or "",
            "api_app_id": p.get("api_app_id") or "",
            "team": p.get("team") or "",
        }

    def get_user_profiles(self) -> dict[str, dict[str, Any]]:
        """Return all user profiles fetched so far, keyed by user ID."""
        return dict(self._profile_cache)

    def get_channel_info(self, channel_id: str) -> dict[str, Any]:
        return _channel_record(
            self.client.conversations_info(channel=channel_id, include_num_members=True)["channel"]
        )

    def get_channel_members(self, channel_id: str) -> list[str]:
        members: list[str] = []
        cursor = None
        while True:
            kwargs: dict[str, Any] = {"channel": channel_id, "limit": 200}
            if cursor:
                kwargs["cursor"] = cursor
            resp = self.client.conversations_members(**kwargs)
            members.extend(resp.get("members") or [])
            cursor = (resp.get("response_metadata") or {}).get("next_cursor") or ""
            if not cursor:
                break
            time.sleep(self.delay)
        return members

    def get_emoji(self) -> dict[str, str]:
        self._ensure_emoji()
        return self._emoji_cache or {}

    def get_emoji_categories(self) -> list[dict[str, Any]]:
        self._ensure_emoji()
        return list(self._emoji_categories or [])

    def _ensure_emoji(self) -> None:
        if self._emoji_cache is not None:
            return
        raw = self.client.emoji_list(include_categories=True)
        emoji = raw.get("emoji") or {}
        self._emoji_cache = {str(k): str(v) for k, v in emoji.items()}
        cats = raw.get("categories") or []
        self._emoji_categories = [c for c in cats if isinstance(c, dict)]

    def get_bookmarks(self, channel_id: str) -> list[dict[str, Any]]:
        return list(self.client.bookmarks_list(channel_id=channel_id).get("bookmarks") or [])

    def get_pins(self, channel_id: str) -> list[dict[str, Any]]:
        return list(self.client.pins_list(channel=channel_id).get("items") or [])

    def get_usergroups(self) -> list[dict[str, Any]]:
        if self._usergroups is None:
            raw = (
                self.client.usergroups_list(
                    include_users=True, include_count=True, include_disabled=True
                ).get("usergroups")
                or []
            )
            self._usergroups = list(raw)
        return self._usergroups

    def fetch_workspace_users(self) -> dict[str, dict[str, Any]]:
        if not self._users_listed:
            cursor = None
            while True:
                kwargs: dict[str, Any] = {"limit": 200, "include_locale": True}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = self.client.users_list(**kwargs)
                for user_obj in resp.get("members") or []:
                    if isinstance(user_obj, dict):
                        self._cache_user_obj(user_obj)
                cursor = (resp.get("response_metadata") or {}).get("next_cursor") or ""
                if not cursor:
                    break
                time.sleep(self.delay)
            self._users_listed = True
        return self.get_user_profiles()

    def list_conversations(self) -> list[dict[str, Any]]:
        if self._conversations is None:
            items: list[dict[str, Any]] = []
            cursor = None
            while True:
                kwargs: dict[str, Any] = {
                    "types": _ALL_CONV_TYPES,
                    "limit": 200,
                    "exclude_archived": False,
                }
                if cursor:
                    kwargs["cursor"] = cursor
                resp = self.client.conversations_list(**kwargs)
                for ch in resp.get("channels") or []:
                    if not isinstance(ch, dict):
                        continue
                    items.append(_channel_record(ch))
                cursor = (resp.get("response_metadata") or {}).get("next_cursor") or ""
                if not cursor:
                    break
                time.sleep(self.delay)
            self._conversations = items
        return self._conversations

    def get_stars(self) -> list[dict[str, Any]]:
        if self._stars is None:
            items: list[dict[str, Any]] = []
            cursor = None
            while True:
                kwargs: dict[str, Any] = {"limit": 100}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = self.client.stars_list(**kwargs)
                items.extend(resp.get("items") or [])
                cursor = (resp.get("response_metadata") or {}).get("next_cursor") or ""
                if not cursor:
                    break
                time.sleep(self.delay)
            self._stars = items
        return self._stars

    def get_reminders(self) -> list[dict[str, Any]]:
        if self._reminders is None:
            raw = self.client.reminders_list().get("reminders") or []
            self._reminders = list(raw)
        return self._reminders

    def get_dnd(self) -> dict[str, Any]:
        if self._dnd is None:
            raw = self.client.dnd_teamInfo().get("users") or {}
            self._dnd = dict(raw) if isinstance(raw, dict) else {}
        return self._dnd

    def get_team_profile(self) -> dict[str, Any]:
        if self._team_profile is None:
            raw = self.client.team_profile_get(visibility="all").get("profile") or {}
            self._team_profile = dict(raw) if isinstance(raw, dict) else {}
        return self._team_profile

    def get_team_info(self) -> dict[str, Any]:
        if self._team_info is None:
            raw = self.client.team_info().get("team") or {}
            self._team_info = dict(raw) if isinstance(raw, dict) else {}
        return self._team_info

    def get_files(self) -> list[dict[str, Any]]:
        if self._files_list is None:
            items: list[dict[str, Any]] = []
            page = 1
            while True:
                resp = self.client.files_list(count=100, page=page, show_files_hidden_by_limit=True)
                items.extend(resp.get("files") or [])
                paging = resp.get("paging") or {}
                pages = int(paging.get("pages") or 1)
                if page >= pages:
                    break
                page += 1
                time.sleep(self.delay)
            self._files_list = items
        return self._files_list

    def get_file_info(self, file_id: str) -> dict[str, Any]:
        raw = dict(self.client.files_info(file=file_id))
        file_obj = dict(raw.get("file") or {})
        comments = raw.get("comments")
        if comments is not None:
            file_obj["comments"] = comments
        return file_obj

    def get_remote_files(self) -> list[dict[str, Any]]:
        if self._remote_files is None:
            items: list[dict[str, Any]] = []
            cursor = None
            while True:
                kwargs: dict[str, Any] = {"limit": 100}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = self.client.files_remote_list(**kwargs)
                items.extend(resp.get("files") or [])
                cursor = (resp.get("response_metadata") or {}).get("next_cursor") or ""
                if not cursor:
                    break
                time.sleep(self.delay)
            self._remote_files = items
        return self._remote_files

    def get_presence(self, user: str | None = None) -> dict[str, Any]:
        key = user or ""
        stored = self._presence.get(key)
        if stored is None:
            kwargs: dict[str, Any] = {}
            if user:
                kwargs["user"] = user
            raw = dict(self.client.users_getPresence(**kwargs))
            raw.pop("ok", None)
            stored = raw
            self._presence[key] = stored
        return stored

    def get_billable_info(self) -> dict[str, Any]:
        if self._billable is None:
            raw = self.client.team_billableInfo().get("billable_info") or {}
            self._billable = dict(raw) if isinstance(raw, dict) else {}
        return self._billable

    def get_integration_logs(self) -> list[dict[str, Any]]:
        if self._integration_logs is None:
            items: list[dict[str, Any]] = []
            page = 1
            while True:
                resp = self.client.team_integrationLogs(count=100, page=page)
                items.extend(resp.get("logs") or [])
                paging = resp.get("paging") or {}
                pages = int(paging.get("pages") or 1)
                if page >= pages:
                    break
                page += 1
                time.sleep(self.delay)
            self._integration_logs = items
        return self._integration_logs

    def get_access_logs(self) -> list[dict[str, Any]]:
        if self._access_logs is None:
            items: list[dict[str, Any]] = []
            page = 1
            while True:
                resp = self.client.team_accessLogs(count=100, page=page)
                items.extend(resp.get("logins") or [])
                paging = resp.get("paging") or {}
                pages = int(paging.get("pages") or 1)
                if page >= pages:
                    break
                page += 1
                time.sleep(self.delay)
            self._access_logs = items
        return self._access_logs

    def get_team_preferences(self) -> dict[str, Any]:
        if self._team_prefs is None:
            raw = dict(self.client.api_call("team.preferences.list"))
            prefs = raw.get("prefs")
            self._team_prefs = dict(prefs) if isinstance(prefs, dict) else {}
        return self._team_prefs

    def get_external_teams(self) -> list[dict[str, Any]]:
        if self._external_teams is None:
            raw = dict(self.client.api_call("team.externalTeams.list"))
            teams = raw.get("teams")
            self._external_teams = list(teams) if isinstance(teams, list) else []
        return self._external_teams

    def get_scheduled_messages(self) -> list[dict[str, Any]]:
        if self._scheduled is None:
            items: list[dict[str, Any]] = []
            cursor = None
            while True:
                kwargs: dict[str, Any] = {"limit": 100}
                if cursor:
                    kwargs["cursor"] = cursor
                resp = self.client.chat_scheduledMessages_list(**kwargs)
                items.extend(resp.get("scheduled_messages") or [])
                cursor = (resp.get("response_metadata") or {}).get("next_cursor") or ""
                if not cursor:
                    break
                time.sleep(self.delay)
            self._scheduled = items
        return self._scheduled

    def enrich_reply(self, r: dict[str, Any], channel_id: str | None = None) -> dict[str, Any]:
        """Enrich a single reply dict — name resolution and mention substitution only,
        no recursive thread fetch (replies don't have sub-threads)."""
        user_id = r.get("user", "")
        raw_text = r.get("text") or ""
        out = {
            "ts": r["ts"],
            "user": user_id,
            "user_name": self.get_user_name(user_id) if user_id else "unknown",
            "text": self.resolve_mentions(raw_text),
            "text_raw": raw_text,
            "reactions": [
                {"name": rx["name"], "count": rx["count"], "users": rx.get("users", [])}
                for rx in r.get("reactions", [])
            ],
            "files": r.get("files", []),
        }
        for key in _PASSTHROUGH:
            if key in r:
                out[key] = r[key]
        if channel_id and r.get("ts"):
            try:
                team = self.get_workspace()
            except Exception:
                team = ""
            if team:
                stamp = str(r["ts"]).replace(".", "")
                out["permalink"] = f"https://{team}.slack.com/archives/{channel_id}/p{stamp}"
        return out

    def enrich(self, channel_id: str, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result = []
        for msg in messages:
            enriched = {**self.enrich_reply(msg, channel_id=channel_id), "thread": []}
            # reply_count can be null from the API (deleted thread) — guard with or 0
            if (msg.get("reply_count") or 0) > 0:
                raw_replies = self.get_replies(channel_id, msg["ts"])
                enriched["thread"] = [
                    self.enrich_reply(r, channel_id=channel_id) for r in raw_replies
                ]
            result.append(enriched)
        return result
