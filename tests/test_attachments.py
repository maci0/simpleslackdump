from unittest.mock import MagicMock, patch
from urllib.error import HTTPError, URLError

import pytest

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


class _FakeResponse:
    """Minimal urlopen response: context manager + chunked ``read``."""

    def __init__(self, body: bytes = b"", *, boom_after: bytes | None = None):
        self._chunks: list[bytes | BaseException] = []
        if boom_after is not None:
            self._chunks.append(boom_after)
            self._chunks.append(OSError("disk full"))
        elif body:
            self._chunks.append(body)
        self._idx = 0

    def read(self, size: int = -1) -> bytes:
        del size  # urlopen callers pass a chunk size; we yield whole buffered chunks
        if self._idx >= len(self._chunks):
            return b""
        item = self._chunks[self._idx]
        self._idx += 1
        if isinstance(item, BaseException):
            raise item
        return item

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *args: object) -> None:
        return None


def _patch_urlopen(body: bytes = b"data", **kwargs: object):
    return patch(
        "ssd.attachments._urlopen",
        return_value=_FakeResponse(body, **kwargs),
    )


def test_download_creates_attachments_dir(tmp_path):
    with _patch_urlopen(b"data") as mock_get:
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    mock_get.assert_called_once()
    assert (tmp_path / "attachments").is_dir()
    expected = tmp_path / "attachments" / "1705320720_000000_report.pdf"
    assert expected.read_bytes() == b"data"
    assert result[0]["files"][0]["local_path"] == "attachments/1705320720_000000_report.pdf"


def test_download_writes_file(tmp_path):
    with _patch_urlopen(b"pdfdata"):
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    # ts_prefix now uses full ts: 1705320720_000000_report.pdf
    expected = tmp_path / "attachments" / "1705320720_000000_report.pdf"
    assert expected.exists()
    assert expected.read_bytes() == b"pdfdata"
    assert result[0]["files"][0]["local_path"] == "attachments/1705320720_000000_report.pdf"


def test_download_skips_existing(tmp_path):
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    existing = att_dir / "1705320720_000000_report.pdf"
    existing.write_bytes(b"x" * 1024)  # same size as fixture

    with patch("ssd.attachments._urlopen") as mock_get:
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    mock_get.assert_not_called()
    assert existing.read_bytes() == b"x" * 1024
    assert result[0]["files"][0]["local_path"] == "attachments/1705320720_000000_report.pdf"


def test_download_redownloads_when_size_mismatches(tmp_path):
    """Known size that does not match the on-disk file must force a re-download."""
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    existing = att_dir / "1705320720_000000_report.pdf"
    existing.write_bytes(b"stale")

    with _patch_urlopen(b"fresh") as mock_get:
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    mock_get.assert_called_once()
    assert existing.read_bytes() == b"fresh"
    assert result[0]["files"][0]["local_path"] == "attachments/1705320720_000000_report.pdf"


def test_download_redownloads_when_size_omitted(tmp_path):
    """Size=None means Slack omitted size; always re-download to avoid stale partials."""
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    existing = att_dir / "1705320720_000000_report.pdf"
    existing.write_bytes(b"x" * 1024)

    messages = [
        {
            **MESSAGES_WITH_FILE[0],
            "files": [{**MESSAGES_WITH_FILE[0]["files"][0], "size": None}],
        }
    ]
    with _patch_urlopen(b"refetched") as mock_get:
        result = download_attachments(tmp_path, messages, "xoxd-fake")
    mock_get.assert_called_once()
    assert existing.read_bytes() == b"refetched"
    assert result[0]["files"][0]["local_path"] == "attachments/1705320720_000000_report.pdf"


def test_download_noop_for_messages_without_files(tmp_path):
    with patch("ssd.attachments._urlopen") as mock_get:
        result = download_attachments(tmp_path, MESSAGES_NO_FILE, "xoxd-fake")
    mock_get.assert_not_called()
    assert result == MESSAGES_NO_FILE


def test_safe_name_strips_dotdot():
    from ssd.attachments import _safe_name

    # / → _; each ".." → "__"; no residual traversal or separators.
    assert _safe_name("../../etc/passwd") == "______etc_passwd"
    assert ".." not in _safe_name("../../etc/passwd")
    assert "/" not in _safe_name("../../etc/passwd")


def test_safe_name_null_byte():
    from ssd.attachments import _safe_name

    assert _safe_name("file\x00name.pdf") == "file_name.pdf"


def test_safe_name_single_dot_returns_file():
    from ssd.attachments import _safe_name

    # A lone "." becomes empty after lstrip("."), so "file" is returned
    assert _safe_name(".") == "file"


def test_safe_name_normal_name_unchanged():
    from ssd.attachments import _safe_name

    assert _safe_name("report.pdf") == "report.pdf"


def test_download_preserves_file_id(tmp_path):
    messages = [
        {
            "ts": "1705320720.000000",
            "user_name": "alice",
            "text": "see attached",
            "reactions": [],
            "thread": [],
            "files": [
                {
                    "id": "F123ABC",
                    "name": "report.pdf",
                    "url_private_download": "https://files.slack.com/files/report.pdf",
                    "mimetype": "application/pdf",
                    "size": 1024,
                    "user": "U001",
                }
            ],
        }
    ]
    with _patch_urlopen(b"pdfdata"):
        result = download_attachments(tmp_path, messages, "xoxd-fake")
    f = result[0]["files"][0]
    assert f["id"] == "F123ABC"
    assert f["user"] == "U001"
    assert f["local_path"]


def test_download_unlink_on_write_error(tmp_path):
    """A mid-write OSError must not leave a truncated file that looks complete."""
    with _patch_urlopen(boom_after=b"partial"):
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    target = tmp_path / "attachments" / "1705320720_000000_report.pdf"
    assert not target.exists()
    assert not (tmp_path / "attachments" / "1705320720_000000_report.pdf.tmp").exists()
    assert result[0]["files"][0]["local_path"] == ""


def test_download_same_name_files_get_distinct_paths(tmp_path):
    """Parallel workers must not share one on-disk path for same-named files."""
    messages = [
        {
            "ts": "1705320720.000000",
            "user_name": "alice",
            "text": "two reports",
            "reactions": [],
            "thread": [],
            "files": [
                {
                    "id": "F111",
                    "name": "report.pdf",
                    "url_private_download": "https://files.slack.com/files/a.pdf",
                    "mimetype": "application/pdf",
                    "size": 1,
                },
                {
                    "id": "F222",
                    "name": "report.pdf",
                    "url_private_download": "https://files.slack.com/files/b.pdf",
                    "mimetype": "application/pdf",
                    "size": 1,
                },
            ],
        }
    ]
    bodies = {
        "https://files.slack.com/files/a.pdf": b"A",
        "https://files.slack.com/files/b.pdf": b"B",
    }

    def fake_urlopen(req, timeout=None):
        url = req.full_url if hasattr(req, "full_url") else req
        return _FakeResponse(bodies[url])

    with patch("ssd.attachments._urlopen", side_effect=fake_urlopen):
        result = download_attachments(tmp_path, messages, "xoxd-fake")

    paths = [f["local_path"] for f in result[0]["files"]]
    assert paths[0] != paths[1]
    assert len(set(paths)) == 2
    data = {(tmp_path / p).read_bytes() for p in paths}
    assert data == {b"A", b"B"}


def test_download_rerequests_when_size_unknown(tmp_path):
    """size=None means Slack omitted the field; always re-download to avoid stale partials."""
    messages = [
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
                    # no 'size' key → f.get("size") is None
                }
            ],
        }
    ]
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    target = att_dir / "1705320720_000000_report.pdf"
    target.write_bytes(b"stale")

    with _patch_urlopen(b"fresh") as mock_get:
        download_attachments(tmp_path, messages, "xoxd-fake")

    mock_get.assert_called_once()
    assert target.read_bytes() == b"fresh"


def test_download_network_timeout_returns_empty_local_path(tmp_path, capsys):
    """Timeout during GET must not crash; local_path stays empty."""
    with patch("ssd.attachments._urlopen") as mock_get:
        mock_get.side_effect = TimeoutError("timed out")
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    assert result[0]["files"][0]["local_path"] == ""
    err = capsys.readouterr().err
    assert "attachment download failed" in err
    assert "timed out" in err


def test_download_connection_error_returns_empty_local_path(tmp_path, capsys):
    """URLError during GET must not crash; local_path stays empty."""
    with patch("ssd.attachments._urlopen") as mock_get:
        mock_get.side_effect = URLError("no route to host")
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    assert result[0]["files"][0]["local_path"] == ""
    assert "attachment download failed" in capsys.readouterr().err


def test_download_non_200_status_returns_empty_local_path(tmp_path, capsys):
    """HTTP 403/404 from Slack must not crash; local_path stays empty."""
    with patch("ssd.attachments._urlopen") as mock_get:
        mock_get.side_effect = HTTPError(
            "https://files.slack.com/files/report.pdf",
            403,
            "Forbidden",
            hdrs=MagicMock(),
            fp=None,
        )
        result = download_attachments(tmp_path, MESSAGES_WITH_FILE, "xoxd-fake")
    assert result[0]["files"][0]["local_path"] == ""
    assert "HTTP 403" in capsys.readouterr().err


def test_download_rejects_non_slack_url(tmp_path, capsys):
    """Crafted url_private must not trigger a request (SSRF / token exfil)."""
    messages = [
        {
            "ts": "1705320720.000000",
            "user_name": "alice",
            "text": "see attached",
            "reactions": [],
            "thread": [],
            "files": [
                {
                    "name": "pwn.pdf",
                    "url_private_download": "https://evil.example/steal",
                    "mimetype": "application/pdf",
                    "size": 10,
                }
            ],
        }
    ]
    with patch("ssd.attachments._urlopen") as mock_get:
        result = download_attachments(tmp_path, messages, "xoxc-secret-token")
    mock_get.assert_not_called()
    assert result[0]["files"][0]["local_path"] == ""
    assert "non-Slack URL" in capsys.readouterr().err


def test_is_slack_file_url_allowlist():
    from ssd.attachments import _is_slack_file_url

    assert _is_slack_file_url("https://files.slack.com/files-pri/T-F/download/x")
    assert _is_slack_file_url("https://a.slack-edge.com/path")
    assert not _is_slack_file_url("http://files.slack.com/x")
    assert not _is_slack_file_url("https://evil.com/files.slack.com/x")
    assert not _is_slack_file_url("https://files.slack.com.evil.com/x")
    assert not _is_slack_file_url("https://169.254.169.254/")


def test_redirect_to_non_slack_host_blocked():
    """Cross-host redirects must not forward the bearer token off Slack."""
    from email.message import Message

    from ssd.attachments import _SlackHostRedirectHandler

    handler = _SlackHostRedirectHandler()
    req = __import__("urllib.request", fromlist=["Request"]).Request(
        "https://files.slack.com/files/x",
        headers={"Authorization": "Bearer xoxc-secret"},
    )
    headers = Message()
    headers["Location"] = "https://evil.example/steal"
    with pytest.raises(HTTPError) as exc:
        handler.redirect_request(
            req,
            fp=None,
            code=302,
            msg="Found",
            headers=headers,
            newurl="https://evil.example/steal",
        )
    assert "redirect blocked" in str(exc.value.reason)


def test_redirect_to_slack_host_allowed():
    from email.message import Message

    from ssd.attachments import _SlackHostRedirectHandler

    handler = _SlackHostRedirectHandler()
    req = __import__("urllib.request", fromlist=["Request"]).Request(
        "https://files.slack.com/files/x",
        headers={"Authorization": "Bearer xoxc-secret"},
    )
    headers = Message()
    new = handler.redirect_request(
        req,
        fp=None,
        code=302,
        msg="Found",
        headers=headers,
        newurl="https://files.slack.com/files/y",
    )
    assert new is not None
    assert new.full_url == "https://files.slack.com/files/y"
    assert new.get_header("Authorization") == "Bearer xoxc-secret"


def test_download_skips_existing_when_size_is_string(tmp_path):
    """Slack-ish string sizes must still match on-disk length for the skip path."""
    att_dir = tmp_path / "attachments"
    att_dir.mkdir()
    existing = att_dir / "1705320720_000000_report.pdf"
    existing.write_bytes(b"x" * 1024)
    messages = [
        {
            **MESSAGES_WITH_FILE[0],
            "files": [{**MESSAGES_WITH_FILE[0]["files"][0], "size": "1024"}],
        }
    ]
    with patch("ssd.attachments._urlopen") as mock_get:
        result = download_attachments(tmp_path, messages, "xoxd-fake")
    mock_get.assert_not_called()
    assert result[0]["files"][0]["local_path"] == "attachments/1705320720_000000_report.pdf"
    assert result[0]["files"][0]["size"] == 1024


def test_download_rejects_body_over_declared_size(tmp_path, capsys):
    """A response longer than the declared size must not be kept on disk."""
    messages = [
        {
            **MESSAGES_WITH_FILE[0],
            "files": [{**MESSAGES_WITH_FILE[0]["files"][0], "size": 4}],
        }
    ]
    with _patch_urlopen(b"too-long"):
        result = download_attachments(tmp_path, messages, "xoxd-fake")
    target = tmp_path / "attachments" / "1705320720_000000_report.pdf"
    assert not target.exists()
    assert result[0]["files"][0]["local_path"] == ""
    assert "byte limit" in capsys.readouterr().err
