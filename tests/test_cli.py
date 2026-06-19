def test_help(invoke):
    result = invoke("--help")
    assert result.exit_code == 0
    assert "token" in result.output
    assert "dump" in result.output
    assert "sync" in result.output
    assert "add" in result.output
    assert "update" in result.output
