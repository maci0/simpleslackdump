import json
from unittest.mock import MagicMock

import pytest

from ssd.dump import run_dump


@pytest.fixture
def mock_api():
    api = MagicMock()
    api.get_workspace.return_value = "testteam"
    api.resolve_channel.return_value = ("C123", "general")
    api.get_messages.return_value = [
        {"ts": "1705320720.000000", "user": "U1", "text": "hi", "reply_count": 0}
    ]
    api.enrich.return_value = [
        {
            "ts": "1705320720.000000",
            "user": "U1",
            "user_name": "alice",
            "text": "hi",
            "reactions": [],
            "thread": [],
        }
    ]
    api.get_channel_info.return_value = {
        "id": "C123",
        "name": "general",
        "topic": "talk",
        "purpose": "chat",
        "is_private": False,
        "created": 1,
        "num_members": 2,
        "creator": "U1",
    }
    api.get_channel_members.return_value = ["U1", "U2"]
    api.get_emoji.return_value = {"shipit": "https://emoji.test/shipit.png"}
    api.get_emoji_categories.return_value = [{"name": "Custom", "emoji_names": ["shipit"]}]
    api.get_auth.return_value = {
        "ok": True,
        "url": "https://testteam.slack.com/",
        "team": "testteam",
        "team_id": "T1",
        "user": "alice",
        "user_id": "U1",
    }
    api.get_bookmarks.return_value = [{"id": "Bk1", "title": "docs", "type": "link"}]
    api.get_pins.return_value = [{"type": "message", "channel": "C123"}]
    api.get_usergroups.return_value = [{"id": "S1", "handle": "eng"}]
    api.fetch_workspace_users.return_value = {
        "U1": {
            "id": "U1",
            "handle": "alice",
            "display_name": "alice",
            "real_name": "Alice",
            "title": "",
            "email": "alice@test",
            "phone": "",
            "status_text": "",
            "status_emoji": "",
            "timezone": "",
            "timezone_label": "",
            "is_bot": False,
            "image": "",
        }
    }
    api.list_conversations.return_value = [
        {"id": "C123", "name": "general", "is_private": False, "is_channel": True}
    ]
    api.get_stars.return_value = [{"type": "message", "channel": "C123"}]
    api.get_reminders.return_value = [{"id": "Rm1", "text": "ping"}]
    api.get_dnd.return_value = {"U1": {"dnd_enabled": False}}
    api.get_team_profile.return_value = {"fields": [{"id": "Xf1", "label": "Title"}]}
    api.get_scheduled_messages.return_value = [{"id": "Q1", "channel_id": "C123", "text": "later"}]
    api.get_team_info.return_value = {
        "id": "T1",
        "name": "testteam",
        "domain": "testteam",
        "email_domain": "test",
    }
    api.get_files.return_value = [{"id": "Fws", "name": "workspace.png"}]
    api.get_presence.return_value = {"presence": "active", "online": True}
    api.get_billable_info.return_value = {"U1": {"billing_active": True}}
    api.get_integration_logs.return_value = [{"user_id": "U1", "service_id": "S1"}]
    api.get_access_logs.return_value = [{"user_id": "U1", "ip": "1.1.1.1"}]
    api.get_team_preferences.return_value = {"msg_edit_window_mins": "0"}
    api.get_external_teams.return_value = [{"id": "E1", "name": "Partner"}]
    api.get_auth_teams.return_value = [{"id": "T1", "name": "testteam"}]
    api.get_remote_files.return_value = [{"id": "Fr1", "name": "drive.doc", "is_external": True}]
    api.get_file_info.return_value = {}
    api.delay = 0
    return api


def test_run_dump_creates_output_files(tmp_path, mock_api):
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    out_dir = tmp_path / "testteam" / "general_C123"
    assert (out_dir / "messages.json").exists()
    assert (out_dir / "messages.md").exists()
    assert (out_dir / ".cursor").exists()


def test_run_dump_writes_messages(tmp_path, mock_api):
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    out_dir = tmp_path / "testteam" / "general_C123"
    data = json.loads((out_dir / "messages.json").read_text())
    assert data[0]["text"] == "hi"


def test_run_dump_prefetches_users_before_enrich(tmp_path, mock_api):
    order: list[str] = []
    users_ret = mock_api.fetch_workspace_users.return_value
    mock_api.fetch_workspace_users.side_effect = lambda: order.append("users") or users_ret
    enrich_ret = mock_api.enrich.return_value
    mock_api.enrich.side_effect = lambda *a, **k: order.append("enrich") or enrich_ret
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    assert "users" in order
    assert "enrich" in order
    assert order.index("users") < order.index("enrich")


def test_run_dump_cursor_is_latest_ts(tmp_path, mock_api):
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    out_dir = tmp_path / "testteam" / "general_C123"
    cursor = (out_dir / ".cursor").read_text().strip()
    assert cursor == "1705320720.000000"


def test_dump_refreshes_stale_workspace_sidecar(tmp_path, mock_api):
    ws = tmp_path / "testteam"
    ws.mkdir()
    (ws / "stars.json").write_text('[{"type":"message","channel":"OLD"}]', encoding="utf-8")
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    stars = json.loads((ws / "stars.json").read_text())
    assert stars[0]["channel"] == "C123"


def test_run_dump_writes_sidecars(tmp_path, mock_api):
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    out_dir = tmp_path / "testteam" / "general_C123"
    channel = json.loads((out_dir / "channel.json").read_text())
    assert channel["topic"] == "talk"
    assert json.loads((out_dir / "members.json").read_text()) == ["U1", "U2"]
    assert json.loads((tmp_path / "testteam" / "emoji.json").read_text())["shipit"].startswith(
        "https://"
    )
    auth = json.loads((tmp_path / "testteam" / "auth.json").read_text())
    assert auth["team_id"] == "T1"
    assert json.loads((out_dir / "bookmarks.json").read_text())[0]["id"] == "Bk1"
    assert json.loads((out_dir / "pins.json").read_text())[0]["type"] == "message"
    assert json.loads((tmp_path / "testteam" / "usergroups.json").read_text())[0]["handle"] == "eng"
    assert "U1" in json.loads((tmp_path / "testteam" / "users.json").read_text())
    assert json.loads((tmp_path / "testteam" / "conversations.json").read_text())[0]["id"] == "C123"
    assert json.loads((tmp_path / "testteam" / "stars.json").read_text())[0]["type"] == "message"
    assert json.loads((tmp_path / "testteam" / "reminders.json").read_text())[0]["id"] == "Rm1"
    dnd = json.loads((tmp_path / "testteam" / "dnd.json").read_text())
    assert dnd["U1"]["dnd_enabled"] is False
    assert (
        json.loads((tmp_path / "testteam" / "team_profile.json").read_text())["fields"][0]["id"]
        == "Xf1"
    )
    assert (
        json.loads((tmp_path / "testteam" / "scheduled_messages.json").read_text())[0]["id"] == "Q1"
    )
    assert json.loads((tmp_path / "testteam" / "team.json").read_text())["id"] == "T1"
    assert json.loads((tmp_path / "testteam" / "files.json").read_text())[0]["id"] == "Fws"
    presence = json.loads((tmp_path / "testteam" / "presence.json").read_text())
    assert presence["U1"]["presence"] == "active"
    billable = json.loads((tmp_path / "testteam" / "billable_info.json").read_text())
    assert billable["U1"]["billing_active"] is True
    logs = json.loads((tmp_path / "testteam" / "integration_logs.json").read_text())
    assert logs[0]["service_id"] == "S1"
    access = json.loads((tmp_path / "testteam" / "access_logs.json").read_text())
    assert access[0]["ip"] == "1.1.1.1"
    prefs = json.loads((tmp_path / "testteam" / "team_preferences.json").read_text())
    assert prefs["msg_edit_window_mins"] == "0"
    ext = json.loads((tmp_path / "testteam" / "external_teams.json").read_text())
    assert ext[0]["id"] == "E1"
    teams = json.loads((tmp_path / "testteam" / "teams.json").read_text())
    assert teams[0]["id"] == "T1"
    remote = json.loads((tmp_path / "testteam" / "remote_files.json").read_text())
    assert remote[0]["id"] == "Fr1"
    cats = json.loads((tmp_path / "testteam" / "emoji_categories.json").read_text())
    assert cats[0]["name"] == "Custom"
    stats = json.loads((out_dir / "stats.json").read_text())
    assert stats["messages"] == 1
    assert stats["replies"] == 0
    assert json.loads((out_dir / "reactions.json").read_text()) == []
    assert json.loads((out_dir / "files.json").read_text()) == []
    assert json.loads((out_dir / "calls.json").read_text()) == []
    assert json.loads((out_dir / "threads.json").read_text()) == []


def test_write_channel_stats_writes_reactions(tmp_path):
    from ssd.dump import write_channel_stats

    out_dir = tmp_path / "acme" / "general_C123"
    out_dir.mkdir(parents=True)
    write_channel_stats(
        out_dir,
        [
            {
                "ts": "1.0",
                "user": "U1",
                "text": "hi",
                "reactions": [{"name": "thumbsup", "users": ["U2"], "count": 1}],
                "thread": [
                    {
                        "ts": "1.1",
                        "user": "U3",
                        "text": "yo",
                        "reactions": [{"name": "heart", "users": ["U1"], "count": 1}],
                    }
                ],
                "files": [{"id": "F1", "name": "a.png", "filetype": "png"}],
                "room": {"id": "R1", "name": "standup"},
            }
        ],
    )
    rows = json.loads((out_dir / "reactions.json").read_text())
    assert {(r["reaction"], r["user"]) for r in rows} == {("thumbsup", "U2"), ("heart", "U1")}
    assert all(r["channel"] == "C123" for r in rows)
    files = json.loads((out_dir / "files.json").read_text())
    assert [f["id"] for f in files] == ["F1"]
    calls = json.loads((out_dir / "calls.json").read_text())
    assert [c["id"] for c in calls] == ["R1"]
    threads = json.loads((out_dir / "threads.json").read_text())
    assert threads == [
        {
            "channel": "C123",
            "thread_ts": "1.0",
            "reply_count": 1,
            "latest_reply": "1.1",
            "reply_users": ["U3"],
            "reply_users_count": 1,
        }
    ]


def test_run_dump_resolves_channel_name(tmp_path, mock_api):
    run_dump(mock_api, "testteam", "#general", str(tmp_path))
    mock_api.resolve_channel.assert_called_once_with("general")


def test_run_dump_thread_url(tmp_path, mock_api):
    """Thread-only dump uses get_replies + enrich_reply, not get_messages."""
    raw_reply = {"ts": "1.1", "user": "U2", "text": "reply", "reactions": [], "files": []}
    mock_api.get_replies.return_value = [raw_reply]
    mock_api.enrich_reply.return_value = {
        "ts": "1.1",
        "user": "U2",
        "user_name": "bob",
        "text": "reply",
        "reactions": [],
        "files": [],
    }
    run_dump(
        mock_api,
        "testteam",
        "https://testteam.slack.com/archives/C123/p1705320720000000",
        str(tmp_path),
    )
    mock_api.get_replies.assert_called_once()
    mock_api.enrich_reply.assert_called_once_with(raw_reply, channel_id="C123")
    assert mock_api.get_replies.call_args.kwargs.get("include_parent") is True


def test_run_dump_thread_prefetches_users_before_enrich(tmp_path, mock_api):
    order: list[str] = []
    users_ret = mock_api.fetch_workspace_users.return_value
    mock_api.fetch_workspace_users.side_effect = lambda: order.append("users") or users_ret
    mock_api.get_replies.return_value = [
        {"ts": "1.1", "user": "U2", "text": "reply", "reactions": [], "files": []}
    ]
    mock_api.enrich_reply.side_effect = lambda r, channel_id=None: (
        order.append("enrich")
        or {
            "ts": r["ts"],
            "user": r["user"],
            "user_name": "bob",
            "text": r["text"],
            "reactions": [],
            "files": [],
        }
    )
    run_dump(
        mock_api,
        "testteam",
        "https://testteam.slack.com/archives/C123/p1705320720000000",
        str(tmp_path),
    )
    assert order.index("users") < order.index("enrich")


def test_run_dump_thread_writes_parent(tmp_path, mock_api):
    mock_api.get_replies.return_value = [
        {"ts": "1.0", "user": "U1", "text": "root"},
        {"ts": "1.1", "user": "U2", "text": "reply"},
    ]
    mock_api.enrich_reply.side_effect = lambda r, channel_id=None: {
        "ts": r["ts"],
        "user": r["user"],
        "user_name": "n",
        "text": r["text"],
        "reactions": [],
        "files": [],
    }
    run_dump(
        mock_api,
        "testteam",
        "https://testteam.slack.com/archives/C123/p1705320720000000",
        str(tmp_path),
    )
    thread_json = next((tmp_path / "testteam").rglob("thread.json"))
    rows = json.loads(thread_json.read_text())
    assert [m["ts"] for m in rows] == ["1.0", "1.1"]
    assert rows[0]["text"] == "root"


def test_run_dump_writes_bots(tmp_path, mock_api):
    mock_api.enrich.return_value = [
        {
            "ts": "1705320720.000000",
            "user": "B99",
            "user_name": "deploybot",
            "bot_id": "B99",
            "app_id": "A1",
            "username": "deploybot",
            "bot_profile": {
                "id": "B99",
                "name": "deploybot",
                "app_id": "A1",
                "icons": {"image_48": "https://e.test/b.png"},
                "team_id": "T1",
                "updated": 9,
                "is_workflow_bot": True,
            },
            "text": "shipped",
            "reactions": [],
            "thread": [],
        }
    ]
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    bots = json.loads((tmp_path / "testteam" / "bots.json").read_text())
    assert bots["B99"]["name"] == "deploybot"
    assert bots["B99"]["app_id"] == "A1"
    assert bots["B99"]["icons"]["image_48"].endswith("/b.png")
    assert bots["B99"]["team_id"] == "T1"
    assert bots["B99"]["updated"] == 9
    assert bots["B99"]["is_workflow_bot"] is True


def test_dump_presence_includes_channel_members(tmp_path, mock_api):
    mock_api.get_channel_members.return_value = ["U1", "U2"]
    mock_api.get_presence.side_effect = lambda uid: {"presence": "away", "user": uid}
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    presence = json.loads((tmp_path / "testteam" / "presence.json").read_text())
    assert "U1" in presence
    assert "U2" in presence


def test_dump_presence_merges_second_channel(tmp_path, mock_api):
    mock_api.get_channel_members.return_value = ["U1"]
    mock_api.get_presence.side_effect = lambda uid: {"presence": "away", "user": uid}
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    mock_api.resolve_channel.return_value = ("C999", "other")
    mock_api.get_channel_members.return_value = ["U3"]
    run_dump(mock_api, "testteam", "C999", str(tmp_path))
    presence = json.loads((tmp_path / "testteam" / "presence.json").read_text())
    assert "U1" in presence
    assert "U3" in presence


def test_dump_presence_skips_failed_member(tmp_path, mock_api):
    mock_api.get_channel_members.return_value = ["U1", "Ubad"]

    def presence(uid: str) -> dict:
        if uid == "Ubad":
            raise RuntimeError("users_not_found")
        return {"presence": "away", "user": uid}

    mock_api.get_presence.side_effect = presence
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    presence_data = json.loads((tmp_path / "testteam" / "presence.json").read_text())
    assert "U1" in presence_data
    assert "Ubad" not in presence_data


def test_dump_file_comments(tmp_path, mock_api):
    fobj = {
        "id": "F1",
        "name": "shot.png",
        "url_private": "https://files.slack.com/shot.png",
        "comments_count": 2,
    }
    mock_api.get_files.return_value = [dict(fobj)]
    mock_api.enrich.return_value = [
        {
            "ts": "1705320720.000000",
            "user": "U1",
            "user_name": "alice",
            "text": "hi",
            "reactions": [],
            "thread": [],
            "files": [dict(fobj)],
        }
    ]
    mock_api.get_file_info.return_value = {
        "id": "F1",
        "comments": [{"id": "Fc1", "comment": "nice", "user": "U2"}],
        "comments_count": 2,
    }
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    files = json.loads((tmp_path / "testteam" / "files.json").read_text())
    assert files[0]["comments"][0]["comment"] == "nice"
    ch = tmp_path / "testteam" / "general_C123"
    ch_files = json.loads((ch / "files.json").read_text())
    assert ch_files[0]["comments"][0]["id"] == "Fc1"


def test_write_channel_stats_reuses_file_comments(tmp_path):
    from ssd.dump import write_channel_stats

    out_dir = tmp_path / "acme" / "general_C123"
    out_dir.mkdir(parents=True)
    (out_dir / "files.json").write_text(
        json.dumps(
            [
                {
                    "id": "F1",
                    "name": "a.png",
                    "comments_count": 1,
                    "comments": [{"id": "Fc1", "comment": "nice"}],
                }
            ]
        ),
        encoding="utf-8",
    )
    api = MagicMock()
    write_channel_stats(
        out_dir,
        [
            {
                "ts": "1.0",
                "user": "U1",
                "text": "hi",
                "reactions": [],
                "thread": [],
                "files": [{"id": "F1", "name": "a.png", "comments_count": 1}],
            }
        ],
        api=api,
    )
    api.get_file_info.assert_not_called()
    files = json.loads((out_dir / "files.json").read_text())
    assert files[0]["comments"][0]["id"] == "Fc1"


def test_dump_canvases(tmp_path, mock_api):
    mock_api.get_files.return_value = [
        {"id": "Fc", "name": "notes", "filetype": "canvas", "title": "notes"},
        {
            "id": "F1",
            "name": "shot.png",
            "url_private": "https://files.slack.com/shot.png",
            "comments_count": 0,
        },
    ]
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    canvases = json.loads((tmp_path / "testteam" / "canvases.json").read_text())
    assert canvases[0]["id"] == "Fc"
    assert all(c["filetype"] == "canvas" for c in canvases)
