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
            ts_prefix = msg["ts"].replace(".", "_").split("_")[0]
            filename = f"{ts_prefix}_{f['name']}"
            dest = att_dir / filename
            size = f.get("size", 0)
            if dest.exists() and dest.stat().st_size == size:
                local_path = str(dest)
            else:
                url = f["url_private_download"]
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
                    local_path = url  # fallback to original URL
            enriched_files.append(
                {
                    "name": f["name"],
                    "url": f["url_private_download"],
                    "local_path": local_path,
                    "mimetype": f.get("mimetype", ""),
                    "size": size,
                }
            )
        result.append({**msg, "files": enriched_files})
    return result
