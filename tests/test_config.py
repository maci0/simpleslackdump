import pytest
from pathlib import Path
from ssd.config import (
    load_config,
    add_channel,
    add_thread,
    remove_entry,
)


def test_load_missing_file_returns_empty(tmp_path):
    cfg = load_config(tmp_path / "ssd.toml")
    assert cfg.channels == []
    assert cfg.threads == []


def test_add_and_load_channel(tmp_path):
    p = tmp_path / "ssd.toml"
    add_channel(p, id="C123", name="general", url="https://x.slack.com/archives/C123", since=None)
    cfg = load_config(p)
    assert len(cfg.channels) == 1
    assert cfg.channels[0].id == "C123"
    assert cfg.channels[0].name == "general"
    assert cfg.channels[0].since is None


def test_add_channel_with_since(tmp_path):
    p = tmp_path / "ssd.toml"
    add_channel(p, id="C456", name="eng", url="https://x.slack.com/archives/C456", since="2024-01-01")
    cfg = load_config(p)
    assert cfg.channels[0].since == "2024-01-01"


def test_add_thread(tmp_path):
    p = tmp_path / "ssd.toml"
    add_thread(p, channel_id="C123", thread_ts="1234567890.123456",
               url="https://x.slack.com/archives/C123/p1234567890123456")
    cfg = load_config(p)
    assert len(cfg.threads) == 1
    assert cfg.threads[0].thread_ts == "1234567890.123456"


def test_remove_channel(tmp_path):
    p = tmp_path / "ssd.toml"
    add_channel(p, id="C123", name="general", url="https://...", since=None)
    add_channel(p, id="C456", name="random", url="https://...", since=None)
    removed = remove_entry(p, "C123")
    assert removed is True
    cfg = load_config(p)
    assert len(cfg.channels) == 1
    assert cfg.channels[0].id == "C456"


def test_remove_nonexistent(tmp_path):
    p = tmp_path / "ssd.toml"
    removed = remove_entry(p, "CNOPE")
    assert removed is False


def test_add_channel_idempotent(tmp_path):
    p = tmp_path / "ssd.toml"
    add_channel(p, id="C123", name="general", url="https://...", since=None)
    add_channel(p, id="C123", name="general", url="https://...", since=None)
    cfg = load_config(p)
    assert len(cfg.channels) == 1
