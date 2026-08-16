import json


def test_help(invoke):
    result = invoke("--help")
    assert result.exit_code == 0
    assert "token" in result.output
    assert "dump" in result.output
    assert "sync" in result.output
    assert "add" in result.output
    assert "update" in result.output


def test_dump_help_has_all(invoke):
    result = invoke("dump", "--help")
    assert result.exit_code == 0
    assert "--all" in result.output
    assert "--dms" in result.output


def test_help_lists_query(invoke):
    result = invoke("--help")
    assert "query" in result.output


def test_query_stats(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"hi","reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "stats")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert data["channels"] == 1
    assert data["messages"] == 1


def test_query_search(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"hello dump",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "search", "hello")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["messages"]["total"] >= 1


def _query_dump(tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"hi",'
        '"reactions":[],"files":[{"id":"F1","name":"a.txt"}],"thread":[]}]',
        encoding="utf-8",
    )
    return ch


def test_query_users(invoke, tmp_path):
    _query_dump(tmp_path)
    result = invoke("--output", str(tmp_path), "query", "users")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ok"] is True
    assert any(u["id"] == "U1" for u in data["members"])


def test_query_users_info(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice","real_name":"Alice"}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "users", "alice")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["user"]["id"] == "U1"


def test_query_users_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice","email":"alice@acme.test"},'
        '"U2":{"id":"U2","handle":"bob","email":"bob@acme.test"}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "users", "--search", "alice")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [u["id"] for u in data["members"]] == ["U1"]


def test_query_files(invoke, tmp_path):
    _query_dump(tmp_path)
    result = invoke("--output", str(tmp_path), "query", "files")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {f["id"] for f in data["files"]} == {"F1"}


def test_query_files_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fa","name":"a.png","filetype":"png"},'
        '{"id":"Fb","name":"b.txt","filetype":"text"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "files", "--search", "a.png")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [f["id"] for f in data["files"]] == ["Fa"]


def test_query_files_by_name(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fext","name":"shared.png","filetype":"png"}]', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "files", "shared.png")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["file"]["id"] == "Fext"


def test_query_message(invoke, tmp_path):
    _query_dump(tmp_path)
    result = invoke("--output", str(tmp_path), "query", "message", "C123", "1.0")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["message"]["text"] == "hi"


def test_query_export(invoke, tmp_path):
    _query_dump(tmp_path)
    dest = tmp_path / "out.jsonl"
    result = invoke("--output", str(tmp_path), "query", "export", str(dest))
    assert result.exit_code == 0
    assert dest.is_file()
    assert int(result.output.strip()) >= 1


def test_query_emoji(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "emoji.json").write_text('{"shipit":"https://e.test/s.png"}', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "emoji")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["emoji"]["shipit"].startswith("https://")


def test_query_identity(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "auth.json").write_text(
        '{"user":"alice","user_id":"U1","team":"acme","team_id":"T1"}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "identity")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["user"]["id"] == "U1"


def test_query_pins(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "pins.json").write_text(
        '[{"type":"message","channel":"C123","message":{"ts":"9.0","text":"pin"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "pins", "C123")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["items"][0]["message"]["text"] == "pin"


def test_query_pins_info(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "pins.json").write_text(
        '[{"type":"message","channel":"C123","message":{"ts":"9.0","text":"pin"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "pins", "C123", "9.0")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["item"]["message"]["text"] == "pin"


def test_query_pins_search(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "pins.json").write_text(
        '[{"type":"message","channel":"C123","message":{"ts":"9.0","text":"pin"}},'
        '{"type":"message","channel":"C123","message":{"ts":"8.0","text":"other"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "pins", "--search", "pin")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [i["message"]["ts"] for i in data["items"]] == ["9.0"]


def test_query_scheduled(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "scheduled_messages.json").write_text(
        '[{"id":"Q1","channel_id":"C123","text":"later"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "scheduled")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["scheduled_messages"][0]["id"] == "Q1"


def test_query_scheduled_info(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "scheduled_messages.json").write_text(
        '[{"id":"Q1","channel_id":"C123","text":"later"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "scheduled", "Q1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["scheduled_message"]["text"] == "later"


def test_query_scheduled_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "scheduled_messages.json").write_text(
        '[{"id":"Q1","channel_id":"C123","text":"later"},'
        '{"id":"Q2","channel_id":"C123","text":"soon"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "scheduled", "--search", "lat")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["id"] for m in data["scheduled_messages"]] == ["Q1"]


def test_query_threads(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "threads.json").write_text(
        '[{"channel":"C123","thread_ts":"1.0","reply_count":2,"latest_reply":"1.2"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "threads", "C123")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["threads"][0]["thread_ts"] == "1.0"


def test_query_threads_info(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "threads.json").write_text(
        '[{"channel":"C123","thread_ts":"1.0","reply_count":2,"latest_reply":"1.2"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "threads", "C123", "1.0")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["thread"]["reply_count"] == 2


def test_query_threads_search(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "threads.json").write_text(
        '[{"channel":"C123","thread_ts":"1.0","reply_count":2},'
        '{"channel":"C123","thread_ts":"9.0","reply_count":4}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "threads", "--search", "9.0")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [t["thread_ts"] for t in data["threads"]] == ["9.0"]


def test_query_replies(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"root",'
        '"reactions":[],"files":[],"thread":[{"ts":"1.1","user":"U2",'
        '"user_name":"b","text":"reply","reactions":[],"files":[]}]}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "replies", "C123", "1.0")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]] == ["root", "reply"]


def test_query_replies_search(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"root",'
        '"reactions":[],"files":[],"thread":[{"ts":"1.1","user":"U2",'
        '"user_name":"b","text":"reply","reactions":[],"files":[]}]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "replies", "C123", "1.0", "--search", "reply"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]] == ["reply"]


def test_query_api(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"hi",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "api",
        "conversations.history",
        "channel=C123",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["messages"][0]["text"] == "hi"


def test_query_bookmarks(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "bookmarks.json").write_text('[{"id":"Bk1","title":"docs"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "bookmarks", "C123")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["bookmarks"][0]["id"] == "Bk1"


def test_query_bookmarks_all(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "bookmarks.json").write_text('[{"id":"Bk1","title":"docs"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "bookmarks")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["bookmarks"][0]["id"] == "Bk1"


def test_query_bookmarks_info(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "bookmarks.json").write_text('[{"id":"Bk1","title":"docs"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "bookmarks", "Bk1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["bookmark"]["title"] == "docs"


def test_query_bookmarks_search(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "bookmarks.json").write_text(
        '[{"id":"Bk1","title":"docs"},{"id":"Bk2","title":"wiki"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "bookmarks", "--search", "doc")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [b["id"] for b in data["bookmarks"]] == ["Bk1"]


def test_query_permalink(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"hi",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "permalink", "C123", "1.0")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "archives/C123/p10" in data["permalink"]


def test_query_stars(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "stars.json").write_text('[{"type":"message","channel":"C123"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "stars")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["items"][0]["channel"] == "C123"


def test_query_stars_info(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "stars.json").write_text(
        '[{"type":"message","channel":"C123","message":{"ts":"1.0","text":"starred"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "stars", "C123", "1.0")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["item"]["message"]["text"] == "starred"


def test_query_stars_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "stars.json").write_text(
        '[{"type":"message","channel":"C123","message":{"ts":"1.0","text":"starred"}},'
        '{"type":"message","channel":"C123","message":{"ts":"2.0","text":"other"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "stars", "--search", "star")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [i["message"]["ts"] for i in data["items"]] == ["1.0"]


def test_query_reminders(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "reminders.json").write_text('[{"id":"Rm1","text":"ping"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "reminders")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["reminders"][0]["id"] == "Rm1"


def test_query_reminders_info(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "reminders.json").write_text('[{"id":"Rm1","text":"ping"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "reminders", "Rm1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["reminder"]["text"] == "ping"


def test_query_reminders_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "reminders.json").write_text(
        '[{"id":"Rm1","text":"standup"},{"id":"Rm2","text":"lunch"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "reminders", "--search", "stand")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [r["id"] for r in data["reminders"]] == ["Rm1"]


def test_query_usergroups(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text('[{"id":"S1","handle":"eng"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "usergroups")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["usergroups"][0]["handle"] == "eng"


def test_query_usergroups_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng","name":"Engineering"},'
        '{"id":"S2","handle":"ops","name":"Ops"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "usergroups", "--search", "eng")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [g["id"] for g in data["usergroups"]] == ["S1"]


def test_query_usergroups_info(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng","name":"Engineering"}]', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "usergroups", "eng")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["usergroup"]["id"] == "S1"


def test_query_presence(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "auth.json").write_text('{"user_id":"U1"}', encoding="utf-8")
    (ws / "presence.json").write_text('{"U1":{"presence":"active"}}', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "presence")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["presence"] == "active"


def test_query_presence_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "presence.json").write_text(
        '{"U1":{"presence":"active"},"U2":{"presence":"away"}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "presence", "--search", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert list(data["users"]) == ["U1"]


def test_query_access_logs(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "access_logs.json").write_text('[{"user_id":"U1","ip":"9.9.9.9"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "access-logs")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["logins"][0]["ip"] == "9.9.9.9"


def test_query_access_logs_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "access_logs.json").write_text(
        '[{"user_id":"U1","ip":"1.1.1.1"},{"user_id":"U2","ip":"2.2.2.2"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "access-logs", "--search", "1.1.1.1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["user_id"] for row in data["logins"]] == ["U1"]


def test_query_team(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "team.json").write_text('{"id":"T9","name":"Acme Inc"}', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "team")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["team"]["id"] == "T9"


def test_query_bots(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","bot_id":"B1","username":"deploybot","text":"hi",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "bots", "B1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["bot"]["id"] == "B1"


def test_query_members(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "members.json").write_text('["U1","U2"]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "members", "C123")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["members"] == ["U1", "U2"]


def test_query_members_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "members.json").write_text('["U1","U2"]', encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice"},"U2":{"id":"U2","handle":"bob"}}',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "members", "C123", "--search", "alice"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["members"] == ["U1"]


def test_query_reactions(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"hi",'
        '"reactions":[{"name":"thumbsup","count":1,"users":["U2"]}],'
        '"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "reactions", "C123", "1.0")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["message"]["reactions"][0]["name"] == "thumbsup"


def test_query_reactions_search(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "reactions.json").write_text(
        '[{"type":"message","channel":"C123","reaction":"wave","user":"U2",'
        '"message":{"ts":"1.0","text":"hi"}},'
        '{"type":"message","channel":"C123","reaction":"fire","user":"U1",'
        '"message":{"ts":"2.0","text":"nope"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "reactions", "--search", "wave")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [i["reaction"] for i in data["items"]] == ["wave"]


def test_query_dnd(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "dnd.json").write_text('{"U1":{"dnd_enabled":true}}', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "dnd", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["dnd_enabled"] is True


def test_query_dnd_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "dnd.json").write_text(
        '{"U1":{"dnd_enabled":true},"U2":{"dnd_enabled":false}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "dnd", "--search", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert list(data["users"]) == ["U1"]


def test_query_comments(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"f",'
        '"reactions":[],"files":[{"id":"F1","name":"a.txt",'
        '"comments":[{"id":"Fc1","comment":"nice"},{"id":"Fc2","comment":"also"}]}],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "comments", "F1", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [c["id"] for c in data["comments"]] == ["Fc2"]


def test_query_comments_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"f",'
        '"reactions":[],"files":[{"id":"F1","name":"a.txt",'
        '"comments":[{"id":"Fc1","comment":"nice"},{"id":"Fc2","comment":"also"}]}],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "comments", "F1", "--search", "nice"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [c["id"] for c in data["comments"]] == ["Fc1"]


def test_query_email(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice","email":"alice@acme.test"}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "email", "alice@acme.test")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["user"]["id"] == "U1"


def test_query_files_info(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text('[{"id":"F1","name":"a.png"}]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "files-info", "F1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["file"]["name"] == "a.png"


def test_query_usergroup_users(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng","users":["U1","U2"]}]', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "usergroup-users", "eng")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["users"] == ["U1", "U2"]


def test_query_usergroup_users_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng","users":["U1","U2"]}]', encoding="utf-8"
    )
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice"},"U2":{"id":"U2","handle":"bob"}}',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "usergroup-users", "eng", "--search", "alice"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["users"] == ["U1"]


def test_query_usergroup_users_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng","users":["U1","U2"]}]', encoding="utf-8"
    )
    first = invoke(
        "--output", str(tmp_path), "query", "usergroup-users", "eng", "--limit", "1"
    )
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "usergroup-users",
        "eng",
        "--limit",
        "1",
        "--cursor",
        cursor,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["users"] == ["U2"]


def test_query_calls(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"h",'
        '"reactions":[],"files":[],"thread":[],'
        '"room":{"id":"R1","name":"standup"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "calls", "R1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["call"]["name"] == "standup"


def test_query_calls_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "calls.json").write_text(
        '[{"id":"R1","name":"standup"},{"id":"R2","name":"retro"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "calls", "--search", "stand")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [c["id"] for c in data["calls"]] == ["R1"]


def test_query_billable(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "billable_info.json").write_text(
        '{"U1":{"billing_active":true}}', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "billable")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["billable_info"]["U1"]["billing_active"] is True


def test_query_billable_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "billable_info.json").write_text(
        '{"U1":{"billing_active":true},"U2":{"billing_active":false}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "billable", "--search", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert list(data["billable_info"]) == ["U1"]


def test_query_integration_logs(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "integration_logs.json").write_text(
        '[{"user_id":"U1","service_id":"S1"}]', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "integration-logs")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["logs"][0]["service_id"] == "S1"


def test_query_integration_logs_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "integration_logs.json").write_text(
        '[{"user_id":"U1","service_id":"S1"},{"user_id":"U2","service_id":"S2"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "integration-logs", "--search", "S1"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["user_id"] for row in data["logs"]] == ["U1"]


def test_query_pins_all(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "pins.json").write_text(
        '[{"type":"message","channel":"C123","message":{"text":"pin"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "pins")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["items"][0]["message"]["text"] == "pin"


def test_query_participants(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"h",'
        '"reactions":[],"files":[],"thread":[],'
        '"room":{"id":"R1","participant_history":["U1"]}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "participants", "R1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["participants"][0]["slack_id"] == "U1"


def test_query_participants_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"h",'
        '"reactions":[],"files":[],"thread":[],'
        '"room":{"id":"R1","participant_history":["U1","U2"]}}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "participants", "R1", "--search", "U2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [p["slack_id"] for p in data["participants"]] == ["U2"]


def test_query_bots_list(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "bots.json").write_text(
        '{"B1":{"id":"B1","name":"deploybot"}}', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "bots")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["bots"][0]["id"] == "B1"


def test_query_bots_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "bots.json").write_text(
        '{"B1":{"id":"B1","name":"deploybot"},"B2":{"id":"B2","name":"alertbot"}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "bots", "--search", "deploy")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [b["id"] for b in data["bots"]] == ["B1"]


def test_query_remote_files(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fext","name":"drive.doc","is_external":true}]', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "remote-files")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["files"][0]["id"] == "Fext"


def test_query_remote_files_info(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "remote_files.json").write_text(
        '[{"id":"Fext","name":"drive.doc","is_external":true,"external_id":"g1"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "remote-files", "Fext")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["file"]["external_id"] == "g1"


def test_query_remote_files_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "remote_files.json").write_text(
        '[{"id":"Fext","name":"drive.doc","is_external":true,"external_id":"g1"},'
        '{"id":"Fother","name":"sheet.xls","is_external":true,"external_id":"g2"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "remote-files", "--search", "drive")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [f["id"] for f in data["files"]] == ["Fext"]


def test_query_files_user(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fa","name":"a.txt","user":"U1"},{"id":"Fb","name":"b.txt","user":"U2"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "files", "--user", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {f["id"] for f in data["files"]} == {"Fa"}


def test_query_access_logs_user(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "access_logs.json").write_text(
        '[{"user_id":"U1","ip":"1.1.1.1"},{"user_id":"U2","ip":"2.2.2.2"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "access-logs", "--user", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["ip"] for row in data["logins"]] == ["1.1.1.1"]


def test_query_integration_logs_change_type(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "integration_logs.json").write_text(
        '[{"user_id":"U1","service_id":"S1","change_type":"added"},'
        '{"user_id":"U2","service_id":"S2","change_type":"removed"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "integration-logs", "--change-type", "added"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["service_id"] for row in data["logs"]] == ["S1"]


def test_query_access_logs_after(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "access_logs.json").write_text(
        '[{"user_id":"U1","ip":"1.1.1.1","date_last":100},'
        '{"user_id":"U2","ip":"2.2.2.2","date_last":200}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "access-logs", "--after", "150")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["ip"] for row in data["logins"]] == ["2.2.2.2"]


def test_query_users_no_message_users(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U9","user_name":"dave","text":"hi",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice"}}', encoding="utf-8"
    )
    result = invoke(
        "--output", str(tmp_path), "query", "users", "--no-message-users"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {u["id"] for u in data["members"]} == {"U1"}


def test_query_files_ts_from(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fold","name":"old.txt","created":10},'
        '{"id":"Fnew","name":"new.txt","created":50}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "files", "--ts-from", "20", "--ts-to", "60"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {f["id"] for f in data["files"]} == {"Fnew"}


def test_query_channels_exclude_archived(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "conversations.json").write_text(
        '[{"id":"C123","name":"general","is_archived":false,"is_channel":true},'
        '{"id":"C999","name":"old","is_archived":true,"is_channel":true}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "channels", "--exclude-archived"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {c["id"] for c in data["channels"]} == {"C123"}


def test_query_history_oldest(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"old",'
        '"reactions":[],"files":[],"thread":[]},'
        '{"ts":"2.0","user":"U1","user_name":"a","text":"new",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "history", "C123", "--oldest", "1.5"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]] == ["new"]


def test_query_history_search(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"old",'
        '"reactions":[],"files":[],"thread":[]},'
        '{"ts":"2.0","user":"U1","user_name":"a","text":"new",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "history", "C123", "--search", "old"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]] == ["old"]


def test_query_history_cursor(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"a",'
        '"reactions":[],"files":[],"thread":[]},'
        '{"ts":"2.0","user":"U1","user_name":"a","text":"b",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "history",
        "C123",
        "--limit",
        "1",
        "--cursor",
        "1",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]] == ["a"]


def test_query_cursor(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / ".cursor").write_text("9.0", encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "cursor", "C123")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["ts"] == "9.0"


def test_query_cursors(invoke, tmp_path):
    a = tmp_path / "acme" / "general_C123"
    b = tmp_path / "acme" / "other_C999"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "messages.json").write_text("[]", encoding="utf-8")
    (b / "messages.json").write_text("[]", encoding="utf-8")
    (a / ".cursor").write_text("1.0", encoding="utf-8")
    (b / ".cursor").write_text("2.0", encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "cursor")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {r["channel"]: r["ts"] for r in data["cursors"]} == {"C123": "1.0", "C999": "2.0"}


def test_query_cursor_search(invoke, tmp_path):
    a = tmp_path / "acme" / "general_C123"
    b = tmp_path / "acme" / "other_C999"
    a.mkdir(parents=True)
    b.mkdir(parents=True)
    (a / "messages.json").write_text("[]", encoding="utf-8")
    (b / "messages.json").write_text("[]", encoding="utf-8")
    (a / ".cursor").write_text("1.0", encoding="utf-8")
    (b / ".cursor").write_text("2.0", encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "cursor", "--search", "C123")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [r["channel"] for r in data["cursors"]] == ["C123"]


def test_query_scheduled_oldest(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "scheduled_messages.json").write_text(
        '[{"id":"Q1","channel_id":"C123","post_at":10},'
        '{"id":"Q2","channel_id":"C123","post_at":50}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "scheduled", "--oldest", "20"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["id"] for m in data["scheduled_messages"]] == ["Q2"]


def test_query_reminders_no_complete(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "reminders.json").write_text(
        '[{"id":"Rm1","text":"open","complete_ts":0},'
        '{"id":"Rm2","text":"done","complete":true}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "reminders", "--no-complete"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [r["id"] for r in data["reminders"]] == ["Rm1"]


def test_query_users_no_bots(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice","is_bot":false},'
        '"UB":{"id":"UB","handle":"deploybot","is_bot":true}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "users", "--no-bots")
    assert result.exit_code == 0
    data = json.loads(result.output)
    ids = {u["id"] for u in data["members"]}
    assert "U1" in ids
    assert "UB" not in ids


def test_query_integration_logs_app_id(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "integration_logs.json").write_text(
        '[{"user_id":"U1","service_id":"S1","app_id":"A1"},'
        '{"user_id":"U2","service_id":"S2","app_id":"A2"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "integration-logs", "--app-id", "A1"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["service_id"] for row in data["logs"]] == ["S1"]


def test_query_calls_list(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"h",'
        '"reactions":[],"files":[],"thread":[],'
        '"room":{"id":"R1","name":"standup"}}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "calls")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {c["id"] for c in data["calls"]} == {"R1"}


def test_query_channels_types(invoke, tmp_path):
    ws = tmp_path / "acme"
    (ws / "general_C123").mkdir(parents=True)
    (ws / "general_C123" / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "alice_D111").mkdir(parents=True)
    (ws / "alice_D111" / "messages.json").write_text("[]", encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "channels", "--types", "im")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {c["id"] for c in data["channels"]} == {"D111"}


def test_query_usergroups_include_disabled(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng","date_delete":0},'
        '{"id":"S2","handle":"old","date_delete":9}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "usergroups", "--include-disabled"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [g["id"] for g in data["usergroups"]] == ["S1", "S2"]


def test_query_users_no_deleted(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice","deleted":false},'
        '"U2":{"id":"U2","handle":"gone","deleted":true}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "users", "--no-deleted")
    assert result.exit_code == 0
    data = json.loads(result.output)
    ids = {u["id"] for u in data["members"]}
    assert "U1" in ids
    assert "U2" not in ids


def test_query_convos(invoke, tmp_path):
    _query_dump(tmp_path)
    result = invoke("--output", str(tmp_path), "query", "convos", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert {c["id"] for c in data["channels"]} == {"C123"}


def test_query_search_page(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"2.0","user":"U1","user_name":"a","text":"hit two",'
        '"reactions":[],"files":[],"thread":[]},'
        '{"ts":"1.0","user":"U1","user_name":"a","text":"hit one",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "search", "hit", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]["matches"]] == ["hit one"]


def test_query_replies_oldest(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"root",'
        '"reactions":[],"files":[],"thread":[{"ts":"1.1","user":"U2",'
        '"user_name":"b","text":"reply","reactions":[],"files":[]}]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "replies", "C123", "1.0", "--oldest", "1.05"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]] == ["reply"]


def test_query_replies_cursor(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"root",'
        '"reactions":[],"files":[],"thread":[{"ts":"1.1","user":"U2",'
        '"user_name":"b","text":"reply","reactions":[],"files":[]}]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "replies",
        "C123",
        "1.0",
        "--limit",
        "1",
        "--cursor",
        "1",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]] == ["reply"]


def test_query_team_profile(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "team_profile.json").write_text(
        '{"fields":[{"id":"Xf1","label":"Title"}]}', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "team-profile")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["profile"]["fields"][0]["id"] == "Xf1"


def test_query_team_profile_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "team_profile.json").write_text(
        '{"fields":[{"id":"Xf1","label":"Title"},{"id":"Xf2","label":"Dept"}]}',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "team-profile", "--search", "Title"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [f["id"] for f in data["profile"]["fields"]] == ["Xf1"]


def test_query_profile(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice","email":"alice@acme.test"}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "profile", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["profile"]["email"] == "alice@acme.test"


def test_query_search_sort_dir(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"2.0","user":"U1","user_name":"a","text":"hit two",'
        '"reactions":[],"files":[],"thread":[]},'
        '{"ts":"1.0","user":"U1","user_name":"a","text":"hit one",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "search", "hit", "--sort-dir", "asc"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]["matches"]] == ["hit one", "hit two"]


def test_query_history_inclusive(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"old",'
        '"reactions":[],"files":[],"thread":[]},'
        '{"ts":"2.0","user":"U1","user_name":"a","text":"new",'
        '"reactions":[],"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "history",
        "C123",
        "--oldest",
        "1.0",
        "--inclusive",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["text"] for m in data["messages"]] == ["new", "old"]


def test_query_files_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fa","name":"a.txt"},{"id":"Fb","name":"b.txt"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "files", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [f["id"] for f in data["files"]] == ["Fb"]


def test_query_files_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fa","name":"a.txt"},{"id":"Fb","name":"b.txt"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "files",
        "--count",
        "1",
        "--cursor",
        "1",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [f["id"] for f in data["files"]] == ["Fb"]


def test_query_auth(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "auth.json").write_text(
        '{"user":"alice","user_id":"U1","team":"acme","team_id":"T1"}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "auth")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["user_id"] == "U1"
    assert data["team"] == "acme"


def test_query_usergroups_include_count(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng","users":["U1","U2"]}]', encoding="utf-8"
    )
    result = invoke(
        "--output", str(tmp_path), "query", "usergroups", "--include-count"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["usergroups"][0]["user_count"] == 2


def test_query_usergroups_no_users(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng","users":["U1","U2"]}]', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "usergroups", "--no-users")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert "users" not in data["usergroups"][0]


def test_query_migration(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice","display_name":"alice"}}',
        encoding="utf-8",
    )
    (ws / "auth.json").write_text('{"team_id":"T1","user_id":"U1"}', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "migration", "U1,U404")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["user_id_map"]["U1"] == "U1"
    assert "U404" in data["invalid_user_ids"]


def test_query_stars_channel(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "stars.json").write_text(
        '[{"type":"message","channel":"C123"},{"type":"message","channel":"C999"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "stars", "--channel", "C123")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [i["channel"] for i in data["items"]] == ["C123"]


def test_query_dnd_users(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "dnd.json").write_text(
        '{"U1":{"dnd_enabled":true},"U2":{"dnd_enabled":false}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "dnd", "--users", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert list(data["users"]) == ["U1"]
    assert data["users"]["U1"]["dnd_enabled"] is True


def test_query_remote_files_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fa","name":"a.doc","is_external":true},'
        '{"id":"Fb","name":"b.doc","is_external":true}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "remote-files", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [f["id"] for f in data["files"]] == ["Fb"]


def test_query_remote_files_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "files.json").write_text(
        '[{"id":"Fa","name":"a.doc","is_external":true},'
        '{"id":"Fb","name":"b.doc","is_external":true}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "remote-files",
        "--count",
        "1",
        "--cursor",
        "1",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [f["id"] for f in data["files"]] == ["Fb"]


def test_query_reminders_user(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "reminders.json").write_text(
        '[{"id":"Rm1","text":"mine","user":"U1"},{"id":"Rm2","text":"theirs","user":"U2"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "reminders", "--user", "U1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [r["id"] for r in data["reminders"]] == ["Rm1"]


def test_query_access_logs_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "access_logs.json").write_text(
        '[{"user_id":"U1","ip":"1.1.1.1"},{"user_id":"U2","ip":"2.2.2.2"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "access-logs", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["ip"] for row in data["logins"]] == ["2.2.2.2"]


def test_query_access_logs_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "access_logs.json").write_text(
        '[{"user_id":"U1","ip":"1.1.1.1"},{"user_id":"U2","ip":"2.2.2.2"}]',
        encoding="utf-8",
    )
    first = invoke("--output", str(tmp_path), "query", "access-logs", "--count", "1")
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "access-logs", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["ip"] for row in data["logins"]] == ["2.2.2.2"]


def test_query_members_limit(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "members.json").write_text('["U1","U2","U3"]', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "members", "C123", "--limit", "2")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["members"] == ["U1", "U2"]


def test_query_members_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "members.json").write_text('["U1","U2","U3"]', encoding="utf-8")
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "members",
        "C123",
        "--limit",
        "1",
        "--cursor",
        "1",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["members"] == ["U2"]


def test_query_pins_page(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "pins.json").write_text(
        '[{"type":"message","channel":"C123","message":{"text":"a"}},'
        '{"type":"message","channel":"C123","message":{"text":"b"}}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "pins", "C123", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [item["message"]["text"] for item in data["items"]] == ["b"]


def test_query_pins_cursor(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "pins.json").write_text(
        '[{"type":"message","channel":"C123","message":{"text":"a"}},'
        '{"type":"message","channel":"C123","message":{"text":"b"}}]',
        encoding="utf-8",
    )
    first = invoke(
        "--output", str(tmp_path), "query", "pins", "C123", "--count", "1"
    )
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "pins", "C123", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [item["message"]["text"] for item in data["items"]] == ["b"]


def test_query_prefs(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "team_preferences.json").write_text(
        '{"msg_edit_window_mins":"0"}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "prefs")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["prefs"]["msg_edit_window_mins"] == "0"


def test_query_prefs_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "team_preferences.json").write_text(
        '{"msg_edit_window_mins":"0","display_real_names":true}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "prefs", "--search", "msg_edit")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert list(data["prefs"]) == ["msg_edit_window_mins"]


def test_query_bots_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "bots.json").write_text(
        '{"B1":{"id":"B1","name":"alpha"},"B2":{"id":"B2","name":"beta"}}',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "bots", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [b["id"] for b in data["bots"]] == ["B2"]


def test_query_bots_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "bots.json").write_text(
        '{"B1":{"id":"B1","name":"alpha"},"B2":{"id":"B2","name":"beta"}}',
        encoding="utf-8",
    )
    first = invoke("--output", str(tmp_path), "query", "bots", "--count", "1")
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "bots", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [b["id"] for b in data["bots"]] == ["B2"]


def test_query_rtm(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "auth.json").write_text(
        '{"user_id":"U1","user":"alice","team_id":"T99","team":"Acme"}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "rtm")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["self"]["id"] == "U1"
    assert data["team"]["id"] == "T99"


def test_query_rtm_start(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "auth.json").write_text(
        '{"user_id":"U1","user":"alice","team_id":"T99","team":"Acme"}',
        encoding="utf-8",
    )
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice"}}', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "rtm", "--start")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["self"]["id"] == "U1"
    assert any(u["id"] == "U1" for u in data["users"])


def test_query_stars_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "stars.json").write_text(
        '[{"type":"message","channel":"C123","message":{"text":"a"}},'
        '{"type":"message","channel":"C123","message":{"text":"b"}}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "stars", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [item["message"]["text"] for item in data["items"]] == ["b"]


def test_query_stars_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "stars.json").write_text(
        '[{"type":"message","channel":"C123","message":{"text":"a"}},'
        '{"type":"message","channel":"C123","message":{"text":"b"}}]',
        encoding="utf-8",
    )
    first = invoke("--output", str(tmp_path), "query", "stars", "--count", "1")
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "stars", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [item["message"]["text"] for item in data["items"]] == ["b"]


def test_query_external_teams(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "external_teams.json").write_text(
        '[{"id":"E1","name":"Partner"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "external-teams")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["teams"][0]["id"] == "E1"


def test_query_external_teams_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "external_teams.json").write_text(
        '[{"id":"E1","name":"Partner"},{"id":"E2","name":"Other"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "external-teams", "--search", "part"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [t["id"] for t in data["teams"]] == ["E1"]


def test_query_bookmarks_page(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "bookmarks.json").write_text(
        '[{"id":"Bk1","title":"a"},{"id":"Bk2","title":"b"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "bookmarks",
        "C123",
        "--count",
        "1",
        "--page",
        "2",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [b["id"] for b in data["bookmarks"]] == ["Bk2"]


def test_query_bookmarks_cursor(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ch / "bookmarks.json").write_text(
        '[{"id":"Bk1","title":"a"},{"id":"Bk2","title":"b"}]',
        encoding="utf-8",
    )
    first = invoke(
        "--output", str(tmp_path), "query", "bookmarks", "C123", "--count", "1"
    )
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "bookmarks",
        "C123",
        "--count",
        "1",
        "--cursor",
        cursor,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [b["id"] for b in data["bookmarks"]] == ["Bk2"]


def test_query_scheduled_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "scheduled_messages.json").write_text(
        '[{"id":"Q1","text":"a"},{"id":"Q2","text":"b"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "scheduled", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["id"] for m in data["scheduled_messages"]] == ["Q2"]


def test_query_scheduled_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "scheduled_messages.json").write_text(
        '[{"id":"Q1","text":"a"},{"id":"Q2","text":"b"}]',
        encoding="utf-8",
    )
    first = invoke("--output", str(tmp_path), "query", "scheduled", "--count", "1")
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "scheduled", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [m["id"] for m in data["scheduled_messages"]] == ["Q2"]


def test_query_calls_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"h",'
        '"reactions":[],"files":[],"thread":[],'
        '"room":{"id":"R1","name":"a"}},'
        '{"ts":"2.0","user":"U1","user_name":"a","text":"h",'
        '"reactions":[],"files":[],"thread":[],'
        '"room":{"id":"R2","name":"b"}}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "calls", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [c["id"] for c in data["calls"]] == ["R2"]


def test_query_calls_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"h",'
        '"reactions":[],"files":[],"thread":[],'
        '"room":{"id":"R1","name":"a"}},'
        '{"ts":"2.0","user":"U1","user_name":"a","text":"h",'
        '"reactions":[],"files":[],"thread":[],'
        '"room":{"id":"R2","name":"b"}}]',
        encoding="utf-8",
    )
    first = invoke("--output", str(tmp_path), "query", "calls", "--count", "1")
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "calls", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [c["id"] for c in data["calls"]] == ["R2"]


def test_query_integration_logs_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "integration_logs.json").write_text(
        '[{"user_id":"U1","service_id":"S1"},{"user_id":"U2","service_id":"S2"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "integration-logs",
        "--count",
        "1",
        "--page",
        "2",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["service_id"] for row in data["logs"]] == ["S2"]


def test_query_integration_logs_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "integration_logs.json").write_text(
        '[{"user_id":"U1","service_id":"S1"},{"user_id":"U2","service_id":"S2"}]',
        encoding="utf-8",
    )
    first = invoke(
        "--output", str(tmp_path), "query", "integration-logs", "--count", "1"
    )
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "integration-logs",
        "--count",
        "1",
        "--cursor",
        cursor,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["service_id"] for row in data["logs"]] == ["S2"]


def test_query_reminders_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "reminders.json").write_text(
        '[{"id":"Rm1","text":"a"},{"id":"Rm2","text":"b"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "reminders", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [r["id"] for r in data["reminders"]] == ["Rm2"]


def test_query_reminders_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "reminders.json").write_text(
        '[{"id":"Rm1","text":"a"},{"id":"Rm2","text":"b"}]',
        encoding="utf-8",
    )
    first = invoke("--output", str(tmp_path), "query", "reminders", "--count", "1")
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "reminders", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [r["id"] for r in data["reminders"]] == ["Rm2"]


def test_query_usergroups_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng"},{"id":"S2","handle":"ops"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "usergroups", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [g["id"] for g in data["usergroups"]] == ["S2"]


def test_query_usergroups_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "usergroups.json").write_text(
        '[{"id":"S1","handle":"eng"},{"id":"S2","handle":"ops"}]',
        encoding="utf-8",
    )
    first = invoke("--output", str(tmp_path), "query", "usergroups", "--count", "1")
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "usergroups", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [g["id"] for g in data["usergroups"]] == ["S2"]


def test_query_presence_all(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "presence.json").write_text(
        '{"U8":{"presence":"active","online":true}}', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "presence", "--all")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["users"] == [{"user_id": "U8", "presence": "active", "online": True}]


def test_query_reactions_list(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"a",'
        '"reactions":[{"name":"a","count":1,"users":["U2"]}],'
        '"files":[],"thread":[]},'
        '{"ts":"2.0","user":"U1","user_name":"a","text":"b",'
        '"reactions":[{"name":"b","count":1,"users":["U2"]}],'
        '"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output", str(tmp_path), "query", "reactions", "--count", "1", "--page", "2"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["reaction"] for row in data["items"]] == ["b"]


def test_query_reactions_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text(
        '[{"ts":"1.0","user":"U1","user_name":"a","text":"a",'
        '"reactions":[{"name":"a","count":1,"users":["U2"]}],'
        '"files":[],"thread":[]},'
        '{"ts":"2.0","user":"U1","user_name":"a","text":"b",'
        '"reactions":[{"name":"b","count":1,"users":["U2"]}],'
        '"files":[],"thread":[]}]',
        encoding="utf-8",
    )
    first = invoke("--output", str(tmp_path), "query", "reactions", "--count", "1")
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output", str(tmp_path), "query", "reactions", "--count", "1", "--cursor", cursor
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["reaction"] for row in data["items"]] == ["b"]


def test_query_emoji_one(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "emoji.json").write_text('{"shipit":"https://e.test/s.png"}', encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "emoji", "shipit")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["emoji"]["shipit"].startswith("https://")


def test_query_emoji_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "emoji.json").write_text(
        '{"shipit":"https://e.test/s.png","wave":"alias:wave"}', encoding="utf-8"
    )
    result = invoke("--output", str(tmp_path), "query", "emoji", "--search", "ship")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [row["name"] for row in data["emoji"]] == ["shipit"]


def test_query_channels_limit(invoke, tmp_path):
    ws = tmp_path / "acme"
    (ws / "a_C1").mkdir(parents=True)
    (ws / "a_C1" / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "b_C2").mkdir(parents=True)
    (ws / "b_C2" / "messages.json").write_text("[]", encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "channels", "--limit", "1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data["channels"]) == 1


def test_query_channels_info(invoke, tmp_path):
    ch = tmp_path / "acme" / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "channels", "general")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert data["channel"]["id"] == "C123"


def test_query_channels_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    (ws / "general_C123").mkdir(parents=True)
    (ws / "general_C123" / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "random_C999").mkdir(parents=True)
    (ws / "random_C999" / "messages.json").write_text("[]", encoding="utf-8")
    result = invoke("--output", str(tmp_path), "query", "channels", "--search", "rand")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [c["id"] for c in data["channels"]] == ["C999"]


def test_query_users_limit(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "users.json").write_text(
        '{"U1":{"id":"U1","handle":"alice"},"U2":{"id":"U2","handle":"bob"}}',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "users", "--limit", "1")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data["members"]) == 1


def test_query_external_teams_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "external_teams.json").write_text(
        '[{"id":"E1","name":"A"},{"id":"E2","name":"B"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "external-teams",
        "--count",
        "1",
        "--page",
        "2",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [t["id"] for t in data["teams"]] == ["E2"]


def test_query_external_teams_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "external_teams.json").write_text(
        '[{"id":"E1","name":"A"},{"id":"E2","name":"B"}]',
        encoding="utf-8",
    )
    first = invoke(
        "--output", str(tmp_path), "query", "external-teams", "--count", "1"
    )
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "external-teams",
        "--count",
        "1",
        "--cursor",
        cursor,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [t["id"] for t in data["teams"]] == ["E2"]


def test_query_teams_page(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "teams.json").write_text(
        '[{"id":"T1","name":"A"},{"id":"T2","name":"B"}]',
        encoding="utf-8",
    )
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "teams",
        "--count",
        "1",
        "--page",
        "2",
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [t["id"] for t in data["teams"]] == ["T2"]


def test_query_teams_cursor(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "teams.json").write_text(
        '[{"id":"T1","name":"A"},{"id":"T2","name":"B"}]',
        encoding="utf-8",
    )
    first = invoke(
        "--output", str(tmp_path), "query", "teams", "--count", "1"
    )
    cursor = json.loads(first.output)["response_metadata"]["next_cursor"]
    result = invoke(
        "--output",
        str(tmp_path),
        "query",
        "teams",
        "--count",
        "1",
        "--cursor",
        cursor,
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [t["id"] for t in data["teams"]] == ["T2"]


def test_query_teams_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    ch = ws / "general_C123"
    ch.mkdir(parents=True)
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    (ws / "teams.json").write_text(
        '[{"id":"T1","name":"Acme"},{"id":"T2","name":"Other"}]',
        encoding="utf-8",
    )
    result = invoke("--output", str(tmp_path), "query", "teams", "--search", "acme")
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [t["id"] for t in data["teams"]] == ["T1"]


def test_query_convos_limit(invoke, tmp_path):
    ws = tmp_path / "acme"
    for cid in ("C123", "C456"):
        ch = ws / f"chan_{cid}"
        ch.mkdir(parents=True)
        (ch / "messages.json").write_text("[]", encoding="utf-8")
        (ch / "members.json").write_text('["U1"]', encoding="utf-8")
        (ch / "channel.json").write_text(
            f'{{"id":"{cid}","name":"chan","is_channel":true}}',
            encoding="utf-8",
        )
    result = invoke(
        "--output", str(tmp_path), "query", "convos", "U1", "--limit", "1"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert len(data["channels"]) == 1


def test_query_convos_search(invoke, tmp_path):
    ws = tmp_path / "acme"
    for cid, name in (("C123", "general"), ("C456", "ops")):
        ch = ws / f"{name}_{cid}"
        ch.mkdir(parents=True)
        (ch / "messages.json").write_text("[]", encoding="utf-8")
        (ch / "members.json").write_text('["U1"]', encoding="utf-8")
        (ch / "channel.json").write_text(
            f'{{"id":"{cid}","name":"{name}","is_channel":true}}',
            encoding="utf-8",
        )
    result = invoke(
        "--output", str(tmp_path), "query", "convos", "U1", "--search", "ops"
    )
    assert result.exit_code == 0
    data = json.loads(result.output)
    assert [c["id"] for c in data["channels"]] == ["C456"]
