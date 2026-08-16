from unittest.mock import MagicMock

import pytest

from ssd.api import SlackAPI


@pytest.fixture
def mock_client(mocker):
    client = MagicMock()
    mocker.patch("ssd.api.WebClient", return_value=client)
    return client


def test_get_workspace(mock_client):
    mock_client.auth_test.return_value = {"team_domain": "acme"}
    api = SlackAPI("xoxd-fake")
    assert api.get_workspace() == "acme"


def test_resolve_channel_by_id(mock_client):
    mock_client.conversations_info.return_value = {"channel": {"id": "C123", "name": "general"}}
    api = SlackAPI("xoxd-fake")
    cid, name = api.resolve_channel("C123")
    assert cid == "C123"
    assert name == "general"


def test_resolve_channel_by_name(mock_client):
    mock_client.conversations_list.return_value = {
        "channels": [{"id": "C456", "name": "random"}],
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake")
    cid, name = api.resolve_channel("random")
    assert cid == "C456"
    assert name == "random"


def test_get_messages_paginates(mock_client):
    mock_client.conversations_history.side_effect = [
        {
            "messages": [{"ts": "1.0", "user": "U1", "text": "first", "reply_count": 0}],
            "has_more": True,
            "response_metadata": {"next_cursor": "cursor1"},
        },
        {
            "messages": [{"ts": "2.0", "user": "U2", "text": "second", "reply_count": 0}],
            "has_more": False,
            "response_metadata": {"next_cursor": ""},
        },
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    msgs = api.get_messages("C123")
    assert len(msgs) == 2
    assert mock_client.conversations_history.call_count == 2


def test_get_messages_requests_all_metadata(mock_client):
    mock_client.conversations_history.return_value = {
        "messages": [],
        "has_more": False,
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    api.get_messages("C123")
    kwargs = mock_client.conversations_history.call_args.kwargs
    assert kwargs["include_all_metadata"] is True


def test_get_channel_info_keeps_shared_flags(mock_client):
    mock_client.conversations_info.return_value = {
        "channel": {
            "id": "C123",
            "name": "lounge",
            "created": 10,
            "creator": "U1",
            "is_private": False,
            "is_shared": True,
            "is_ext_shared": True,
            "is_org_shared": True,
            "is_general": True,
            "is_pending_ext_shared": True,
            "is_pending_shared": True,
            "has_canvas": True,
            "is_im_blocked": True,
            "topic": {"value": "daily"},
            "purpose": {"value": "chat"},
            "num_members": 4,
        }
    }
    api = SlackAPI("xoxd-fake")
    info = api.get_channel_info("C123")
    assert info["is_shared"] is True
    assert info["is_ext_shared"] is True
    assert info["is_org_shared"] is True
    assert info["is_general"] is True
    assert info["is_pending_ext_shared"] is True
    assert info["is_pending_shared"] is True
    assert info["has_canvas"] is True
    assert info["is_im_blocked"] is True
    assert info["topic"] == "daily"


def test_list_conversations_keeps_shared_flags(mock_client):
    mock_client.conversations_list.return_value = {
        "channels": [
            {
                "id": "C1",
                "name": "a",
                "is_channel": True,
                "is_shared": True,
                "is_general": True,
            }
        ],
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    convos = api.list_conversations()
    assert convos[0]["is_shared"] is True
    assert convos[0]["is_general"] is True


def test_fetch_workspace_users_keeps_admin_guest(mock_client):
    mock_client.users_list.return_value = {
        "members": [
            {
                "id": "U1",
                "name": "guest",
                "is_admin": True,
                "is_owner": True,
                "is_restricted": True,
                "is_ultra_restricted": True,
                "is_app_user": True,
                "profile": {"display_name_normalized": "guest"},
            }
        ],
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    profiles = api.fetch_workspace_users()
    assert profiles["U1"]["is_admin"] is True
    assert profiles["U1"]["is_owner"] is True
    assert profiles["U1"]["is_restricted"] is True
    assert profiles["U1"]["is_ultra_restricted"] is True
    assert profiles["U1"]["is_app_user"] is True


def test_fetch_workspace_users_requests_locale(mock_client):
    mock_client.users_list.return_value = {
        "members": [
            {
                "id": "U1",
                "name": "alice",
                "color": "9f69e7",
                "updated": 99,
                "tz_offset": 3600,
                "locale": "en-US",
                "profile": {"display_name_normalized": "alice"},
            }
        ],
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    profiles = api.fetch_workspace_users()
    assert mock_client.users_list.call_args.kwargs["include_locale"] is True
    assert profiles["U1"]["locale"] == "en-US"
    assert profiles["U1"]["color"] == "9f69e7"
    assert profiles["U1"]["updated"] == 99
    assert profiles["U1"]["tz_offset"] == 3600


def test_fetch_workspace_users_keeps_stranger(mock_client):
    mock_client.users_list.return_value = {
        "members": [
            {
                "id": "U1",
                "name": "alice",
                "is_stranger": True,
                "is_invited_user": True,
                "is_primary_owner": True,
                "always_active": True,
                "is_email_confirmed": True,
                "huddle_state": "in_a_huddle",
                "huddle_state_expiration_ts": 1700000000,
                "who_can_share_contact_card": "EVERYONE",
                "team_id": "T99",
                "is_forgotten": True,
                "is_workflow_bot": True,
                "has_2fa": True,
                "two_factor_type": "sms",
                "guest_invited_by": "U9",
                "is_connector": True,
                "enterprise_user": {"id": "EU1", "enterprise_id": "E1", "enterprise_name": "Grid"},
                "profile": {
                    "display_name_normalized": "alice",
                    "guest_expiration_ts": 99,
                    "bot_id": "B99",
                    "api_app_id": "A99",
                    "team": "T99",
                },
            }
        ],
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    profiles = api.fetch_workspace_users()
    assert profiles["U1"]["is_stranger"] is True
    assert profiles["U1"]["is_invited_user"] is True
    assert profiles["U1"]["is_primary_owner"] is True
    assert profiles["U1"]["always_active"] is True
    assert profiles["U1"]["is_email_confirmed"] is True
    assert profiles["U1"]["huddle_state"] == "in_a_huddle"
    assert profiles["U1"]["who_can_share_contact_card"] == "EVERYONE"
    assert profiles["U1"]["huddle_state_expiration_ts"] == 1700000000
    assert profiles["U1"]["team_id"] == "T99"
    assert profiles["U1"]["is_forgotten"] is True
    assert profiles["U1"]["is_workflow_bot"] is True
    assert profiles["U1"]["has_2fa"] is True
    assert profiles["U1"]["two_factor_type"] == "sms"
    assert profiles["U1"]["guest_invited_by"] == "U9"
    assert profiles["U1"]["is_connector"] is True
    assert profiles["U1"]["guest_expiration_ts"] == 99
    assert profiles["U1"]["bot_id"] == "B99"
    assert profiles["U1"]["api_app_id"] == "A99"
    assert profiles["U1"]["team"] == "T99"
    assert profiles["U1"]["enterprise_user"]["enterprise_id"] == "E1"


def test_fetch_workspace_users_keeps_profile_names(mock_client):
    mock_client.users_list.return_value = {
        "members": [
            {
                "id": "U1",
                "name": "alice",
                "profile": {
                    "display_name_normalized": "alice",
                    "first_name": "Alice",
                    "last_name": "Smith",
                    "skype": "alice.s",
                    "status_expiration": 99,
                    "avatar_hash": "abc",
                    "pronouns": "she/her",
                    "start_date": "2020-01-15",
                    "status_emoji_display_info": [{"emoji_name": "wave"}],
                    "status_text_canonical": "",
                    "image_72": "https://a.test/72.png",
                    "image_512": "https://a.test/512.png",
                    "image_original": "https://a.test/orig.png",
                    "image_24": "https://a.test/24.png",
                    "image_32": "https://a.test/32.png",
                    "image_48": "https://a.test/48.png",
                    "image_1024": "https://a.test/1024.png",
                    "image_192": "https://a.test/192.png",
                    "is_custom_image": True,
                    "fields": {"Xf1": {"value": "eng", "alt": ""}},
                    "real_name_normalized": "Alice Smith",
                },
            }
        ],
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    profiles = api.fetch_workspace_users()
    assert profiles["U1"]["first_name"] == "Alice"
    assert profiles["U1"]["last_name"] == "Smith"
    assert profiles["U1"]["skype"] == "alice.s"
    assert profiles["U1"]["status_expiration"] == 99
    assert profiles["U1"]["avatar_hash"] == "abc"
    assert profiles["U1"]["pronouns"] == "she/her"
    assert profiles["U1"]["start_date"] == "2020-01-15"
    assert profiles["U1"]["status_emoji_display_info"] == [{"emoji_name": "wave"}]
    assert profiles["U1"]["status_text_canonical"] == ""
    assert profiles["U1"]["image_72"] == "https://a.test/72.png"
    assert profiles["U1"]["image_512"] == "https://a.test/512.png"
    assert profiles["U1"]["image_original"] == "https://a.test/orig.png"
    assert profiles["U1"]["image_24"] == "https://a.test/24.png"
    assert profiles["U1"]["image_32"] == "https://a.test/32.png"
    assert profiles["U1"]["image_48"] == "https://a.test/48.png"
    assert profiles["U1"]["image_1024"] == "https://a.test/1024.png"
    assert profiles["U1"]["image_192"] == "https://a.test/192.png"
    assert profiles["U1"]["is_custom_image"] is True
    assert profiles["U1"]["fields"]["Xf1"]["value"] == "eng"
    assert profiles["U1"]["display_name_normalized"] == "alice"
    assert profiles["U1"]["real_name_normalized"] == "Alice Smith"


def test_get_channel_info_requests_num_members(mock_client):
    mock_client.conversations_info.return_value = {
        "channel": {
            "id": "C123",
            "name": "lounge",
            "locale": "en-US",
            "updated": 50,
            "previous_names": ["old-lounge"],
            "unlinked": 0,
            "is_member": True,
            "conversation_host_id": "T1",
            "connected_team_ids": ["Tother"],
            "internal_team_ids": ["T1"],
            "pending_shared": ["Tpending"],
            "parent_conversation": "Cparent",
            "context_team_id": "T1",
            "is_open": True,
            "is_org_default": True,
            "is_frozen": True,
            "is_global_shared": True,
            "is_org_mandatory": True,
            "is_read_only": True,
            "is_thread_only": True,
            "is_non_threadable": True,
            "shared_team_ids": ["T1", "Tother"],
            "pending_connected_team_ids": ["Tconn"],
            "connected_limited_team_ids": ["Tlim"],
            "properties": {"tabs": [{"id": "files", "label": "Files", "type": "files"}]},
            "priority": 1.5,
            "name_normalized": "lounge",
            "user": "U2",
            "is_user_deleted": True,
            "is_muted": True,
            "is_starred": True,
            "is_moved": 3,
            "use_case": "huddles",
            "last_read": "8.0",
            "unread_count": 4,
            "unread_count_display": 3,
            "latest": {"ts": "9.0", "text": "hi", "type": "message", "user": "U1"},
            "enterprise_id": "E1",
            "file_id": "Fcanvas",
            "topic": {"value": ""},
            "purpose": {"value": ""},
        }
    }
    api = SlackAPI("xoxd-fake")
    info = api.get_channel_info("C123")
    assert mock_client.conversations_info.call_args.kwargs["include_num_members"] is True
    assert info["locale"] == "en-US"
    assert info["updated"] == 50
    assert info["previous_names"] == ["old-lounge"]
    assert info["is_member"] is True
    assert info["conversation_host_id"] == "T1"
    assert info["connected_team_ids"] == ["Tother"]
    assert info["internal_team_ids"] == ["T1"]
    assert info["pending_shared"] == ["Tpending"]
    assert info["parent_conversation"] == "Cparent"
    assert info["context_team_id"] == "T1"
    assert info["is_open"] is True
    assert info["is_org_default"] is True
    assert info["is_frozen"] is True
    assert info["is_global_shared"] is True
    assert info["is_org_mandatory"] is True
    assert info["is_read_only"] is True
    assert info["is_thread_only"] is True
    assert info["is_non_threadable"] is True
    assert info["shared_team_ids"] == ["T1", "Tother"]
    assert info["pending_connected_team_ids"] == ["Tconn"]
    assert info["connected_limited_team_ids"] == ["Tlim"]
    assert info["properties"]["tabs"][0]["type"] == "files"
    assert info["priority"] == 1.5
    assert info["name_normalized"] == "lounge"
    assert info["user"] == "U2"
    assert info["is_user_deleted"] is True
    assert info["is_muted"] is True
    assert info["is_starred"] is True
    assert info["is_moved"] == 3
    assert info["use_case"] == "huddles"
    assert info["last_read"] == "8.0"
    assert info["unread_count"] == 4
    assert info["unread_count_display"] == 3
    assert info["latest"]["ts"] == "9.0"
    assert info["enterprise_id"] == "E1"
    assert info["file_id"] == "Fcanvas"


def test_get_user_name_cached(mock_client):
    mock_client.users_info.return_value = {
        "user": {"profile": {"display_name_normalized": "alice", "real_name": "Alice Smith"}}
    }
    api = SlackAPI("xoxd-fake")
    name1 = api.get_user_name("U001")
    api.get_user_name("U001")
    assert name1 == "alice"
    assert mock_client.users_info.call_count == 1  # cached


def test_get_user_name_falls_back_to_real_name(mock_client):
    mock_client.users_info.return_value = {
        "user": {"profile": {"display_name_normalized": "", "real_name": "Bob Jones"}}
    }
    api = SlackAPI("xoxd-fake")
    assert api.get_user_name("U002") == "Bob Jones"


def test_get_replies_excludes_root(mock_client):
    mock_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1.0", "user": "U1", "text": "root"},
            {"ts": "1.1", "user": "U2", "text": "reply"},
        ],
        "has_more": False,
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    replies = api.get_replies("C123", "1.0")
    assert len(replies) == 1
    assert replies[0]["ts"] == "1.1"


def test_get_replies_passes_oldest(mock_client):
    mock_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1.0", "user": "U1", "text": "root"},
            {"ts": "1.5", "user": "U2", "text": "new reply"},
        ],
        "has_more": False,
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    api.get_replies("C123", "1.0", oldest="1.2")
    call_kwargs = mock_client.conversations_replies.call_args[1]
    assert call_kwargs.get("oldest") == "1.2"


def test_enrich_adds_user_name_and_thread(mock_client):
    # users_info for U1
    mock_client.users_info.return_value = {
        "user": {"profile": {"display_name_normalized": "alice", "real_name": "Alice Smith"}}
    }
    # replies for the thread message
    mock_client.conversations_replies.return_value = {
        "messages": [
            {"ts": "1.0", "user": "U1", "text": "root"},
            {"ts": "1.1", "user": "U1", "text": "reply one"},
        ],
        "has_more": False,
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    messages = [
        {"ts": "1.0", "user": "U1", "text": "root message", "reply_count": 1},
    ]
    enriched = api.enrich("C123", messages)
    assert len(enriched) == 1
    msg = enriched[0]
    assert msg["user_name"] == "alice"
    assert len(msg["thread"]) == 1
    assert msg["thread"][0]["ts"] == "1.1"
    assert msg["thread"][0]["user_name"] == "alice"


def test_enrich_no_replies(mock_client):
    mock_client.users_info.return_value = {
        "user": {"profile": {"display_name_normalized": "bob", "real_name": "Bob Jones"}}
    }
    api = SlackAPI("xoxd-fake", delay=0)
    messages = [
        {"ts": "2.0", "user": "U2", "text": "standalone", "reply_count": 0},
    ]
    enriched = api.enrich("C999", messages)
    assert len(enriched) == 1
    assert enriched[0]["thread"] == []
    assert enriched[0]["user_name"] == "bob"
    mock_client.conversations_replies.assert_not_called()


def test_enrich_bot_message_no_user(mock_client):
    """Bot/app messages with no user field should produce user_name='unknown' and be included."""
    api = SlackAPI("xoxd-fake", delay=0)
    messages = [{"ts": "1.0", "text": "bot msg", "reactions": [], "files": []}]  # no 'user' key
    result = api.enrich("C123", messages)
    assert len(result) == 1
    assert result[0]["user_name"] == "unknown"
    assert result[0]["text"] == "bot msg"
    mock_client.users_info.assert_not_called()


def test_enrich_preserves_subtype_bot_id_blocks_pinned(mock_client):
    api = SlackAPI("xoxd-fake", delay=0)
    messages = [
        {
            "ts": "1.0",
            "user": "U1",
            "text": "bot post",
            "subtype": "bot_message",
            "bot_id": "B99",
            "app_id": "A99",
            "blocks": [{"type": "section"}],
            "pinned_to": ["C123"],
            "edited": {"ts": "1.1", "user": "U1"},
            "reply_count": 0,
            "reactions": [],
            "files": [],
            "room": {"id": "R1", "name": "standup"},
            "reply_broadcast": True,
            "hidden": True,
            "bot_profile": {"id": "B99", "name": "deploybot"},
            "metadata": {"event_type": "task_created"},
            "x_files": ["Fgone"],
            "root": {"ts": "0.9", "text": "parent"},
            "display_as_bot": True,
            "event_ts": "1.0",
            "inviter": "U9",
            "upload": True,
            "source_team": "T1",
            "user_team": "T1",
            "topic": "new topic",
            "purpose": "new purpose",
            "old_name": "old-general",
            "name": "general",
            "comment": {"comment": "nice file"},
            "no_notifications": True,
            "is_starred": True,
            "bot_link": "https://acme.slack.com/services/B99",
            "icons": {"image_48": "https://emoji.test/bot.png"},
            "file": {"id": "Fsolo", "name": "solo.png"},
            "language": "en",
            "is_intro": True,
            "assistant_app_thread": {"title": "help"},
            "connected_team_ids": ["Tother"],
            "is_ephemeral": True,
            "pending_shared": ["Tpending"],
            "file_id": "Fsolo",
            "is_moved": 1,
            "parent_conversation": "Cparent",
            "is_delayed_message": True,
            "scheduled_message_id": "Q9",
            "pending_connected_team_ids": ["Tconn"],
            "channel_type": "channel",
            "no_display": True,
            "is_thread_mention": True,
            "permalink_public": "https://acme.slack.com/p1",
            "skip_channel_mention_warning": True,
            "is_auto_split": True,
            "unfurl_links": True,
            "unfurl_media": False,
            "thread_broadcast": True,
            "is_limited": True,
            "item_type": "file",
            "item": {"id": "Fsolo"},
            "replies": [{"user": "U2", "ts": "1.1"}],
            "deleted_ts": "1.2",
            "hidden_by": "U9",
            "with_files": True,
            "signature": "v0=abc",
            "preview": "see attached",
            "last_read": "1.0",
            "unread_count": 2,
            "unread_count_display": 1,
            "is_restricted": True,
            "preview_highlights": "see",
            "plain_text": "see attached",
            "is_unlocked": True,
            "preview_plain_text": "attached",
            "lines": 3,
            "lines_more": 1,
            "num_stars": 2,
            "permalink": "https://acme.slack.com/archives/C123/p1",
            "user_profile": {"name": "alice", "real_name": "Alice"},
            "is_tombstone": True,
            "members": ["U1", "U2"],
            "source_team_id": "T1",
            "user_team_id": "T1",
            "is_channel_mention": True,
            "parse": "full",
            "mrkdwn": True,
            "item_user": "U2",
            "old_topic": "old topic",
            "old_purpose": "old purpose",
            "invited_user": "U3",
            "deleted": True,
            "is_highlighted": True,
            "client_context_team_id": "T1",
            "saved": True,
            "local_files": [{"id": "Flocal"}],
            "shares": {"C123": [{"ts": "1.0"}]},
            "app_unfurl_url": "https://example.test/unfurl",
            "thread_ts": "0.9",
            "is_share": True,
        }
    ]
    mock_client.users_info.return_value = {
        "user": {"profile": {"display_name_normalized": "alice", "real_name": "Alice"}}
    }
    result = api.enrich("C123", messages)[0]
    assert result["subtype"] == "bot_message"
    assert result["bot_id"] == "B99"
    assert result["app_id"] == "A99"
    assert result["blocks"] == [{"type": "section"}]
    assert result["pinned_to"] == ["C123"]
    assert result["edited"]["ts"] == "1.1"
    assert result["room"]["id"] == "R1"
    assert result["reply_broadcast"] is True
    assert result["hidden"] is True
    assert result["bot_profile"]["name"] == "deploybot"
    assert result["metadata"]["event_type"] == "task_created"
    assert result["x_files"] == ["Fgone"]
    assert result["root"]["ts"] == "0.9"
    assert result["display_as_bot"] is True
    assert result["event_ts"] == "1.0"
    assert result["inviter"] == "U9"
    assert result["upload"] is True
    assert result["source_team"] == "T1"
    assert result["user_team"] == "T1"
    assert result["topic"] == "new topic"
    assert result["purpose"] == "new purpose"
    assert result["old_name"] == "old-general"
    assert result["name"] == "general"
    assert result["comment"]["comment"] == "nice file"
    assert result["no_notifications"] is True
    assert result["is_starred"] is True
    assert result["bot_link"].endswith("/B99")
    assert result["icons"]["image_48"].endswith("/bot.png")
    assert result["file"]["id"] == "Fsolo"
    assert result["language"] == "en"
    assert result["is_intro"] is True
    assert result["assistant_app_thread"]["title"] == "help"
    assert result["connected_team_ids"] == ["Tother"]
    assert result["is_ephemeral"] is True
    assert result["pending_shared"] == ["Tpending"]
    assert result["file_id"] == "Fsolo"
    assert result["is_moved"] == 1
    assert result["parent_conversation"] == "Cparent"
    assert result["is_delayed_message"] is True
    assert result["scheduled_message_id"] == "Q9"
    assert result["pending_connected_team_ids"] == ["Tconn"]
    assert result["channel_type"] == "channel"
    assert result["no_display"] is True
    assert result["is_thread_mention"] is True
    assert result["permalink_public"] == "https://acme.slack.com/p1"
    assert result["skip_channel_mention_warning"] is True
    assert result["is_auto_split"] is True
    assert result["unfurl_links"] is True
    assert result["unfurl_media"] is False
    assert result["thread_broadcast"] is True
    assert result["is_limited"] is True
    assert result["item_type"] == "file"
    assert result["item"]["id"] == "Fsolo"
    assert result["replies"][0]["ts"] == "1.1"
    assert result["deleted_ts"] == "1.2"
    assert result["hidden_by"] == "U9"
    assert result["with_files"] is True
    assert result["signature"] == "v0=abc"
    assert result["preview"] == "see attached"
    assert result["last_read"] == "1.0"
    assert result["unread_count"] == 2
    assert result["unread_count_display"] == 1
    assert result["is_restricted"] is True
    assert result["preview_highlights"] == "see"
    assert result["plain_text"] == "see attached"
    assert result["is_unlocked"] is True
    assert result["preview_plain_text"] == "attached"
    assert result["lines"] == 3
    assert result["lines_more"] == 1
    assert result["num_stars"] == 2
    assert result["permalink"].endswith("/p1")
    assert result["user_profile"]["name"] == "alice"
    assert result["is_tombstone"] is True
    assert result["members"] == ["U1", "U2"]
    assert result["source_team_id"] == "T1"
    assert result["user_team_id"] == "T1"
    assert result["is_channel_mention"] is True
    assert result["parse"] == "full"
    assert result["mrkdwn"] is True
    assert result["item_user"] == "U2"
    assert result["old_topic"] == "old topic"
    assert result["old_purpose"] == "old purpose"
    assert result["invited_user"] == "U3"
    assert result["deleted"] is True
    assert result["is_highlighted"] is True
    assert result["client_context_team_id"] == "T1"
    assert result["saved"] is True
    assert result["local_files"][0]["id"] == "Flocal"
    assert result["shares"]["C123"][0]["ts"] == "1.0"
    assert result["app_unfurl_url"] == "https://example.test/unfurl"
    assert result["thread_ts"] == "0.9"
    assert result["is_share"] is True


def test_enrich_adds_permalink(mock_client):
    mock_client.auth_test.return_value = {"team_domain": "acme"}
    mock_client.users_info.return_value = {
        "user": {"profile": {"display_name_normalized": "alice", "real_name": "Alice"}}
    }
    api = SlackAPI("xoxd-fake", delay=0)
    result = api.enrich(
        "C123",
        [{"ts": "1.2", "user": "U1", "text": "hi", "reply_count": 0, "reactions": [], "files": []}],
    )[0]
    assert result["permalink"] == "https://acme.slack.com/archives/C123/p12"


def test_get_workspace_raises_on_empty_domain(mock_client):
    """get_workspace should raise RuntimeError when no domain can be derived."""
    mock_client.auth_test.return_value = {"ok": True, "url": ""}
    api = SlackAPI("xoxd-fake")
    with pytest.raises(RuntimeError, match="Could not determine workspace domain"):
        api.get_workspace()


def test_get_channel_info_flattens_topic(mock_client):
    mock_client.conversations_info.return_value = {
        "channel": {
            "id": "C123",
            "name": "general",
            "created": 10,
            "creator": "U1",
            "is_private": True,
            "topic": {"value": "daily", "creator": "U9", "last_set": 11},
            "purpose": {"value": "chat", "creator": "U8", "last_set": 12},
            "num_members": 4,
        }
    }
    api = SlackAPI("xoxd-fake")
    info = api.get_channel_info("C123")
    assert info["topic"] == "daily"
    assert info["purpose"] == "chat"
    assert info["topic_creator"] == "U9"
    assert info["topic_last_set"] == 11
    assert info["purpose_creator"] == "U8"
    assert info["purpose_last_set"] == 12
    assert info["is_private"] is True
    assert info["num_members"] == 4


def test_get_channel_members_paginates(mock_client):
    mock_client.conversations_members.side_effect = [
        {"members": ["U1"], "response_metadata": {"next_cursor": "c2"}},
        {"members": ["U2"], "response_metadata": {"next_cursor": ""}},
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    assert api.get_channel_members("C123") == ["U1", "U2"]
    assert mock_client.conversations_members.call_count == 2


def test_get_emoji_cached(mock_client):
    mock_client.emoji_list.return_value = {
        "emoji": {"shipit": "https://e.test/s.png"},
        "categories": [{"name": "Custom", "emoji_names": ["shipit"]}],
    }
    api = SlackAPI("xoxd-fake")
    first = api.get_emoji()
    api.get_emoji()
    assert first["shipit"] == "https://e.test/s.png"
    assert api.get_emoji_categories() == [{"name": "Custom", "emoji_names": ["shipit"]}]
    assert mock_client.emoji_list.call_count == 1
    assert mock_client.emoji_list.call_args.kwargs.get("include_categories") is True


def test_get_auth_uses_auth_test(mock_client):
    mock_client.auth_test.return_value = {
        "ok": True,
        "url": "https://acme.slack.com/",
        "team": "Acme",
        "team_id": "T99",
        "user": "alice",
        "user_id": "U1",
        "team_domain": "acme",
        "enterprise_id": "E99",
        "is_enterprise_install": True,
    }
    api = SlackAPI("xoxd-fake")
    assert api.get_workspace() == "acme"
    auth = api.get_auth()
    assert auth["team_id"] == "T99"
    assert auth["user_id"] == "U1"
    assert auth["enterprise_id"] == "E99"
    assert auth["is_enterprise_install"] is True
    assert mock_client.auth_test.call_count == 1


def test_get_auth_teams(mock_client):
    mock_client.auth_teams_list.return_value = {
        "teams": [{"id": "T1", "name": "Acme"}, {"id": "T2", "name": "Other"}],
        "response_metadata": {"next_cursor": ""},
    }
    api = SlackAPI("xoxd-fake", delay=0)
    teams = api.get_auth_teams()
    assert [t["id"] for t in teams] == ["T1", "T2"]
    api.get_auth_teams()
    assert mock_client.auth_teams_list.call_count == 1
    assert mock_client.auth_teams_list.call_args.kwargs.get("include_icon") is True


def test_get_usergroups_requests_count(mock_client):
    mock_client.usergroups_list.return_value = {"usergroups": []}
    SlackAPI("xoxd-fake", delay=0).get_usergroups()
    kwargs = mock_client.usergroups_list.call_args.kwargs
    assert kwargs["include_users"] is True
    assert kwargs["include_count"] is True
    assert kwargs["include_disabled"] is True


def test_get_bookmarks(mock_client):
    mock_client.bookmarks_list.return_value = {"bookmarks": [{"id": "Bk1"}]}
    api = SlackAPI("xoxd-fake")
    assert api.get_bookmarks("C123") == [{"id": "Bk1"}]


def test_get_pins(mock_client):
    mock_client.pins_list.return_value = {"items": [{"type": "message", "channel": "C123"}]}
    api = SlackAPI("xoxd-fake")
    assert api.get_pins("C123")[0]["type"] == "message"


def test_get_usergroups_cached(mock_client):
    mock_client.usergroups_list.return_value = {"usergroups": [{"id": "S1"}]}
    api = SlackAPI("xoxd-fake")
    assert api.get_usergroups()[0]["id"] == "S1"
    api.get_usergroups()
    assert mock_client.usergroups_list.call_count == 1


def test_fetch_workspace_users_paginates(mock_client):
    mock_client.users_list.side_effect = [
        {
            "members": [
                {"id": "U1", "name": "alice", "profile": {"display_name_normalized": "alice"}}
            ],
            "response_metadata": {"next_cursor": "c2"},
        },
        {
            "members": [{"id": "U2", "name": "bob", "profile": {"display_name_normalized": "bob"}}],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    profiles = api.fetch_workspace_users()
    assert set(profiles) == {"U1", "U2"}
    api.fetch_workspace_users()
    assert mock_client.users_list.call_count == 2


def test_list_conversations_paginates(mock_client):
    mock_client.conversations_list.side_effect = [
        {
            "channels": [{"id": "C1", "name": "a", "is_private": False, "is_channel": True}],
            "response_metadata": {"next_cursor": "n"},
        },
        {
            "channels": [{"id": "D1", "name": "", "is_im": True, "is_private": True}],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    convos = api.list_conversations()
    assert [c["id"] for c in convos] == ["C1", "D1"]
    assert convos[1]["is_im"] is True
    api.list_conversations()
    assert mock_client.conversations_list.call_count == 2


def test_get_dnd_cached(mock_client):
    mock_client.dnd_teamInfo.return_value = {
        "users": {"U1": {"dnd_enabled": True, "next_dnd_start_ts": 9}}
    }
    api = SlackAPI("xoxd-fake")
    assert api.get_dnd()["U1"]["dnd_enabled"] is True
    api.get_dnd()
    assert mock_client.dnd_teamInfo.call_count == 1


def test_get_team_profile_cached(mock_client):
    mock_client.team_profile_get.return_value = {"profile": {"fields": [{"id": "Xf1"}]}}
    api = SlackAPI("xoxd-fake")
    assert api.get_team_profile()["fields"][0]["id"] == "Xf1"
    api.get_team_profile()
    assert mock_client.team_profile_get.call_count == 1
    assert mock_client.team_profile_get.call_args.kwargs.get("visibility") == "all"


def test_get_scheduled_messages_paginates(mock_client):
    mock_client.chat_scheduledMessages_list.side_effect = [
        {
            "scheduled_messages": [{"id": "Q1", "channel_id": "C1"}],
            "response_metadata": {"next_cursor": "n"},
        },
        {
            "scheduled_messages": [{"id": "Q2", "channel_id": "C2"}],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    items = api.get_scheduled_messages()
    assert [m["id"] for m in items] == ["Q1", "Q2"]
    api.get_scheduled_messages()
    assert mock_client.chat_scheduledMessages_list.call_count == 2


def test_get_team_info_cached(mock_client):
    mock_client.team_info.return_value = {
        "team": {"id": "T9", "name": "Acme", "domain": "acme", "email_domain": "acme.test"}
    }
    api = SlackAPI("xoxd-fake")
    assert api.get_team_info()["email_domain"] == "acme.test"
    api.get_team_info()
    assert mock_client.team_info.call_count == 1


def test_get_files_paginates(mock_client):
    mock_client.files_list.side_effect = [
        {"files": [{"id": "F1"}], "paging": {"pages": 2, "page": 1}},
        {"files": [{"id": "F2"}], "paging": {"pages": 2, "page": 2}},
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    files = api.get_files()
    assert [f["id"] for f in files] == ["F1", "F2"]
    api.get_files()
    assert mock_client.files_list.call_count == 2
    assert mock_client.files_list.call_args.kwargs.get("show_files_hidden_by_limit") is True


def test_get_remote_files_paginates(mock_client):
    mock_client.files_remote_list.side_effect = [
        {
            "files": [{"id": "Fr1", "is_external": True}],
            "response_metadata": {"next_cursor": "c2"},
        },
        {
            "files": [{"id": "Fr2", "is_external": True}],
            "response_metadata": {"next_cursor": ""},
        },
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    files = api.get_remote_files()
    assert [f["id"] for f in files] == ["Fr1", "Fr2"]
    api.get_remote_files()
    assert mock_client.files_remote_list.call_count == 2


def test_get_presence_cached(mock_client):
    mock_client.users_getPresence.return_value = {"ok": True, "presence": "away", "online": False}
    api = SlackAPI("xoxd-fake")
    first = api.get_presence("U1")
    api.get_presence("U1")
    assert first["presence"] == "away"
    assert "ok" not in first
    assert mock_client.users_getPresence.call_count == 1


def test_get_team_preferences_cached(mock_client):
    mock_client.api_call.return_value = {
        "ok": True,
        "prefs": {"msg_edit_window_mins": "0"},
    }
    api = SlackAPI("xoxd-fake")
    prefs = api.get_team_preferences()
    api.get_team_preferences()
    assert prefs["msg_edit_window_mins"] == "0"
    mock_client.api_call.assert_called_once_with("team.preferences.list")


def test_get_external_teams_cached(mock_client):
    mock_client.api_call.return_value = {
        "ok": True,
        "teams": [{"id": "E1", "name": "Partner"}],
    }
    api = SlackAPI("xoxd-fake")
    teams = api.get_external_teams()
    api.get_external_teams()
    assert teams[0]["id"] == "E1"
    mock_client.api_call.assert_called_once_with("team.externalTeams.list")


def test_get_billable_info_cached(mock_client):
    mock_client.team_billableInfo.return_value = {
        "billable_info": {"U1": {"billing_active": True}}
    }
    api = SlackAPI("xoxd-fake")
    info = api.get_billable_info()
    api.get_billable_info()
    assert info["U1"]["billing_active"] is True
    assert mock_client.team_billableInfo.call_count == 1


def test_get_integration_logs_paginates(mock_client):
    mock_client.team_integrationLogs.side_effect = [
        {"logs": [{"service_id": "S1"}], "paging": {"pages": 2, "page": 1}},
        {"logs": [{"service_id": "S2"}], "paging": {"pages": 2, "page": 2}},
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    logs = api.get_integration_logs()
    assert [row["service_id"] for row in logs] == ["S1", "S2"]
    api.get_integration_logs()
    assert mock_client.team_integrationLogs.call_count == 2


def test_get_access_logs_paginates(mock_client):
    mock_client.team_accessLogs.side_effect = [
        {"logins": [{"ip": "1.1.1.1"}], "paging": {"pages": 2, "page": 1}},
        {"logins": [{"ip": "2.2.2.2"}], "paging": {"pages": 2, "page": 2}},
    ]
    api = SlackAPI("xoxd-fake", delay=0)
    rows = api.get_access_logs()
    assert [row["ip"] for row in rows] == ["1.1.1.1", "2.2.2.2"]
    api.get_access_logs()
    assert mock_client.team_accessLogs.call_count == 2
