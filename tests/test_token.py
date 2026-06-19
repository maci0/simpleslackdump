import sqlite3
import pytest
from pathlib import Path
import ssd.token as token_mod
from ssd.token import extract_token


def _make_cookies_db(path: Path, value: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)"
    )
    conn.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?)",
        (".slack.com", "d", value, b""),
    )
    conn.commit()
    conn.close()


def test_extract_cookie_from_slack_cookies_sqlite(tmp_path, monkeypatch):
    """_from_slack_cookies provides the xoxd- cookie, not the xoxc- token."""
    from ssd.token import extract_cookie
    db = tmp_path / "Cookies"
    _make_cookies_db(db, "xoxd-test-token-abc123")
    monkeypatch.setattr(token_mod, "COOKIES_PATH", db)
    # patch Chrome path to non-existent so it falls through to Slack cookies
    monkeypatch.setattr(token_mod, "_chrome_d_cookie", lambda: None)
    assert extract_cookie() == "xoxd-test-token-abc123"


def test_extract_skips_empty_value(tmp_path, monkeypatch):
    """Falls through to next method when value column is empty (encrypted cookies)."""
    db = tmp_path / "Cookies"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE cookies (host_key TEXT, name TEXT, value TEXT, encrypted_value BLOB)"
    )
    conn.execute(
        "INSERT INTO cookies VALUES (?, ?, ?, ?)",
        (".slack.com", "d", "", b"\x00\x00"),
    )
    conn.commit()
    conn.close()

    ldb_dir = tmp_path / "leveldb"
    ldb_dir.mkdir()
    # plant a token in a fake .log file (raw byte scan fallback)
    (ldb_dir / "000001.log").write_bytes(b"\x00xoxc-fake-token-456\x00")
    monkeypatch.setattr(token_mod, "COOKIES_PATH", db)
    monkeypatch.setattr(token_mod, "LEVELDB_PATH", ldb_dir)

    assert extract_token() == "xoxc-fake-token-456"


def test_extract_raw_byte_scan(tmp_path, monkeypatch):
    """Raw byte scan finds token in .ldb file."""
    db = tmp_path / "Cookies"
    db.touch()  # empty — no cookies table, sqlite will raise, caught internally

    ldb_dir = tmp_path / "leveldb"
    ldb_dir.mkdir()
    (ldb_dir / "MANIFEST-000001").write_bytes(b"garbage")
    (ldb_dir / "000002.ldb").write_bytes(b"stuff xoxs-real-token-789 more stuff")
    monkeypatch.setattr(token_mod, "COOKIES_PATH", db)
    monkeypatch.setattr(token_mod, "LEVELDB_PATH", ldb_dir)

    assert extract_token() == "xoxs-real-token-789"


def test_extract_raises_when_nothing_found(tmp_path, monkeypatch):
    db = tmp_path / "Cookies"
    db.touch()
    ldb_dir = tmp_path / "leveldb"
    ldb_dir.mkdir()
    (ldb_dir / "000001.log").write_bytes(b"no token here")
    monkeypatch.setattr(token_mod, "COOKIES_PATH", db)
    monkeypatch.setattr(token_mod, "LEVELDB_PATH", ldb_dir)

    with pytest.raises(RuntimeError, match="Could not extract"):
        extract_token()
