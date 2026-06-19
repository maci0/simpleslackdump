import hashlib
import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Optional
from urllib.parse import unquote

COOKIES_PATH = Path.home() / "Library/Application Support/Slack/Cookies"
LEVELDB_PATH = Path.home() / "Library/Application Support/Slack/Local Storage/leveldb"

# Regex for xoxc/d/s/p tokens in binary data (xoxc tokens in LevelDB use full hex hash)
_TOKEN_RE = re.compile(rb"(xox[cdsp]-[A-Za-z0-9\-]+)")
# Regex for URL-encoded cookie values (d cookie contains slashes encoded as %2F)
_COOKIE_RE = re.compile(rb"xox[a-z]+-[A-Za-z0-9%\-]+")


def _from_slack_cookies() -> Optional[str]:
    """Read plaintext d cookie from Slack's own SQLite Cookies file (older Slack/Electron)."""
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
    """Scan LevelDB .ldb/.log files for xoxc token (longest match wins)."""
    if not LEVELDB_PATH.exists():
        return None
    best: Optional[str] = None
    for path in sorted(LEVELDB_PATH.iterdir()):
        if path.suffix not in (".ldb", ".log"):
            continue
        try:
            data = path.read_bytes()
            for m in _TOKEN_RE.finditer(data):
                tok = m.group(1).decode()
                if best is None or len(tok) > len(best):
                    best = tok
        except Exception:
            continue
    return best


def _chrome_d_cookie() -> Optional[str]:
    """Decrypt the Slack 'd' cookie from Chrome's SQLite Cookies using Chrome Safe Storage key.

    Chrome encrypts cookies with AES-128-CBC using a PBKDF2-derived key stored in the
    macOS Keychain under 'Chrome Safe Storage'. The cookie value is URL-encoded in the
    plaintext and may contain %2F (/) and %2B (+) characters.
    """
    chrome_cookies = (
        Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies"
    )
    if not chrome_cookies.exists():
        return None
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key_raw = subprocess.run(
            ["security", "find-generic-password", "-w", "-s", "Chrome Safe Storage", "-a", "Chrome"],
            capture_output=True,
            timeout=5,
        ).stdout.strip()
        if not key_raw:
            return None

        conn = sqlite3.connect(f"file:{chrome_cookies}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT encrypted_value FROM cookies WHERE host_key = '.slack.com' AND name = 'd'"
        ).fetchone()
        conn.close()
        if not row:
            return None

        enc = bytes(row[0])
        if not enc.startswith(b"v10"):
            return None

        key = hashlib.pbkdf2_hmac("sha1", key_raw, b"saltysalt", 1003, 16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16), backend=default_backend())
        padded = cipher.decryptor().update(enc[3:]) + cipher.decryptor().finalize()
        pad_len = padded[-1]
        plain = padded[:-pad_len] if 1 <= pad_len <= 16 else padded

        m = _COOKIE_RE.search(plain)
        if m:
            return unquote(m.group(0).decode("ascii"))
    except Exception:
        pass
    return None


def extract_token() -> str:
    """Return the xoxc- client token from the Slack desktop app's LevelDB."""
    for method in (_from_slack_cookies, _from_leveldb, _from_raw_scan):
        result = method()
        if result:
            return result
    raise RuntimeError(
        "Could not extract Slack token. Is Slack installed and have you logged in?"
    )


def extract_cookie() -> Optional[str]:
    """Return the URL-decoded xoxd- cookie value needed alongside the xoxc- token.

    Newer Slack (Electron) requires both:
      Authorization: Bearer xoxc-...
      Cookie: d=<xoxd-...URL-encoded>

    This tries Chrome's cookie store first since Chrome's encryption is accessible.
    Returns the plaintext xoxd- value (with raw / and + chars), or None if unavailable.
    """
    return _chrome_d_cookie()
