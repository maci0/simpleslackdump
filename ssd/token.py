import contextlib
import hashlib
import re
import sqlite3
import subprocess
from pathlib import Path
from urllib.parse import unquote

COOKIES_PATH = Path.home() / "Library/Application Support/Slack/Cookies"
LEVELDB_PATH = Path.home() / "Library/Application Support/Slack/Local Storage/leveldb"

# Explicit allowlist: xoxb- (bot), xoxc- (client), xoxp- (user OAuth), xoxs- (session).
# xoxd- is excluded — it's a session cookie, not a bearer token; see extract_cookie().
# Add new Slack token prefixes here when Slack introduces them.
_TOKEN_RE = re.compile(rb"(xox[bcps]-[A-Za-z0-9\-]+)")
# Regex for URL-encoded cookie values (d cookie contains slashes encoded as %2F)
_COOKIE_RE = re.compile(rb"xoxd-[A-Za-z0-9%\-]+")


def _from_slack_cookies() -> str | None:
    """Read plaintext d cookie from Slack's own SQLite Cookies file (older Slack/Electron)."""
    if not COOKIES_PATH.exists():
        return None
    try:
        with contextlib.closing(sqlite3.connect(f"file:{COOKIES_PATH}?mode=ro", uri=True)) as conn:
            row = conn.execute(
                "SELECT value FROM cookies WHERE host_key = '.slack.com' AND name = 'd'"
            ).fetchone()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def _from_leveldb() -> str | None:
    if not LEVELDB_PATH.exists():
        return None
    try:
        import plyvel

        db = plyvel.DB(str(LEVELDB_PATH))
        best: str | None = None
        try:
            for _, value in db:
                m = _TOKEN_RE.search(value)
                if m:
                    tok = m.group(1).decode()
                    if best is None or len(tok) > len(best):
                        best = tok
        finally:
            db.close()
        return best
    except Exception:
        pass
    return None


def _from_raw_scan() -> str | None:
    """Scan LevelDB .ldb/.log files for xoxc token (longest match wins)."""
    if not LEVELDB_PATH.exists():
        return None
    best: str | None = None
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


def _chrome_d_cookie() -> str | None:
    """Decrypt the Slack 'd' cookie from Chrome's SQLite Cookies using Chrome Safe Storage key.

    Chrome encrypts cookies with AES-128-CBC using a PBKDF2-derived key stored in the
    macOS Keychain under 'Chrome Safe Storage'. The cookie value is URL-encoded in the
    plaintext and may contain %2F (/) and %2B (+) characters.
    """
    chrome_cookies = Path.home() / "Library/Application Support/Google/Chrome/Default/Cookies"
    if not chrome_cookies.exists():
        return None
    try:
        from cryptography.hazmat.backends import default_backend
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        key_raw = subprocess.run(
            [
                "security",
                "find-generic-password",
                "-w",
                "-s",
                "Chrome Safe Storage",
                "-a",
                "Chrome",
            ],
            capture_output=True,
            timeout=5,
        ).stdout.strip()
        if not key_raw:
            return None

        db_url = f"file:{chrome_cookies}?mode=ro"
        with contextlib.closing(sqlite3.connect(db_url, uri=True)) as conn:
            row = conn.execute(
                "SELECT encrypted_value FROM cookies WHERE host_key = '.slack.com' AND name = 'd'"
            ).fetchone()
        if not row:
            return None

        enc = bytes(row[0])
        if not enc.startswith(b"v10"):
            return None

        key = hashlib.pbkdf2_hmac("sha1", key_raw, b"saltysalt", 1003, 16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16), backend=default_backend())
        dec = cipher.decryptor()
        padded = dec.update(enc[3:]) + dec.finalize()
        pad_len = padded[-1]
        plain = padded[:-pad_len] if 1 <= pad_len <= 16 else padded

        m = _COOKIE_RE.search(plain)
        if m:
            return unquote(m.group(0).decode("ascii"))
    except Exception:
        pass
    return None


def extract_token() -> str:
    """Return the xoxc- client token from the Slack desktop app's LevelDB.

    _from_slack_cookies is NOT included here: it returns xoxd- (a session cookie),
    not the xoxc- bearer token. xoxd- is handled by extract_cookie().
    """
    for method in (_from_leveldb, _from_raw_scan):
        result = method()
        if result:
            return result
    raise RuntimeError("Could not extract Slack token. Is Slack installed and have you logged in?")


def _from_firefox_cookies() -> str | None:
    """Read the Slack 'd' cookie from Firefox's unencrypted cookies.sqlite.

    Firefox stores cookie values in plaintext in moz_cookies, unlike Chrome which
    encrypts them. Tries all profiles; returns first match.
    """
    profiles_dir = Path.home() / "Library/Application Support/Firefox/Profiles"
    if not profiles_dir.exists():
        return None
    for profile in profiles_dir.iterdir():
        if not profile.is_dir():
            continue
        cookies_db = profile / "cookies.sqlite"
        if not cookies_db.exists():
            continue
        try:
            db_url = f"file:{cookies_db}?mode=ro"
            with contextlib.closing(sqlite3.connect(db_url, uri=True)) as conn:
                row = conn.execute(
                    "SELECT value FROM moz_cookies WHERE host LIKE '%slack.com' AND name = 'd'"
                ).fetchone()
            if row and row[0]:
                val = row[0]
                return unquote(val) if "%" in val else val
        except Exception:
            continue
    return None


def extract_cookie() -> str | None:
    """Return the URL-decoded xoxd- cookie value needed alongside the xoxc- token.

    Newer Slack (Electron) requires both:
      Authorization: Bearer xoxc-...
      Cookie: d=<xoxd-...URL-encoded>

    Tries in order: Slack's own Cookies file (older Slack, plaintext),
    Firefox cookies.sqlite (plaintext), Chrome's encrypted cookie store.
    """
    for method in (_from_slack_cookies, _from_firefox_cookies, _chrome_d_cookie):
        result = method()
        if result:
            return result
    return None


def validate_auth(token: str, cookie: str | None) -> bool:
    """Return True if the token (and optional cookie) authenticate successfully."""
    try:
        import json as _json
        import urllib.request
        from urllib.parse import quote as _quote

        headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
        if cookie:
            headers["Cookie"] = f"d={_quote(cookie, safe='')}"
        req = urllib.request.Request("https://slack.com/api/auth.test", headers=headers)
        resp = _json.loads(urllib.request.urlopen(req, timeout=10).read())
        return bool(resp.get("ok"))
    except Exception:
        return False


def extract_cookie_with_validation(token: str, retries: int = 3, delay: float = 2.0) -> str | None:
    """Extract the xoxd- cookie and verify it authenticates against the Slack API.

    Chrome writes cookies to disk with a lag behind the in-memory session.
    This retries extraction up to `retries` times so stale on-disk values are
    not returned as valid.
    """
    import time

    for attempt in range(retries):
        cookie = extract_cookie()
        if cookie and validate_auth(token, cookie):
            return cookie
        if attempt < retries - 1:
            time.sleep(delay)
    return None
