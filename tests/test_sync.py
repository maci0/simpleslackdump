import json
from unittest.mock import MagicMock

import pytest

from ssd.output import channel_dir, write_cursor, write_messages
from ssd.sync import run_sync


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
    return api


def test_sync_reads_cursor_and_passes_oldest(tmp_path, mock_api):
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    out_dir.mkdir(parents=True)
    write_cursor(out_dir, "1705320720.000000")

    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    mock_api.get_messages.assert_called_once_with("C123", oldest="1705320720.000000")


def test_sync_since_overrides_cursor(tmp_path, mock_api):
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    out_dir.mkdir(parents=True)
    write_cursor(out_dir, "1705320720.000000")

    run_sync(mock_api, "testteam", "C123", str(tmp_path), since="2024-02-01")
    call_args = mock_api.get_messages.call_args
    assert call_args[1]["oldest"] != "1705320720.000000"


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


def test_sync_updates_cursor(tmp_path, mock_api):
    out_dir = channel_dir(str(tmp_path), "testteam", "general", "C123")
    out_dir.mkdir(parents=True)
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    assert (out_dir / ".cursor").read_text().strip() == "1705320800.000000"


def test_sync_no_cursor_fetches_all(tmp_path, mock_api):
    run_sync(mock_api, "testteam", "C123", str(tmp_path), since=None)
    mock_api.get_messages.assert_called_once_with("C123", oldest=None)
