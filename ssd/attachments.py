import requests
from pathlib import Path


def _download_file(url: str, dest: Path, size: int | None, token: str) -> str:
    """Download url to dest. Returns local_path on success, '' on failure.

    Skips re-download when size is known and the local file already matches.
    Size=None means Slack omitted the field; we always re-download in that case
    to avoid keeping corrupt partial files from interrupted runs.
    """
    if dest.exists() and size is not None and dest.stat().st_size == size:
        return str(dest)
    resp = requests.get(url, headers={"Authorization": f"Bearer {token}"}, stream=True)
    if resp.status_code == 200:
        with open(dest, "wb") as fh:
            for chunk in resp.iter_content(chunk_size=8192):
                fh.write(chunk)
        return str(dest)
    return ""  # failed; caller preserves original url in the url field


def _enrich_file(f: dict, ts: str, att_dir: Path, token: str) -> dict:
    """Resolve one Slack file object to an enriched dict with local_path set."""
    url = f.get("url_private_download") or f.get("url_private") or ""
    name = f.get("name") or f.get("title") or f.get("id") or "unknown"
    if not url:
        return {"name": name, "url": "", "local_path": "", "mimetype": f.get("mimetype", ""), "size": 0}
    att_dir.mkdir(parents=True, exist_ok=True)
    ts_prefix = ts.replace(".", "_")
    dest = att_dir / f"{ts_prefix}_{name}"
    local_path = _download_file(url, dest, f.get("size"), token)
    return {"name": name, "url": url, "local_path": local_path, "mimetype": f.get("mimetype", ""), "size": f.get("size") or 0}


def download_attachments(dir: Path, messages: list[dict], token: str) -> list[dict]:
    att_dir = dir / "attachments"
    result = []
    for msg in messages:
        updated = dict(msg)
        if msg.get("files"):
            updated["files"] = [_enrich_file(f, msg["ts"], att_dir, token) for f in msg["files"]]
        if msg.get("thread"):
            updated["thread"] = [
                {**reply, "files": [_enrich_file(f, reply["ts"], att_dir, token) for f in reply["files"]]}
                if reply.get("files") else reply
                for reply in msg["thread"]
            ]
        result.append(updated)
    return result
