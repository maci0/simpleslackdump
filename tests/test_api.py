import pytest
from unittest.mock import MagicMock, call, patch
from ssd.api import SlackAPI


@pytest.fixture
def mock_client(mocker):
    client = MagicMock()
    mocker.patch("ssd.api.WebClient", return_value=client)
    return client


def test_get_workspace(mock_client):
    mock_client.auth_test.return_value = {"team_domain": "redhat"}
    api = SlackAPI("xoxd-fake")
    assert api.get_workspace() == "redhat"


def test_resolve_channel_by_id(mock_client):
    mock_client.conversations_info.return_value = {
        "channel": {"id": "C123", "name": "general"}
    }
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


def test_get_user_name_cached(mock_client):
    mock_client.users_info.return_value = {
        "user": {"profile": {"display_name_normalized": "alice", "real_name": "Alice Smith"}}
    }
    api = SlackAPI("xoxd-fake")
    name1 = api.get_user_name("U001")
    name2 = api.get_user_name("U001")
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
