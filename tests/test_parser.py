import pytest
from ssd.parser import parse_target, SlackTarget


def test_parse_channel_url():
    t = parse_target("https://redhat.enterprise.slack.com/archives/C0BAF26EJ2Z")
    assert t.channel_id == "C0BAF26EJ2Z"
    assert t.workspace == "redhat.enterprise"
    assert t.thread_ts is None
    assert t.channel_name is None


def test_parse_thread_url():
    t = parse_target(
        "https://redhat.enterprise.slack.com/archives/C0BAF26EJ2Z/p1234567890123456"
    )
    assert t.channel_id == "C0BAF26EJ2Z"
    assert t.thread_ts == "1234567890.123456"
    assert t.workspace == "redhat.enterprise"


def test_parse_channel_id():
    t = parse_target("C0BAF26EJ2Z")
    assert t.channel_id == "C0BAF26EJ2Z"
    assert t.channel_name is None


def test_parse_dm_id():
    t = parse_target("D0BAF26EJ2Z")
    assert t.channel_id == "D0BAF26EJ2Z"


def test_parse_channel_name_with_hash():
    t = parse_target("#general")
    assert t.channel_name == "general"
    assert t.channel_id is None


def test_parse_channel_name_without_hash():
    t = parse_target("general")
    assert t.channel_name == "general"
    assert t.channel_id is None


def test_parse_enterprise_url():
    t = parse_target("https://myco.enterprise.slack.com/archives/C123ABC")
    assert t.workspace == "myco.enterprise"
    assert t.channel_id == "C123ABC"
