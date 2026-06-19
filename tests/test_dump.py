import pytest
from unittest.mock import MagicMock, patch
from pathlib import Path
import json
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


def test_run_dump_cursor_is_latest_ts(tmp_path, mock_api):
    run_dump(mock_api, "testteam", "C123", str(tmp_path))
    out_dir = tmp_path / "testteam" / "general_C123"
    cursor = (out_dir / ".cursor").read_text().strip()
    assert cursor == "1705320720.000000"


def test_run_dump_resolves_channel_name(tmp_path, mock_api):
    run_dump(mock_api, "testteam", "#general", str(tmp_path))
    mock_api.resolve_channel.assert_called_once_with("general")


def test_run_dump_thread_url(tmp_path, mock_api):
    """Thread-only dump uses get_replies + enrich_reply, not get_messages."""
    raw_reply = {"ts": "1.1", "user": "U2", "text": "reply", "reactions": [], "files": []}
    mock_api.get_replies.return_value = [raw_reply]
    mock_api.enrich_reply.return_value = {
        "ts": "1.1", "user": "U2", "user_name": "bob",
        "text": "reply", "reactions": [], "files": [],
    }
    run_dump(
        mock_api,
        "testteam",
        "https://testteam.slack.com/archives/C123/p1705320720000000",
        str(tmp_path),
    )
    mock_api.get_replies.assert_called_once()
    mock_api.enrich_reply.assert_called_once_with(raw_reply)
