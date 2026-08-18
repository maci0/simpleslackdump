"""Dump directory discovery (ssd.dumpload)."""

import json
from pathlib import Path

import pytest

from ssd.dumpload import (
    Loaded,
    build_word_bigrams,
    discover,
    docs_for_query,
    empty_loaded,
    is_channel_dir,
    kinds_for,
    read_channel_messages,
)


def test_kinds_for_prefixes() -> None:
    assert kinds_for("D123") == frozenset({"im"})
    assert kinds_for("G123") == frozenset({"mpim"})
    assert kinds_for("C123") == frozenset({"public_channel"})
    assert "private_channel" not in kinds_for("C123")


def test_kinds_for_meta_flags() -> None:
    assert kinds_for("C1", {"is_channel": True, "is_private": False}) == frozenset(
        {"public_channel"}
    )
    assert kinds_for("C2", {"is_channel": True, "is_private": True}) == frozenset(
        {"private_channel"}
    )
    assert kinds_for("G3", {"is_mpim": True, "is_group": True, "is_private": True}) == frozenset(
        {"mpim"}
    )
    assert kinds_for("G4", {"is_mpim": False, "is_group": True, "is_private": True}) == frozenset(
        {"private_channel"}
    )


def test_empty_loaded_defaults() -> None:
    loaded = empty_loaded()
    assert loaded.messages == []
    assert loaded.search_ready is False


def test_discover_channel_dir(tmp_path: Path) -> None:
    ch = tmp_path / "general_C123"
    ch.mkdir()
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    assert is_channel_dir(ch)
    found = discover(ch)
    assert set(found) == {"C123"}
    assert found["C123"].name == "general"
    assert found["C123"].path == ch


def test_discover_workspace_root(tmp_path: Path) -> None:
    ws = tmp_path / "myteam"
    ch = ws / "random_C999"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    found = discover(ws)
    assert set(found) == {"C999"}
    assert found["C999"].workspace == "myteam"


def test_read_channel_messages_parallel_false_day_files(tmp_path: Path) -> None:
    ch = tmp_path / "general_C123"
    ch.mkdir()
    (ch / "2024-01-01.json").write_text(
        json.dumps([{"ts": "2.0", "text": "b", "user": "U1"}]),
        encoding="utf-8",
    )
    (ch / "2024-01-02.json").write_text(
        json.dumps([{"ts": "1.0", "text": "a", "user": "U1"}]),
        encoding="utf-8",
    )
    msgs = read_channel_messages(ch, parallel=False)
    assert [m["ts"] for m in msgs] == ["1.0", "2.0"]


def test_docs_for_query_multi_token_intersects(tmp_path: Path) -> None:
    """Multi-word search must use inverted-index intersection, not full scan."""
    m1 = {"ts": "1.0", "text": "alpha beta gamma"}
    m2 = {"ts": "2.0", "text": "alpha only"}
    m3 = {"ts": "3.0", "text": "beta only"}
    docs = [
        ("alpha beta gamma", m1),
        ("alpha only", m2),
        ("beta only", m3),
    ]
    words = {
        "alpha": [0, 1],
        "beta": [0, 2],
        "gamma": [0],
        "only": [1, 2],
    }
    loaded = Loaded(
        [],
        {},
        {},
        {},
        {},
        {},
        {},
        None,
        [],
        docs,
        words,
        word_bigrams=build_word_bigrams(words),
        search_ready=True,
    )
    hits = docs_for_query(loaded, "alpha beta")
    assert [m["ts"] for _, m in hits] == ["1.0"]
    assert docs_for_query(loaded, "zzz missing") == []
    assert len(docs_for_query(loaded, "")) == 3


def test_docs_for_query_substring_token_via_bigrams() -> None:
    """Partial tokens must still hit longer vocabulary keys (e.g. run → running)."""
    m1 = {"ts": "1.0", "text": "still running tests"}
    m2 = {"ts": "2.0", "text": "walk only"}
    docs = [
        ("still running tests", m1),
        ("walk only", m2),
    ]
    words = {
        "still": [0],
        "running": [0],
        "tests": [0],
        "walk": [1],
        "only": [1],
    }
    loaded = Loaded(
        [],
        {},
        {},
        {},
        {},
        {},
        {},
        None,
        [],
        docs,
        words,
        word_bigrams=build_word_bigrams(words),
        search_ready=True,
    )
    hits = docs_for_query(loaded, "run")
    assert [m["ts"] for _, m in hits] == ["1.0"]


def test_read_channel_messages_from_messages_json(tmp_path: Path) -> None:
    ch = tmp_path / "general_C123"
    ch.mkdir()
    (ch / "messages.json").write_text(
        json.dumps([{"ts": "1.0", "text": "hello"}, {"ts": "2.0", "text": "world"}]),
        encoding="utf-8",
    )
    msgs = read_channel_messages(ch)
    assert [m["ts"] for m in msgs] == ["1.0", "2.0"]


@pytest.mark.parametrize("payload", ["{not-json", '{"not": "a list"}'])
def test_read_channel_messages_bad_json_returns_empty(tmp_path: Path, payload: str) -> None:
    ch = tmp_path / "general_C123"
    ch.mkdir()
    (ch / "messages.json").write_text(payload, encoding="utf-8")
    msgs = read_channel_messages(ch)
    assert msgs == []


def test_read_channel_messages_empty_dir_returns_empty(tmp_path: Path) -> None:
    ch = tmp_path / "general_C123"
    ch.mkdir()
    msgs = read_channel_messages(ch)
    assert msgs == []


def test_discover_output_root(tmp_path: Path) -> None:
    ws = tmp_path / "myteam"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    found = discover(tmp_path)
    assert set(found) == {"C123"}
    assert found["C123"].name == "general"
    assert found["C123"].workspace == "myteam"


def test_discover_thread_only_channel_dir(tmp_path: Path) -> None:
    """A named channel dir with only thread_* dumps is still a channel."""
    ch = tmp_path / "general_C123"
    thread = ch / "thread_1705320720_000000"
    thread.mkdir(parents=True)
    (thread / "thread.json").write_text("[]", encoding="utf-8")
    assert is_channel_dir(ch)
    found = discover(ch)
    assert set(found) == {"C123"}
    assert found["C123"].thread_dumps == {"1705320720.000000": thread / "thread.json"}
