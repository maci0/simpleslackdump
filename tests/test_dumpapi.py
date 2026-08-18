import json
from pathlib import Path
from typing import Any

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


def test_discover_prefers_dump_with_messages_on_duplicate_id(tmp_path: Path) -> None:
    """If a rename left two dirs for one id, keep the archive that has messages."""
    ws = tmp_path / "acme"
    empty = ws / "renamed_C123"
    empty.mkdir(parents=True)
    full = ws / "general_C123"
    _write(
        full / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "kept",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    hist = client.conversations_history(channel="C123")
    assert [m["text"] for m in hist["messages"]] == ["kept"]
    assert client.conversations_info(channel="C123")["channel"]["name"] == "general"


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
    hits = client.conversations_history_search(channel="C123", query="hello @bob")
    assert [m["ts"] for m in hits["messages"]] == ["1.0"]
    empty = client.conversations_history_search(channel="C123", query="")
    assert empty["ok"] is False


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
    hits = client.conversations_replies_search(channel="C123", ts="2.0", query="a reply")
    assert [m["ts"] for m in hits["messages"]] == ["2.1"]
    empty = client.conversations_replies_search(channel="C123", ts="2.0", query="")
    assert empty["ok"] is False


def test_conversations_replies_standalone_thread(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_replies(channel="C123", ts="5.000000")
    assert resp["ok"] is True
    assert [m["ts"] for m in resp["messages"]] == ["5.1"]
    assert resp["messages"][0]["text"] == "standalone reply"


def test_standalone_thread_parent_in_history(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ch / "thread_1_0" / "thread.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "root",
                "thread_ts": "1.0",
                "reactions": [],
                "files": [],
            },
            {
                "ts": "1.1",
                "user": "U2",
                "user_name": "bob",
                "text": "reply",
                "thread_ts": "1.0",
                "reactions": [],
                "files": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    hist = client.conversations_history(channel="C123")
    assert [m["text"] for m in hist["messages"]] == ["root"]
    replies = client.conversations_replies(channel="C123", ts="1.0")
    assert [m["text"] for m in replies["messages"]] == ["root", "reply"]


def test_thread_dump_merges_into_existing_parent(tmp_path: Path) -> None:
    """Standalone thread dumps must not be ignored when the parent is in messages.json."""
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "root",
                "reactions": [],
                "files": [],
                "thread": [
                    {
                        "ts": "1.1",
                        "user": "U2",
                        "user_name": "bob",
                        "text": "old reply",
                        "reactions": [],
                        "files": [],
                    }
                ],
            }
        ],
    )
    _write(
        ch / "thread_1_0" / "thread.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "root",
                "reactions": [],
                "files": [],
            },
            {
                "ts": "1.1",
                "user": "U2",
                "user_name": "bob",
                "text": "old reply updated",
                "reactions": [],
                "files": [],
            },
            {
                "ts": "1.2",
                "user": "U3",
                "user_name": "carol",
                "text": "newer reply only in thread dump",
                "reactions": [],
                "files": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    replies = client.conversations_replies(channel="C123", ts="1.0")
    assert [m["ts"] for m in replies["messages"]] == ["1.0", "1.1", "1.2"]
    assert replies["messages"][1]["text"] == "old reply updated"
    assert replies["messages"][2]["text"] == "newer reply only in thread dump"
    hits = client.search_messages(query="newer reply only")["messages"]["matches"]
    assert [m["ts"] for m in hits] == ["1.2"]


def test_search_matches_text_raw(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hi @zoe",
                "text_raw": "hi <@U99>",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="U99")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["hi @zoe"]


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


def test_pins_list_prefers_sidecar(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ch / "pins.json",
        [
            {
                "type": "message",
                "channel": "C123",
                "created_by": "U1",
                "message": {"ts": "9.0", "text": "from api"},
            }
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.pins_list(channel="C123")
    assert resp["items"][0]["message"]["text"] == "from api"
    info = client.pins_info(channel="C123", ts="9.0")
    assert info["ok"] is True
    assert info["item"]["message"]["text"] == "from api"
    missing = client.pins_info(channel="C123", ts="1.0")
    assert missing["ok"] is False
    hits = client.pins_search(query="from api")["items"]
    assert [i["message"]["ts"] for i in hits] == ["9.0"]
    empty = client.pins_search(query="")
    assert empty["ok"] is False


def test_search_with(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "public ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    dm = tmp_path / "acme" / "alice_D111"
    _write(
        dm / "messages.json",
        [
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "secret ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    _write(dm / "members.json", ["U1", "U2"])
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="with:U2 ping")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["secret ping"]


def test_legacy_list_aliases(tmp_path: Path) -> None:
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [])
    _write((tmp_path / "acme" / "alice_D111") / "messages.json", [])
    client = DumpClient(tmp_path)
    channels = client.channels_list()
    assert {ch["id"] for ch in channels["channels"]} == {"C123"}
    ims = client.im_list()
    assert {ch["id"] for ch in ims["channels"]} == {"D111"}
    hist = client.channels_history(channel="C123")
    assert hist["ok"] is True
    info = client.im_history(channel="D111")
    assert info["ok"] is True


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


def test_chat_get_permalink_for_reply(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.chat_getPermalink(channel="C123", message_ts="2.1")
    assert resp["ok"] is True
    assert "p21" in resp["permalink"]


def test_reactions_get(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.reactions_get(channel="C123", timestamp="1.0")
    assert resp["ok"] is True
    names = [r["name"] for r in resp["message"]["reactions"]]
    assert names == ["thumbsup"]
    missing = client.reactions_get(channel="C123", timestamp="99.0")
    assert missing["ok"] is False
    assert missing["error"] == "message_not_found"


def test_users_conversations(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    alice = client.users_conversations(user="U1")
    assert alice["ok"] is True
    assert {c["id"] for c in alice["channels"]} == {"C123"}
    dave = client.users_conversations(user="U9")
    assert {c["id"] for c in dave["channels"]} == {"C456"}
    nobody = client.users_conversations(user="UNOPE")
    assert nobody["channels"] == []
    hits = client.users_conversations_search(user="U9", query="ops")
    assert {c["id"] for c in hits["channels"]} == {"C456"}
    empty = client.users_conversations_search(user="U9", query="")
    assert empty["ok"] is False


def test_search_files(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.search_files(query="report")
    assert resp["ok"] is True
    names = [f["name"] for f in resp["files"]["matches"]]
    assert names == ["report.pdf"]
    assert resp["files"]["total"] == 1
    none = client.search_files(query="zzz-no-file")
    assert none["files"]["matches"] == []


def test_emoji_list_from_reactions(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.emoji_list()
    assert resp["ok"] is True
    assert "thumbsup" in resp["emoji"]


def test_emoji_list_from_catalog(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    _write(ch / "messages.json", [])
    _write(ws / "emoji.json", {"shipit": "https://emoji.test/shipit.png"})
    client = DumpClient(tmp_path)
    resp = client.emoji_list()
    assert resp["emoji"]["shipit"] == "https://emoji.test/shipit.png"
    hits = client.emoji_search(query="ship")["emoji"]
    assert [row["name"] for row in hits] == ["shipit"]
    empty = client.emoji_search(query="")
    assert empty["ok"] is False


def test_search_before_after(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    after = client.search_messages(query="after:1.5")
    texts = [m["text"] for m in after["messages"]["matches"]]
    assert "hello @bob" not in texts
    assert "thread root" in texts
    before = client.search_messages(query="before:2.0 from:alice")
    assert [m["text"] for m in before["messages"]["matches"]] == ["hello @bob"]


def test_search_invalid_after_before_fail_closed(tmp_path: Path) -> None:
    """Junk after:/before: tokens must not widen the result set (match on:/during:)."""
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "100.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hello",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    assert client.search_messages(query="after:not-a-date hello")["messages"]["matches"] == []
    assert client.search_messages(query="before:not-a-date hello")["messages"]["matches"] == []
    assert client.search_messages(query="on:not-a-date hello")["messages"]["matches"] == []


def test_search_before_today_and_after_yesterday(tmp_path: Path, monkeypatch) -> None:
    """before:/after: with natural-language keywords route through parse_bound._day_start."""
    _freeze_search_now(monkeypatch)
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            _msg(_noon_ts(0), "today ping"),
            _msg(_noon_ts(1), "yesterday ping"),
            _msg(_noon_ts(3), "old ping"),
        ],
    )
    client = DumpClient(tmp_path)
    before_today = client.search_messages(query="before:today ping")["messages"]["matches"]
    assert {m["text"] for m in before_today} == {"yesterday ping", "old ping"}

    after_today = client.search_messages(query="after:today ping")["messages"]["matches"]
    assert {m["text"] for m in after_today} == {"today ping"}

    after_yesterday = client.search_messages(query="after:yesterday ping")["messages"]["matches"]
    assert {m["text"] for m in after_yesterday} == {"today ping", "yesterday ping"}


def test_search_has_link(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "see https://example.test/x",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "no url here",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="has:link")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["see https://example.test/x"]


def test_search_to(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.search_messages(query="to:bob")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["hello @bob"]


def test_search_has_canvas(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "doc",
                "reactions": [],
                "files": [{"id": "F2", "name": "notes", "filetype": "canvas"}],
                "thread": [],
            },
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "pic",
                "reactions": [],
                "files": [{"id": "F1", "name": "a.png", "filetype": "png"}],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    canvas = client.search_messages(query="has:canvas")
    assert [m["text"] for m in canvas["messages"]["matches"]] == ["doc"]
    image = client.search_messages(query="has:image")
    assert [m["text"] for m in image["messages"]["matches"]] == ["pic"]


def test_iter_messages(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    all_msgs = list(client.iter_messages())
    texts = {m["text"] for m in all_msgs}
    assert "hello @bob" in texts
    assert "a reply" in texts
    assert "ops ping" in texts
    assert all("channel" in m for m in all_msgs)
    general = list(client.iter_messages(channel="C123", include_replies=False))
    assert {m["text"] for m in general} == {"hello @bob", "thread root", "see file"}


def test_conversations_info_reads_channel_json(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ch / "channel.json",
        {
            "id": "C123",
            "name": "general",
            "topic": "daily standup",
            "purpose": "chat",
            "topic_creator": "U9",
            "topic_last_set": 11,
            "purpose_creator": "U8",
            "purpose_last_set": 12,
            "is_private": True,
            "created": 99,
            "num_members": 4,
            "creator": "U1",
        },
    )
    client = DumpClient(tmp_path)
    info = client.conversations_info(channel="C123")["channel"]
    assert info["topic"]["value"] == "daily standup"
    assert info["topic"]["creator"] == "U9"
    assert info["topic"]["last_set"] == 11
    assert info["purpose"]["value"] == "chat"
    assert info["purpose"]["creator"] == "U8"
    assert info["purpose"]["last_set"] == 12
    assert info["is_private"] is True
    assert info["created"] == 99
    assert info["num_members"] == 4


def test_conversations_members_prefers_members_json(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
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
    _write(ch / "members.json", ["U1", "U2", "U9"])
    client = DumpClient(tmp_path)
    resp = client.conversations_members(channel="C123")
    assert resp["members"] == ["U1", "U2", "U9"]
    _write(
        ch.parent / "users.json",
        {
            "U1": {"id": "U1", "handle": "alice"},
            "U2": {"id": "U2", "handle": "bob"},
            "U9": {"id": "U9", "handle": "dave"},
        },
    )
    hits = DumpClient(tmp_path).conversations_members_search(channel="C123", query="alice")
    assert hits["members"] == ["U1"]
    empty = DumpClient(tmp_path).conversations_members_search(channel="C123", query="")
    assert empty["ok"] is False


def test_conversations_replies_by_reply_ts(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.conversations_replies(channel="C123", ts="2.1")
    assert resp["ok"] is True
    assert [m["ts"] for m in resp["messages"]] == ["2.0", "2.1"]


def test_reactions_list(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    all_rx = client.reactions_list()
    assert all_rx["ok"] is True
    names = {(i["user"], i["reaction"]) for i in all_rx["items"]}
    assert ("U2", "thumbsup") in names
    assert ("U4", "thumbsup") in names
    bob = client.reactions_list(user="U2")
    assert {(i["user"], i["reaction"]) for i in bob["items"]} == {("U2", "thumbsup")}
    none = client.reactions_list(user="U9")
    assert none["items"] == []


def test_auth_test_reads_workspace_auth(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ws / "auth.json",
        {
            "ok": True,
            "url": "https://acme.slack.com/",
            "team": "Acme",
            "team_id": "T99",
            "user": "alice",
            "user_id": "U1",
            "enterprise_id": "E99",
            "is_enterprise_install": True,
        },
    )
    client = DumpClient(tmp_path)
    auth = client.auth_test()
    assert auth["team_id"] == "T99"
    assert auth["user_id"] == "U1"
    assert auth["user"] == "alice"
    assert auth["enterprise_id"] == "E99"
    assert auth["is_enterprise_install"] is True
    team = client.team_info()
    assert team["team"]["id"] == "T99"


def test_iter_files(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    files = list(client.iter_files())
    ids = {f["id"] for f in files}
    assert ids == {"F88", "F99"}
    assert all("channels" in f for f in files)
    general = list(client.iter_files(channel="C123"))
    assert {f["id"] for f in general} == {"F88", "F99"}


def test_iter_threads(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    threads = list(client.iter_threads())
    by_ts = {t["thread_ts"]: t for t in threads}
    assert by_ts["2.0"]["reply_count"] == 1
    assert by_ts["2.0"]["channel"] == "C123"
    assert by_ts["2.0"]["reply_users"] == ["U3"]
    assert by_ts["2.0"]["reply_users_count"] == 1
    assert "5.000000" in by_ts
    only_ops = list(client.iter_threads(channel="C456"))
    assert only_ops == []


def test_history_and_threads_keep_claimed_reply_meta(tmp_path: Path) -> None:
    """Partial dumps must not report reply_count = len(thread) when Slack claimed more."""
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "parent",
                "reactions": [],
                "files": [],
                "reply_count": 5,
                "latest_reply": "1.5",
                "thread": [
                    {
                        "ts": "1.2",
                        "user": "U2",
                        "user_name": "bob",
                        "text": "later stored",
                        "reactions": [],
                        "files": [],
                    },
                    {
                        "ts": "1.1",
                        "user": "U3",
                        "user_name": "carol",
                        "text": "earlier stored",
                        "reactions": [],
                        "files": [],
                    },
                ],
            }
        ],
    )
    client = DumpClient(tmp_path)
    hist = client.conversations_history(channel="C123")
    assert hist["messages"][0]["reply_count"] == 5
    assert hist["messages"][0]["latest_reply"] == "1.5"
    assert "thread" not in hist["messages"][0]
    rows = list(client.iter_threads(channel="C123"))
    assert rows[0]["reply_count"] == 5
    assert rows[0]["latest_reply"] == "1.5"


def test_iter_threads_includes_claimed_without_stored_replies(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "9.0",
                "user": "U1",
                "user_name": "alice",
                "text": "root",
                "reactions": [],
                "files": [],
                "reply_count": 2,
                "latest_reply": "9.2",
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    rows = list(client.iter_threads())
    assert rows == [
        {
            "channel": "C123",
            "thread_ts": "9.0",
            "reply_count": 2,
            "latest_reply": "9.2",
            "reply_users": [],
            "reply_users_count": 0,
        }
    ]


def test_files_list_ts_range(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "a",
                "reactions": [],
                "files": [{"id": "F1", "name": "old.txt", "created": 10, "user": "U1"}],
                "thread": [],
            },
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "b",
                "reactions": [],
                "files": [{"id": "F2", "name": "new.txt", "created": 50, "user": "U1"}],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    mid = client.files_list(ts_from=20, ts_to=60)
    assert {f["id"] for f in mid["files"]} == {"F2"}
    early = client.files_list(ts_to=20)
    assert {f["id"] for f in early["files"]} == {"F1"}


def test_search_files_from_and_in(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    alice = client.search_files(query="from:alice")
    assert {f["name"] for f in alice["files"]["matches"]} == {"report.pdf"}
    in_ops = client.search_files(query="in:team_ops")
    assert in_ops["files"]["matches"] == []
    in_general = client.search_files(query="in:#general pdf")
    assert [f["name"] for f in in_general["files"]["matches"]] == ["report.pdf"]


def test_search_all(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.search_all(query="report")
    assert resp["ok"] is True
    assert resp["files"]["total"] >= 1
    texts = [m["text"] for m in resp["messages"]["matches"]]
    assert "see file" in texts


def test_bookmarks_list(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ch / "bookmarks.json",
        [{"id": "Bk1", "title": "docs", "link": "https://example.test", "type": "link"}],
    )
    client = DumpClient(tmp_path)
    resp = client.bookmarks_list(channel_id="C123")
    assert resp["ok"] is True
    assert resp["bookmarks"][0]["title"] == "docs"
    missing = client.bookmarks_list(channel_id="CNOPE")
    assert missing["ok"] is False
    info = client.bookmarks_info(bookmark="Bk1")
    assert info["ok"] is True
    assert info["bookmark"]["title"] == "docs"
    gone = client.bookmarks_info(bookmark="Bk404")
    assert gone["ok"] is False
    hits = client.bookmarks_search(query="docs")["bookmarks"]
    assert [b["id"] for b in hits] == ["Bk1"]
    empty = client.bookmarks_search(query="")
    assert empty["ok"] is False


def test_usergroups_list(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "usergroups.json",
        [{"id": "S1", "handle": "eng", "name": "Engineering", "users": ["U1"]}],
    )
    client = DumpClient(tmp_path)
    resp = client.usergroups_list()
    assert resp["ok"] is True
    assert resp["usergroups"][0]["handle"] == "eng"
    info = client.usergroups_info(usergroup="eng")
    assert info["usergroup"]["id"] == "S1"
    missing = client.usergroups_info(usergroup="nope")
    assert missing["ok"] is False
    hits = client.usergroups_search(query="eng")["usergroups"]
    assert [g["id"] for g in hits] == ["S1"]
    empty = client.usergroups_search(query="")
    assert empty["ok"] is False


def test_stats(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.stats()
    assert resp["ok"] is True
    assert resp["channels"] == 2
    assert resp["messages"] >= 3
    assert resp["files"] == 2
    assert resp["users"] >= 4


def test_workspace_users_json(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U8": {
                "id": "U8",
                "handle": "erin",
                "display_name": "erin",
                "real_name": "Erin",
                "title": "",
                "email": "erin@acme.test",
                "phone": "",
                "status_text": "",
                "status_emoji": "",
                "timezone": "",
                "timezone_label": "",
                "is_bot": False,
                "image": "",
            }
        },
    )
    client = DumpClient(tmp_path)
    resp = client.users_info(user="U8")
    assert resp["ok"] is True
    assert resp["user"]["name"] == "erin"


def test_get_message(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.get_message(channel="C123", ts="2.1")
    assert resp["ok"] is True
    assert resp["message"]["text"] == "a reply"
    missing = client.get_message(channel="C123", ts="99.0")
    assert missing["error"] == "message_not_found"


def test_iter_users(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    ids = {u["id"] for u in client.iter_users()}
    assert {"U1", "U2", "U3", "U9"} <= ids


def test_usergroups_users(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "usergroups.json",
        [{"id": "S1", "handle": "eng", "name": "Engineering", "users": ["U1", "U2"]}],
    )
    client = DumpClient(tmp_path)
    by_id = client.usergroups_users(usergroup="S1")
    assert by_id["users"] == ["U1", "U2"]
    by_handle = client.usergroups_users(usergroup="eng")
    assert by_handle["users"] == ["U1", "U2"]
    missing = client.usergroups_users(usergroup="nope")
    assert missing["ok"] is False
    page = client.usergroups_users(usergroup="S1", limit=1)
    assert page["users"] == ["U1"]
    nxt = client.usergroups_users(
        usergroup="S1", limit=1, cursor=page["response_metadata"]["next_cursor"]
    )
    assert nxt["users"] == ["U2"]
    _write(
        ws / "users.json",
        {
            "U1": {"id": "U1", "handle": "alice"},
            "U2": {"id": "U2", "handle": "bob"},
        },
    )
    hits = DumpClient(tmp_path).usergroups_users_search(usergroup="eng", query="alice")
    assert hits["users"] == ["U1"]
    empty = DumpClient(tmp_path).usergroups_users_search(usergroup="eng", query="")
    assert empty["ok"] is False


def test_stars_list(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "stars.json",
        [{"type": "message", "channel": "C123", "message": {"ts": "1.0", "text": "starred"}}],
    )
    client = DumpClient(tmp_path)
    resp = client.stars_list()
    assert resp["ok"] is True
    assert resp["items"][0]["message"]["text"] == "starred"
    info = client.stars_info(channel="C123", ts="1.0")
    assert info["ok"] is True
    assert info["item"]["message"]["text"] == "starred"
    missing = client.stars_info(channel="C123", ts="9.0")
    assert missing["ok"] is False
    hits = client.stars_search(query="starred")["items"]
    assert [i["message"]["ts"] for i in hits] == ["1.0"]
    empty = client.stars_search(query="")
    assert empty["ok"] is False


def test_conversations_catalog_stub(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "conversations.json",
        [
            {
                "id": "C123",
                "name": "general",
                "is_private": True,
                "is_channel": True,
            },
            {
                "id": "C999",
                "name": "secret",
                "is_private": True,
                "is_channel": True,
            },
        ],
    )
    client = DumpClient(tmp_path)
    listed = {c["id"]: c for c in client.conversations_list()["channels"]}
    assert listed["C123"]["is_private"] is True
    assert listed["C999"]["name"] == "secret"
    hist = client.conversations_history(channel="C999")
    assert hist["ok"] is True
    assert hist["messages"] == []


def test_reminders_list(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "reminders.json",
        [{"id": "Rm1", "text": "standup", "user": "U1", "complete_ts": 0}],
    )
    client = DumpClient(tmp_path)
    resp = client.reminders_list()
    assert resp["ok"] is True
    assert resp["reminders"][0]["text"] == "standup"


def test_export_jsonl(dump_root: Path, tmp_path: Path) -> None:
    client = DumpClient(dump_root)
    out = tmp_path / "all.jsonl"
    n = client.export_jsonl(out)
    assert n >= 4
    lines = out.read_text(encoding="utf-8").splitlines()
    assert len(lines) == n
    row = json.loads(lines[0])
    assert "text" in row
    assert "channel" in row


def test_users_profile_get(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.users_profile_get(user="U1")
    assert resp["ok"] is True
    assert resp["profile"]["email"] == "alice@acme.test"
    missing = client.users_profile_get(user="UNOPE")
    assert missing["ok"] is False


def test_files_comments(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "file",
                "reactions": [],
                "files": [
                    {
                        "id": "F1",
                        "name": "a.txt",
                        "comments": [
                            {"id": "Fc1", "comment": "nice", "user": "U2"},
                            {"id": "Fc2", "comment": "also", "user": "U1"},
                        ],
                    }
                ],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.files_comments(file="F1")
    assert resp["ok"] is True
    assert resp["comments"][0]["comment"] == "nice"
    page = client.files_comments(file="F1", count=1, page=2)
    assert [c["id"] for c in page["comments"]] == ["Fc2"]
    hits = client.files_comments_search(file="F1", query="nice")["comments"]
    assert [c["id"] for c in hits] == ["Fc1"]
    empty = client.files_comments_search(file="F1", query="")
    assert empty["ok"] is False
    missing = client.files_comments(file="FNOPE")
    assert missing["ok"] is False


def test_dnd_team_info(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "dnd.json", {"U1": {"dnd_enabled": True, "next_dnd_start_ts": 1}})
    client = DumpClient(tmp_path)
    resp = client.dnd_teamInfo()
    assert resp["ok"] is True
    assert resp["users"]["U1"]["dnd_enabled"] is True
    hits = client.dnd_search(query="U1")["users"]
    assert list(hits) == ["U1"]
    empty = client.dnd_search(query="")
    assert empty["ok"] is False


def test_team_profile_get(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "team_profile.json", {"fields": [{"id": "Xf1", "label": "Title"}]})
    client = DumpClient(tmp_path)
    resp = client.team_profile_get()
    assert resp["ok"] is True
    assert resp["profile"]["fields"][0]["id"] == "Xf1"
    hits = client.team_profile_search(query="Title")["profile"]["fields"]
    assert [f["id"] for f in hits] == ["Xf1"]
    empty = client.team_profile_search(query="")
    assert empty["ok"] is False


def test_users_identity(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "auth.json",
        {"user": "alice", "user_id": "U1", "team": "acme", "team_id": "T1"},
    )
    client = DumpClient(tmp_path)
    resp = client.users_identity()
    assert resp["ok"] is True
    assert resp["user"]["id"] == "U1"
    assert resp["team"]["id"] == "T1"


def test_bots_info(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "UBOT",
                "user_name": "deploybot",
                "bot_id": "B99",
                "app_id": "A1",
                "username": "deploybot",
                "text": "shipped",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.bots_info(bot="B99")
    assert resp["ok"] is True
    assert resp["bot"]["id"] == "B99"
    assert resp["bot"]["name"] == "deploybot"
    assert resp["bot"]["app_id"] == "A1"
    missing = client.bots_info(bot="BNOPE")
    assert missing["ok"] is False


def test_files_list_types(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "docs",
                "reactions": [],
                "files": [
                    {"id": "F1", "name": "a.png", "filetype": "png"},
                    {"id": "F2", "name": "notes", "filetype": "canvas"},
                ],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.files_list(types="canvas")
    assert {f["id"] for f in resp["files"]} == {"F2"}


def test_scheduled_messages_list(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "scheduled_messages.json",
        [
            {"id": "Q1", "channel_id": "C123", "text": "later", "post_at": 9},
            {"id": "Q2", "channel_id": "C456", "text": "other", "post_at": 10},
        ],
    )
    client = DumpClient(tmp_path)
    all_q = client.chat_scheduledMessages_list()
    assert {m["id"] for m in all_q["scheduled_messages"]} == {"Q1", "Q2"}
    one = client.chat_scheduledMessages_list(channel="C123")
    assert [m["id"] for m in one["scheduled_messages"]] == ["Q1"]
    info = client.chat_scheduledMessages_info(id="Q2")
    assert info["ok"] is True
    assert info["scheduled_message"]["text"] == "other"
    missing = client.chat_scheduledMessages_info(id="Q404")
    assert missing["ok"] is False
    hits = client.chat_scheduledMessages_search(query="later")["scheduled_messages"]
    assert [m["id"] for m in hits] == ["Q1"]
    empty = client.chat_scheduledMessages_search(query="")
    assert empty["ok"] is False


def test_api_call_dotted_method(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.api_call("conversations.history", params={"channel": "C123", "pretty": 1})
    assert resp["ok"] is True
    assert {m["type"] for m in resp["messages"]} == {"message"}
    missing = client.api_call("chat.postMessage", json={"channel": "C123", "text": "nope"})
    assert missing["ok"] is False
    assert missing["error"] == "unknown_method"


def test_slack_export_layout(tmp_path: Path) -> None:
    root = tmp_path / "export"
    _write(
        root / "users.json",
        [
            {
                "id": "U1",
                "name": "alice",
                "real_name": "Alice Smith",
                "is_bot": False,
                "tz": "UTC",
                "tz_label": "UTC",
                "profile": {
                    "display_name": "alice",
                    "real_name": "Alice Smith",
                    "email": "alice@acme.test",
                    "image_192": "https://example.test/a.png",
                },
            }
        ],
    )
    _write(
        root / "channels.json",
        [{"id": "C123", "name": "general", "is_channel": True, "is_private": False}],
    )
    _write(
        root / "general" / "2024-01-15.json",
        [
            {
                "type": "message",
                "ts": "1.0",
                "user": "U1",
                "text": "hello export",
                "reply_count": 1,
                "thread_ts": "1.0",
            },
            {
                "type": "message",
                "ts": "1.1",
                "user": "U2",
                "text": "export reply",
                "thread_ts": "1.0",
            },
            {
                "type": "message",
                "ts": "2.0",
                "user": "U1",
                "text": "later that day",
            },
        ],
    )
    client = DumpClient(root)
    listed = client.conversations_list()
    assert {ch["id"] for ch in listed["channels"]} == {"C123"}
    hist = client.conversations_history(channel="C123")
    assert [m["text"] for m in hist["messages"]] == ["later that day", "hello export"]
    replies = client.conversations_replies(channel="C123", ts="1.0")
    assert [m["text"] for m in replies["messages"]] == ["hello export", "export reply"]
    users = client.users_list()
    alice = next(u for u in users["members"] if u["id"] == "U1")
    assert alice["name"] == "alice"
    assert alice["profile"]["email"] == "alice@acme.test"


def test_flat_thread_ts_in_messages_json(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {"ts": "1.0", "user": "U1", "user_name": "alice", "text": "root", "type": "message"},
            {
                "ts": "1.1",
                "user": "U2",
                "user_name": "bob",
                "text": "reply",
                "type": "message",
                "thread_ts": "1.0",
            },
        ],
    )
    client = DumpClient(tmp_path)
    hist = client.conversations_history(channel="C123")
    assert [m["text"] for m in hist["messages"]] == ["root"]
    replies = client.conversations_replies(channel="C123", ts="1.0")
    assert [m["text"] for m in replies["messages"]] == ["root", "reply"]


def test_flat_thread_ts_merges_into_parent_for_threads_list(tmp_path: Path) -> None:
    """Export-style loose replies must nest under the parent, not duplicate threads_list."""
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "root",
                "type": "message",
                "reply_count": 1,
                "thread_ts": "1.0",
                "latest_reply": "",
            },
            {
                "ts": "1.1",
                "user": "U2",
                "user_name": "bob",
                "text": "reply",
                "type": "message",
                "thread_ts": "1.0",
            },
        ],
    )
    client = DumpClient(tmp_path)
    rows = list(client.iter_threads(channel="C123"))
    assert len(rows) == 1
    assert rows[0]["thread_ts"] == "1.0"
    assert rows[0]["latest_reply"] == "1.1"
    info = client.threads_info(channel="C123", ts="1.0")
    assert info["ok"] is True
    assert info["thread"]["latest_reply"] == "1.1"


def test_search_has_attachment(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "bot card",
                "reactions": [],
                "files": [],
                "thread": [],
                "attachments": [{"id": 1, "title": "card"}],
            },
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "plain",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="has:attachment")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["bot card"]


def test_conversations_list_exclude_archived(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "conversations.json",
        [
            {"id": "C123", "name": "general", "is_archived": False, "is_channel": True},
            {"id": "C999", "name": "old", "is_archived": True, "is_channel": True},
        ],
    )
    client = DumpClient(tmp_path)
    all_ch = {c["id"] for c in client.conversations_list()["channels"]}
    assert all_ch == {"C123", "C999"}
    live = {c["id"] for c in client.conversations_list(exclude_archived=True)["channels"]}
    assert live == {"C123"}


def test_conversations_list_types_respect_channel_meta(tmp_path: Path) -> None:
    """Type filters must follow channel.json flags, not prefix-only kinds."""
    ws = tmp_path / "acme"
    pub = ws / "general_C111"
    priv = ws / "secret_C222"
    mpim = ws / "mpdm_G333"
    group = ws / "closed_G444"
    for path in (pub, priv, mpim, group):
        path.mkdir(parents=True)
        _write(path / "messages.json", [])
    _write(
        pub / "channel.json",
        {
            "id": "C111",
            "name": "general",
            "is_channel": True,
            "is_private": False,
            "is_mpim": False,
            "is_group": False,
            "is_im": False,
        },
    )
    _write(
        priv / "channel.json",
        {
            "id": "C222",
            "name": "secret",
            "is_channel": True,
            "is_private": True,
            "is_mpim": False,
            "is_group": False,
            "is_im": False,
        },
    )
    _write(
        mpim / "channel.json",
        {
            "id": "G333",
            "name": "mpdm",
            "is_channel": False,
            "is_private": True,
            "is_mpim": True,
            "is_group": True,
            "is_im": False,
        },
    )
    _write(
        group / "channel.json",
        {
            "id": "G444",
            "name": "closed",
            "is_channel": False,
            "is_private": True,
            "is_mpim": False,
            "is_group": True,
            "is_im": False,
        },
    )
    client = DumpClient(tmp_path)
    assert {c["id"] for c in client.conversations_list(types="public_channel")["channels"]} == {
        "C111"
    }
    assert {c["id"] for c in client.conversations_list(types="private_channel")["channels"]} == {
        "C222",
        "G444",
    }
    assert {c["id"] for c in client.conversations_list(types="mpim")["channels"]} == {"G333"}


def test_channel_obj_flags_follow_kinds_when_meta_omits_is_mpim(tmp_path: Path) -> None:
    """G private groups must not keep prefix is_mpim=true when kinds say private_channel."""
    ws = tmp_path / "acme"
    group = ws / "closed_G444"
    group.mkdir(parents=True)
    _write(group / "messages.json", [])
    # Catalog classifies as private group but omits is_mpim (common thin export).
    _write(
        ws / "conversations.json",
        [{"id": "G444", "name": "closed", "is_group": True, "is_private": True}],
    )
    client = DumpClient(tmp_path)
    info = client.conversations_info(channel="G444")["channel"]
    assert info["is_mpim"] is False
    assert info["is_group"] is True
    assert info["is_private"] is True
    start = client.rtm_start()
    assert [c["id"] for c in start["mpims"]] == []
    assert [c["id"] for c in start["groups"]] == ["G444"]


def test_team_info_from_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "team.json",
        {"id": "T9", "name": "Acme Inc", "domain": "acme", "email_domain": "acme.test"},
    )
    client = DumpClient(tmp_path)
    resp = client.team_info()
    assert resp["team"]["id"] == "T9"
    assert resp["team"]["email_domain"] == "acme.test"


def test_dnd_info(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "dnd.json", {"U1": {"dnd_enabled": True, "next_dnd_start_ts": 9}})
    client = DumpClient(tmp_path)
    resp = client.dnd_info(user="U1")
    assert resp["ok"] is True
    assert resp["dnd_enabled"] is True
    missing = client.dnd_info(user="UNOPE")
    assert missing["ok"] is False


def test_team_integration_logs(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "integration_logs.json", [{"user_id": "U1", "service_id": "S1"}])
    client = DumpClient(tmp_path)
    resp = client.team_integrationLogs()
    assert resp["logs"][0]["service_id"] == "S1"


def test_search_is_thread(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.search_messages(query="is:thread")
    texts = {m["text"] for m in resp["messages"]["matches"]}
    assert "thread root" in texts
    assert "a reply" in texts
    assert "hello @bob" not in texts


def test_search_is_dm(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "public ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    _write(
        (tmp_path / "acme" / "alice_D111") / "messages.json",
        [
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "dm ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="is:dm ping")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["dm ping"]


def test_bookmarks_list_channel_param(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(ch / "bookmarks.json", [{"id": "Bk1", "title": "docs"}])
    client = DumpClient(tmp_path)
    resp = client.bookmarks_list(channel="C123")
    assert resp["bookmarks"][0]["id"] == "Bk1"


def test_files_info_from_workspace_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "files.json", [{"id": "Fext", "name": "shared.png", "filetype": "png"}])
    client = DumpClient(tmp_path)
    resp = client.files_info(file="Fext")
    assert resp["file"]["name"] == "shared.png"
    by_name = client.files_info(file="shared.png")
    assert by_name["ok"] is True
    assert by_name["file"]["id"] == "Fext"
    listed = client.files_list()
    assert {f["id"] for f in listed["files"]} == {"Fext"}


def test_im_replies_alias(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    resp = client.im_replies(channel="C123", ts="2.0")
    assert resp["ok"] is True
    assert next(m["text"] for m in resp["messages"]) == "thread root"


def test_api_test(tmp_path: Path) -> None:
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [])
    client = DumpClient(tmp_path)
    resp = client.api_test()
    assert resp["ok"] is True
    echoed = client.api_call("api.test", params={"foo": "bar"})
    assert echoed["ok"] is True
    assert echoed.get("foo") == "bar"


def test_reminders_info(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "reminders.json", [{"id": "Rm1", "text": "ping", "complete": False}])
    client = DumpClient(tmp_path)
    resp = client.reminders_info(reminder="Rm1")
    assert resp["ok"] is True
    assert resp["reminder"]["text"] == "ping"
    missing = client.reminders_info(reminder="Rm404")
    assert missing["ok"] is False
    hits = client.reminders_search(query="ping")["reminders"]
    assert [r["id"] for r in hits] == ["Rm1"]
    empty = client.reminders_search(query="")
    assert empty["ok"] is False


def test_search_is_bot(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "human ping",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "2.0",
                "bot_id": "B1",
                "username": "deploybot",
                "text": "bot ping",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="is:bot ping")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["bot ping"]


def test_search_is_channel_and_private(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "chan ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    _write(
        (tmp_path / "acme" / "alice_D111") / "messages.json",
        [
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "dm ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    _write(
        (tmp_path / "acme" / "secret_G999") / "messages.json",
        [
            {
                "ts": "3.0",
                "user": "U1",
                "user_name": "alice",
                "text": "priv ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    channel_hits = {
        m["text"] for m in client.search_messages(query="is:channel ping")["messages"]["matches"]
    }
    assert channel_hits == {"chan ping"}
    private_hits = {
        m["text"] for m in client.search_messages(query="is:private ping")["messages"]["matches"]
    }
    assert private_hits == {"dm ping", "priv ping"}
    public_hits = {
        m["text"] for m in client.search_messages(query="is:public ping")["messages"]["matches"]
    }
    assert public_hits == {"chan ping"}


def test_search_is_private_from_channel_json(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "secret_C999"
    _write(ch / "messages.json", [_msg("1.0", "secret ping")])
    _write(
        ch / "channel.json",
        {"id": "C999", "name": "secret", "is_private": True, "is_channel": True},
    )
    client = DumpClient(tmp_path)
    private_hits = {
        m["text"] for m in client.search_messages(query="is:private ping")["messages"]["matches"]
    }
    public_hits = {
        m["text"] for m in client.search_messages(query="is:public ping")["messages"]["matches"]
    }
    assert private_hits == {"secret ping"}
    assert public_hits == set()


def test_im_info_and_groups_info_alias(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    im = client.im_info(channel="C123")
    assert im["ok"] is True
    assert im["channel"]["id"] == "C123"
    groups = client.groups_info(channel="C123")
    assert groups["channel"]["name"] == "general"


def test_team_access_logs(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "access_logs.json", [{"user_id": "U1", "ip": "1.2.3.4"}])
    client = DumpClient(tmp_path)
    resp = client.team_accessLogs()
    assert resp["logins"][0]["ip"] == "1.2.3.4"


def test_users_get_presence(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "auth.json", {"user_id": "U1", "team": "acme"})
    _write(ws / "presence.json", {"U1": {"presence": "active", "online": True}})
    client = DumpClient(tmp_path)
    mine = client.users_getPresence()
    assert mine["presence"] == "active"
    other = client.users_getPresence(user="U404")
    assert other["ok"] is False
    hits = client.presence_search(query="U1")["users"]
    assert list(hits) == ["U1"]
    empty = client.presence_search(query="")
    assert empty["ok"] is False


def test_auth_teams_list(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "auth.json", {"user_id": "U1", "team": "acme", "team_id": "T1"})
    client = DumpClient(tmp_path)
    resp = client.auth_teams_list()
    assert resp["teams"][0]["id"] == "T1"
    assert resp["teams"][0]["name"] == "acme"


def test_search_is_group(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "chan ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    _write(
        (tmp_path / "acme" / "secret_G999") / "messages.json",
        [
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "group ping",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    client = DumpClient(tmp_path)
    hits = {m["text"] for m in client.search_messages(query="is:group ping")["messages"]["matches"]}
    assert hits == {"group ping"}


def test_files_remote_list(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "files.json",
        [
            {"id": "Flocal", "name": "local.png", "filetype": "png"},
            {"id": "Fext", "name": "drive.doc", "is_external": True, "external_id": "g1"},
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.files_remote_list()
    assert {f["id"] for f in resp["files"]} == {"Fext"}
    info = client.files_remote_info(file="Fext")
    assert info["file"]["external_id"] == "g1"
    missing = client.files_remote_info(file="Flocal")
    assert missing["ok"] is False


def test_team_billable_info(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "billable_info.json", {"U1": {"billing_active": True}})
    client = DumpClient(tmp_path)
    resp = client.team_billableInfo()
    assert resp["billable_info"]["U1"]["billing_active"] is True


def test_search_from_me(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "mine",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "2.0",
                "user": "U2",
                "user_name": "bob",
                "text": "theirs",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    _write(tmp_path / "acme" / "auth.json", {"user_id": "U1", "user": "alice"})
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="from:me")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["mine"]


def test_expand_me_sentinel_and_auth_expansion() -> None:
    """expand_me: unknown auth yields NUL sentinel; known auth yields id + name."""
    from ssd.dumpsearch import expand_me

    assert expand_me(["me"], {}) == ["\0"]
    assert expand_me(["me", "bob"], {}) == ["\0", "bob"]
    assert expand_me(["me"], {"user_id": "U1", "user": "alice"}) == ["U1", "alice"]
    assert expand_me(["@me"], {"user_id": "U1", "user": "alice"}) == ["U1", "alice"]


def test_search_from_me_without_auth_matches_nothing(tmp_path: Path) -> None:
    """from:me must fail closed when auth.json has no user identity."""
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "mine",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="from:me")
    assert resp["messages"]["matches"] == []
    assert resp["messages"]["total"] == 0


def test_search_to_me(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U2",
                "user_name": "bob",
                "text": "hey @alice",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "2.0",
                "user": "U2",
                "user_name": "bob",
                "text": "hey @bob",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    _write(tmp_path / "acme" / "auth.json", {"user_id": "U1", "user": "alice"})
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="to:me")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["hey @alice"]


@pytest.mark.parametrize("query", ["is:starred", "has:star"])
def test_search_star_aliases(dump_root: Path, query: str) -> None:
    _write(
        dump_root / "acme" / "stars.json",
        [{"type": "message", "channel": "C123", "message": {"ts": "1.0"}}],
    )
    client = DumpClient(dump_root)
    texts = {m["text"] for m in client.search_messages(query=query)["messages"]["matches"]}
    assert texts == {"hello @bob"}


def test_search_around(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1000000000.0",
                "user": "U1",
                "user_name": "alice",
                "text": "near",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "1000001000.0",
                "user": "U1",
                "user_name": "alice",
                "text": "close",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "1000100000.0",
                "user": "U1",
                "user_name": "alice",
                "text": "far",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="around:1000000000")["messages"]["matches"]
    texts = {m["text"] for m in hits}
    assert texts == {"near", "close"}
    assert "far" not in texts


def test_files_list_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "files.json",
        [
            {"id": "Fa", "name": "a.png", "filetype": "png"},
            {"id": "Fb", "name": "b.png", "filetype": "png"},
        ],
    )
    client = DumpClient(tmp_path)
    page2 = client.files_list(count=1, page=2)
    assert [f["id"] for f in page2["files"]] == ["Fb"]
    hits = client.files_list_search(query="a.png")["files"]
    assert [f["id"] for f in hits] == ["Fa"]
    empty = client.files_list_search(query="")
    assert empty["ok"] is False


def test_files_list_sidecar_skips_message_files(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write(
        (ws / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "see file",
                "reactions": [],
                "files": [{"id": "Fmsg", "name": "msg.png", "filetype": "png"}],
                "thread": [],
            }
        ],
    )
    _write(ws / "files.json", [{"id": "Fws", "name": "workspace.png", "filetype": "png"}])
    client = DumpClient(tmp_path)
    assert {f["id"] for f in client.files_list()["files"]} == {"Fws"}


def test_search_on_date(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1717257600.0",
                "user": "U1",
                "user_name": "alice",
                "text": "on day",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "1717344000.0",
                "user": "U1",
                "user_name": "alice",
                "text": "next day",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="on:2024-06-01")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["on day"]


def test_search_multiple_on_windows_are_or(tmp_path: Path) -> None:
    """Exclusive day windows must OR; AND would match nothing."""
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1717200000.0",  # 2024-06-01
                "user": "U1",
                "user_name": "alice",
                "text": "day one",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "1717344000.0",  # 2024-06-02
                "user": "U1",
                "user_name": "alice",
                "text": "day two",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "1717430400.0",  # 2024-06-03
                "user": "U1",
                "user_name": "alice",
                "text": "day three",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    texts = {
        m["text"]
        for m in client.search_messages(query="on:2024-06-01 on:2024-06-02")["messages"]["matches"]
    }
    assert texts == {"day one", "day two"}


def test_search_after_bound_preserves_microseconds(tmp_path: Path) -> None:
    """after:/before: must use Decimal bounds; float collapses these timestamps."""
    lo, hi = "9999999999.000001", "9999999999.000002"
    assert float(lo) == float(hi)
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": lo,
                "user": "U1",
                "user_name": "alice",
                "text": "first",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": hi,
                "user": "U1",
                "user_name": "alice",
                "text": "second",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    texts = [m["text"] for m in client.search_messages(query=f"after:{lo}")["messages"]["matches"]]
    assert texts == ["second"]


def test_search_negation(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "alpha keep",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "alpha drop",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="alpha -drop")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["alpha keep"]


def test_search_files_negation(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    _write(
        tmp_path / "acme" / "files.json",
        [
            {
                "id": "F1",
                "name": "report.pdf",
                "title": "report",
                "filetype": "pdf",
                "mimetype": "application/pdf",
                "user": "U1",
                "created": 1,
            },
            {
                "id": "F2",
                "name": "draft-report.pdf",
                "title": "draft report",
                "filetype": "pdf",
                "mimetype": "application/pdf",
                "user": "U1",
                "created": 2,
            },
        ],
    )
    client = DumpClient(tmp_path)
    hits = [f["id"] for f in client.search_files(query="report -draft")["files"]["matches"]]
    assert hits == ["F1"]


def test_search_messages_page(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hit two",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hit one",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    page2 = client.search_messages(query="hit", count=1, page=2)
    assert [m["text"] for m in page2["messages"]["matches"]] == ["hit one"]


def test_search_messages_page_beyond_window_returns_empty(tmp_path: Path) -> None:
    """Pages past ``_MAX_SEARCH_NEED`` must not allocate a huge heap."""
    from ssd import dumpapi as dumpapi_mod

    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hit",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    # page such that start == _MAX_SEARCH_NEED with count=1
    page = dumpapi_mod._MAX_SEARCH_NEED + 1
    resp = client.search_messages(query="hit", count=1, page=page)
    assert resp["messages"]["total"] == 1
    assert resp["messages"]["matches"] == []


def test_migration_exchange(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {"U1": {"id": "U1", "handle": "alice", "display_name": "alice", "real_name": "Alice"}},
    )
    _write(ws / "auth.json", {"team_id": "T1", "user_id": "U1"})
    client = DumpClient(tmp_path)
    resp = client.migration_exchange(users="U1,U404")
    assert resp["ok"] is True
    assert resp["team_id"] == "T1"
    assert resp["user_id_map"]["U1"] == "U1"
    assert "U404" in resp["invalid_user_ids"]


def test_users_conversations_exclude_archived(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write((ws / "general_C123") / "members.json", ["U1"])
    _write((ws / "old_C999") / "messages.json", [])
    _write((ws / "old_C999") / "members.json", ["U1"])
    _write(
        ws / "conversations.json",
        [
            {"id": "C123", "name": "general", "is_archived": False, "is_channel": True},
            {"id": "C999", "name": "old", "is_archived": True, "is_channel": True},
        ],
    )
    client = DumpClient(tmp_path)
    all_ch = {c["id"] for c in client.users_conversations(user="U1")["channels"]}
    assert all_ch == {"C123", "C999"}
    live = {
        c["id"] for c in client.users_conversations(user="U1", exclude_archived=True)["channels"]
    }
    assert live == {"C123"}


def test_search_sort_dir_asc(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "2.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hit two",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "hit one",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.search_messages(query="hit", sort_dir="asc")
    assert [m["text"] for m in resp["messages"]["matches"]] == ["hit one", "hit two"]


def test_search_files_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "files.json",
        [
            {"id": "Fa", "name": "a.png", "filetype": "png"},
            {"id": "Fb", "name": "b.png", "filetype": "png"},
        ],
    )
    client = DumpClient(tmp_path)
    page2 = client.search_files(query="png", count=1, page=2)
    assert [f["id"] for f in page2["files"]["matches"]] == ["Fb"]


def test_stars_list_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "stars.json",
        [
            {"type": "message", "channel": "C123", "message": {"ts": "1.0"}},
            {"type": "message", "channel": "C123", "message": {"ts": "2.0"}},
        ],
    )
    client = DumpClient(tmp_path)
    page2 = client.stars_list(count=1, page=2)
    assert page2["items"][0]["message"]["ts"] == "2.0"


def test_usergroups_list_include_count(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "usergroups.json",
        [{"id": "S1", "handle": "eng", "name": "Engineering", "users": ["U1", "U2"]}],
    )
    client = DumpClient(tmp_path)
    resp = client.usergroups_list(include_count=True)
    assert resp["usergroups"][0]["user_count"] == 2


def test_calls_info(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "huddle",
                "reactions": [],
                "files": [],
                "thread": [],
                "room": {"id": "R1", "name": "standup", "participant_history": ["U1"]},
            }
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.calls_info(id="R1")
    assert resp["ok"] is True
    assert resp["call"]["name"] == "standup"
    missing = client.calls_info(id="R404")
    assert missing["ok"] is False


def test_stats_from_channel_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    _write(ch / "messages.json", [])
    _write(ch / "stats.json", {"messages": 9, "replies": 3, "files": 1})
    _write(ws / "users.json", {"U1": {"id": "U1", "handle": "alice"}})
    client = DumpClient(tmp_path)
    resp = client.stats()
    assert resp["messages"] == 9
    assert resp["replies"] == 3
    assert resp["users"] == 1


def _FIXED_SEARCH_NOW():
    from datetime import UTC, datetime

    # Saturday 2024-06-15 noon UTC; weekday()==5 so lastweek/lastmonth math is stable.
    return datetime(2024, 6, 15, 12, 0, 0, tzinfo=UTC)


def _freeze_search_now(monkeypatch, when=None):
    """Pin ssd.dumpsearch.datetime.now so relative day/week/month filters are deterministic."""
    from datetime import datetime

    when = when or _FIXED_SEARCH_NOW()

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            return when

    monkeypatch.setattr("ssd.dumpsearch.datetime", _FrozenDateTime)
    return when


def _noon_ts(days_ago: int, now=None) -> str:
    from datetime import timedelta

    now = now or _FIXED_SEARCH_NOW()
    noon = now.replace(hour=12, minute=0, second=0, microsecond=0)
    return str((noon - timedelta(days=days_ago)).timestamp())


def _msg(ts: str, text: str) -> dict:
    return {
        "ts": ts,
        "user": "U1",
        "user_name": "alice",
        "text": text,
        "reactions": [],
        "files": [],
        "thread": [],
    }


@pytest.mark.parametrize(
    ("msg_query", "file_query", "hit_text", "hit_file"),
    [
        ("has:space", "has:space", "post", {"id": "Fp", "name": "doc", "filetype": "space"}),
        ("has:email", "has:email", "mail", {"id": "Fe", "name": "note.eml", "filetype": "email"}),
        ("has:pdf", "has:pdf", "doc", {"id": "Fpdf", "name": "a.pdf", "filetype": "pdf"}),
        (
            "has:spreadsheet",
            "has:spreadsheet",
            "sheet",
            {"id": "Fx", "name": "a.xlsx", "filetype": "xlsx"},
        ),
        ("has:zip", "has:zip", "zip", {"id": "Fz", "name": "a.zip", "filetype": "zip"}),
        (
            "has:presentation",
            "has:presentation",
            "deck",
            {"id": "Fp", "name": "talk.pptx", "filetype": "pptx"},
        ),
        ("has:list", "has:list", "listed", {"id": "Fl", "name": "tasks", "filetype": "list"}),
        ("has:doc", "has:doc", "doc", {"id": "Fd", "name": "spec.docx", "filetype": "docx"}),
        ("has:txt", "has:txt", "note", {"id": "Ft", "name": "notes.txt", "filetype": "text"}),
        ("has:gif", "has:gif", "gif", {"id": "Fg", "name": "loop.gif", "filetype": "gif"}),
        ("has:json", "has:json", "blob", {"id": "Fj", "name": "data.json", "filetype": "json"}),
        ("has:csv", "has:csv", "sheet", {"id": "Fc", "name": "data.csv", "filetype": "csv"}),
        ("has:xml", "has:xml", "blob", {"id": "Fx", "name": "data.xml", "filetype": "xml"}),
        ("has:md", "has:markdown", "doc", {"id": "Fm", "name": "notes.md", "filetype": "markdown"}),
        ("has:yaml", "has:yml", "blob", {"id": "Fy", "name": "data.yaml", "filetype": "yaml"}),
        (
            "has:toml",
            "has:toml",
            "blob",
            {"id": "Ft", "name": "pyproject.toml", "filetype": "toml"},
        ),
        ("has:python", "has:py", "script", {"id": "Fp", "name": "app.py", "filetype": "python"}),
        ("has:video", "has:video", "clip", {"id": "Fv", "name": "a.mp4", "filetype": "mp4", "mimetype": "video/mp4"}),
        ("has:audio", "has:audio", "sound", {"id": "Fa", "name": "a.mp3", "filetype": "mp3", "mimetype": "audio/mpeg"}),
    ],
    ids=[
        "space",
        "email",
        "pdf",
        "spreadsheet",
        "zip",
        "presentation",
        "list",
        "doc",
        "txt",
        "gif",
        "json",
        "csv",
        "xml",
        "md",
        "yaml",
        "toml",
        "python",
        "video",
        "audio",
    ],
)
def test_search_has_file_kind(
    tmp_path: Path,
    msg_query: str,
    file_query: str,
    hit_text: str,
    hit_file: dict,
) -> None:
    hit = _msg("1.0", hit_text)
    hit["files"] = [hit_file]
    pic = _msg("2.0", "pic")
    pic["files"] = [{"id": "Fi", "name": "a.png", "filetype": "png"}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [hit, pic])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query=msg_query)["messages"]["matches"]
    assert [m["text"] for m in hits] == [hit_text]
    files = client.search_files(query=file_query)["files"]["matches"]
    assert [f["id"] for f in files] == [hit_file["id"]]


@pytest.mark.parametrize(
    ("query", "hit", "miss", "hit_dir", "miss_dir"),
    [
        (
            "is:org_shared hello",
            {"id": "C123", "name": "grid", "is_channel": True, "is_org_shared": True},
            {"id": "C999", "name": "local", "is_channel": True, "is_org_shared": False},
            "grid_C123",
            "local_C999",
        ),  # org_shared
        (
            "is:archived hello",
            {"id": "C123", "name": "old", "is_channel": True, "is_archived": True},
            {"id": "C999", "name": "live", "is_channel": True, "is_archived": False},
            "old_C123",
            "live_C999",
        ),  # archived
        (
            "is:frozen hello",
            {"id": "C123", "name": "ice", "is_channel": True, "is_frozen": True},
            {"id": "C999", "name": "live", "is_channel": True, "is_frozen": False},
            "ice_C123",
            "live_C999",
        ),  # frozen
        (
            "is:muted hello",
            {"id": "C123", "name": "quiet", "is_channel": True, "is_muted": True},
            {"id": "C999", "name": "loud", "is_channel": True, "is_muted": False},
            "quiet_C123",
            "loud_C999",
        ),  # muted
        (
            "is:open hello",
            {"id": "D123", "name": "dm", "is_im": True, "is_open": True},
            {"id": "D999", "name": "closed", "is_im": True, "is_open": False},
            "dm_D123",
            "closed_D999",
        ),  # open
        (
            "is:org_default hello",
            {"id": "C123", "name": "allhands", "is_channel": True, "is_org_default": True},
            {"id": "C999", "name": "other", "is_channel": True},
            "allhands_C123",
            "other_C999",
        ),  # org_default
        (
            "is:global_shared hello",
            {"id": "C123", "name": "grid", "is_channel": True, "is_global_shared": True},
            {"id": "C999", "name": "local", "is_channel": True},
            "grid_C123",
            "local_C999",
        ),  # global_shared
        (
            "is:org_mandatory hello",
            {"id": "C123", "name": "must", "is_channel": True, "is_org_mandatory": True},
            {"id": "C999", "name": "opt", "is_channel": True},
            "must_C123",
            "opt_C999",
        ),  # org_mandatory
        (
            "is:member hello",
            {"id": "C123", "name": "in", "is_channel": True, "is_member": True},
            {"id": "C999", "name": "out", "is_channel": True, "is_member": False},
            "in_C123",
            "out_C999",
        ),  # member
        (
            "is:pending_ext_shared hello",
            {"id": "C123", "name": "wait", "is_channel": True, "is_pending_ext_shared": True},
            {"id": "C999", "name": "ok", "is_channel": True},
            "wait_C123",
            "ok_C999",
        ),  # pending_ext_shared
        (
            "is:pending_shared hello",
            {"id": "C123", "name": "wait", "is_channel": True, "is_pending_shared": True},
            {"id": "C999", "name": "ok", "is_channel": True},
            "wait_C123",
            "ok_C999",
        ),  # pending_shared
        (
            "is:has_canvas hello",
            {"id": "C123", "name": "docs", "is_channel": True, "has_canvas": True},
            {"id": "C999", "name": "chat", "is_channel": True},
            "docs_C123",
            "chat_C999",
        ),  # has_canvas
        (
            "is:unlinked hello",
            {"id": "C123", "name": "old", "is_channel": True, "unlinked": 99},
            {"id": "C999", "name": "live", "is_channel": True},
            "old_C123",
            "live_C999",
        ),  # unlinked
        (
            "is:im_blocked hello",
            {"id": "D123", "name": "blocked", "is_im": True, "is_im_blocked": True},
            {"id": "D999", "name": "open", "is_im": True},
            "blocked_D123",
            "open_D999",
        ),  # im_blocked
        (
            "is:read_only hello",
            {"id": "C123", "name": "ro", "is_channel": True, "is_read_only": True},
            {"id": "C999", "name": "rw", "is_channel": True},
            "ro_C123",
            "rw_C999",
        ),  # read_only
        (
            "is:thread_only hello",
            {"id": "C123", "name": "threads", "is_channel": True, "is_thread_only": True},
            {"id": "C999", "name": "chat", "is_channel": True},
            "threads_C123",
            "chat_C999",
        ),  # thread_only
        (
            "is:non_threadable hello",
            {"id": "C123", "name": "flat", "is_channel": True, "is_non_threadable": True},
            {"id": "C999", "name": "chat", "is_channel": True},
            "flat_C123",
            "chat_C999",
        ),  # non_threadable
        (
            "is:user_deleted hello",
            {"id": "D1", "name": "gone", "is_im": True, "is_user_deleted": True, "user": "U2"},
            {"id": "D9", "name": "live", "is_im": True, "user": "U3"},
            "gone_D1",
            "live_D9",
        ),  # user_deleted
    ],
    ids=[
        "org_shared",
        "archived",
        "frozen",
        "muted",
        "open",
        "org_default",
        "global_shared",
        "org_mandatory",
        "member",
        "pending_ext_shared",
        "pending_shared",
        "has_canvas",
        "unlinked",
        "im_blocked",
        "read_only",
        "thread_only",
        "non_threadable",
        "user_deleted",
    ],
)
def test_search_is_channel_flag(
    tmp_path: Path,
    query: str,
    hit: dict,
    miss: dict,
    hit_dir: str,
    miss_dir: str,
) -> None:
    _write(tmp_path / "acme" / "conversations.json", [hit, miss])
    _write((tmp_path / "acme" / hit_dir) / "messages.json", [_msg("1.0", "hello")])
    _write((tmp_path / "acme" / miss_dir) / "messages.json", [_msg("2.0", "hello")])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query=query)["messages"]["matches"]
    assert [m["channel"]["id"] for m in hits] == [hit["id"]]


def test_search_after_yesterday(tmp_path: Path, monkeypatch) -> None:
    _freeze_search_now(monkeypatch)
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            _msg(_noon_ts(0), "today ping"),
            _msg(_noon_ts(1), "yesterday ping"),
            _msg(_noon_ts(3), "old ping"),
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="after:yesterday ping")["messages"]["matches"]
    texts = {m["text"] for m in hits}
    assert texts == {"today ping", "yesterday ping"}


def test_search_during_today(tmp_path: Path, monkeypatch) -> None:
    _freeze_search_now(monkeypatch)
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            _msg(_noon_ts(0), "today ping"),
            _msg(_noon_ts(1), "yesterday ping"),
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="during:today ping")["messages"]["matches"]
    texts = {m["text"] for m in hits}
    assert texts == {"today ping"}


def test_search_all_page(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [_msg("2.0", "hit two"), _msg("1.0", "hit one")],
    )
    client = DumpClient(tmp_path)
    page2 = client.search_all(query="hit", count=1, page=2)
    assert [m["text"] for m in page2["messages"]["matches"]] == ["hit one"]


def test_reminders_list_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "reminders.json", [{"id": "Rm1", "text": "a"}, {"id": "Rm2", "text": "b"}])
    client = DumpClient(tmp_path)
    page2 = client.reminders_list(count=1, page=2)
    assert page2["reminders"][0]["id"] == "Rm2"


def test_team_access_logs_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "access_logs.json",
        [{"user_id": "U1", "ip": "1.1.1.1"}, {"user_id": "U2", "ip": "2.2.2.2"}],
    )
    client = DumpClient(tmp_path)
    page2 = client.team_accessLogs(count=1, page=2)
    assert page2["logins"][0]["ip"] == "2.2.2.2"


def test_search_during_year(tmp_path: Path, monkeypatch) -> None:
    now = _freeze_search_now(monkeypatch)
    this_year = now.replace(month=6, day=15, hour=12, minute=0, second=0, microsecond=0)
    last_year = this_year.replace(year=this_year.year - 1)
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            _msg(str(this_year.timestamp()), "this year ping"),
            _msg(str(last_year.timestamp()), "last year ping"),
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="during:year ping")["messages"]["matches"]
    texts = {m["text"] for m in hits}
    assert texts == {"this year ping"}


def test_team_billable_info_user(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "billable_info.json",
        {"U1": {"billing_active": True}, "U2": {"billing_active": False}},
    )
    client = DumpClient(tmp_path)
    resp = client.team_billableInfo(user="U1")
    assert list(resp["billable_info"]) == ["U1"]
    assert resp["billable_info"]["U1"]["billing_active"] is True
    hits = client.team_billableInfo_search(query="U1")["billable_info"]
    assert list(hits) == ["U1"]
    empty = client.team_billableInfo_search(query="")
    assert empty["ok"] is False


def test_team_integration_logs_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "integration_logs.json",
        [{"service_id": "S1"}, {"service_id": "S2"}],
    )
    client = DumpClient(tmp_path)
    page2 = client.team_integrationLogs(count=1, page=2)
    assert page2["logs"][0]["service_id"] == "S2"


def test_scheduled_messages_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "scheduled_messages.json",
        [
            {"id": "Q1", "channel_id": "C123", "text": "a"},
            {"id": "Q2", "channel_id": "C123", "text": "b"},
        ],
    )
    client = DumpClient(tmp_path)
    page2 = client.chat_scheduledMessages_list(count=1, page=2)
    assert page2["scheduled_messages"][0]["id"] == "Q2"


def test_usergroups_list_include_users_false(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "usergroups.json",
        [{"id": "S1", "handle": "eng", "users": ["U1", "U2"]}],
    )
    client = DumpClient(tmp_path)
    resp = client.usergroups_list(include_users=False)
    assert "users" not in resp["usergroups"][0]


def test_usergroups_list_include_disabled(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "usergroups.json",
        [
            {"id": "S1", "handle": "eng", "users": ["U1"], "date_delete": 0},
            {"id": "S2", "handle": "old", "users": ["U2"], "date_delete": 9},
        ],
    )
    client = DumpClient(tmp_path)
    active = client.usergroups_list()
    assert [g["id"] for g in active["usergroups"]] == ["S1"]
    all_g = client.usergroups_list(include_disabled=True)
    assert [g["id"] for g in all_g["usergroups"]] == ["S1", "S2"]


def test_dnd_team_info_users(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "dnd.json",
        {
            "U1": {"dnd_enabled": True},
            "U2": {"dnd_enabled": False},
        },
    )
    client = DumpClient(tmp_path)
    resp = client.dnd_teamInfo(users="U1")
    assert list(resp["users"]) == ["U1"]
    assert resp["users"]["U1"]["dnd_enabled"] is True


def test_search_during_lastweek(tmp_path: Path, monkeypatch) -> None:
    from datetime import timedelta

    noon = _freeze_search_now(monkeypatch)
    this_monday = noon - timedelta(days=noon.weekday())
    last_monday = this_monday - timedelta(days=7)
    older = last_monday - timedelta(days=7)
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            _msg(str(last_monday.timestamp()), "last week ping"),
            _msg(str(this_monday.timestamp()), "this week ping"),
            _msg(str(older.timestamp()), "old ping"),
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="during:lastweek ping")["messages"]["matches"]
    texts = {m["text"] for m in hits}
    assert texts == {"last week ping"}


def test_search_during_lastmonth(tmp_path: Path, monkeypatch) -> None:
    now = _freeze_search_now(monkeypatch)
    this_mid = now.replace(day=15, hour=12, minute=0, second=0, microsecond=0)
    if this_mid.month == 1:
        last_mid = this_mid.replace(year=this_mid.year - 1, month=12)
    else:
        last_mid = this_mid.replace(month=this_mid.month - 1)
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            _msg(str(last_mid.timestamp()), "last month ping"),
            _msg(str(this_mid.timestamp()), "this month ping"),
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="during:lastmonth ping")["messages"]["matches"]
    texts = {m["text"] for m in hits}
    assert texts == {"last month ping"}


def test_pins_list_all_channels(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write((ws / "random_C456") / "messages.json", [])
    _write((ws / "general_C123") / "pins.json", [{"type": "message", "channel": "C123"}])
    _write((ws / "random_C456") / "pins.json", [{"type": "message", "channel": "C456"}])
    client = DumpClient(tmp_path)
    resp = client.pins_list()
    assert {item["channel"] for item in resp["items"]} == {"C123", "C456"}


def test_bookmarks_list_all_channels(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write((ws / "random_C456") / "messages.json", [])
    _write((ws / "general_C123") / "bookmarks.json", [{"id": "Bk1", "title": "a"}])
    _write((ws / "random_C456") / "bookmarks.json", [{"id": "Bk2", "title": "b"}])
    client = DumpClient(tmp_path)
    resp = client.bookmarks_list()
    assert {b["id"] for b in resp["bookmarks"]} == {"Bk1", "Bk2"}


def test_search_files_during_year(tmp_path: Path, monkeypatch) -> None:
    now = _freeze_search_now(monkeypatch)
    this_year = now.replace(month=6, day=15, hour=12, minute=0, second=0, microsecond=0)
    last_year = this_year.replace(year=this_year.year - 1)
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "files.json",
        [
            {"id": "Fnew", "name": "new.png", "created": this_year.timestamp()},
            {"id": "Fold", "name": "old.png", "created": last_year.timestamp()},
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_files(query="during:year png")["files"]["matches"]
    assert {f["id"] for f in hits} == {"Fnew"}


def test_files_remote_list_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "files.json",
        [
            {"id": "F1", "name": "a.doc", "is_external": True},
            {"id": "F2", "name": "b.doc", "is_external": True},
        ],
    )
    client = DumpClient(tmp_path)
    page2 = client.files_remote_list(count=1, page=2)
    assert page2["files"][0]["id"] == "F2"


def test_calls_participants(tmp_path: Path) -> None:
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "U1",
                "user_name": "alice",
                "text": "huddle",
                "reactions": [],
                "files": [],
                "thread": [],
                "room": {"id": "R1", "participant_history": ["U1", "U2"]},
            }
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.calls_participants(id="R1")
    assert [p["slack_id"] for p in resp["participants"]] == ["U1", "U2"]
    hits = client.calls_participants_search(id="R1", query="U2")["participants"]
    assert [p["slack_id"] for p in hits] == ["U2"]
    empty = client.calls_participants_search(id="R1", query="")
    assert empty["ok"] is False


def test_search_has_named_reaction(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    hits = client.search_messages(query="has::thumbsup:")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["hello @bob"]


def test_search_files_has_image(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "files.json",
        [
            {"id": "Fimg", "name": "a.png", "filetype": "png", "mimetype": "image/png"},
            {"id": "Ftxt", "name": "a.txt", "filetype": "text", "mimetype": "text/plain"},
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_files(query="has:image")["files"]["matches"]
    assert {f["id"] for f in hits} == {"Fimg"}


def test_users_list_include_deleted(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U1": {"id": "U1", "handle": "alice", "deleted": False},
            "U2": {"id": "U2", "handle": "gone", "deleted": True},
        },
    )
    client = DumpClient(tmp_path)
    all_u = {u["id"] for u in client.users_list()["members"]}
    assert {"U1", "U2"} <= all_u
    active = {u["id"] for u in client.users_list(include_deleted=False)["members"]}
    assert "U1" in active
    assert "U2" not in active


def test_bots_info_from_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "bots.json",
        {"B99": {"id": "B99", "name": "deploybot", "app_id": "A1", "deleted": False}},
    )
    client = DumpClient(tmp_path)
    resp = client.bots_info(bot="B99")
    assert resp["bot"]["name"] == "deploybot"
    assert resp["bot"]["app_id"] == "A1"


def test_pins_list_page(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ch / "pins.json",
        [
            {"type": "message", "channel": "C123", "message": {"ts": "1.0"}},
            {"type": "message", "channel": "C123", "message": {"ts": "2.0"}},
        ],
    )
    client = DumpClient(tmp_path)
    page2 = client.pins_list(channel="C123", count=1, page=2)
    assert page2["items"][0]["message"]["ts"] == "2.0"


def test_bookmarks_list_page(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ch / "bookmarks.json",
        [{"id": "Bk1", "title": "a"}, {"id": "Bk2", "title": "b"}],
    )
    client = DumpClient(tmp_path)
    page2 = client.bookmarks_list(channel="C123", count=1, page=2)
    assert page2["bookmarks"][0]["id"] == "Bk2"


def test_integration_logs_user(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "integration_logs.json",
        [
            {"user_id": "U1", "service_id": "S1"},
            {"user_id": "U2", "service_id": "S2"},
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.team_integrationLogs(user="U1")
    assert [row["service_id"] for row in resp["logs"]] == ["S1"]
    hits = client.team_integrationLogs_search(query="S1")["logs"]
    assert [row["user_id"] for row in hits] == ["U1"]
    empty = client.team_integrationLogs_search(query="")
    assert empty["ok"] is False


def test_bots_list_from_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "bots.json",
        {
            "B1": {
                "id": "B1",
                "name": "deploybot",
                "app_id": "A1",
                "icons": {"image_48": "https://e.test/b.png"},
                "team_id": "T1",
                "updated": 9,
                "is_workflow_bot": True,
            },
            "B2": {"id": "B2", "name": "alertbot", "app_id": "A2"},
        },
    )
    client = DumpClient(tmp_path)
    resp = client.bots_list()
    assert {b["id"] for b in resp["bots"]} == {"B1", "B2"}
    deploy = next(b for b in resp["bots"] if b["id"] == "B1")
    assert deploy["icons"]["image_48"].endswith("/b.png")
    assert deploy["team_id"] == "T1"
    assert deploy["updated"] == 9
    assert deploy["is_workflow_bot"] is True
    hits = client.bots_search(query="deploy")["bots"]
    assert [b["id"] for b in hits] == ["B1"]
    empty = client.bots_search(query="")
    assert empty["ok"] is False


def test_users_list_include_bots(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U1": {"id": "U1", "handle": "alice", "is_bot": False},
            "UB": {"id": "UB", "handle": "deploybot", "is_bot": True},
        },
    )
    client = DumpClient(tmp_path)
    all_u = {u["id"] for u in client.users_list()["members"]}
    assert {"U1", "UB"} <= all_u
    humans = {u["id"] for u in client.users_list(include_bots=False)["members"]}
    assert "U1" in humans
    assert "UB" not in humans


def test_access_logs_user(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "access_logs.json",
        [
            {"user_id": "U1", "ip": "1.1.1.1"},
            {"user_id": "U2", "ip": "2.2.2.2"},
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.team_accessLogs(user="U1")
    assert [row["ip"] for row in resp["logins"]] == ["1.1.1.1"]
    hits = client.team_accessLogs_search(query="1.1.1.1")["logins"]
    assert [row["user_id"] for row in hits] == ["U1"]
    empty = client.team_accessLogs_search(query="")
    assert empty["ok"] is False


def test_search_has_snippet(tmp_path: Path) -> None:
    clip = _msg("1.0", "snippet")
    clip["files"] = [{"id": "Fs", "name": "a.py", "mode": "snippet", "filetype": "python"}]
    pic = _msg("2.0", "pic")
    pic["files"] = [{"id": "Fi", "name": "a.png", "filetype": "png"}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [clip, pic])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="has:snippet")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["snippet"]


def test_reactions_list_page(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    page2 = client.reactions_list(count=1, page=2)
    assert len(page2["items"]) == 1


def test_reactions_list_prefers_reactions_json(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [_msg("9.0", "unrelated")])
    _write(
        ch / "reactions.json",
        [
            {
                "type": "message",
                "channel": "C123",
                "reaction": "wave",
                "user": "U2",
                "message": {"ts": "1.0", "text": "hi", "user": "U1"},
            }
        ],
    )
    items = DumpClient(tmp_path).reactions_list()["items"]
    assert [(i["reaction"], i["user"], i["message"]["text"]) for i in items] == [
        ("wave", "U2", "hi")
    ]
    filtered = DumpClient(tmp_path).reactions_list(user="U9")["items"]
    assert filtered == []
    hits = DumpClient(tmp_path).reactions_search(query="wave")["items"]
    assert [i["reaction"] for i in hits] == ["wave"]
    empty = DumpClient(tmp_path).reactions_search(query="")
    assert empty["ok"] is False


def test_reminders_list_include_complete(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "reminders.json",
        [
            {"id": "Rm1", "text": "open", "complete_ts": 0},
            {"id": "Rm2", "text": "done", "complete": True, "complete_ts": 9},
        ],
    )
    client = DumpClient(tmp_path)
    open_only = client.reminders_list(include_complete=False)
    assert [r["id"] for r in open_only["reminders"]] == ["Rm1"]


def test_search_is_edited(tmp_path: Path) -> None:
    plain = _msg("1.0", "plain")
    edited = _msg("2.0", "fixed")
    edited["edited"] = {"user": "U1", "ts": "2.1"}
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [plain, edited])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:edited")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["fixed"]


def test_search_has_mention(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    hits = client.search_messages(query="has:mention")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["hello @bob"]


def test_users_list_directory_only(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    ids = {u["id"] for u in client.users_list(include_message_users=False)["members"]}
    assert ids == {"U1", "U2"}


def test_access_logs_after(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "access_logs.json",
        [
            {"user_id": "U1", "ip": "1.1.1.1", "date_last": 100},
            {"user_id": "U2", "ip": "2.2.2.2", "date_last": 200},
        ],
    )
    client = DumpClient(tmp_path)
    after = client.team_accessLogs(after=150)
    assert [row["ip"] for row in after["logins"]] == ["2.2.2.2"]
    before = client.team_accessLogs(before=150)
    assert [row["ip"] for row in before["logins"]] == ["1.1.1.1"]


def test_integration_logs_change_type(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "integration_logs.json",
        [
            {"user_id": "U1", "service_id": "S1", "change_type": "added"},
            {"user_id": "U2", "service_id": "S2", "change_type": "removed"},
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.team_integrationLogs(change_type="added")
    assert [row["service_id"] for row in resp["logs"]] == ["S1"]


def test_bots_list_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "bots.json",
        {
            "B1": {"id": "B1", "name": "alertbot"},
            "B2": {"id": "B2", "name": "deploybot"},
        },
    )
    client = DumpClient(tmp_path)
    page2 = client.bots_list(count=1, page=2)
    assert len(page2["bots"]) == 1
    assert page2["bots"][0]["id"] == "B2"


def test_iter_pins_bookmarks_bots(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write((ws / "general_C123") / "pins.json", [{"type": "message", "channel": "C123"}])
    _write((ws / "general_C123") / "bookmarks.json", [{"id": "Bk1", "title": "docs"}])
    _write(ws / "bots.json", {"B1": {"id": "B1", "name": "deploybot"}})
    client = DumpClient(tmp_path)
    assert [p["channel"] for p in client.iter_pins()] == ["C123"]
    assert [b["id"] for b in client.iter_bookmarks()] == ["Bk1"]
    assert [b["id"] for b in client.iter_bots()] == ["B1"]


def test_search_is_unthreaded(dump_root: Path) -> None:
    client = DumpClient(dump_root)
    hits = client.search_messages(query="is:unthreaded")["messages"]["matches"]
    texts = {m["text"] for m in hits}
    assert "hello @bob" in texts
    assert "ops ping" in texts
    assert "thread root" not in texts
    assert "a reply" not in texts
    assert "standalone reply" not in texts


def test_search_during_lastyear(tmp_path: Path, monkeypatch) -> None:
    now = _freeze_search_now(monkeypatch)
    this_year = now.replace(month=6, day=15, hour=12, minute=0, second=0, microsecond=0)
    last_year = this_year.replace(year=this_year.year - 1)
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [
            _msg(str(this_year.timestamp()), "this year ping"),
            _msg(str(last_year.timestamp()), "last year ping"),
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="during:lastyear ping")["messages"]["matches"]
    assert {m["text"] for m in hits} == {"last year ping"}


def test_iter_stars_and_reminders(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "stars.json",
        [{"type": "message", "channel": "C123", "message": {"ts": "1.0", "text": "starred"}}],
    )
    _write(ws / "reminders.json", [{"id": "Rm1", "text": "nudge"}])
    client = DumpClient(tmp_path)
    assert [s["message"]["text"] for s in client.iter_stars()] == ["starred"]
    assert [r["id"] for r in client.iter_reminders()] == ["Rm1"]


def test_search_is_broadcast(tmp_path: Path) -> None:
    plain = _msg("1.0", "plain")
    shout = _msg("2.0", "shout")
    shout["subtype"] = "thread_broadcast"
    also = _msg("3.0", "also")
    also["reply_broadcast"] = True
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [plain, shout, also])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:broadcast")["messages"]["matches"]
    assert {m["text"] for m in hits} == {"shout", "also"}


def test_search_has_block(tmp_path: Path) -> None:
    plain = _msg("1.0", "plain")
    rich = _msg("2.0", "rich")
    rich["blocks"] = [{"type": "section"}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [plain, rich])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="has:block")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["rich"]


def test_integration_logs_app_id(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "integration_logs.json",
        [
            {"user_id": "U1", "service_id": "S1", "app_id": "A1"},
            {"user_id": "U2", "service_id": "S2", "app_id": "A2"},
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.team_integrationLogs(app_id="A1")
    assert [row["service_id"] for row in resp["logs"]] == ["S1"]


def test_scheduled_messages_oldest(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "scheduled_messages.json",
        [
            {"id": "Q1", "channel_id": "C123", "post_at": 10},
            {"id": "Q2", "channel_id": "C123", "post_at": 50},
        ],
    )
    client = DumpClient(tmp_path)
    late = client.chat_scheduledMessages_list(oldest="20")
    assert [m["id"] for m in late["scheduled_messages"]] == ["Q2"]
    early = client.chat_scheduledMessages_list(latest="20")
    assert [m["id"] for m in early["scheduled_messages"]] == ["Q1"]


def test_iter_usergroups(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "usergroups.json", [{"id": "S1", "handle": "eng"}])
    client = DumpClient(tmp_path)
    assert [g["id"] for g in client.iter_usergroups()] == ["S1"]


@pytest.mark.parametrize(
    ("key", "value", "query", "hit_text"),
    [
        ("is_locked", True, "is:locked", "locked"),
        ("subtype", "tombstone", "is:tombstone", "deleted"),
        ("app_id", "A1", "is:app", "app msg"),
        ("subtype", "file_share", "is:file_share", "file share"),
        ("hidden", True, "is:hidden", "hidden"),
        ("x_files", ["Fgone"], "has:x_files", "x files"),
        ("subtype", "me_message", "is:me_message", "me msg"),
        ("metadata", {"event_type": "task_created"}, "has:metadata", "meta"),
    ],
    ids=["locked", "tombstone", "app", "file_share", "hidden", "x_files", "me_message", "metadata"],
)
def test_search_simple_message_flag(tmp_path: Path, key: str, value: Any, query: str, hit_text: str) -> None:
    special = _msg("1.0", hit_text)
    special[key] = value
    plain = _msg("2.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [special, plain])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query=query)["messages"]["matches"]
    assert [m["text"] for m in hits] == [hit_text]


def test_iter_scheduled(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "scheduled_messages.json", [{"id": "Q1", "channel_id": "C123", "post_at": 9}])
    client = DumpClient(tmp_path)
    assert [m["id"] for m in client.iter_scheduled()] == ["Q1"]


def test_search_has_call(tmp_path: Path) -> None:
    huddle = _msg("1.0", "huddle")
    huddle["room"] = {"id": "R1", "name": "standup"}
    plain = _msg("2.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [huddle, plain])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="has:call")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["huddle"]


def test_calls_list_and_iter(tmp_path: Path) -> None:
    huddle = _msg("1.0", "huddle")
    huddle["room"] = {"id": "R1", "name": "standup"}
    also = _msg("2.0", "also")
    also["call"] = {"id": "R2", "name": "retro"}
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [huddle, also])
    client = DumpClient(tmp_path)
    resp = client.calls_list()
    assert {c["id"] for c in resp["calls"]} == {"R1", "R2"}
    assert {c["id"] for c in client.iter_calls()} == {"R1", "R2"}


def test_search_in_me(tmp_path: Path) -> None:
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [_msg("1.0", "public")])
    dm = tmp_path / "acme" / "alice_D111"
    _write(dm / "messages.json", [_msg("2.0", "dm hi")])
    _write(dm / "members.json", ["U1", "U2"])
    _write(tmp_path / "acme" / "auth.json", {"user_id": "U1", "user": "alice"})
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="in:me")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["dm hi"]


def test_search_with_me(tmp_path: Path) -> None:
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [_msg("1.0", "public")])
    dm = tmp_path / "acme" / "alice_D111"
    _write(dm / "messages.json", [_msg("2.0", "dm hi")])
    _write(dm / "members.json", ["U1", "U2"])
    other = tmp_path / "acme" / "bob_D222"
    _write(other / "messages.json", [_msg("3.0", "other dm")])
    _write(other / "members.json", ["U2", "U3"])
    _write(tmp_path / "acme" / "auth.json", {"user_id": "U1", "user": "alice"})
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="with:me")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["dm hi"]


def test_search_is_me(tmp_path: Path) -> None:
    mine = _msg("1.0", "mine")
    theirs = _msg("2.0", "theirs")
    theirs["user"] = "U2"
    theirs["user_name"] = "bob"
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [mine, theirs])
    _write(tmp_path / "acme" / "auth.json", {"user_id": "U1", "user": "alice"})
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:me")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["mine"]


def test_search_has_replies(tmp_path: Path) -> None:
    root = _msg("1.0", "root")
    root["thread"] = [
        {"ts": "1.1", "user": "U2", "user_name": "b", "text": "r", "reactions": [], "files": []}
    ]
    lone = _msg("2.0", "lone")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [root, lone])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="has:replies")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["root"]


def test_iter_reactions(tmp_path: Path) -> None:
    msg = _msg("1.0", "hi")
    msg["reactions"] = [{"name": "thumbsup", "count": 1, "users": ["U2"]}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [msg])
    client = DumpClient(tmp_path)
    assert [r["reaction"] for r in client.iter_reactions()] == ["thumbsup"]


def test_openid_connect_userinfo(tmp_path: Path) -> None:
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [])
    _write(tmp_path / "acme" / "auth.json", {"user": "alice", "user_id": "U1", "team": "acme"})
    client = DumpClient(tmp_path)
    resp = client.api_call("openid.connect.userInfo")
    assert resp["ok"] is True
    assert resp["user"]["id"] == "U1"


def test_search_is_join_and_leave(tmp_path: Path) -> None:
    joined = _msg("1.0", "joined")
    joined["subtype"] = "channel_join"
    left = _msg("2.0", "left")
    left["subtype"] = "channel_leave"
    plain = _msg("3.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [joined, left, plain])
    client = DumpClient(tmp_path)
    assert [m["text"] for m in client.search_messages(query="is:join")["messages"]["matches"]] == [
        "joined"
    ]
    assert [m["text"] for m in client.search_messages(query="is:leave")["messages"]["matches"]] == [
        "left"
    ]


def test_search_has_remote(tmp_path: Path) -> None:
    remote = _msg("1.0", "remote")
    remote["files"] = [{"id": "Fr", "name": "drive.doc", "is_external": True, "mode": "remote"}]
    local = _msg("2.0", "local")
    local["files"] = [{"id": "Fl", "name": "a.txt", "filetype": "text"}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [remote, local])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="has:remote")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["remote"]
    files = client.search_files(query="has:remote")["files"]["matches"]
    assert [f["id"] for f in files] == ["Fr"]


def test_stars_list_channel(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write((ws / "other_C999") / "messages.json", [])
    _write(
        ws / "stars.json",
        [
            {"type": "message", "channel": "C123", "message": {"ts": "1.0"}},
            {"type": "message", "channel": "C999", "message": {"ts": "2.0"}},
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.stars_list(channel="C123")
    assert [i["channel"] for i in resp["items"]] == ["C123"]


def test_iter_emoji(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "emoji.json", {"shipit": "https://emoji.test/shipit.png"})
    client = DumpClient(tmp_path)
    assert list(client.iter_emoji()) == [{"name": "shipit", "url": "https://emoji.test/shipit.png"}]


def test_search_is_topic_and_purpose(tmp_path: Path) -> None:
    topic = _msg("1.0", "set the topic")
    topic["subtype"] = "channel_topic"
    purpose = _msg("2.0", "set the purpose")
    purpose["subtype"] = "channel_purpose"
    plain = _msg("3.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [topic, purpose, plain])
    client = DumpClient(tmp_path)
    assert [m["text"] for m in client.search_messages(query="is:topic")["messages"]["matches"]] == [
        "set the topic"
    ]
    hits = client.search_messages(query="is:purpose")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["set the purpose"]


def test_search_is_parent(tmp_path: Path) -> None:
    root = _msg("1.0", "root")
    root["thread"] = [
        {"ts": "1.1", "user": "U2", "user_name": "b", "text": "r", "reactions": [], "files": []}
    ]
    lone = _msg("2.0", "lone")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [root, lone])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:parent")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["root"]


def test_reminders_list_user(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "reminders.json",
        [
            {"id": "Rm1", "text": "mine", "user": "U1"},
            {"id": "Rm2", "text": "theirs", "user": "U2"},
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.reminders_list(user="U1")
    assert [r["id"] for r in resp["reminders"]] == ["Rm1"]


def test_iter_access_logs(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "access_logs.json", [{"user_id": "U1", "ip": "1.2.3.4"}])
    client = DumpClient(tmp_path)
    assert [row["ip"] for row in client.iter_access_logs()] == ["1.2.3.4"]


def test_search_is_archive_and_rename(tmp_path: Path) -> None:
    archived = _msg("1.0", "archived")
    archived["subtype"] = "channel_archive"
    restored = _msg("2.0", "unarchived")
    restored["subtype"] = "channel_unarchive"
    renamed = _msg("3.0", "renamed")
    renamed["subtype"] = "channel_name"
    plain = _msg("4.0", "plain")
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [archived, restored, renamed, plain],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:archive")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["archived"]
    hits = client.search_messages(query="is:unarchive")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["unarchived"]
    hits = client.search_messages(query="is:rename")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["renamed"]


def test_search_is_subscribed(tmp_path: Path) -> None:
    watched = _msg("1.0", "watched")
    watched["subscribed"] = True
    plain = _msg("2.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [watched, plain])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:subscribed")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["watched"]


def test_iter_integration_logs(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "integration_logs.json", [{"user_id": "U1", "service_id": "S1"}])
    client = DumpClient(tmp_path)
    assert [row["service_id"] for row in client.iter_integration_logs()] == ["S1"]


def test_iter_stars_channel(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "stars.json",
        [
            {"type": "message", "channel": "C123", "message": {"ts": "1.0"}},
            {"type": "message", "channel": "C999", "message": {"ts": "2.0"}},
        ],
    )
    client = DumpClient(tmp_path)
    assert [s["channel"] for s in client.iter_stars(channel="C123")] == ["C123"]


def test_search_is_pinned(tmp_path: Path) -> None:
    pinned = _msg("1.0", "pinned")
    pinned["pinned_to"] = ["C123"]
    plain = _msg("2.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [pinned, plain])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:pinned")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["pinned"]


def test_search_is_workflow(tmp_path: Path) -> None:
    flow = _msg("1.0", "flow")
    flow["bot_profile"] = {"id": "B1", "name": "Flow", "is_workflow_bot": True}
    bot = _msg("2.0", "bot")
    bot["bot_id"] = "B9"
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [flow, bot])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:workflow")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["flow"]
    hits = client.search_messages(query="has:workflow")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["flow"]


def test_search_is_shared(tmp_path: Path) -> None:
    _write(
        tmp_path / "acme" / "conversations.json",
        [
            {"id": "C123", "name": "shared", "is_channel": True, "is_ext_shared": True},
            {"id": "C999", "name": "local", "is_channel": True, "is_ext_shared": False},
        ],
    )
    _write((tmp_path / "acme" / "shared_C123") / "messages.json", [_msg("1.0", "hello")])
    _write((tmp_path / "acme" / "local_C999") / "messages.json", [_msg("2.0", "hello")])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:shared hello")["messages"]["matches"]
    assert [m["channel"]["id"] for m in hits] == ["C123"]
    hits = client.search_messages(query="is:ext_shared hello")["messages"]["matches"]
    assert [m["channel"]["id"] for m in hits] == ["C123"]


def test_team_preferences_list(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "team_preferences.json", {"msg_edit_window_mins": "0"})
    client = DumpClient(tmp_path)
    resp = client.team_preferences_list()
    assert resp["ok"] is True
    assert resp["prefs"]["msg_edit_window_mins"] == "0"
    via = client.api_call("team.preferences.list")
    assert via["prefs"]["msg_edit_window_mins"] == "0"
    hits = client.team_preferences_search(query="msg_edit")["prefs"]
    assert list(hits) == ["msg_edit_window_mins"]
    empty = client.team_preferences_search(query="")
    assert empty["ok"] is False


def test_iter_members(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(ch / "members.json", ["U1", "U2"])
    client = DumpClient(tmp_path)
    assert list(client.iter_members(channel="C123")) == ["U1", "U2"]


def test_search_is_call(tmp_path: Path) -> None:
    huddle = _msg("1.0", "huddle")
    huddle["room"] = {"id": "R1", "name": "standup"}
    plain = _msg("2.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [huddle, plain])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:call")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["huddle"]
    hits = client.search_messages(query="is:huddle")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["huddle"]


def test_rtm_connect(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "auth.json",
        {
            "url": "https://acme.slack.com/",
            "team": "Acme",
            "team_id": "T99",
            "user": "alice",
            "user_id": "U1",
        },
    )
    client = DumpClient(tmp_path)
    resp = client.rtm_connect()
    assert resp["ok"] is True
    assert resp["self"]["id"] == "U1"
    assert resp["self"]["name"] == "alice"
    assert resp["team"]["id"] == "T99"
    via = client.api_call("rtm.connect")
    assert via["team"]["id"] == "T99"


def test_iter_billable(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "billable_info.json", {"U1": {"billing_active": True}})
    client = DumpClient(tmp_path)
    rows = list(client.iter_billable())
    assert rows == [{"user_id": "U1", "billing_active": True}]


def test_conversations_info_num_members_from_roster(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(ch / "members.json", ["U1", "U2", "U3"])
    client = DumpClient(tmp_path)
    info = client.conversations_info(channel="C123")["channel"]
    assert info["num_members"] == 3


@pytest.mark.parametrize(
    ("channel_a", "channel_b", "query", "want_id"),
    [
        ("general_C123", "random_C999", "is:general hello", "C123"),
        ("random_C123", "general_C999", "is:random hello", "C123"),
    ],
)
def test_search_is_named_channel(
    tmp_path: Path, channel_a: str, channel_b: str, query: str, want_id: str
) -> None:
    _write((tmp_path / "acme" / channel_a) / "messages.json", [_msg("1.0", "hello")])
    _write((tmp_path / "acme" / channel_b) / "messages.json", [_msg("2.0", "hello")])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query=query)["messages"]["matches"]
    assert [m["channel"]["id"] for m in hits] == [want_id]


def test_search_has_button(tmp_path: Path) -> None:
    btn = _msg("1.0", "btn")
    btn["blocks"] = [{"type": "actions", "elements": [{"type": "button", "text": {"text": "Go"}}]}]
    plain = _msg("2.0", "plain")
    plain["blocks"] = [{"type": "section", "text": {"text": "hi"}}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [btn, plain])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="has:button")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["btn"]


def test_usergroups_users_list_alias(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "usergroups.json",
        [{"id": "S1", "handle": "eng", "users": ["U1"]}],
    )
    client = DumpClient(tmp_path)
    resp = client.api_call("usergroups.users.list", params={"usergroup": "S1"})
    assert resp["users"] == ["U1"]


def test_rtm_start_alias(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "auth.json", {"user_id": "U1", "user": "alice", "team_id": "T99"})
    client = DumpClient(tmp_path)
    resp = client.api_call("rtm.start")
    assert resp["self"]["id"] == "U1"


def test_team_external_teams_list(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "external_teams.json", [{"id": "E1", "name": "Partner"}])
    client = DumpClient(tmp_path)
    resp = client.team_externalTeams_list()
    assert resp["ok"] is True
    assert resp["teams"][0]["id"] == "E1"
    via = client.api_call("team.externalTeams.list")
    assert via["teams"][0]["name"] == "Partner"


def test_search_is_ephemeral(tmp_path: Path) -> None:
    eph = _msg("1.0", "eph")
    eph["is_ephemeral"] = True
    plain = _msg("2.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [eph, plain])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:ephemeral")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["eph"]


def test_users_info_presence_from_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U8": {
                "id": "U8",
                "handle": "erin",
                "display_name": "erin",
                "real_name": "Erin",
                "title": "",
                "email": "erin@acme.test",
                "phone": "",
                "status_text": "",
                "status_emoji": "",
                "timezone": "",
                "timezone_label": "",
                "is_bot": False,
                "image": "",
            }
        },
    )
    _write(ws / "presence.json", {"U8": {"presence": "active", "online": True}})
    client = DumpClient(tmp_path)
    resp = client.users_info(user="U8")
    assert resp["user"]["presence"] == "active"
    by_handle = client.users_info(user="@erin")
    assert by_handle["ok"] is True
    assert by_handle["user"]["id"] == "U8"


def test_users_search(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U1": {
                "id": "U1",
                "handle": "alice",
                "display_name": "alice",
                "real_name": "Alice Smith",
                "email": "alice@acme.test",
            },
            "U2": {
                "id": "U2",
                "handle": "bob",
                "display_name": "bob",
                "real_name": "Bob Jones",
                "email": "bob@acme.test",
            },
        },
    )
    client = DumpClient(tmp_path)
    hits = client.users_search(query="alice")["members"]
    assert [u["id"] for u in hits] == ["U1"]
    page = client.users_search(query="acme.test", count=1, page=2)
    assert [u["id"] for u in page["members"]] == ["U2"]
    empty = client.users_search(query="")
    assert empty["ok"] is False


def test_conversations_search(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write((ws / "random_C999") / "messages.json", [])
    client = DumpClient(tmp_path)
    hits = client.conversations_search(query="rand")["channels"]
    assert [c["id"] for c in hits] == ["C999"]
    page = client.conversations_search(query="C", count=1, page=2)
    assert len(page["channels"]) == 1
    empty = client.conversations_search(query="")
    assert empty["ok"] is False


def test_iter_external_teams(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "external_teams.json", [{"id": "E1", "name": "Partner"}])
    client = DumpClient(tmp_path)
    assert [row["id"] for row in client.iter_external_teams()] == ["E1"]


def test_search_is_creator(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "channel.json", {"id": "C123", "name": "general", "creator": "U1"})
    other = _msg("2.0", "other")
    other["user"] = "U2"
    _write(ch / "messages.json", [_msg("1.0", "owner"), other])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:creator")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["owner"]


def test_users_list_presence_from_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U8": {
                "id": "U8",
                "handle": "erin",
                "display_name": "erin",
                "real_name": "Erin",
                "title": "",
                "email": "erin@acme.test",
                "phone": "",
                "status_text": "",
                "status_emoji": "",
                "timezone": "",
                "timezone_label": "",
                "is_bot": False,
                "image": "",
            }
        },
    )
    _write(ws / "presence.json", {"U8": {"presence": "away"}})
    client = DumpClient(tmp_path)
    members = client.users_list(include_message_users=False)["members"]
    assert members[0]["id"] == "U8"
    assert members[0]["presence"] == "away"


def test_iter_presence(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "presence.json", {"U8": {"presence": "active", "online": True}})
    client = DumpClient(tmp_path)
    rows = list(client.iter_presence())
    assert rows == [{"user_id": "U8", "presence": "active", "online": True}]


def test_search_is_delayed(tmp_path: Path) -> None:
    late = _msg("1.0", "later")
    late["is_delayed_message"] = True
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [late, _msg("2.0", "now")],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:delayed")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["later"]


def test_search_is_scheduled(tmp_path: Path) -> None:
    queued = _msg("1.0", "queued")
    queued["scheduled_message_id"] = "Q1"
    _write(
        (tmp_path / "acme" / "general_C123") / "messages.json",
        [queued, _msg("2.0", "live")],
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:scheduled")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["queued"]


def test_usergroups_list_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "usergroups.json",
        [
            {"id": "S1", "handle": "eng", "users": ["U1"]},
            {"id": "S2", "handle": "ops", "users": ["U2"]},
        ],
    )
    client = DumpClient(tmp_path)
    resp = client.usergroups_list(count=1, page=2)
    assert [g["id"] for g in resp["usergroups"]] == ["S2"]


def test_search_is_guest(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    guest = _msg("1.0", "from guest")
    guest["user"] = "U9"
    _write((ws / "general_C123") / "messages.json", [guest, _msg("2.0", "from member")])
    _write(
        ws / "users.json",
        {
            "U1": {"id": "U1", "handle": "alice", "is_restricted": False},
            "U9": {"id": "U9", "handle": "guest", "is_restricted": True},
        },
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:guest")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["from guest"]


def test_users_list_restricted_flag(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {"U9": {"id": "U9", "handle": "guest", "is_restricted": True, "is_admin": False}},
    )
    client = DumpClient(tmp_path)
    members = client.users_list(include_message_users=False)["members"]
    assert members[0]["id"] == "U9"
    assert members[0]["is_restricted"] is True


def test_search_is_general_flag(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "lounge_C123"
    _write(ch / "channel.json", {"id": "C123", "name": "lounge", "is_general": True})
    _write(ch / "messages.json", [_msg("1.0", "hello")])
    _write((tmp_path / "acme" / "random_C999") / "messages.json", [_msg("2.0", "hello")])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:general hello")["messages"]["matches"]
    assert [m["channel"]["id"] for m in hits] == ["C123"]


def test_emoji_get(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "emoji.json", {"shipit": "https://e.test/s.png"})
    client = DumpClient(tmp_path)
    resp = client.emoji_get(name="shipit")
    assert resp["ok"] is True
    assert resp["emoji"]["shipit"] == "https://e.test/s.png"
    missing = client.emoji_get(name="nope")
    assert missing["ok"] is False


@pytest.mark.parametrize(
    ("flag", "query", "hit_text"),
    [
        ("is_admin", "is:admin", "from admin"),
        ("is_owner", "is:owner", "from owner"),
        ("is_app_user", "is:app_user", "from app user"),
        ("is_connector", "is:connector", "from connector"),
        ("is_workflow_bot", "is:workflow_bot", "from workflow bot"),
    ],
)
def test_search_is_role_flag(tmp_path: Path, flag: str, query: str, hit_text: str) -> None:
    ws = tmp_path / "acme"
    hit = _msg("1.0", hit_text)
    hit["user"] = "U9"
    _write((ws / "general_C123") / "messages.json", [hit, _msg("2.0", "from member")])
    _write(
        ws / "users.json",
        {
            "U1": {"id": "U1", "handle": "alice", flag: False},
            "U9": {"id": "U9", "handle": "boss", flag: True},
        },
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query=query)["messages"]["matches"]
    assert [m["text"] for m in hits] == [hit_text]


def test_users_list_locale_color(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U1": {
                "id": "U1",
                "handle": "alice",
                "locale": "en-US",
                "color": "9f69e7",
                "tz_offset": 3600,
                "updated": 99,
            }
        },
    )
    client = DumpClient(tmp_path)
    member = client.users_list(include_message_users=False)["members"][0]
    assert member["locale"] == "en-US"
    assert member["color"] == "9f69e7"
    assert member["tz_offset"] == 3600
    assert member["updated"] == 99


def test_conversations_info_locale(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "lounge_C123"
    _write(
        ch / "channel.json",
        {
            "id": "C123",
            "name": "lounge",
            "locale": "en-US",
            "updated": 50,
            "previous_names": ["old"],
            "is_member": True,
        },
    )
    _write(ch / "messages.json", [])
    client = DumpClient(tmp_path)
    info = client.conversations_info(channel="C123")["channel"]
    assert info["locale"] == "en-US"
    assert info["updated"] == 50
    assert info["previous_names"] == ["old"]
    assert info["is_member"] is True


def test_team_external_teams_page(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "external_teams.json",
        [{"id": "E1", "name": "A"}, {"id": "E2", "name": "B"}],
    )
    client = DumpClient(tmp_path)
    resp = client.team_externalTeams_list(count=1, page=2)
    assert [t["id"] for t in resp["teams"]] == ["E2"]
    hits = client.team_externalTeams_search(query="A")["teams"]
    assert [t["id"] for t in hits] == ["E1"]
    empty = client.team_externalTeams_search(query="")
    assert empty["ok"] is False


def test_auth_teams_list_from_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "auth.json", {"user_id": "U1", "team": "acme", "team_id": "T1"})
    _write(
        ws / "teams.json",
        [{"id": "T1", "name": "Acme"}, {"id": "T2", "name": "Other"}],
    )
    client = DumpClient(tmp_path)
    resp = client.auth_teams_list()
    assert [t["id"] for t in resp["teams"]] == ["T1", "T2"]
    page = client.auth_teams_list(count=1, page=2)
    assert [t["id"] for t in page["teams"]] == ["T2"]
    hits = client.auth_teams_search(query="acme")["teams"]
    assert [t["id"] for t in hits] == ["T1"]
    empty = client.auth_teams_search(query="")
    assert empty["ok"] is False


def test_users_list_stranger_flag(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {"U9": {"id": "U9", "handle": "ext", "is_stranger": True, "is_invited_user": True}},
    )
    client = DumpClient(tmp_path)
    member = client.users_list(include_message_users=False)["members"][0]
    assert member["is_stranger"] is True
    assert member["is_invited_user"] is True
    assert member["profile"]["first_name"] == ""
    profile = client.users_profile_get(user="U9")["profile"]
    assert "first_name" in profile


def test_users_profile_keeps_names(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U1": {
                "id": "U1",
                "handle": "alice",
                "first_name": "Alice",
                "last_name": "Smith",
                "skype": "alice.s",
                "status_expiration": 99,
                "avatar_hash": "abc",
            }
        },
    )
    client = DumpClient(tmp_path)
    profile = client.users_profile_get(user="U1")["profile"]
    assert profile["first_name"] == "Alice"
    assert profile["last_name"] == "Smith"
    assert profile["skype"] == "alice.s"
    assert profile["status_expiration"] == 99
    assert profile["avatar_hash"] == "abc"


def test_files_remote_list_from_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "remote_files.json",
        [{"id": "Fr1", "name": "drive.doc", "is_external": True, "external_id": "g1"}],
    )
    client = DumpClient(tmp_path)
    resp = client.files_remote_list()
    assert [f["id"] for f in resp["files"]] == ["Fr1"]
    info = client.files_remote_info(file="Fr1")
    assert info["file"]["external_id"] == "g1"
    by_ext = client.files_remote_info(external_id="g1")
    assert by_ext["file"]["id"] == "Fr1"
    hits = client.files_remote_search(query="drive")["files"]
    assert [f["id"] for f in hits] == ["Fr1"]
    empty = client.files_remote_search(query="")
    assert empty["ok"] is False


@pytest.mark.parametrize(
    ("flag_key", "query", "expected_text"),
    [
        ("is_stranger", "is:stranger", "from stranger"),
        ("is_invited_user", "is:invited", "from invited"),
        ("is_primary_owner", "is:primary_owner", "from owner"),
        ("is_ultra_restricted", "is:ultra_restricted", "from guest"),
    ],
)
def test_search_is_user_flag(tmp_path: Path, flag_key: str, query: str, expected_text: str) -> None:
    ws = tmp_path / "acme"
    msg = _msg("1.0", expected_text)
    msg["user"] = "U9"
    _write((ws / "general_C123") / "messages.json", [msg, _msg("2.0", "from member")])
    _write(
        ws / "users.json",
        {
            "U1": {"id": "U1", "handle": "alice"},
            "U9": {"id": "U9", "handle": "u9", flag_key: True},
        },
    )
    hits = DumpClient(tmp_path).search_messages(query=query)["messages"]["matches"]
    assert [m["text"] for m in hits] == [expected_text]


def test_iter_teams(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "teams.json", [{"id": "T1", "name": "Acme"}, {"id": "T2", "name": "Other"}])
    client = DumpClient(tmp_path)
    assert [t["id"] for t in client.iter_teams()] == ["T1", "T2"]


def test_emoji_list_categories(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "emoji.json", {"shipit": "https://e.test/s.png"})
    _write(ws / "emoji_categories.json", [{"name": "Custom", "emoji_names": ["shipit"]}])
    client = DumpClient(tmp_path)
    resp = client.emoji_list()
    assert resp["emoji"]["shipit"].startswith("https://")
    assert resp["categories"][0]["name"] == "Custom"
    hits = client.emoji_categories_search(query="Custom")["categories"]
    assert [c["name"] for c in hits] == ["Custom"]
    empty = client.emoji_categories_search(query="")
    assert empty["ok"] is False


def test_search_has_html_and_svg(tmp_path: Path) -> None:
    html = _msg("1.0", "page")
    html["files"] = [{"id": "Fh", "name": "index.html", "filetype": "html"}]
    svg = _msg("2.0", "icon")
    svg["files"] = [{"id": "Fs", "name": "logo.svg", "filetype": "svg"}]
    pic = _msg("3.0", "pic")
    pic["files"] = [{"id": "Fp", "name": "a.png", "filetype": "png"}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [html, svg, pic])
    client = DumpClient(tmp_path)
    pages = client.search_messages(query="has:html")["messages"]["matches"]
    assert [m["text"] for m in pages] == ["page"]
    icons = client.search_files(query="has:svg")["files"]["matches"]
    assert [f["id"] for f in icons] == ["Fs"]


def test_search_is_canvas(tmp_path: Path) -> None:
    share = _msg("1.0", "shared canvas")
    share["subtype"] = "canvas_share"
    file_msg = _msg("2.0", "canvas file")
    file_msg["files"] = [{"id": "Fc", "name": "notes", "filetype": "canvas"}]
    other = _msg("3.0", "plain")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [share, file_msg, other])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:canvas")["messages"]["matches"]
    assert {m["text"] for m in hits} == {"shared canvas", "canvas file"}


def test_iter_remote_files(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "remote_files.json",
        [{"id": "Fr1", "name": "drive.doc", "is_external": True}],
    )
    client = DumpClient(tmp_path)
    assert [f["id"] for f in client.iter_remote_files()] == ["Fr1"]


def test_users_profile_keeps_pronouns(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U1": {
                "id": "U1",
                "handle": "alice",
                "pronouns": "she/her",
                "start_date": "2020-01-15",
                "is_primary_owner": True,
            }
        },
    )
    client = DumpClient(tmp_path)
    user = client.users_info(user="U1")["user"]
    assert user["is_primary_owner"] is True
    profile = user["profile"]
    assert profile["pronouns"] == "she/her"
    assert profile["start_date"] == "2020-01-15"


def test_conversations_info_frozen_open(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ch / "channel.json",
        {
            "id": "C123",
            "name": "general",
            "is_frozen": True,
            "is_open": True,
            "is_org_default": True,
            "shared_team_ids": ["T1", "Tother"],
        },
    )
    info = DumpClient(tmp_path).conversations_info(channel="C123")["channel"]
    assert info["is_frozen"] is True
    assert info["is_open"] is True
    assert info["is_org_default"] is True
    assert info["shared_team_ids"] == ["T1", "Tother"]


def test_search_is_unreads(tmp_path: Path) -> None:
    _write(
        tmp_path / "acme" / "conversations.json",
        [
            {
                "id": "C123",
                "name": "inbox",
                "is_channel": True,
                "unread_count": 2,
                "last_read": "1.0",
            },
            {"id": "C999", "name": "caught", "is_channel": True, "unread_count": 0},
        ],
    )
    _write(
        (tmp_path / "acme" / "inbox_C123") / "messages.json",
        [_msg("1.0", "hello"), _msg("2.0", "hello")],
    )
    _write((tmp_path / "acme" / "caught_C999") / "messages.json", [_msg("3.0", "hello")])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:unreads hello")["messages"]["matches"]
    assert [m["ts"] for m in hits] == ["2.0"]


def test_rtm_start_includes_users_channels(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "auth.json", {"user_id": "U1", "user": "alice", "team_id": "T99", "team": "Acme"})
    _write(ws / "users.json", {"U1": {"id": "U1", "handle": "alice"}})
    _write(ws / "conversations.json", [{"id": "C123", "name": "general", "is_channel": True}])
    client = DumpClient(tmp_path)
    start = client.rtm_start()
    assert {u["id"] for u in start["users"]} == {"U1"}
    assert {c["id"] for c in start["channels"]} == {"C123"}
    connect = client.rtm_connect()
    assert "users" not in connect


def test_rtm_start_includes_bots(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "auth.json", {"user_id": "U1", "user": "alice", "team_id": "T99"})
    _write(ws / "bots.json", {"B9": {"id": "B9", "name": "deploybot", "app_id": "A9"}})
    client = DumpClient(tmp_path)
    start = client.rtm_start()
    assert [b["id"] for b in start["bots"]] == ["B9"]
    connect = client.rtm_connect()
    assert "bots" not in connect


def test_search_has_javascript(tmp_path: Path) -> None:
    js = _msg("1.0", "script")
    js["files"] = [{"id": "Fj", "name": "app.js", "filetype": "javascript"}]
    ts = _msg("2.0", "types")
    ts["files"] = [{"id": "Ft", "name": "app.ts", "filetype": "typescript"}]
    pic = _msg("3.0", "pic")
    pic["files"] = [{"id": "Fi", "name": "a.png", "filetype": "png"}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [js, ts, pic])
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="has:js")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["script"]
    files = client.search_files(query="has:typescript")["files"]["matches"]
    assert [f["id"] for f in files] == ["Ft"]


def test_get_cursor(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [_msg("1.0", "hi")])
    (ch / ".cursor").write_text("9.0", encoding="utf-8")
    client = DumpClient(tmp_path)
    resp = client.get_cursor(channel="C123")
    assert resp["ok"] is True
    assert resp["ts"] == "9.0"
    missing = client.get_cursor(channel="C404")
    assert missing["ok"] is False
    assert list(client.iter_cursors()) == [{"channel": "C123", "ts": "9.0"}]


def test_users_list_huddle_state(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U1": {
                "id": "U1",
                "handle": "alice",
                "huddle_state": "in_a_huddle",
                "huddle_state_expiration_ts": 1700000000,
                "who_can_share_contact_card": "EVERYONE",
                "team_id": "T99",
                "real_name": "Alice",
                "is_forgotten": True,
                "is_workflow_bot": True,
                "has_2fa": True,
                "two_factor_type": "sms",
                "status_emoji_display_info": [{"emoji_name": "wave"}],
                "status_text_canonical": "out",
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
                "guest_invited_by": "U9",
                "is_connector": True,
                "guest_expiration_ts": 99,
                "bot_id": "B99",
                "api_app_id": "A99",
                "team": "T99",
                "display_name_normalized": "alice",
                "real_name_normalized": "Alice",
                "enterprise_user": {"id": "EU1", "enterprise_id": "E1"},
            }
        },
    )
    client = DumpClient(tmp_path)
    member = client.users_list(include_message_users=False)["members"][0]
    assert member["huddle_state"] == "in_a_huddle"
    assert member["who_can_share_contact_card"] == "EVERYONE"
    assert member["huddle_state_expiration_ts"] == 1700000000
    assert member["team_id"] == "T99"
    assert member["real_name"] == "Alice"
    assert member["is_forgotten"] is True
    assert member["is_workflow_bot"] is True
    assert member["has_2fa"] is True
    assert member["two_factor_type"] == "sms"
    assert member["profile"]["status_emoji_display_info"] == [{"emoji_name": "wave"}]
    assert member["profile"]["status_text_canonical"] == "out"
    assert member["profile"]["image_72"] == "https://a.test/72.png"
    assert member["profile"]["image_512"] == "https://a.test/512.png"
    assert member["profile"]["image_original"] == "https://a.test/orig.png"
    assert member["profile"]["image_24"] == "https://a.test/24.png"
    assert member["profile"]["image_32"] == "https://a.test/32.png"
    assert member["profile"]["image_48"] == "https://a.test/48.png"
    assert member["profile"]["image_1024"] == "https://a.test/1024.png"
    assert member["profile"]["image_192"] == "https://a.test/192.png"
    assert member["profile"]["is_custom_image"] is True
    assert member["profile"]["fields"]["Xf1"]["value"] == "eng"
    assert member["guest_invited_by"] == "U9"
    assert member["is_connector"] is True
    assert member["profile"]["guest_expiration_ts"] == 99
    assert member["profile"]["bot_id"] == "B99"
    assert member["profile"]["api_app_id"] == "A99"
    assert member["profile"]["team"] == "T99"
    assert member["profile"]["display_name_normalized"] == "alice"
    assert member["profile"]["real_name_normalized"] == "Alice"
    assert member["enterprise_user"]["enterprise_id"] == "E1"


def test_iter_emoji_categories(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "emoji.json", {"shipit": "https://e.test/s.png"})
    _write(ws / "emoji_categories.json", [{"name": "Custom", "emoji_names": ["shipit"]}])
    client = DumpClient(tmp_path)
    cats = list(client.iter_emoji_categories())
    assert [c["name"] for c in cats] == ["Custom"]


def test_users_list_always_active(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "users.json",
        {
            "U1": {
                "id": "U1",
                "handle": "alice",
                "always_active": True,
                "is_email_confirmed": True,
            }
        },
    )
    client = DumpClient(tmp_path)
    member = client.users_list(include_message_users=False)["members"][0]
    assert member["always_active"] is True
    assert member["is_email_confirmed"] is True


@pytest.mark.parametrize(
    ("ch123_extra", "ch123_name", "ch999_name", "ch999_extra", "query"),
    [
        (
            {"connected_limited_team_ids": ["Tlim"]},
            "lim",
            "full",
            {"connected_team_ids": ["T2"]},
            "is:connected_limited",
        ),
        ({"conversation_host_id": "T1"}, "hosted", "guest", {}, "is:host"),
        ({"internal_team_ids": ["T1"]}, "inside", "outside", {}, "is:internal"),
        ({"connected_team_ids": ["Tother"]}, "shared", "local", {}, "is:connected"),
    ],
)
def test_search_is_team_channel_flag(
    tmp_path: Path,
    ch123_extra: dict,
    ch123_name: str,
    ch999_name: str,
    ch999_extra: dict,
    query: str,
) -> None:
    _write(
        tmp_path / "acme" / "conversations.json",
        [
            {"id": "C123", "name": ch123_name, "is_channel": True, **ch123_extra},
            {"id": "C999", "name": ch999_name, "is_channel": True, **ch999_extra},
        ],
    )
    _write((tmp_path / "acme" / f"{ch123_name}_C123") / "messages.json", [_msg("1.0", "hello")])
    _write((tmp_path / "acme" / f"{ch999_name}_C999") / "messages.json", [_msg("2.0", "hello")])
    hits = DumpClient(tmp_path).search_messages(query=f"{query} hello")["messages"]["matches"]
    assert [m["channel"]["id"] for m in hits] == ["C123"]


def test_rtm_start_splits_conversation_types(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write((ws / "secret_C999") / "messages.json", [])
    _write((ws / "dm_D1") / "messages.json", [])
    _write((ws / "mpdm_G1") / "messages.json", [])
    _write(
        ws / "conversations.json",
        [
            {"id": "C123", "name": "general", "is_channel": True},
            {"id": "C999", "name": "secret", "is_channel": True, "is_private": True},
            {"id": "D1", "name": "dm", "is_im": True},
            {"id": "G1", "name": "mpdm", "is_mpim": True},
        ],
    )
    _write(ws / "auth.json", {"user_id": "U1", "user": "alice", "team_id": "T99"})
    start = DumpClient(tmp_path).rtm_start()
    assert [c["id"] for c in start["channels"]] == ["C123"]
    assert [c["id"] for c in start["groups"]] == ["C999"]
    assert [c["id"] for c in start["ims"]] == ["D1"]
    assert [c["id"] for c in start["mpims"]] == ["G1"]
    connect = DumpClient(tmp_path).rtm_connect()
    assert "groups" not in connect
    assert "ims" not in connect
    assert "mpims" not in connect


def test_cursors_list(tmp_path: Path) -> None:
    a = tmp_path / "acme" / "general_C123"
    b = tmp_path / "acme" / "other_C999"
    _write(a / "messages.json", [])
    _write(b / "messages.json", [])
    (a / ".cursor").write_text("1.0", encoding="utf-8")
    (b / ".cursor").write_text("2.0", encoding="utf-8")
    client = DumpClient(tmp_path)
    rows = client.cursors_list()["cursors"]
    assert {r["channel"]: r["ts"] for r in rows} == {"C123": "1.0", "C999": "2.0"}
    hits = client.cursors_search(query="C123")["cursors"]
    assert [r["channel"] for r in hits] == ["C123"]
    empty = client.cursors_search(query="")
    assert empty["ok"] is False


def test_conversations_info_read_only(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    _write(
        ch / "channel.json",
        {
            "id": "C123",
            "name": "general",
            "is_read_only": True,
            "pending_connected_team_ids": ["Tconn"],
            "connected_limited_team_ids": ["Tlim"],
            "is_thread_only": True,
            "is_non_threadable": True,
            "properties": {"tabs": [{"id": "files", "label": "Files", "type": "files"}]},
            "priority": 1.5,
            "is_moved": 3,
            "is_muted": True,
            "is_starred": True,
            "use_case": "huddles",
            "last_read": "8.0",
            "unread_count": 4,
            "unread_count_display": 3,
            "latest": {"ts": "9.0", "text": "hi", "type": "message", "user": "U1"},
            "enterprise_id": "E1",
            "file_id": "Fcanvas",
            "is_pending_shared": True,
            "has_canvas": True,
            "is_im_blocked": True,
            "connected_team_ids": ["Tother"],
        },
    )
    info = DumpClient(tmp_path).conversations_info(channel="C123")["channel"]
    assert info["is_read_only"] is True
    assert info["pending_connected_team_ids"] == ["Tconn"]
    assert info["connected_limited_team_ids"] == ["Tlim"]
    assert info["is_thread_only"] is True
    assert info["is_non_threadable"] is True
    assert info["properties"]["tabs"][0]["type"] == "files"
    assert info["priority"] == 1.5
    assert info["is_moved"] == 3
    assert info["is_muted"] is True
    assert info["is_starred"] is True
    assert info["use_case"] == "huddles"
    assert info["last_read"] == "8.0"
    assert info["unread_count"] == 4
    assert info["unread_count_display"] == 3
    assert info["latest"]["ts"] == "9.0"
    assert info["enterprise_id"] == "E1"
    assert info["file_id"] == "Fcanvas"
    assert info["is_pending_shared"] is True
    assert info["has_canvas"] is True
    assert info["is_im_blocked"] is True
    assert info["connected_team_ids"] == ["Tother"]


def test_search_is_forgotten(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    gone = _msg("1.0", "hello")
    gone["user"] = "Ugone"
    stay = _msg("2.0", "hello")
    stay["user"] = "U1"
    _write((ws / "general_C123") / "messages.json", [gone, stay])
    _write(
        ws / "users.json",
        {
            "Ugone": {"id": "Ugone", "handle": "gone", "is_forgotten": True},
            "U1": {"id": "U1", "handle": "alice"},
        },
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:forgotten hello")["messages"]["matches"]
    assert [m["user"] for m in hits] == ["Ugone"]


def test_rtm_start_cache_ts(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    _write(ch / "messages.json", [])
    (ch / ".cursor").write_text("9.5", encoding="utf-8")
    _write((tmp_path / "acme") / "auth.json", {"user_id": "U1", "team_id": "T99"})
    start = DumpClient(tmp_path).rtm_start()
    assert start["cache_ts"] == "9.5"
    connect = DumpClient(tmp_path).rtm_connect()
    assert "cache_ts" not in connect


def test_im_user_without_members_json(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    ch = ws / "dm_D1"
    _write(ch / "messages.json", [])
    _write(
        ch / "channel.json",
        {
            "id": "D1",
            "name": "dm",
            "is_im": True,
            "user": "U2",
            "is_user_deleted": True,
            "name_normalized": "dm",
        },
    )
    _write(ws / "auth.json", {"user_id": "U1", "user": "alice", "team_id": "T99"})
    client = DumpClient(tmp_path)
    info = client.conversations_info(channel="D1")["channel"]
    assert info["user"] == "U2"
    assert info["is_user_deleted"] is True
    assert info["name_normalized"] == "dm"
    ids = {c["id"] for c in client.users_conversations(user="U2")["channels"]}
    assert "D1" in ids
    mine = {c["id"] for c in client.users_conversations(user="U1")["channels"]}
    assert "D1" in mine


def test_search_is_enterprise(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    grid = _msg("1.0", "hello")
    grid["user"] = "Ugrid"
    local = _msg("2.0", "hello")
    local["user"] = "U1"
    _write((ws / "general_C123") / "messages.json", [grid, local])
    _write(
        ws / "users.json",
        {
            "Ugrid": {
                "id": "Ugrid",
                "handle": "grid",
                "enterprise_user": {"id": "EU1", "enterprise_id": "E1"},
            },
            "U1": {"id": "U1", "handle": "alice"},
        },
    )
    client = DumpClient(tmp_path)
    hits = client.search_messages(query="is:enterprise hello")["messages"]["matches"]
    assert [m["user"] for m in hits] == ["Ugrid"]


def test_search_has_go_and_rust(tmp_path: Path) -> None:
    go = _msg("1.0", "gopher")
    go["files"] = [{"id": "Fg", "name": "main.go", "filetype": "go"}]
    rust = _msg("2.0", "crab")
    rust["files"] = [{"id": "Fr", "name": "lib.rs", "filetype": "rust"}]
    pic = _msg("3.0", "pic")
    pic["files"] = [{"id": "Fi", "name": "a.png", "filetype": "png"}]
    gold = _msg("4.0", "gold")
    gold["files"] = [{"id": "Fx", "name": "logo.gold", "filetype": "gold"}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [go, rust, pic, gold])
    client = DumpClient(tmp_path)
    assert [m["text"] for m in client.search_messages(query="has:go")["messages"]["matches"]] == [
        "gopher"
    ]
    files = client.search_files(query="has:rust")["files"]["matches"]
    assert [f["id"] for f in files] == ["Fr"]


def test_search_is_moved(tmp_path: Path) -> None:
    moved = _msg("1.0", "relocated")
    moved["is_moved"] = 1
    plain = _msg("2.0", "stayed")
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [moved, plain])
    hits = DumpClient(tmp_path).search_messages(query="is:moved")["messages"]["matches"]
    assert [m["text"] for m in hits] == ["relocated"]


def test_search_has_sql_css_sh(tmp_path: Path) -> None:
    sql = _msg("1.0", "query")
    sql["files"] = [{"id": "Fq", "name": "schema.sql", "filetype": "sql"}]
    css = _msg("2.0", "style")
    css["files"] = [{"id": "Fc", "name": "app.css", "filetype": "css"}]
    sh = _msg("3.0", "script")
    sh["files"] = [{"id": "Fs", "name": "run.sh", "filetype": "shell"}]
    pic = _msg("4.0", "pic")
    pic["files"] = [{"id": "Fi", "name": "a.png", "filetype": "png"}]
    _write((tmp_path / "acme" / "general_C123") / "messages.json", [sql, css, sh, pic])
    client = DumpClient(tmp_path)
    assert [m["text"] for m in client.search_messages(query="has:sql")["messages"]["matches"]] == [
        "query"
    ]
    assert [f["id"] for f in client.search_files(query="has:css")["files"]["matches"]] == ["Fc"]
    assert [m["text"] for m in client.search_messages(query="has:sh")["messages"]["matches"]] == [
        "script"
    ]


def test_calls_list_prefers_calls_json(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    huddle = _msg("1.0", "huddle")
    huddle["room"] = {"id": "Rmsg", "name": "from-messages"}
    _write(ch / "messages.json", [huddle])
    _write(ch / "calls.json", [{"id": "Rside", "name": "from-sidecar"}])
    client = DumpClient(tmp_path)
    assert [c["id"] for c in client.calls_list()["calls"]] == ["Rside"]
    assert client.calls_info(id="Rside")["call"]["name"] == "from-sidecar"
    hits = client.calls_search(query="sidecar")["calls"]
    assert [c["id"] for c in hits] == ["Rside"]
    empty = client.calls_search(query="")
    assert empty["ok"] is False


def test_files_list_channel_prefers_files_json(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    msg = _msg("1.0", "see file")
    msg["files"] = [{"id": "Fmsg", "name": "msg.png", "filetype": "png"}]
    _write(ch / "messages.json", [msg])
    _write(ch / "files.json", [{"id": "Fside", "name": "side.png", "filetype": "png"}])
    client = DumpClient(tmp_path)
    assert [f["id"] for f in client.files_list(channel="C123")["files"]] == ["Fside"]
    assert [f["id"] for f in client.iter_files(channel="C123")] == ["Fside"]


def test_files_list_uses_channel_files_when_workspace_missing(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    msg = _msg("1.0", "see file")
    msg["files"] = [{"id": "Fmsg", "name": "msg.png", "filetype": "png"}]
    _write(ch / "messages.json", [msg])
    _write(ch / "files.json", [{"id": "Fside", "name": "side.png", "filetype": "png"}])
    client = DumpClient(tmp_path)
    assert [f["id"] for f in client.files_list()["files"]] == ["Fside"]


def test_iter_threads_prefers_threads_json(tmp_path: Path) -> None:
    ch = tmp_path / "acme" / "general_C123"
    root = _msg("1.0", "root")
    root["thread"] = [_msg("1.1", "reply")]
    _write(ch / "messages.json", [root])
    _write(
        ch / "threads.json",
        [{"channel": "C123", "thread_ts": "9.0", "reply_count": 4, "latest_reply": "9.4"}],
    )
    client = DumpClient(tmp_path)
    rows = list(client.iter_threads())
    assert rows == [
        {"channel": "C123", "thread_ts": "9.0", "reply_count": 4, "latest_reply": "9.4"}
    ]
    page = client.threads_list(count=1)
    assert page["threads"] == rows
    assert page["response_metadata"]["next_cursor"] == ""
    info = client.threads_info(channel="C123", ts="9.0")
    assert info["ok"] is True
    assert info["thread"]["reply_count"] == 4
    missing = client.threads_info(channel="C123", ts="1.0")
    assert missing["ok"] is False
    hits = client.threads_search(query="9.0")["threads"]
    assert [t["thread_ts"] for t in hits] == ["9.0"]
    empty = client.threads_search(query="")
    assert empty["ok"] is False


def test_users_list_extras_from_members_without_load_all(tmp_path: Path, mocker) -> None:
    ch = tmp_path / "acme" / "general_C123"
    extra = _msg("1.0", "from message user")
    extra["user"] = "U3"
    extra["user_name"] = "carol"
    _write(ch / "messages.json", [extra])
    _write(
        ch / "users.json",
        {"U1": {"id": "U1", "handle": "alice", "display_name": "alice", "is_bot": False}},
    )
    _write(ch / "members.json", ["U1", "U99"])
    _write(
        tmp_path / "acme" / "conversations.json",
        [{"id": "D1", "name": "im-bob", "is_im": True, "user": "U88"}],
    )
    client = DumpClient(tmp_path)
    spy = mocker.spy(client, "_load_all")
    ids = {u["id"] for u in client.users_list(include_message_users=True)["members"]}
    spy.assert_not_called()
    assert "U1" in ids
    assert "U99" in ids
    assert "U88" in ids
    assert "U3" not in ids


def test_files_list_includes_canvases_sidecar(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(ws / "files.json", [{"id": "F1", "name": "shot.png"}])
    _write(ws / "canvases.json", [{"id": "Fc", "filetype": "canvas", "name": "notes"}])
    client = DumpClient(tmp_path)
    ids = {f["id"] for f in client.files_list()["files"]}
    assert "F1" in ids
    assert "Fc" in ids


def test_load_all_file_merge_stable_across_channels(tmp_path: Path) -> None:
    """Parallel _load_all must merge self._files in discovery order (last wins)."""
    ws = tmp_path / "acme"
    msg = {
        "ts": "1.0",
        "user": "U1",
        "user_name": "alice",
        "text": "f",
        "reactions": [],
        "thread": [],
    }
    _write(
        (ws / "aaa_C001") / "messages.json",
        [{**msg, "files": [{"id": "Fdup", "name": "from-aaa.png", "title": "aaa"}]}],
    )
    _write(
        (ws / "zzz_C002") / "messages.json",
        [{**msg, "files": [{"id": "Fdup", "name": "from-zzz.png", "title": "zzz"}]}],
    )
    client = DumpClient(tmp_path)
    assert list(client._channels) == ["C001", "C002"]
    client._load_all()
    assert client._files["Fdup"]["title"] == "zzz"
    for _ in range(8):
        again = DumpClient(tmp_path)
        again._load_all()
        assert again._files["Fdup"]["title"] == "zzz"


def test_users_search_includes_message_users_and_filters(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    _write(
        ch / "messages.json",
        [
            {
                "ts": "1.0",
                "user": "UONLY",
                "user_name": "onlymsg",
                "text": "hi",
                "reactions": [],
                "thread": [],
            },
            {
                "ts": "2.0",
                "user": "UBOT",
                "user_name": "botty",
                "text": "beep",
                "reactions": [],
                "thread": [],
            },
        ],
    )
    # Mark bot via users.json so include_bots can filter it.
    _write(
        ws / "users.json",
        {
            "UBOT": {
                "id": "UBOT",
                "handle": "botty",
                "display_name": "botty",
                "is_bot": True,
            }
        },
    )
    client = DumpClient(tmp_path)
    listed = {u["id"] for u in client.users_list()["members"]}
    assert "UONLY" in listed
    hits = client.users_search(query="onlymsg")["members"]
    assert [u["id"] for u in hits] == ["UONLY"]
    no_bots = client.users_search(query="botty", include_bots=False)["members"]
    assert no_bots == []


def test_scheduled_search_honors_oldest_latest(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "scheduled_messages.json",
        [
            {"id": "Q1", "channel_id": "C123", "text": "standup notes", "post_at": 100},
            {"id": "Q2", "channel_id": "C123", "text": "standup later", "post_at": 200},
        ],
    )
    client = DumpClient(tmp_path)
    hits = client.chat_scheduledMessages_search(query="standup", oldest="150", latest="250")[
        "scheduled_messages"
    ]
    assert [m["id"] for m in hits] == ["Q2"]


def test_usergroups_search_honors_include_flags(tmp_path: Path) -> None:
    ws = tmp_path / "acme"
    _write((ws / "general_C123") / "messages.json", [])
    _write(
        ws / "usergroups.json",
        [{"id": "S1", "handle": "eng", "name": "Engineering", "users": ["U1", "U2"]}],
    )
    client = DumpClient(tmp_path)
    hits = client.usergroups_search(query="eng", include_count=True, include_users=False)[
        "usergroups"
    ]
    assert len(hits) == 1
    assert hits[0]["user_count"] == 2
    assert "users" not in hits[0]


def test_load_all_concurrent_callers_see_complete_state(tmp_path: Path) -> None:
    """Two threads calling _load_all must not observe a half-merged client."""
    import threading

    ws = tmp_path / "acme"
    for i in range(4):
        ch = ws / f"chan{i}_C{i}"
        _write(
            ch / "messages.json",
            [
                {
                    "ts": f"{i}.0",
                    "user": "U1",
                    "user_name": "alice",
                    "text": f"msg{i}",
                    "reactions": [],
                    "files": [{"id": f"F{i}", "name": f"f{i}.txt"}],
                    "thread": [],
                }
            ],
        )
    client = DumpClient(tmp_path)
    errors: list[Exception] = []

    def worker() -> None:
        try:
            client._load_all()
            assert client._all_loaded
            assert client._files_len() == 4
            for ch in client._channels.values():
                if ch.path.is_dir():
                    assert ch.loaded is not None
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "worker did not finish within 30s"
    assert errors == []


def test_ensure_workspace_files_concurrent_callers_see_complete_state(
    tmp_path: Path,
) -> None:
    """_ws_files_loaded must not flip true before ingest finishes."""
    import threading

    ws = tmp_path / "acme"
    _write(
        ws / "files.json",
        [{"id": f"F{i}", "name": f"file{i}.txt", "user": "U1"} for i in range(24)],
    )
    _write(
        ws / "general_C1" / "messages.json",
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
    errors: list[Exception] = []
    barrier = threading.Barrier(8)

    def worker() -> None:
        try:
            barrier.wait(timeout=30)
            client._ensure_workspace_files()
            assert client._ws_files_loaded
            assert not client._ws_files_in_progress
            assert client._files_len() == 24
            for i in range(24):
                assert client._files_get(f"F{i}") is not None
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "worker did not finish within 30s"
    assert errors == []


def test_files_iter_safe_under_concurrent_fill(tmp_path: Path) -> None:
    """Readers must not raise while another thread merges into _files."""
    import threading

    ws = tmp_path / "acme"
    for i in range(6):
        ch = ws / f"chan{i}_C{i}"
        _write(
            ch / "messages.json",
            [
                {
                    "ts": f"{i}.0",
                    "user": "U1",
                    "user_name": "alice",
                    "text": f"msg{i}",
                    "reactions": [],
                    "files": [{"id": f"F{i}", "name": f"f{i}.txt"}],
                    "thread": [],
                }
            ],
        )
    client = DumpClient(tmp_path)
    errors: list[Exception] = []
    barrier = threading.Barrier(4)

    def reader() -> None:
        try:
            barrier.wait(timeout=30)
            for _ in range(30):
                list(client.iter_files())
                client.files_info(file="F0")
        except Exception as exc:
            errors.append(exc)

    def filler() -> None:
        try:
            barrier.wait(timeout=30)
            client._fill_files()
        except Exception as exc:
            errors.append(exc)

    threads = [threading.Thread(target=reader) for _ in range(3)]
    threads.append(threading.Thread(target=filler))
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=30)
        assert not t.is_alive(), "worker did not finish within 30s"
    assert errors == []
