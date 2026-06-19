import pytest
from click.testing import CliRunner
from ssd.cli import main


@pytest.fixture
def runner():
    return CliRunner()


@pytest.fixture
def invoke(runner):
    def _invoke(*args, **kwargs):
        return runner.invoke(main, args, catch_exceptions=False, **kwargs)
    return _invoke
