import dataclasses
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional
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
    since: Optional[str] = None
    attachments: Optional[bool] = None


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


def _filter_fields(dc_class, raw: dict) -> dict:
    """Keep only keys that are valid fields of the dataclass, ignoring unknown TOML keys."""
    valid = {f.name for f in dataclasses.fields(dc_class)}
    return {k: v for k, v in raw.items() if k in valid}


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    doc = tomlkit.loads(path.read_text())
    settings = Settings(**_filter_fields(Settings, doc.get("settings", {})))
    channels = [
        ChannelEntry(**_filter_fields(ChannelEntry, dict(ch)))
        for ch in doc.get("channels", [])
    ]
    threads = [
        ThreadEntry(**_filter_fields(ThreadEntry, dict(t)))
        for t in doc.get("threads", [])
    ]
    return Config(settings=settings, channels=channels, threads=threads)


def save_config(path: Path, config: Config) -> None:
    doc = tomlkit.document()
    s = tomlkit.table()
    s.add("output_dir", config.settings.output_dir)
    s.add("token_file", config.settings.token_file)
    s.add("attachments", config.settings.attachments)
    doc.add("settings", s)
    if config.channels:
        aot = tomlkit.aot()
        for ch in config.channels:
            t = tomlkit.table()
            t.add("id", ch.id)
            t.add("name", ch.name)
            t.add("url", ch.url)
            if ch.since:
                t.add("since", ch.since)
            if ch.attachments is not None:
                t.add("attachments", ch.attachments)
            aot.append(t)
        doc.add("channels", aot)
    if config.threads:
        aot = tomlkit.aot()
        for th in config.threads:
            t = tomlkit.table()
            t.add("channel_id", th.channel_id)
            t.add("thread_ts", th.thread_ts)
            t.add("url", th.url)
            aot.append(t)
        doc.add("threads", aot)
    path.write_text(tomlkit.dumps(doc))


def add_channel(
    path: Path, *, id: str, name: str, url: str, since: Optional[str]
) -> None:
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


def remove_entry(path: Path, channel_id: str, thread_ts: Optional[str] = None) -> bool:
    cfg = load_config(path)
    orig_ch = len(cfg.channels)
    orig_th = len(cfg.threads)
    if thread_ts:
        # remove specific thread only
        cfg.threads = [t for t in cfg.threads if not (t.channel_id == channel_id and t.thread_ts == thread_ts)]
    else:
        # remove channel + all its threads
        cfg.channels = [ch for ch in cfg.channels if ch.id != channel_id]
        cfg.threads = [t for t in cfg.threads if t.channel_id != channel_id]
    if len(cfg.channels) == orig_ch and len(cfg.threads) == orig_th:
        return False
    save_config(path, cfg)
    return True
