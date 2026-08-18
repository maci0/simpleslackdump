from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ssd.cli import main
from ssd.config import load_config


@pytest.fixture
def runner():
    return CliRunner()


def invoke(runner, *args, config_path, output_path):
    return runner.invoke(
        main,
        ["--config", str(config_path), "--output", str(output_path), *args],
        catch_exceptions=False,
    )


def test_add_channel_url(tmp_path, runner):
    config = tmp_path / "ssd.toml"
    with patch("ssd.cli.SlackAPI") as MockAPI:
        mock_api = MagicMock()
        MockAPI.return_value = mock_api
        mock_api.resolve_channel.return_value = ("C123", "general")
        mock_api.get_workspace.return_value = "testteam"
        with patch("ssd.cli._get_token", return_value="xoxd-fake"):
            result = invoke(
                runner,
                "add",
                "https://testteam.slack.com/archives/C123",
                config_path=config,
                output_path=tmp_path / "output",
            )
    assert result.exit_code == 0
    from ssd.config import load_config

    cfg = load_config(config)
    assert len(cfg.channels) == 1
    assert cfg.channels[0].id == "C123"


def test_remove_channel(tmp_path, runner):
    from ssd.config import add_channel

    config = tmp_path / "ssd.toml"
    add_channel(config, id="C123", name="general", url="https://...", since=None)
    with patch("ssd.cli._get_token", return_value="xoxd-fake"):
        result = invoke(
            runner, "remove", "C123", config_path=config, output_path=tmp_path / "output"
        )
    assert result.exit_code == 0
    from ssd.config import load_config

    cfg = load_config(config)
    assert len(cfg.channels) == 0


def test_list_shows_channels(tmp_path, runner):
    from ssd.config import add_channel

    config = tmp_path / "ssd.toml"
    add_channel(config, id="C123", name="general", url="https://...", since=None)
    result = invoke(runner, "list", config_path=config, output_path=tmp_path / "output")
    assert result.exit_code == 0
    assert "general" in result.output
    assert "C123" in result.output


def test_list_finds_dump_after_channel_rename(tmp_path, runner):
    from ssd.config import add_channel, add_thread

    out = tmp_path / "output"
    ch = out / "acme" / "old-name_C123"
    ch.mkdir(parents=True)
    (ch / ".cursor").write_text("99.0", encoding="utf-8")
    (ch / "messages.json").write_text("[]", encoding="utf-8")
    td = ch / "thread_1_0"
    td.mkdir()
    (td / ".cursor").write_text("1.5", encoding="utf-8")
    (td / "thread.json").write_text("[]", encoding="utf-8")
    config = tmp_path / "ssd.toml"
    add_channel(config, id="C123", name="new-name", url="https://...", since=None)
    add_thread(
        config,
        channel_id="C123",
        thread_ts="1.0",
        url="https://x.slack.com/archives/C123/p1000000",
    )
    result = invoke(runner, "list", config_path=config, output_path=out)
    assert result.exit_code == 0
    assert "new-name" in result.output
    assert "last=99.0" in result.output
    assert "thread 1.0" in result.output
    assert "last=1.5" in result.output


def test_update_calls_sync_for_each_channel(tmp_path, runner):
    from ssd.config import add_channel

    config = tmp_path / "ssd.toml"
    add_channel(config, id="C123", name="general", url="https://...", since=None)
    add_channel(config, id="C456", name="random", url="https://...", since=None)
    with (
        patch("ssd.cli.run_sync") as mock_sync,
        patch("ssd.cli.SlackAPI") as MockAPI,
        patch("ssd.cli._get_token", return_value="xoxd-fake"),
    ):
        mock_api = MagicMock()
        MockAPI.return_value = mock_api
        mock_api.get_workspace.return_value = "testteam"
        result = invoke(runner, "update", config_path=config, output_path=tmp_path / "output")
    assert result.exit_code == 0
    assert mock_sync.call_count == 2
    assert {call.args[2] for call in mock_sync.call_args_list} == {"C123", "C456"}


def test_add_thread_url_no_api_call(tmp_path, runner):
    """Adding a thread URL writes a [[threads]] entry without making any API call."""
    config = tmp_path / "ssd.toml"
    with patch("ssd.cli.SlackAPI") as MockAPI:
        result = invoke(
            runner,
            "add",
            "https://testteam.slack.com/archives/C123/p1705320720000000",
            config_path=config,
            output_path=tmp_path / "output",
        )
    assert result.exit_code == 0
    # No API should be constructed for a thread-only add
    MockAPI.assert_not_called()
    cfg = load_config(config)
    assert len(cfg.threads) == 1
    assert cfg.threads[0].thread_ts == "1705320720.000000"
