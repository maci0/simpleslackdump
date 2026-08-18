import json
from unittest.mock import MagicMock

import pytest

from ssd.output import channel_dir, write_cursor, write_messages, write_thread
from ssd.sync import _since_to_ts, run_sync


@pytest.fixture
def mock_api():
    api = MagicMock()
    api.resolve_channel.return_value = ("C123", "general")
    api.enrich.return_value = [
        {
            "ts": "1705320800.000000",
            "user": "U2",
            "user_name": "bob",
            "text": "new message",
            "reactions": [],
            "thread": [],
        }
    ]
    api.get_messages.return_value = [
        {"ts": "1705320800.000000", "user": "U2", "text": "new message", "reply_count": 0}
    ]
    # MagicMock.delay otherwise float()-coerces to 1.0 and time.sleep stalls each thread refresh.
    api.delay = 0
    return api


def test_sync_keeps_existing_workspace_sidecar(tmp_path, mock_api):
    ws = tmp_path / "testteam"
    ws.mkdir()
    (ws / "stars.json").write_text('[{"type":"message","channel":"OLD"}]', encoding="utf-8")
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    stars = json.loads((ws / "stars.json").read_text())
    assert stars[0]["channel"] == "OLD"
    mock_api.get_stars.assert_not_called()


def test_sync_derives_canvases_from_files_json(tmp_path, mock_api):
    ws = tmp_path / "testteam"
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    out_dir.mkdir(parents=True)
    write_cursor(out_dir, "1705320720.000000")
    (ws / "files.json").write_text(
        '[{"id":"Fc","filetype":"canvas","name":"notes"},{"id":"F1","filetype":"png"}]',
        encoding="utf-8",
    )
    mock_api.get_messages.return_value = []
    mock_api.get_files.return_value = []
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    mock_api.get_files.assert_not_called()
    canvases = json.loads((ws / "canvases.json").read_text())
    assert [c["id"] for c in canvases] == ["Fc"]


def test_sync_reads_cursor_and_passes_oldest(tmp_path, mock_api):
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    out_dir.mkdir(parents=True)
    write_cursor(out_dir, "1705320720.000000")

    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    mock_api.get_messages.assert_called_once_with("C123", oldest="1705320720.000000")
    data = json.loads((out_dir / "messages.json").read_text())
    assert [m["ts"] for m in data] == ["1705320800.000000"]
    assert (out_dir / ".cursor").read_text().strip() == "1705320800.000000"


def test_sync_since_overrides_cursor(tmp_path, mock_api):
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    out_dir.mkdir(parents=True)
    write_cursor(out_dir, "1705320720.000000")

    run_sync(mock_api, "testteam", "C123", str(tmp_path), since="2024-02-01")
    call_args = mock_api.get_messages.call_args
    # since="2024-02-01" -> unix ts 1706745600; verify cursor was not used
    assert float(call_args[1]["oldest"]) == pytest.approx(1706745600.0, rel=1e-3)
    # Fixture message ts is before --since, so sync must not write it.
    assert not (out_dir / "messages.json").exists()
    mock_api.enrich.assert_not_called()


def test_sync_merges_with_existing(tmp_path, mock_api):
    existing = [
        {
            "ts": "1705320720.000000",
            "user": "U1",
            "user_name": "alice",
            "text": "old",
            "reactions": [],
            "thread": [],
        }
    ]
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    write_messages(out_dir, existing)

    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    data = json.loads((out_dir / "messages.json").read_text())
    assert len(data) == 2
    stats = json.loads((out_dir / "stats.json").read_text())
    assert stats["messages"] == 2


def test_sync_updates_cursor(tmp_path, mock_api):
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    out_dir.mkdir(parents=True)
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    assert (out_dir / ".cursor").read_text().strip() == "1705320800.000000"


def test_sync_heals_cursor_from_archive_when_no_new_messages(tmp_path, mock_api):
    """Empty sync still aligns .cursor to messages.json when the watermark drifted high."""
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    write_messages(
        out_dir,
        [
            {
                "ts": "1705320720.000000",
                "user_name": "alice",
                "text": "only",
                "reactions": [],
                "files": [],
                "thread": [],
            }
        ],
    )
    write_cursor(out_dir, "1705320999.000000")
    mock_api.get_messages.return_value = []
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    assert (out_dir / ".cursor").read_text().strip() == "1705320720.000000"


def test_thread_sync_heals_cursor_when_no_new_replies(tmp_path, mock_api):
    """Empty thread sync still aligns .cursor to thread.json when the watermark drifted high."""
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    thread_dir = out_dir / "thread_1705320700_000000"
    thread_dir.mkdir(parents=True)
    write_thread(
        thread_dir,
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
    write_cursor(thread_dir, "1705320999.000000")
    mock_api.get_replies.return_value = []
    run_sync(
        mock_api,
        "testteam",
        "https://testteam.slack.com/archives/C123/p1705320700000000",
        str(tmp_path),
        since=None,
    )
    assert (thread_dir / ".cursor").read_text().strip() == "1705320800.000000"


def test_sync_skips_inclusive_oldest_boundary(tmp_path, mock_api):
    """Slack oldest= is inclusive; channel sync must not re-merge the cursor msg."""
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    out_dir.mkdir(parents=True)
    write_cursor(out_dir, "1705320800.000000")
    write_messages(
        out_dir,
        [
            {
                "ts": "1705320800.000000",
                "user": "U2",
                "user_name": "bob",
                "text": "already stored",
                "reactions": [],
                "thread": [],
            }
        ],
    )
    # API returns only the inclusive boundary hit (no truly new messages)
    mock_api.get_messages.return_value = [
        {"ts": "1705320800.000000", "user": "U2", "text": "already stored", "reply_count": 0}
    ]
    mock_api.enrich.return_value = [
        {
            "ts": "1705320800.000000",
            "user": "U2",
            "user_name": "bob",
            "text": "should not overwrite",
            "reactions": [],
            "thread": [],
        }
    ]
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    mock_api.enrich.assert_not_called()
    data = json.loads((out_dir / "messages.json").read_text())
    assert data[0]["text"] == "already stored"
    assert (out_dir / ".cursor").read_text().strip() == "1705320800.000000"


def test_sync_no_cursor_fetches_all(tmp_path, mock_api):
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    mock_api.get_messages.assert_called_once_with("C123", oldest=None)
    data = json.loads((out_dir / "messages.json").read_text())
    assert [m["ts"] for m in data] == ["1705320800.000000"]
    assert (out_dir / ".cursor").read_text().strip() == "1705320800.000000"


def test_sync_prefetches_users_before_enrich(tmp_path, mock_api):
    order: list[str] = []
    users_ret = mock_api.fetch_workspace_users.return_value
    mock_api.fetch_workspace_users.side_effect = lambda: order.append("users") or users_ret
    enrich_ret = mock_api.enrich.return_value
    mock_api.enrich.side_effect = lambda *a, **k: order.append("enrich") or enrich_ret
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    assert order.index("users") < order.index("enrich")


def test_refresh_old_threads_picks_up_new_replies(tmp_path, mock_api):
    """_refresh_old_threads fetches and merges new replies for threads older than cursor."""
    from ssd.output import channel_dir, write_messages
    from ssd.sync import _refresh_old_threads

    # Set up a channel dir with one message that has an existing thread reply
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    existing = [
        {
            "ts": "1705320720.000000",
            "user": "U1",
            "user_name": "alice",
            "text": "top msg",
            "reactions": [],
            "files": [],
            "thread": [
                {
                    "ts": "1705320730.000000",
                    "user": "U2",
                    "user_name": "bob",
                    "text": "existing reply",
                    "reactions": [],
                    "files": [],
                }
            ],
        }
    ]
    write_messages(out_dir, existing)

    # Mock: one new reply after ts 1705320730
    new_raw_reply = {
        "ts": "1705320800.000000",
        "user": "U3",
        "text": "new reply",
        "reactions": [],
        "files": [],
    }
    mock_api.get_replies.return_value = [new_raw_reply]
    mock_api.enrich_reply.return_value = {
        "ts": "1705320800.000000",
        "user": "U3",
        "user_name": "carol",
        "text": "new reply",
        "reactions": [],
        "files": [],
    }

    _refresh_old_threads(mock_api, "C123", out_dir, "1705320750.000000")

    stored = json.loads((out_dir / "messages.json").read_text())
    thread = stored[0]["thread"]
    assert len(thread) == 2
    assert thread[-1]["ts"] == "1705320800.000000"
    assert thread[-1]["user_name"] == "carol"
    assert stored[0]["reply_count"] == 2
    assert stored[0]["latest_reply"] == "1705320800.000000"


def test_refresh_old_threads_includes_message_at_sync_floor(tmp_path, mock_api):
    """Cursor message is not re-enriched by exclusive history; refresh its threads."""
    from ssd.output import channel_dir, write_messages
    from ssd.sync import _refresh_old_threads

    floor = "1705320720.000000"
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    write_messages(
        out_dir,
        [
            {
                "ts": floor,
                "user": "U1",
                "user_name": "alice",
                "text": "cursor msg",
                "reactions": [],
                "files": [],
                "thread": [
                    {
                        "ts": "1705320730.000000",
                        "user": "U2",
                        "user_name": "bob",
                        "text": "existing",
                        "reactions": [],
                        "files": [],
                    }
                ],
            }
        ],
    )
    mock_api.get_replies.return_value = [
        {"ts": "1705320800.000000", "user": "U3", "text": "new", "reactions": [], "files": []}
    ]
    mock_api.enrich_reply.return_value = {
        "ts": "1705320800.000000",
        "user": "U3",
        "user_name": "carol",
        "text": "new",
        "reactions": [],
        "files": [],
    }
    _refresh_old_threads(mock_api, "C123", out_dir, floor)
    mock_api.get_replies.assert_called_once_with("C123", floor, oldest="1705320730.000000")
    stored = json.loads((out_dir / "messages.json").read_text())
    assert stored[0]["thread"][-1]["text"] == "new"


@pytest.mark.parametrize("raw", ["Jan 1 2024", "2024-01-01T12:00:00"])
def test_since_to_ts_invalid_raises(raw):
    with pytest.raises(ValueError, match="Invalid --since"):
        _since_to_ts(raw)


def test_since_to_ts_rejects_float_literals():
    for bad in ("inf", "nan", "1e10", "-1", "+1704067200"):
        with pytest.raises(ValueError, match="Invalid --since"):
            _since_to_ts(bad)


def test_since_to_ts_unix_and_ymd():
    assert _since_to_ts("1704067200") == "1704067200"
    assert _since_to_ts("1704067200.123456") == "1704067200.123456"
    assert _since_to_ts("2024-01-01") == "1704067200"


def test_refresh_old_threads_skips_messages_with_empty_thread(tmp_path, mock_api):
    """Empty thread and reply_count=0: no API call (new threads on old msgs stay undiscovered)."""
    from ssd.output import channel_dir, write_messages
    from ssd.sync import _refresh_old_threads

    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    # Message with empty thread
    write_messages(
        out_dir,
        [
            {
                "ts": "1705320720.000000",
                "user_name": "alice",
                "text": "top msg",
                "reactions": [],
                "files": [],
                "thread": [],  # no prior replies
            }
        ],
    )
    before = (out_dir / "messages.json").read_text()

    mock_api.get_replies.return_value = []
    _refresh_old_threads(mock_api, "C123", out_dir, "1705320750.000000")
    # get_replies should not have been called for an empty thread
    mock_api.get_replies.assert_not_called()
    assert (out_dir / "messages.json").read_text() == before


def test_refresh_old_threads_recovers_claimed_but_missing_replies(tmp_path, mock_api):
    """Parents that recorded reply_count but stored no replies are re-fetched."""
    from ssd.output import channel_dir, write_messages
    from ssd.sync import _refresh_old_threads

    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    write_messages(
        out_dir,
        [
            {
                "ts": "1705320720.000000",
                "user_name": "alice",
                "text": "top msg",
                "reactions": [],
                "files": [],
                "reply_count": 1,
                "thread": [],
            }
        ],
    )
    mock_api.get_replies.return_value = [
        {"ts": "1705320730.000000", "user": "U2", "text": "recovered", "reactions": [], "files": []}
    ]
    mock_api.enrich_reply.return_value = {
        "ts": "1705320730.000000",
        "user": "U2",
        "user_name": "bob",
        "text": "recovered",
        "reactions": [],
        "files": [],
    }
    _refresh_old_threads(mock_api, "C123", out_dir, "1705320750.000000")
    mock_api.get_replies.assert_called_once_with("C123", "1705320720.000000")
    stored = json.loads((out_dir / "messages.json").read_text())
    assert stored[0]["thread"][0]["text"] == "recovered"


def test_refresh_old_threads_isolates_per_thread_errors(tmp_path, mock_api):
    """A Slack API error on one thread does not discard updates from threads that succeeded."""
    from ssd.output import channel_dir, write_messages
    from ssd.sync import _refresh_old_threads

    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    write_messages(
        out_dir,
        [
            {
                "ts": "1705320700.000000",
                "user_name": "alice",
                "text": "first",
                "reactions": [],
                "files": [],
                "reply_count": 1,
                "thread": [
                    {
                        "ts": "1705320710.000000",
                        "user_name": "bob",
                        "text": "reply1",
                        "reactions": [],
                        "files": [],
                    }
                ],
            },
            {
                "ts": "1705320720.000000",
                "user_name": "carol",
                "text": "second",
                "reactions": [],
                "files": [],
                "reply_count": 1,
                "thread": [
                    {
                        "ts": "1705320725.000000",
                        "user_name": "dave",
                        "text": "reply2",
                        "reactions": [],
                        "files": [],
                    }
                ],
            },
        ],
    )

    new_reply = {
        "ts": "1705320715.000000",
        "user": "U3",
        "user_name": "eve",
        "text": "new reply to first",
        "reactions": [],
        "files": [],
    }

    call_count = 0

    def side_effect(channel_id, thread_ts, oldest=None):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            # First thread succeeds
            return [new_reply]
        raise RuntimeError("Slack API error")

    mock_api.get_replies.side_effect = side_effect
    mock_api.enrich_reply.return_value = new_reply

    # Should not raise even though the second thread fails
    _refresh_old_threads(mock_api, "C123", out_dir, "1705320750.000000")

    stored = json.loads((out_dir / "messages.json").read_text())
    # First thread's update was written despite the second thread's failure
    assert len(stored[0]["thread"]) == 2, "first thread should have the new reply"
    assert stored[1]["thread"][0]["text"] == "reply2", "second thread unchanged"


def test_thread_sync_uses_later_of_since_and_cursor(tmp_path, mock_api):
    """--since older than .cursor must not rewind the fetch floor."""
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    thread_dir = out_dir / "thread_1705320700_000000"
    thread_dir.mkdir(parents=True)
    write_cursor(thread_dir, "1705320800.000000")
    mock_api.get_replies.return_value = []
    run_sync(
        mock_api,
        "testteam",
        "https://testteam.slack.com/archives/C123/p1705320700000000",
        str(tmp_path),
        since="1700000000",
    )
    mock_api.get_replies.assert_called_once()
    assert mock_api.get_replies.call_args.kwargs["oldest"] == "1705320800.000000"


def test_thread_sync_since_keeps_missing_parent(tmp_path, mock_api):
    """First thread sync with --since still stores the parent message."""
    parent = {
        "ts": "1705320700.000000",
        "user": "U1",
        "text": "root",
        "reply_count": 1,
    }
    reply = {
        "ts": "1705320900.000000",
        "user": "U2",
        "text": "later",
        "thread_ts": "1705320700.000000",
    }
    mock_api.get_replies.return_value = [parent, reply]
    mock_api.enrich_reply.side_effect = lambda r, channel_id=None, team=None: {
        **r,
        "user_name": r["user"],
        "reactions": [],
        "files": [],
    }
    run_sync(
        mock_api,
        "testteam",
        "https://testteam.slack.com/archives/C123/p1705320700000000",
        str(tmp_path),
        since="1705320800",
    )
    kwargs = mock_api.get_replies.call_args.kwargs
    assert kwargs["include_parent"] is True
    assert kwargs["oldest"] == "1705320800"
    thread_dir = channel_dir(str(tmp_path), "testteam", "general", "C123") / (
        "thread_1705320700_000000"
    )
    data = json.loads((thread_dir / "thread.json").read_text())
    assert [m["ts"] for m in data] == ["1705320700.000000", "1705320900.000000"]
