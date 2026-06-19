import pytest
from pathlib import Path
from unittest.mock import patch, MagicMock
from ssd.attachments import download_attachments


MESSAGES_WITH_FILE = [
    {
        "ts": "1705320720.000000",
        "user_name": "alice",
        "text": "see attached",
        "reactions": [],
        "thread": [],
        "files": [
            {
                "name": "report.pdf",
                "url_private_download": "https://files.slack.com/files/report.pdf",
                "mimetype": "application/pdf",
                "size": 1024,
            }
        ],
    }
]

MESSAGES_NO_FILE = [
    {
        "ts": "1705320720.000000",
        "user_name": "alice",
        "text": "hello",
        "reactions": [],
        "thread": [],
    }
]


def test_download_creates_attachments_dir(tmp_path):
    with patch("ssd.attachments.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200, iter_content=lambda chunk_size: [b"data"]
        )
        download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    assert (tmp_path / "attachments").is_dir()


def test_download_writes_file(tmp_path):
    with patch("ssd.attachments.requests.get") as mock_get:
        mock_get.return_value = MagicMock(
            status_code=200, iter_content=lambda chunk_size: [b"pdfdata"]
        )
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    # ts_prefix now uses full ts: 1705320720_000000_report.pdf
    expected = tmp_path / "attachments" / "1705320720_000000_report.pdf"
    assert expected.exists()
    assert result[0]["files"][0]["local_path"] == str(expected)


def test_download_skips_existing(tmp_path):
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    existing = att_dir / "1705320720_000000_report.pdf"
    existing.write_bytes(b"x" * 1024)  # same size as fixture

    with patch("ssd.attachments.requests.get") as mock_get:
        download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    mock_get.assert_not_called()


def test_download_noop_for_messages_without_files(tmp_path):
    with patch("ssd.attachments.requests.get") as mock_get:
        result = download_attachments(tmp_path, MESSAGES_NO_FILE, "xoxd-fake")
    mock_get.assert_not_called()
    assert result == MESSAGES_NO_FILE
