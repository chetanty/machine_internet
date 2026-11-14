import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.index import SCHEMAS, _restore_rewritten_path  # noqa: E402


def test_rewritten_path_is_restored_and_other_params_kept():
    scope = {"path": "/api/index", "query_string": b"__path=mcp/bundlephobia&x=1"}
    out = _restore_rewritten_path(scope)
    assert out["path"] == "/mcp/bundlephobia"
    assert out["query_string"] == b"x=1"


def test_scope_without_rewrite_is_untouched():
    scope = {"path": "/mcp/bundlephobia", "query_string": b""}
    assert _restore_rewritten_path(scope) is scope


def test_all_examples_are_hosted():
    assert {"yc_companies", "vscode_marketplace", "bundlephobia"} == set(SCHEMAS)


def test_every_tool_has_a_working_playground_preset_shape():
    from api.index import PRESETS, _servers_json
    for srv in _servers_json("https://x"):
        for tool in srv["tools"]:
            required = tool["input_schema"].get("required", [])
            missing = [r for r in required if r not in tool["example"]]
            assert not missing, f"{srv['name']}.{tool['name']} preset lacks {missing}"
    assert set(PRESETS) <= {s["name"] for s in _servers_json("https://x")}
