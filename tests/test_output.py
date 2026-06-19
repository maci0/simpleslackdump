import json
import pytest
from pathlib import Path
from ssd.output import (
    channel_dir,
    write_messages,
    merge_messages,
    read_cursor,
    write_cursor,
    format_markdown,
)

MSG_A = {
    "ts": "1705320720.000000",
    "user": "U001",
    "user_name": "alice",
    "text": "hello world",
    "reactions": [{"name": "thumbsup", "count": 2}],
    "thread": [],
}
MSG_B = {
    "ts": "1705320780.000000",
    "user": "U002",
    "user_name": "bob",
    "text": "hey there",
    "reactions": [],
    "thread": [
        {
            "ts": "1705320800.000000",
            "user": "U001",
            "user_name": "alice",
            "text": "reply from alice",
            "reactions": [],
        }
    ],
}


def test_channel_dir_path(tmp_path):
    d = channel_dir(str(tmp_path), "redhat.enterprise", "general", "C0BAF26EJ2Z")
    assert d == tmp_path / "redhat.enterprise" / "general_C0BAF26EJ2Z"


def test_write_messages_creates_json(tmp_path):
    write_messages(tmp_path, [MSG_A])
    data = json.loads((tmp_path / "messages.json").read_text())
    assert len(data) == 1
    assert data[0]["text"] == "hello world"


def test_write_messages_sorted_by_ts(tmp_path):
    write_messages(tmp_path, [MSG_B, MSG_A])
    data = json.loads((tmp_path / "messages.json").read_text())
    assert data[0]["ts"] == MSG_A["ts"]
    assert data[1]["ts"] == MSG_B["ts"]


def test_merge_deduplicates_by_ts(tmp_path):
    write_messages(tmp_path, [MSG_A])
    merge_messages(tmp_path, [MSG_A, MSG_B])
    data = json.loads((tmp_path / "messages.json").read_text())
    assert len(data) == 2


def test_merge_never_loses_data(tmp_path):
    write_messages(tmp_path, [MSG_A, MSG_B])
    merge_messages(tmp_path, [])  # empty new batch
    data = json.loads((tmp_path / "messages.json").read_text())
    assert len(data) == 2


def test_cursor_roundtrip(tmp_path):
    assert read_cursor(tmp_path) is None
    write_cursor(tmp_path, "1705320720.000000")
    assert read_cursor(tmp_path) == "1705320720.000000"


def test_format_markdown_contains_username(tmp_path):
    md = format_markdown([MSG_A])
    assert "alice" in md
    assert "hello world" in md


def test_format_markdown_reactions(tmp_path):
    md = format_markdown([MSG_A])
    assert "thumbsup" in md


def test_format_markdown_thread_reply(tmp_path):
    md = format_markdown([MSG_B])
    assert "alice" in md
    assert "reply from alice" in md


def test_write_messages_creates_md(tmp_path):
    write_messages(tmp_path, [MSG_A])
    md = (tmp_path / "messages.md").read_text()
    assert "alice" in md
