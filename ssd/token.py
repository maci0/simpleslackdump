"""Extract Slack tokens and cookies from local Slack client installs."""

import contextlib
import hashlib
import importlib.util
import json
import re
import sqlite3
import subprocess
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote as _quote
from urllib.parse import unquote

COOKIES_PATH = Path.home() / "Library/Application Support/Slack/Cookies"
LEVELDB_PATH = Path.home() / "Library/Application Support/Slack/Local Storage/leveldb"

# Explicit allowlist: xoxb- (bot), xoxc- (client), xoxp- (user OAuth), xoxs- (session).
# xoxd- is excluded: session cookie, not a bearer token; see extract_cookie().
# Add new Slack token prefixes here when Slack introduces them.
_TOKEN_RE = re.compile(rb"(xox[bcps]-[A-Za-z0-9\-]+)")
# Regex for URL-encoded cookie values (d cookie contains slashes encoded as %2F)
_COOKIE_RE = re.compile(rb"xoxd-[A-Za-z0-9%\-]+")


def _prefer_longer(current: str | None, candidate: str) -> str:
    """Keep the longer token; Slack scans often find truncated siblings of the real value."""
    if current is None or len(candidate) > len(current):
        return candidate
    return current


def _from_slack_cookies() -> str | None:
    """Read plaintext d cookie from Slack's own SQLite Cookies file (older Slack/Electron)."""
    if not COOKIES_PATH.exists():
        return None
    try:
        with contextlib.closing(
            sqlite3.connect(f"file:{_quote(str(COOKIES_PATH), safe='/')}?mode=ro", uri=True)
        ) as conn:
            row = conn.execute(
                "SELECT value FROM cookies WHERE host_key = '.slack.com' AND name = 'd'"
            ).fetchone()
        if row and row[0]:
            return str(row[0])
    except Exception:
        pass
    return None


def _from_leveldb() -> str | None:
    if not LEVELDB_PATH.exists():
        return None
    try:
        import plyvel
    except ImportError:
        # Optional extra: uv sync --extra leveldb. Raw .ldb scan still works.
        return None
    try:
        db = plyvel.DB(str(LEVELDB_PATH))
        best: str | None = None
        try:
            for _, value in db:
                match = _TOKEN_RE.search(value)
                if match:
                    best = _prefer_longer(best, match.group(1).decode())
        finally:
            db.close()
        return best
    except Exception:
        pass
    return None


def _scan_ldb_dir(ldb_dir: Path) -> str | None:
    """Longest xox[bcps]- token found across all .ldb/.log files in a LevelDB dir."""
    best: str | None = None
    for path in sorted(ldb_dir.iterdir()):
        if path.suffix not in (".ldb", ".log"):
            continue
        try:
            data = path.read_bytes()
            for match in _TOKEN_RE.finditer(data):
                best = _prefer_longer(best, match.group(1).decode())
        except Exception:
            continue
    return best


def _from_raw_scan() -> str | None:
    """Scan Slack desktop app LevelDB for the longest xox[bcps]- token."""
    return _scan_ldb_dir(LEVELDB_PATH) if LEVELDB_PATH.exists() else None


def _from_chrome_storage() -> str | None:
    """Scan Chrome's Slack localStorage LevelDB for the longest xox[bcps]- token.

    Useful when the Slack desktop app isn't installed but Slack is open in Chrome.
    """
    chrome_ldb = (
        Path.home() / "Library/Application Support/Google/Chrome/Default/Local Storage/leveldb"
    )
    return _scan_ldb_dir(chrome_ldb) if chrome_ldb.exists() else None


def _unpad_pkcs7(padded: bytes) -> bytes | None:
    """Strip PKCS#7 padding; return None when the pad bytes are not well-formed."""
    if not padded:
        return None
    pad_len = padded[-1]
    if not (1 <= pad_len <= 16) or len(padded) < pad_len:
        return None
    if padded[-pad_len:] != bytes([pad_len]) * pad_len:
        return None
    return padded[:-pad_len]


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
        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    except ImportError:
        # Optional extra: uv sync --extra chrome (AES decrypt for Chrome cookies).
        return None
    try:
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
            check=False,
            timeout=5,
        ).stdout.strip()
        if not key_raw:
            return None

        db_url = f"file:{_quote(str(chrome_cookies), safe='/')}?mode=ro"
        with contextlib.closing(sqlite3.connect(db_url, uri=True)) as conn:
            row = conn.execute(
                "SELECT encrypted_value FROM cookies WHERE host_key = '.slack.com' AND name = 'd'"
            ).fetchone()
        if not row:
            return None

        encrypted = bytes(row[0])
        if not encrypted.startswith(b"v10"):
            return None

        key = hashlib.pbkdf2_hmac("sha1", key_raw, b"saltysalt", 1003, 16)
        cipher = Cipher(algorithms.AES(key), modes.CBC(b" " * 16))
        decryptor = cipher.decryptor()
        padded = decryptor.update(encrypted[3:]) + decryptor.finalize()
        plain = _unpad_pkcs7(padded)
        if plain is None:
            return None

        match = _COOKIE_RE.search(plain)
        if match:
            return unquote(match.group(0).decode("ascii"))
    except Exception:
        pass
    return None


def missing_optional_extras() -> list[str]:
    """Return optional extra names whose packages are not importable in this env."""
    missing: list[str] = []
    if importlib.util.find_spec("cryptography") is None:
        missing.append("chrome")
    if importlib.util.find_spec("plyvel") is None:
        missing.append("leveldb")
    return missing


def extract_token() -> str:
    """Return the xox[bcps]- bearer token from Slack desktop or Chrome LevelDB.

    Tries Slack app Local Storage (via plyvel), a raw LevelDB file scan, then
    Chrome's Slack localStorage. ``_from_slack_cookies`` is not used here: it
    returns xoxd- (a session cookie), not a bearer token. xoxd- is handled by
    ``extract_cookie()``.
    """
    for method in (_from_leveldb, _from_raw_scan, _from_chrome_storage):
        result = method()
        if result:
            return result
    raise RuntimeError(
        "Could not extract Slack token. Open Slack (desktop app or Chrome) and try again."
    )


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
            db_url = f"file:{_quote(str(cookies_db), safe='/')}?mode=ro"
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
        headers: dict[str, str] = {"Authorization": f"Bearer {token}"}
        if cookie:
            headers["Cookie"] = f"d={_quote(cookie, safe='')}"
        request = urllib.request.Request("https://slack.com/api/auth.test", headers=headers)
        resp = json.loads(urllib.request.urlopen(request, timeout=10).read())
        return bool(resp.get("ok"))
    except Exception:
        return False


def extract_cookie_with_validation(token: str, retries: int = 3, delay: float = 2.0) -> str | None:
    """Extract the xoxd- cookie and verify it authenticates against the Slack API.

    Chrome writes cookies to disk with a lag behind the in-memory session.
    This retries extraction up to `retries` times so stale on-disk values are
    not returned as valid.
    """
    for attempt in range(retries):
        cookie = extract_cookie()
        if cookie and validate_auth(token, cookie):
            return cookie
        if attempt < retries - 1:
            time.sleep(delay)
    return None
