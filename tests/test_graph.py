import json
from pathlib import Path

from ssd.graph import _mention_candidates, build_graph, render_html


def _write_messages(dir: Path, messages: list[dict]) -> None:
    dir.mkdir(parents=True, exist_ok=True)
    (dir / "messages.json").write_text(json.dumps(messages))


def test_build_graph_nodes_from_messages(tmp_path):
    _write_messages(tmp_path, [
        {"ts": "1.0", "user_name": "alice", "text": "hello", "thread": [], "files": []},
        {"ts": "2.0", "user_name": "bob", "text": "world", "thread": [], "files": []},
    ])
    g = build_graph([tmp_path])
    ids = {n["id"] for n in g["nodes"]}
    assert ids == {"alice", "bob"}
    alice = next(n for n in g["nodes"] if n["id"] == "alice")
    assert alice["messages"] == 1


def test_build_graph_reply_edge(tmp_path):
    _write_messages(tmp_path, [
        {
            "ts": "1.0", "user_name": "alice", "text": "hi", "files": [],
            "thread": [
                {"ts": "1.1", "user_name": "bob", "text": "hey", "files": []},
            ],
        }
    ])
    g = build_graph([tmp_path])
    # bob replied to alice -> edge bob->alice
    links = {(lnk["source"], lnk["target"]): lnk["value"] for lnk in g["links"]}
    assert ("bob", "alice") in links


def test_build_graph_mention_edge(tmp_path):
    _write_messages(tmp_path, [
        {"ts": "1.0", "user_name": "alice", "text": "@bob thanks", "thread": [], "files": []},
        {"ts": "2.0", "user_name": "bob", "text": "sure", "thread": [], "files": []},
    ])
    g = build_graph([tmp_path])
    links = {(lnk["source"], lnk["target"]) for lnk in g["links"]}
    assert ("alice", "bob") in links


def test_build_graph_excludes_unknown(tmp_path):
    _write_messages(tmp_path, [
        {"ts": "1.0", "user_name": "unknown", "text": "bot msg", "thread": [], "files": []},
        {"ts": "2.0", "user_name": "alice", "text": "real msg", "thread": [], "files": []},
    ])
    g = build_graph([tmp_path])
    assert all(n["id"] != "unknown" for n in g["nodes"])


def test_build_graph_no_self_edges(tmp_path):
    _write_messages(tmp_path, [
        {
            "ts": "1.0", "user_name": "alice", "text": "@alice self mention",
            "thread": [], "files": [],
        },
    ])
    g = build_graph([tmp_path])
    assert not any(lnk["source"] == lnk["target"] for lnk in g["links"])


def test_build_graph_reads_thread_dumps(tmp_path):
    # Thread-only dump in thread_1_0/thread.json
    thread_dir = tmp_path / "thread_1_0"
    thread_dir.mkdir()
    (thread_dir / "thread.json").write_text(json.dumps([
        {"ts": "1.1", "user_name": "carol", "text": "thread reply", "files": []},
    ]))
    g = build_graph([tmp_path])
    ids = {n["id"] for n in g["nodes"]}
    assert "carol" in ids
    carol = next(n for n in g["nodes"] if n["id"] == "carol")
    assert carol["replies"] == 1


def test_render_html_escapes_script_injection(tmp_path):
    injection = "</script><script>alert(1)</script>"
    g = {
        "nodes": [{"id": injection, "messages": 1, "replies": 0}],
        "links": [],
        "channels": [],
    }
    html = render_html(g)
    assert "</script><script>" not in html
    assert "\\u003c/script\\u003e" in html or "\\u003c" in html


def test_mention_candidates_strips_punctuation():
    assert _mention_candidates("alice,") == ["alice,", "alice"]
    assert _mention_candidates("bob.") == ["bob.", "bob"]
    assert _mention_candidates("carol") == ["carol"]


def test_build_graph_missing_dir_skipped(tmp_path):
    missing = tmp_path / "nonexistent"
    g = build_graph([missing])
    assert g["nodes"] == []
    assert g["links"] == []
