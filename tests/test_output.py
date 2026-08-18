import json

import pytest

from ssd.output import (
    channel_dir,
    format_markdown,
    merge_messages,
    merge_thread_into_channel,
    read_cursor,
    refresh_thread_dump_dirs,
    write_cursor,
    write_messages,
    write_thread,
    write_users,
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
    d = channel_dir(str(tmp_path), "acme.enterprise", "general", "C0BAF26EJ2Z")
    assert d == tmp_path / "acme.enterprise" / "general_C0BAF26EJ2Z"


def test_channel_dir_reuses_existing_id_after_rename(tmp_path):
    """Channel id is durable; a Slack rename must not fork the dump directory."""
    ws = tmp_path / "acme"
    old = ws / "old-name_C123"
    old.mkdir(parents=True)
    (old / "messages.json").write_text("[]", encoding="utf-8")
    got = channel_dir(str(tmp_path), "acme", "new-name", "C123")
    assert got == old


def test_channel_dir_prefers_preferred_name_when_present(tmp_path):
    ws = tmp_path / "acme"
    old = ws / "old-name_C123"
    preferred = ws / "new-name_C123"
    old.mkdir(parents=True)
    preferred.mkdir(parents=True)
    got = channel_dir(str(tmp_path), "acme", "new-name", "C123")
    assert got == preferred


def test_channel_dir_blocks_path_traversal(tmp_path):
    """Workspace/channel names must not escape the output root."""
    got = channel_dir(str(tmp_path), "../outside", "../../evil", "C123")
    resolved = got.resolve()
    assert resolved.is_relative_to(tmp_path.resolve())
    assert ".." not in str(got.relative_to(tmp_path))
    assert got == tmp_path / "___outside" / "______evil_C123"


def test_channel_dir_prefers_messages_among_renamed_dirs(tmp_path):
    """Preferred name absent: reuse the id-match that already has messages.json."""
    ws = tmp_path / "acme"
    empty = ws / "old-a_C123"
    empty.mkdir(parents=True)
    full = ws / "old-b_C123"
    full.mkdir(parents=True)
    (full / "messages.json").write_text("[]", encoding="utf-8")
    got = channel_dir(str(tmp_path), "acme", "new-name", "C123")
    assert got == full


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


def test_merge_keeps_thread_replies_when_incoming_empty(tmp_path):
    write_messages(tmp_path, [MSG_B])
    thinner = {**MSG_B, "text": "hey there (edited)", "thread": []}
    merge_messages(tmp_path, [thinner])
    data = json.loads((tmp_path / "messages.json").read_text())
    assert data[0]["text"] == "hey there (edited)"
    assert len(data[0]["thread"]) == 1
    assert data[0]["thread"][0]["text"] == "reply from alice"


def test_merge_unions_thread_replies_by_ts(tmp_path):
    write_messages(tmp_path, [MSG_B])
    extra = {
        **MSG_B,
        "thread": [
            {
                "ts": "1705320900.000000",
                "user": "U002",
                "user_name": "bob",
                "text": "second reply",
                "reactions": [],
            }
        ],
    }
    merge_messages(tmp_path, [extra])
    data = json.loads((tmp_path / "messages.json").read_text())
    assert [r["ts"] for r in data[0]["thread"]] == [
        "1705320800.000000",
        "1705320900.000000",
    ]


def test_merge_keeps_file_local_path(tmp_path):
    with_path = {
        **MSG_A,
        "files": [{"id": "F1", "name": "a.txt", "local_path": "attachments/a.txt"}],
    }
    write_messages(tmp_path, [with_path])
    merge_messages(
        tmp_path,
        [{**MSG_A, "files": [{"id": "F1", "name": "a.txt", "url": "https://x/a.txt"}]}],
    )
    data = json.loads((tmp_path / "messages.json").read_text())
    assert data[0]["files"][0]["local_path"] == "attachments/a.txt"
    assert data[0]["files"][0]["url"] == "https://x/a.txt"


def test_merge_keeps_reactions_when_incoming_empty(tmp_path):
    write_messages(tmp_path, [MSG_A])
    merge_messages(tmp_path, [{**MSG_A, "text": "hello world (edited)", "reactions": []}])
    data = json.loads((tmp_path / "messages.json").read_text())
    assert data[0]["text"] == "hello world (edited)"
    assert data[0]["reactions"] == [{"name": "thumbsup", "count": 2}]


def test_merge_keeps_reply_count_when_incoming_underclaims(tmp_path):
    """Thin re-fetch must not shrink reply_count below archived thread length."""
    rooted = {
        **MSG_B,
        "reply_count": 1,
        "latest_reply": "1705320800.000000",
    }
    write_messages(tmp_path, [rooted])
    merge_messages(
        tmp_path,
        [{**MSG_B, "text": "hey there (edited)", "thread": [], "reply_count": 0}],
    )
    data = json.loads((tmp_path / "messages.json").read_text())
    assert len(data[0]["thread"]) == 1
    assert data[0]["reply_count"] == 1
    assert data[0]["latest_reply"] == "1705320800.000000"


def test_thread_reply_meta_prefers_claimed_count_and_numeric_latest():
    from ssd.output import thread_reply_meta

    assert thread_reply_meta({"ts": "1.0", "thread": []}) is None
    partial = {
        "ts": "1.0",
        "reply_count": 5,
        "latest_reply": "1.5",
        "thread": [
            {"ts": "1.2", "text": "later"},
            {"ts": "1.1", "text": "earlier"},
        ],
    }
    assert thread_reply_meta(partial) == (5, "1.5")
    unsorted_only = {
        "ts": "2.0",
        "thread": [
            {"ts": "2.2", "text": "later"},
            {"ts": "2.1", "text": "earlier"},
        ],
    }
    assert thread_reply_meta(unsorted_only) == (2, "2.2")
    claimed_only = {"ts": "3.0", "reply_count": 3, "latest_reply": "3.3", "thread": []}
    assert thread_reply_meta(claimed_only) == (3, "3.3")


def test_merge_thread_into_channel_updates_reply_meta(tmp_path):
    write_messages(
        tmp_path,
        [
            {
                "ts": "1.0",
                "user_name": "alice",
                "text": "root",
                "reactions": [],
                "files": [],
                "reply_count": 1,
                "latest_reply": "1.1",
                "thread": [
                    {
                        "ts": "1.1",
                        "user_name": "bob",
                        "text": "old",
                        "reactions": [],
                        "files": [],
                    }
                ],
            }
        ],
    )
    assert merge_thread_into_channel(
        tmp_path,
        "1.0",
        [
            {"ts": "1.0", "user_name": "alice", "text": "root", "reactions": [], "files": []},
            {"ts": "1.1", "user_name": "bob", "text": "old", "reactions": [], "files": []},
            {"ts": "1.2", "user_name": "carol", "text": "new", "reactions": [], "files": []},
        ],
    )
    data = json.loads((tmp_path / "messages.json").read_text())
    assert [r["ts"] for r in data[0]["thread"]] == ["1.1", "1.2"]
    assert data[0]["reply_count"] == 2
    assert data[0]["latest_reply"] == "1.2"


def test_cursor_roundtrip(tmp_path):
    assert read_cursor(tmp_path) is None
    write_cursor(tmp_path, "1705320720.000000")
    assert read_cursor(tmp_path) == "1705320720.000000"


def test_write_cursor_from_messages_uses_archive_max(tmp_path):
    from ssd.output import write_cursor_from_messages

    write_messages(
        tmp_path,
        [
            {
                "ts": "1705320720.000000",
                "user_name": "alice",
                "text": "older",
                "reactions": [],
                "files": [],
                "thread": [],
            },
            {
                "ts": "1705320800.000000",
                "user_name": "bob",
                "text": "newer",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    # Stale high watermark must be corrected to the archive max.
    write_cursor(tmp_path, "1705320999.000000")
    assert write_cursor_from_messages(tmp_path) == "1705320800.000000"
    assert read_cursor(tmp_path) == "1705320800.000000"


def test_write_cursor_from_thread_uses_archive_max(tmp_path):
    from ssd.output import write_cursor_from_thread, write_thread

    write_thread(
        tmp_path,
        [
            {
                "ts": "1705320700.000000",
                "user_name": "alice",
                "text": "parent",
                "reactions": [],
                "files": [],
            },
            {
                "ts": "1705320800.000000",
                "user_name": "bob",
                "text": "reply",
                "reactions": [],
                "files": [],
            },
        ],
    )
    write_cursor(tmp_path, "1705320999.000000")
    assert write_cursor_from_thread(tmp_path) == "1705320800.000000"
    assert read_cursor(tmp_path) == "1705320800.000000"


def test_max_ts_is_numeric_not_lexicographic():
    from ssd.output import max_ts

    # Lexicographic max would pick the 9-digit string; numeric max is correct.
    assert max_ts(["999999999.999999", "1000000000.000001"]) == "1000000000.000001"


def test_format_markdown_contains_username(tmp_path):
    md = format_markdown([MSG_A])
    assert "alice" in md
    assert "hello world" in md


def test_format_markdown_reactions(tmp_path):
    md = format_markdown([MSG_A])
    assert ":thumbsup: x2" in md


def test_format_markdown_thread_reply(tmp_path):
    md = format_markdown([MSG_B])
    assert "alice" in md
    assert "reply from alice" in md
    assert md.count("> **alice**") == 1


def test_write_messages_creates_md(tmp_path):
    write_messages(tmp_path, [MSG_A])
    md = (tmp_path / "messages.md").read_text()
    assert "alice" in md
    assert "hello world" in md
    assert ":thumbsup: x2" in md


def test_write_thread_creates_json_and_md(tmp_path):
    write_thread(tmp_path, [MSG_B, MSG_A])
    data = json.loads((tmp_path / "thread.json").read_text())
    assert [m["ts"] for m in data] == [MSG_A["ts"], MSG_B["ts"]]
    md = (tmp_path / "thread.md").read_text()
    assert "alice" in md
    assert "bob" in md


def test_write_thread_markdown_links_parent_attachments(tmp_path):
    write_thread(
        tmp_path,
        [
            {
                "ts": "1.0",
                "user_name": "alice",
                "text": "file",
                "reactions": [],
                "files": [{"name": "a.txt", "local_path": "attachments/a.txt"}],
                "thread": [],
            }
        ],
    )
    md = (tmp_path / "thread.md").read_text()
    assert "../attachments/a.txt" in md
    assert "](attachments/a.txt)" not in md


def test_write_users(tmp_path):
    write_users(
        tmp_path,
        {
            "U2": {"id": "U2", "display_name": "bob"},
            "U1": {"id": "U1", "display_name": "alice"},
        },
    )
    data = json.loads((tmp_path / "users.json").read_text())
    assert list(data) == ["U1", "U2"]
    assert data["U1"]["display_name"] == "alice"


def test_write_users_merges_existing(tmp_path):
    write_users(tmp_path, {"U1": {"id": "U1", "display_name": "alice"}})
    write_users(tmp_path, {"U2": {"id": "U2", "display_name": "bob"}})
    data = json.loads((tmp_path / "users.json").read_text())
    assert set(data) == {"U1", "U2"}


def test_write_users_skips_empty(tmp_path):
    write_users(tmp_path, {})
    assert not (tmp_path / "users.json").exists()


def test_merge_thread_into_channel_folds_replies(tmp_path):
    write_messages(
        tmp_path,
        [
            {
                "ts": "1.0",
                "user_name": "alice",
                "text": "root",
                "reactions": [],
                "files": [],
                "thread": [
                    {
                        "ts": "1.1",
                        "user_name": "bob",
                        "text": "old",
                        "reactions": [],
                        "files": [],
                    }
                ],
            }
        ],
    )
    assert merge_thread_into_channel(
        tmp_path,
        "1.0",
        [
            {"ts": "1.0", "user_name": "alice", "text": "root", "reactions": [], "files": []},
            {"ts": "1.1", "user_name": "bob", "text": "old", "reactions": [], "files": []},
            {"ts": "1.2", "user_name": "carol", "text": "new", "reactions": [], "files": []},
        ],
    )
    data = json.loads((tmp_path / "messages.json").read_text())
    assert [r["ts"] for r in data[0]["thread"]] == ["1.1", "1.2"]


def test_merge_thread_into_channel_noop_without_parent(tmp_path):
    write_messages(tmp_path, [MSG_A])
    assert not merge_thread_into_channel(
        tmp_path,
        "9.0",
        [{"ts": "9.0", "user_name": "x", "text": "orphan", "reactions": [], "files": []}],
    )


def test_refresh_thread_dump_dirs_updates_existing(tmp_path):
    write_messages(
        tmp_path,
        [
            {
                "ts": "1.0",
                "user_name": "alice",
                "text": "root",
                "reactions": [],
                "files": [],
                "thread": [
                    {
                        "ts": "1.1",
                        "user_name": "bob",
                        "text": "reply",
                        "reactions": [],
                        "files": [],
                    }
                ],
            }
        ],
    )
    thread_dir = tmp_path / "thread_1_0"
    write_thread(
        thread_dir,
        [{"ts": "1.0", "user_name": "alice", "text": "stale", "reactions": [], "files": []}],
    )
    refresh_thread_dump_dirs(tmp_path)
    rows = json.loads((thread_dir / "thread.json").read_text())
    assert [m["ts"] for m in rows] == ["1.0", "1.1"]
    assert rows[0]["text"] == "root"


def test_format_markdown_missing_user_name():
    """Export-shaped rows without user_name must still render (unknown fallback)."""
    md = format_markdown([{"ts": "1.0", "text": "hi", "reactions": [], "files": [], "thread": []}])
    assert "unknown" in md
    assert "hi" in md


def test_merge_preserves_local_path_for_idless_files(tmp_path):
    """Id-less files must merge by name/url, not by list index (index shifts on concat)."""
    from ssd.output import merge_by_ts

    base = [
        {
            "ts": "1.0",
            "user_name": "alice",
            "text": "x",
            "files": [
                {
                    "name": "a.txt",
                    "url": "https://files.slack.com/a",
                    "local_path": "attachments/a.txt",
                }
            ],
            "reactions": [],
            "thread": [],
        }
    ]
    incoming = [
        {
            "ts": "1.0",
            "user_name": "alice",
            "text": "x",
            "files": [{"name": "a.txt", "url": "https://files.slack.com/a"}],
            "reactions": [],
            "thread": [],
        }
    ]
    merged = merge_by_ts(base, incoming)[0]
    assert len(merged["files"]) == 1
    assert merged["files"][0]["local_path"] == "attachments/a.txt"


def test_write_messages_skips_rows_without_ts(tmp_path):
    write_messages(
        tmp_path,
        [
            {"text": "orphan", "user_name": "x"},
            {
                "ts": "2.0",
                "user_name": "alice",
                "text": "kept",
                "reactions": [],
                "files": [],
                "thread": [],
            },
        ],
    )
    data = json.loads((tmp_path / "messages.json").read_text())
    assert [m["ts"] for m in data] == ["2.0"]
    assert "kept" in (tmp_path / "messages.md").read_text()


def test_merge_messages_raises_on_corrupt_existing(tmp_path):
    """Corrupt messages.json must not be treated as [] then overwritten."""
    path = tmp_path / "messages.json"
    path.write_text("{not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        merge_messages(tmp_path, [MSG_A])
    assert path.read_text(encoding="utf-8") == "{not-json"


def test_merge_messages_raises_on_wrong_shape(tmp_path):
    path = tmp_path / "messages.json"
    path.write_text('{"not": "a list"}', encoding="utf-8")
    with pytest.raises(RuntimeError, match="expected list"):
        merge_messages(tmp_path, [MSG_A])
    assert json.loads(path.read_text(encoding="utf-8")) == {"not": "a list"}


def test_write_users_raises_on_corrupt_existing(tmp_path):
    path = tmp_path / "users.json"
    path.write_text("not-json", encoding="utf-8")
    with pytest.raises(RuntimeError, match="unreadable"):
        write_users(tmp_path, {"U1": {"id": "U1", "display_name": "alice"}})
    assert path.read_text(encoding="utf-8") == "not-json"


def test_format_markdown_rejects_non_http_file_urls():
    """javascript:/data: attachment URLs must not become markdown links."""
    md = format_markdown(
        [
            {
                "ts": "1.0",
                "user_name": "alice",
                "text": "x",
                "reactions": [],
                "thread": [],
                "files": [
                    {"name": "evil](http://x/", "url": "javascript:alert(1)"},
                    {"name": "ok.pdf", "url": "https://files.slack.com/ok.pdf"},
                ],
            }
        ]
    )
    assert "javascript:" not in md
    assert "[ok.pdf](https://files.slack.com/ok.pdf)" in md
    assert "evil\\](http://x/" in md or "evil\\]" in md
