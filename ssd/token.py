import re
import sqlite3
from pathlib import Path
from typing import Optional

COOKIES_PATH = Path.home() / "Library/Application Support/Slack/Cookies"
LEVELDB_PATH = Path.home() / "Library/Application Support/Slack/Local Storage/leveldb"

_TOKEN_RE = re.compile(rb"(xox[cdsp]-[A-Za-z0-9\-]+)")


def _from_cookies() -> Optional[str]:
    if not COOKIES_PATH.exists():
        return None
    try:
        conn = sqlite3.connect(f"file:{COOKIES_PATH}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT value FROM cookies WHERE host_key = '.slack.com' AND name = 'd'"
        ).fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def _from_leveldb() -> Optional[str]:
    if not LEVELDB_PATH.exists():
        return None
    try:
        import plyvel
        db = plyvel.DB(str(LEVELDB_PATH))
        for _, value in db:
            m = _TOKEN_RE.search(value)
            if m:
                db.close()
                return m.group(1).decode()
        db.close()
    except Exception:
        pass
    return None


def _from_raw_scan() -> Optional[str]:
    if not LEVELDB_PATH.exists():
        return None
    for path in LEVELDB_PATH.iterdir():
        if path.suffix not in (".ldb", ".log"):
            continue
        try:
            data = path.read_bytes()
            m = _TOKEN_RE.search(data)
            if m:
                return m.group(1).decode()
        except Exception:
            continue
    return None


def extract_token() -> str:
    for method in (_from_cookies, _from_leveldb, _from_raw_scan):
        result = method()
        if result:
            return result
    raise RuntimeError(
        "Could not extract Slack token. Is Slack installed and have you logged in?"
    )
