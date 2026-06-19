import pytest
import json
from pathlib import Path
from click.testing import CliRunner
from unittest.mock import MagicMock, patch
from ssd.cli import main


@pytest.fixture
def runner():
    return CliRunner()


def invoke(runner, *args, config_path, output_path):
    return runner.invoke(
        main,
        ["--config", str(config_path), "--output", str(output_path)] + list(args),
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
        result = invoke(runner, "remove", "C123", config_path=config, output_path=tmp_path / "output")
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


def test_update_calls_sync_for_each_channel(tmp_path, runner):
    from ssd.config import add_channel
    config = tmp_path / "ssd.toml"
    add_channel(config, id="C123", name="general", url="https://...", since=None)
    add_channel(config, id="C456", name="random", url="https://...", since=None)
    with patch("ssd.cli.run_sync") as mock_sync, \
         patch("ssd.cli.SlackAPI") as MockAPI, \
         patch("ssd.cli._get_token", return_value="xoxd-fake"):
        mock_api = MagicMock()
        MockAPI.return_value = mock_api
        mock_api.get_workspace.return_value = "testteam"
        result = invoke(runner, "update", config_path=config, output_path=tmp_path / "output")
    assert mock_sync.call_count == 2
