import json
from pathlib import Path

import pytest

from ssd.dumpapi import DumpClient


def _write(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _mini_dump(tmp_path: Path) -> Path:
    """output-root layout: <root>/<workspace>/<name>_<id>/"""
    ws = tmp_path / "acme"
    general = ws / "general_C123"
    _write(
        general / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hello @bob",
                "reactions": [{"name": "thumbsup", "count": 2, "users": ["U2", "U4"]}],
                "files": [],
                "thread": [],
            },
            {
                "ts": "2.0",
                "user": "U2",
                "user_name": "bob",
                "text": "thread root",
                "reactions": [],
                "files": [],
                "thread": [
                    {
                        "ts": "2.1",
                        "user": "U3",
                        "user_name": "carol",
                        "text": "a reply",
                        "reactions": [],
                        "files": [
                            {
                                "id": "F88",
                                "name": "note.txt",
                                "mimetype": "text/plain",
                                "size": 4,
                            }
                        ],
                    }
                ],
            },
            {
                "ts": "3.0",
                "user": "U1",
                "user_name": "alice",
                "text": "see file",
                "reactions": [],
                "files": [
                    {
                        "id": "F99",
                        "name": "report.pdf",
                        "mimetype": "application/pdf",
                        "size": 1024,
                        "url_private": "https://files.slack.com/report.pdf",
                    }
                ],
                "thread": [],
            },
        ],
    )
    _write(
        general / "users.json",
        {
            "U1": {
                "id": "U1",
                "handle": "alice",
                "real_name": "Alice Smith",
                "display_name": "alice",
                "title": "eng",
                "email": "alice@acme.test",
                "phone": "",
                "status_text": "",
                "status_emoji": "",
                "timezone": "UTC",
                "timezone_label": "UTC",
                "is_bot": False,
                "image": "https://example.test/a.png",
            },
            "U2": {
                "id": "U2",
                "handle": "bob",
                "real_name": "Bob Jones",
                "display_name": "bob",
                "title": "",
                "email": "bob@acme.test",
                "phone": "",
                "status_text": "",
                "status_emoji": "",
                "timezone": "UTC",
                "timezone_label": "UTC",
                "is_bot": False,
                "image": "",
            },
        },
    )
    random = ws / "team_ops_C456"
    _write(
        random / "messages.json",
        [
            {
                "ts": "9.0",
                "user": "U9",
                "user_name": "dave",
                "text": "ops ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    _write(
        general / "thread_5_000000" / "thread.json",
        [
            {
                "ts": "5.1",
                "user": "U3",
                "user_name": "carol",
                "text": "standalone reply",
                "reactions": [],
                "files": [],
            }
        ],
    )
    return tmp_path


@pytest.fixture
def dump_root(tmp_path: Path) -> Path:
    return _mini_dump(tmp_path)


def test_conversations_list_from_output_root(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_list()
    assert resp["ok"] is True
    ids = {ch["id"] for ch in resp["channels"]}
    assert ids == {"C123", "C456"}
    by_id = {ch["id"]: ch for ch in resp["channels"]}
    assert by_id["C123"]["name"] == "general"
    assert by_id["C456"]["name"] == "team_ops"


def test_conversations_list_includes_im_by_default(tmp_path: Path) -> None:
    dm = tmp_path / "acme" / "alice_D111"
    _write(
        dm / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hi",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    listed = client.conversations_list()
    assert [ch["id"] for ch in listed["channels"]] == ["D111"]
    assert listed["channels"][0]["is_im"] is True
    public = client.conversations_list(types="public_channel")
    assert public["channels"] == []
    hist = client.conversations_history(channel="D111")
    assert [m["text"] for m in hist["messages"]] == ["hi"]


def test_conversations_info(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_info(channel="C123")
    assert resp["ok"] is True
    assert resp["channel"]["id"] == "C123"
    assert resp["channel"]["name"] == "general"


def test_conversations_info_not_found(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_info(channel="CNOPE")
    assert resp["ok"] is False
    assert resp["error"] == "channel_not_found"


def test_conversations_history_newest_first(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_history(channel="C123")
    assert resp["ok"] is True
    ts = [m["ts"] for m in resp["messages"]]
    assert ts == ["3.0", "2.0", "1.0"]
    assert "thread" not in resp["messages"][1]
    assert resp["messages"][1]["reply_count"] == 1
    assert resp["messages"][1]["thread_ts"] == "2.0"


def test_conversations_history_pagination(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    page1 = client.conversations_history(channel="C123", limit=2)
    assert [m["ts"] for m in page1["messages"]] == ["3.0", "2.0"]
    assert page1["has_more"] is True
    cursor = page1["response_metadata"]["next_cursor"]
    page2 = client.conversations_history(channel="C123", limit=2, cursor=cursor)
    assert [m["ts"] for m in page2["messages"]] == ["1.0"]
    assert page2["has_more"] is False
    assert page2["response_metadata"]["next_cursor"] == ""


def test_conversations_history_oldest_latest(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_history(channel="C123", oldest="1.0", latest="3.0")
    assert [m["ts"] for m in resp["messages"]] == ["2.0"]
    inclusive = client.conversations_history(
        channel="C123", oldest="1.0", latest="3.0", inclusive=True
    )
    assert [m["ts"] for m in inclusive["messages"]] == ["3.0", "2.0", "1.0"]


def test_conversations_replies_includes_parent(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_replies(channel="C123", ts="2.0")
    assert resp["ok"] is True
    assert [m["ts"] for m in resp["messages"]] == ["2.0", "2.1"]
    assert resp["messages"][1]["text"] == "a reply"


def test_conversations_replies_standalone_thread(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_replies(channel="C123", ts="5.000000")
    assert resp["ok"] is True
    assert [m["ts"] for m in resp["messages"]] == ["5.1"]
    assert resp["messages"][0]["text"] == "standalone reply"


def test_conversations_replies_not_found(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_replies(channel="C123", ts="99.0")
    assert resp["ok"] is False
    assert resp["error"] == "thread_not_found"


def test_users_list_and_info(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    listed = client.users_list()
    assert listed["ok"] is True
    ids = {u["id"] for u in listed["members"]}
    assert {"U1", "U2", "U3", "U9"} <= ids
    alice = client.users_info(user="U1")
    assert alice["ok"] is True
    user = alice["user"]
    assert user["name"] == "alice"
    assert user["profile"]["email"] == "alice@acme.test"
    assert user["profile"]["real_name"] == "Alice Smith"
    assert user["tz"] == "UTC"
    dave = client.users_info(user="U9")
    assert dave["ok"] is True
    assert dave["user"]["profile"]["display_name"] == "dave"


def test_users_info_not_found(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.users_info(user="UNOPE")
    assert resp["ok"] is False
    assert resp["error"] == "user_not_found"


def test_files_info(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    top = client.files_info(file="F99")
    assert top["ok"] is True
    assert top["file"]["name"] == "report.pdf"
    nested = client.files_info(file="F88")
    assert nested["ok"] is True
    assert nested["file"]["name"] == "note.txt"
    missing = client.files_info(file="FNOPE")
    assert missing["ok"] is False
    assert missing["error"] == "file_not_found"


def test_search_messages(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.search_messages(query="thread")
    assert resp["ok"] is True
    texts = [m["text"] for m in resp["messages"]["matches"]]
    assert "thread root" in texts
    assert resp["messages"]["total"] >= 1
    none = client.search_messages(query="zzz-no-match")
    assert none["messages"]["matches"] == []
    assert none["messages"]["total"] == 0


def test_auth_test(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.auth_test()
    assert resp["ok"] is True
    assert resp["team"] == "acme"
    assert resp["url"] == "https://acme.slack.com/"


def test_open_from_workspace_and_channel_dir(dump_root: Path) -> None:
    ws = DumpClient(dump_root / "acme")
    assert {ch["id"] for ch in ws.conversations_list()["channels"]} == {"C123", "C456"}
    ch = DumpClient(dump_root / "acme" / "general_C123")
    listed = ch.conversations_list()["channels"]
    assert [c["id"] for c in listed] == ["C123"]
    hist = ch.conversations_history(channel="C123")
    assert len(hist["messages"]) == 3


def test_missing_path_raises(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        DumpClient(tmp_path / "nope")


def test_conversations_members(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_members(channel="C123")
    assert resp["ok"] is True
    assert set(resp["members"]) == {"U1", "U2", "U3", "U4"}
    ops = client.conversations_members(channel="#team_ops")
    assert ops["members"] == ["U9"]
    missing = client.conversations_members(channel="CNOPE")
    assert missing["ok"] is False
    assert missing["error"] == "channel_not_found"


def test_search_from_and_in(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    from_alice = client.search_messages(query="from:alice")
    texts = [m["text"] for m in from_alice["messages"]["matches"]]
    assert "hello @bob" in texts
    assert "see file" in texts
    assert "thread root" not in texts
    assert "ops ping" not in texts

    from_id = client.search_messages(query="from:U2")
    assert [m["text"] for m in from_id["messages"]["matches"]] == ["thread root"]

    in_ops = client.search_messages(query="in:team_ops ping")
    assert [m["text"] for m in in_ops["messages"]["matches"]] == ["ops ping"]

    in_hash = client.search_messages(query="in:#general thread")
    assert [m["text"] for m in in_hash["messages"]["matches"]] == ["thread root"]

    in_cid = client.search_messages(query="in:C456")
    assert [m["text"] for m in in_cid["messages"]["matches"]] == ["ops ping"]

    both = client.search_messages(query="from:alice in:general file")
    assert [m["text"] for m in both["messages"]["matches"]] == ["see file"]

    none = client.search_messages(query="from:nobody hello")
    assert none["messages"]["matches"] == []
    assert none["messages"]["total"] == 0


def test_search_has_file(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.search_messages(query="has:file")
    texts = {m["text"] for m in resp["messages"]["matches"]}
    assert "see file" in texts
    assert "a reply" in texts
    assert "hello @bob" not in texts


def test_users_lookup_by_email(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.users_lookupByEmail(email="alice@acme.test")
    assert resp["ok"] is True
    assert resp["user"]["id"] == "U1"
    mixed = client.users_lookupByEmail(email="BOB@acme.test")
    assert mixed["user"]["id"] == "U2"
    missing = client.users_lookupByEmail(email="nope@acme.test")
    assert missing["ok"] is False
    assert missing["error"] == "users_not_found"


def test_files_list(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.files_list()
    assert resp["ok"] is True
    ids = {f["id"] for f in resp["files"]}
    assert ids == {"F88", "F99"}
    in_general = client.files_list(channel="C123")
    assert {f["id"] for f in in_general["files"]} == {"F88", "F99"}
    in_ops = client.files_list(channel="C456")
    assert in_ops["files"] == []
    by_user = client.files_list(user="U1")
    assert {f["id"] for f in by_user["files"]} == {"F99"}


def test_pins_list(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    empty = client.pins_list(channel="C123")
    assert empty["ok"] is True
    assert empty["items"] == []
    missing = client.pins_list(channel="CNOPE")
    assert missing["ok"] is False
    assert missing["error"] == "channel_not_found"


def test_pins_list_from_pinned_to(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "pinned",
                "reactions": [],
                "files": [],
                "thread": [],
                "pinned_to": ["C123"],
                "pinned_info": {"pinned_by": "U2", "pinned_ts": 11},
            }
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.pins_list(channel="C123")
    assert len(resp["items"]) == 1
    item = resp["items"][0]
    assert item["type"] == "message"
    assert item["channel"] == "C123"
    assert item["created_by"] == "U2"
    assert item["message"]["text"] == "pinned"


def test_team_info(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.team_info()
    assert resp["ok"] is True
    assert resp["team"]["domain"] == "acme"
    assert resp["team"]["name"] == "acme"


def test_chat_get_permalink(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.chat_getPermalink(channel="C123", message_ts="1.0")
    assert resp["ok"] is True
    assert resp["permalink"] == "https://acme.slack.com/archives/C123/p10"
    missing = client.chat_getPermalink(channel="C123", message_ts="99.0")
    assert missing["ok"] is False
    assert missing["error"] == "message_not_found"
