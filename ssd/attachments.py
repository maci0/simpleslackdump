"""Download private Slack file attachments into a dump directory."""

import contextlib
import os
import re as _re
import sys
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

# Single timeout for private file downloads (seconds). urllib has no
# separate connect/read pair; 70s covers a slow connect plus a large body.
_DOWNLOAD_TIMEOUT = 70

# Read and write in 256 KiB chunks; hard-cap body size so a runaway response
# cannot fill the disk even when Slack omitted Content-Length / size.
_DOWNLOAD_CHUNK_BYTES = 262_144
_DOWNLOAD_BYTES_MAX = 512 * 1024 * 1024

# Cap parallel fetches so a channel with hundreds of files does not open
# unbounded sockets against Slack (network-bound; amortizes RTT).
_DOWNLOAD_WORKERS = min(8, max(2, (os.cpu_count() or 4)))

# Only fetch private files from Slack hosts so a crafted url_private cannot SSRF
# or exfiltrate the bearer token to an arbitrary server.
_SLACK_HOST_SUFFIXES = (".slack.com", ".slack-edge.com")
_SAFE_NAME_RE = _re.compile(r"[/\\:\x00\r\n]")


def _safe_name(name: str) -> str:
    """Strip path separators, colons, null bytes, newlines, and relative-path escape sequences."""
    safe = _SAFE_NAME_RE.sub("_", name)
    safe = safe.replace("..", "__")
    return safe.lstrip(".") or "file"


def _is_slack_file_url(url: str) -> bool:
    """Return True when url is https to a Slack file/CDN host."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    return any(host == sfx.lstrip(".") or host.endswith(sfx) for sfx in _SLACK_HOST_SUFFIXES)


class _SlackHostRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only while the target stays on an allowlisted Slack host.

    stdlib urllib copies Authorization onto the next request. Without this check,
    a Slack URL that 302s to an attacker host would exfiltrate the bearer token.
    """

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: Any,
        code: int,
        msg: str,
        headers: Any,
        newurl: str,
    ) -> urllib.request.Request | None:
        if not _is_slack_file_url(newurl):
            raise urllib.error.HTTPError(
                req.full_url,
                code,
                f"redirect blocked (non-Slack host): {newurl}",
                headers,
                fp,
            )
        return super().redirect_request(req, fp, code, msg, headers, newurl)


# Opener used for attachment GETs so redirect policy applies on every worker.
_OPENER = urllib.request.build_opener(_SlackHostRedirectHandler)


def _urlopen(req: urllib.request.Request, timeout: float) -> Any:
    return _OPENER.open(req, timeout=timeout)


def _coerce_size(size: Any) -> int | None:
    """Return a non-negative byte count, or None when size is absent/unusable."""
    if size is None:
        return None
    try:
        n = int(size)
    except (TypeError, ValueError):
        return None
    if n < 0:
        return None
    return n


def _attachment_basename(ts: str, f: dict[str, Any], *, uniq: str = "") -> str:
    """Build a dump filename for one Slack file under a message ts."""
    raw_name = f.get("name") or f.get("title") or f.get("id") or "unknown"
    name = _safe_name(str(raw_name))
    ts_prefix = ts.replace(".", "_")
    if uniq:
        return f"{ts_prefix}_{_safe_name(uniq)}_{name}"
    return f"{ts_prefix}_{name}"


def _unique_attachment_targets(
    att_dir: Path, jobs: list[tuple[dict[str, Any], dict[str, Any], str]]
) -> list[Path]:
    """Assign one filesystem path per job so parallel workers never share a file.

    Prefer the legacy ``{ts}_{name}`` form when free so existing dumps keep
    skipping re-downloads. On collision, fall back to file id, then job index.
    """
    used: set[str] = set()
    targets: list[Path] = []
    for i, (_slot, f, ts) in enumerate(jobs):
        fid = str(f.get("id") or "")
        candidates = [_attachment_basename(ts, f)]
        if fid:
            candidates.append(_attachment_basename(ts, f, uniq=fid))
        candidates.append(_attachment_basename(ts, f, uniq=str(i)))
        chosen = next(c for c in candidates if c not in used)
        used.add(chosen)
        targets.append(att_dir / chosen)
    return targets


def _download_file(url: str, target: Path, size: int | None, token: str) -> bool:
    """Download url to target. Returns True on success, False on failure.

    Skips re-download when size is known and the local file already matches.
    Size=None means Slack omitted the field; we always re-download in that case
    to avoid keeping corrupt partial files from interrupted runs.

    Bytes are written to a sibling ``.tmp`` then ``os.replace``d into place so a
    parallel worker (or crash) cannot leave a half-written final path.
    """
    if not _is_slack_file_url(url):
        print(f"  attachment skipped (non-Slack URL): {target.name}", file=sys.stderr, flush=True)
        return False
    declared = _coerce_size(size)
    if target.exists() and declared is not None and target.stat().st_size == declared:
        return True
    # Timeout so a stalled host cannot hang the dump forever.
    request = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    tmp = target.with_name(target.name + ".tmp")
    # Prefer the declared size as the ceiling when known; always hard-cap.
    byte_limit = (
        min(_DOWNLOAD_BYTES_MAX, declared) if declared is not None else _DOWNLOAD_BYTES_MAX
    )
    assert byte_limit >= 0
    try:
        with (
            _urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as resp,
            open(tmp, "wb") as fh,
        ):
            written = 0
            while True:
                chunk = resp.read(_DOWNLOAD_CHUNK_BYTES)
                if not chunk:
                    break
                if written + len(chunk) > byte_limit:
                    raise OSError(
                        f"download exceeded {byte_limit} byte limit for {target.name}"
                    )
                fh.write(chunk)
                written += len(chunk)
        os.replace(tmp, target)
        return True
    except urllib.error.HTTPError as exc:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        print(
            f"  attachment download failed ({target.name}): HTTP {exc.code}",
            file=sys.stderr,
            flush=True,
        )
        return False  # failed; caller preserves original url in the url field
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        # Drop a truncated body so the next run does not treat it as complete.
        # Covers mid-stream network errors and local write failures (disk full).
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)
        with contextlib.suppress(OSError):
            target.unlink(missing_ok=True)
        # URLError wraps the underlying reason; surface that for clearer logs.
        detail = exc.reason if isinstance(exc, urllib.error.URLError) else exc
        print(
            f"  attachment download failed ({target.name}): {detail}",
            file=sys.stderr,
            flush=True,
        )
        return False


def _enrich_file(
    f: dict[str, Any],
    att_dir: Path,
    token: str,
    *,
    target: Path,
) -> dict[str, Any]:
    """Resolve one Slack file object; ``local_path`` is dump-relative when set."""
    url = f.get("url_private_download") or f.get("url_private") or ""
    raw_name = f.get("name") or f.get("title") or f.get("id") or "unknown"
    name = _safe_name(str(raw_name))
    out = dict(f)
    out["name"] = name
    out["mimetype"] = f.get("mimetype", "")
    if not url:
        out["url"] = ""
        out["local_path"] = ""
        out["size"] = 0
        return out
    att_dir.mkdir(parents=True, exist_ok=True)
    out["url"] = url
    # Store dump-relative paths so archives stay portable across machines.
    out["local_path"] = (
        str(target.relative_to(att_dir.parent))
        if _download_file(url, target, _coerce_size(f.get("size")), token)
        else ""
    )
    out["size"] = _coerce_size(f.get("size")) or 0
    return out


def download_attachments(
    out_dir: Path, messages: list[dict[str, Any]], token: str
) -> list[dict[str, Any]]:
    """Download file attachments, parallelizing HTTP GETs across the batch.

    Message order and nested thread structure are preserved; only the network
    fetches run concurrently (bounded by ``_DOWNLOAD_WORKERS``). Each job gets a
    distinct on-disk path so same-named files in one message cannot corrupt each
    other under the thread pool.
    """
    att_dir = out_dir / "attachments"
    result: list[dict[str, Any]] = []
    # (slot dict to fill, raw file object, message ts)
    jobs: list[tuple[dict[str, Any], dict[str, Any], str]] = []

    for msg in messages:
        updated = dict(msg)
        if msg.get("files"):
            files_out: list[dict[str, Any]] = []
            for f in msg["files"]:
                slot: dict[str, Any] = {}
                files_out.append(slot)
                jobs.append((slot, f, msg["ts"]))
            updated["files"] = files_out
        if msg.get("thread"):
            thread_out: list[dict[str, Any]] = []
            for reply in msg["thread"]:
                reply_out = {**reply}
                reply_files: list[dict[str, Any]] = []
                for f in reply.get("files", []):
                    slot = {}
                    reply_files.append(slot)
                    jobs.append((slot, f, reply["ts"]))
                reply_out["files"] = reply_files
                thread_out.append(reply_out)
            updated["thread"] = thread_out
        result.append(updated)

    if not jobs:
        return result

    att_dir.mkdir(parents=True, exist_ok=True)
    targets = _unique_attachment_targets(att_dir, jobs)

    def _run(item: tuple[tuple[dict[str, Any], dict[str, Any], str], Path]) -> None:
        (slot, f, _ts), target = item
        slot.update(_enrich_file(f, att_dir, token, target=target))

    paired = list(zip(jobs, targets, strict=True))
    if len(paired) == 1:
        _run(paired[0])
    else:
        workers = min(_DOWNLOAD_WORKERS, len(paired))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            list(pool.map(_run, paired))
    return result
