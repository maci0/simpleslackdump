import pytest

from ssd.parser import dir_rank, is_slack_id, parse_target, ts_from_thread_dir, ts_key


def test_ts_key_preserves_microsecond_ordering():
    assert ts_key("1735689600.000001") < ts_key("1735689600.000002")
    assert ts_key("1735689600.0") == ts_key("1735689600.000000")
    # Returns usec-since-epoch as int; two nearby ts that collapse to the same float must differ.
    assert ts_key("1735689600.000001") == 1735689600_000001
    assert ts_key("1735689600.000002") == 1735689600_000002


def test_ts_key_rejects_invalid():
    for bad in ("", "not-a-ts", "12.34.56", None):
        with pytest.raises(ValueError, match="Invalid Slack ts"):
            ts_key(bad)  # type: ignore[arg-type]


def test_ts_from_thread_dir_roundtrip():
    assert ts_from_thread_dir("thread_1705320720_000000") == "1705320720.000000"
    assert ts_from_thread_dir("thread_1_2") == "1.2"
    assert ts_from_thread_dir("messages") is None
    assert ts_from_thread_dir("thread_") is None
    assert ts_from_thread_dir("thread_onlysec") is None


def test_dir_rank_prefers_messages_json(tmp_path):
    empty = tmp_path / "empty_C1"
    empty.mkdir()
    full = tmp_path / "full_C1"
    full.mkdir()
    (full / "messages.json").write_text("[]", encoding="utf-8")
    assert dir_rank(full)[0] > dir_rank(empty)[0]


def test_is_slack_id_accepts_cdg_only():
    assert is_slack_id("C123ABC")
    assert is_slack_id("D0BAF26EJ2Z")
    assert is_slack_id("G9")
    assert not is_slack_id("U123")
    assert not is_slack_id("#general")
    assert not is_slack_id("")
    assert not is_slack_id("c123")


def test_parse_channel_url():
    t = parse_target("https://acme.enterprise.slack.com/archives/C0BAF26EJ2Z")
    assert t.channel_id == "C0BAF26EJ2Z"
    assert t.workspace == "acme.enterprise"
    assert t.thread_ts is None
    assert t.channel_name is None


def test_parse_thread_url():
    t = parse_target("https://acme.enterprise.slack.com/archives/C0BAF26EJ2Z/p1234567890123456")
    assert t.channel_id == "C0BAF26EJ2Z"
    assert t.thread_ts == "1234567890.123456"
    assert t.workspace == "acme.enterprise"


def test_parse_channel_id():
    t = parse_target("C0BAF26EJ2Z")
    assert t.channel_id == "C0BAF26EJ2Z"
    assert t.channel_name is None


def test_parse_dm_id():
    t = parse_target("D0BAF26EJ2Z")
    assert t.channel_id == "D0BAF26EJ2Z"


@pytest.mark.parametrize("raw", ["#general", "general"])
def test_parse_channel_name(raw):
    t = parse_target(raw)
    assert t.channel_name == "general"
    assert t.channel_id is None


def test_parse_enterprise_url():
    t = parse_target("https://myco.enterprise.slack.com/archives/C123ABC")
    assert t.workspace == "myco.enterprise"
    assert t.channel_id == "C123ABC"
