import json

from click.testing import CliRunner

from machine_internet.cli import EXAMPLES_DIR, _coerce, main
from machine_internet.discovery.schema import CondensedSchema


def test_every_bundled_example_is_a_valid_schema():
    files = list(EXAMPLES_DIR.glob("*.json"))
    assert len(files) >= 5
    for f in files:
        schema = CondensedSchema(**json.loads(f.read_text(encoding="utf-8")))
        assert schema.tools, f"{f.name} has no tools"


def test_list_shows_examples():
    result = CliRunner().invoke(main, ["list"])
    assert result.exit_code == 0
    assert "bundlephobia" in result.output


def test_config_prints_valid_client_json():
    result = CliRunner().invoke(main, ["config", "caniuse"])
    assert result.exit_code == 0
    cfg = json.loads(result.output)
    assert cfg["mcpServers"]["caniuse"]["args"] == ["machine-internet", "serve", "caniuse"]


def test_unknown_schema_is_a_clean_error():
    result = CliRunner().invoke(main, ["tools", "does_not_exist"])
    assert result.exit_code != 0
    assert "No schema named" in result.output


def test_call_reports_missing_required_args():
    result = CliRunner().invoke(main, ["call", "bundlephobia", "get_package_size"])
    assert result.exit_code != 0
    assert "package=..." in result.output


def test_coerce_types():
    assert _coerce("5", "integer") == 5
    assert _coerce("true", "boolean") is True
    assert _coerce('["a"]', "array") == ["a"]
    assert _coerce("x", "integer") == "x"
