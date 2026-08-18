"""TOML config load/store for tracked channels and threads."""

import contextlib
import dataclasses
import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import tomlkit


@dataclass
class Settings:
    output_dir: str = "./output"
    token_file: str = ".token"
    attachments: bool = False


@dataclass
class ChannelEntry:
    id: str
    name: str
    url: str
    since: str | None = None
    attachments: bool | None = None


@dataclass
class ThreadEntry:
    channel_id: str
    thread_ts: str
    url: str


@dataclass
class Config:
    settings: Settings = field(default_factory=Settings)
    channels: list[ChannelEntry] = field(default_factory=list)
    threads: list[ThreadEntry] = field(default_factory=list)


def _filter_fields(dc_class: type, raw: dict[str, Any]) -> dict[str, Any]:
    """Keep only keys that are valid fields of the dataclass, ignoring unknown TOML keys."""
    valid = {f.name for f in dataclasses.fields(dc_class)}
    return {k: v for k, v in raw.items() if k in valid}


def _toml_table(items: list[tuple[str, Any]], *, skip_none: bool = False) -> Any:
    table = tomlkit.table()
    for key, value in items:
        if skip_none and value is None:
            continue
        table.add(key, value)
    return table


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        doc = tomlkit.loads(path.read_text())
    except Exception as exc:
        raise RuntimeError(f"Cannot parse config {path}: {exc}") from exc
    settings = Settings(**_filter_fields(Settings, doc.get("settings", {})))
    channels = [
        ChannelEntry(**_filter_fields(ChannelEntry, dict(ch))) for ch in doc.get("channels", [])
    ]
    threads = [ThreadEntry(**_filter_fields(ThreadEntry, dict(t))) for t in doc.get("threads", [])]
    return Config(settings=settings, channels=channels, threads=threads)


def save_config(path: Path, config: Config) -> None:
    doc = tomlkit.document()
    doc.add(
        "settings",
        _toml_table(
            [
                ("output_dir", config.settings.output_dir),
                ("token_file", config.settings.token_file),
                ("attachments", config.settings.attachments),
            ]
        ),
    )
    if config.channels:
        aot = tomlkit.aot()
        for ch in config.channels:
            aot.append(
                _toml_table(
                    [
                        ("id", ch.id),
                        ("name", ch.name),
                        ("url", ch.url),
                        ("since", ch.since),
                        ("attachments", ch.attachments),
                    ],
                    skip_none=True,
                )
            )
        doc.add("channels", aot)
    if config.threads:
        aot = tomlkit.aot()
        for th in config.threads:
            aot.append(
                _toml_table(
                    [
                        ("channel_id", th.channel_id),
                        ("thread_ts", th.thread_ts),
                        ("url", th.url),
                    ]
                )
            )
        doc.add("threads", aot)
    content = tomlkit.dumps(doc)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".tmp_ssd_")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(content)
        os.replace(tmp, path)
    except Exception:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def add_channel(path: Path, *, id: str, name: str, url: str, since: str | None) -> None:
    cfg = load_config(path)
    for ch in cfg.channels:
        if ch.id == id:
            return  # idempotent
    cfg.channels.append(ChannelEntry(id=id, name=name, url=url, since=since))
    save_config(path, cfg)


def add_thread(path: Path, *, channel_id: str, thread_ts: str, url: str) -> None:
    cfg = load_config(path)
    for th in cfg.threads:
        if th.channel_id == channel_id and th.thread_ts == thread_ts:
            return
    cfg.threads.append(ThreadEntry(channel_id=channel_id, thread_ts=thread_ts, url=url))
    save_config(path, cfg)


def remove_entry(path: Path, channel_id: str, thread_ts: str | None = None) -> bool:
    cfg = load_config(path)
    orig_ch = len(cfg.channels)
    orig_th = len(cfg.threads)
    if thread_ts:
        cfg.threads = [
            t for t in cfg.threads if not (t.channel_id == channel_id and t.thread_ts == thread_ts)
        ]
    else:
        cfg.channels = [ch for ch in cfg.channels if ch.id != channel_id]
        cfg.threads = [t for t in cfg.threads if t.channel_id != channel_id]
    if len(cfg.channels) == orig_ch and len(cfg.threads) == orig_th:
        return False
    save_config(path, cfg)
    return True
