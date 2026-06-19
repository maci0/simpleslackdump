import requests
from pathlib import Path


def download_attachments(dir: Path, messages: list[dict], token: str) -> list[dict]:
    att_dir = dir / "attachments"
    result = []
    for msg in messages:
        files = msg.get("files", [])
        if not files:
            result.append(msg)
            continue
        att_dir.mkdir(parents=True, exist_ok=True)
        enriched_files = []
        for f in files:
            # skip external/deleted files with no downloadable URL
            url = f.get("url_private_download") or f.get("url_private")
            name = f.get("name") or f.get("title") or f.get("id", "unknown")
            if not url:
                enriched_files.append({"name": name, "url": "", "local_path": "", "mimetype": f.get("mimetype", ""), "size": 0})
                continue
            ts_prefix = msg["ts"].replace(".", "_")
            filename = f"{ts_prefix}_{name}"
            dest = att_dir / filename
            size = f.get("size")  # None if Slack omits the field
            if dest.exists() and (size is None or dest.stat().st_size == size):
                local_path = str(dest)
            else:
                resp = requests.get(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    stream=True,
                )
                if resp.status_code == 200:
                    with open(dest, "wb") as fh:
                        for chunk in resp.iter_content(chunk_size=8192):
                            fh.write(chunk)
                    local_path = str(dest)
                else:
                    local_path = url
            enriched_files.append(
                {
                    "name": name,
                    "url": url,
                    "local_path": local_path,
                    "mimetype": f.get("mimetype", ""),
                    "size": size or 0,
                }
            )
        result.append({**msg, "files": enriched_files})
    return result
