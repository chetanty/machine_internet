import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from api.index import SCHEMAS, _restore_rewritten_path  # noqa: E402


def test_rewritten_path_is_restored_and_other_params_kept():
    scope = {"path": "/api/index", "query_string": b"__path=mcp/caniuse&x=1"}
    out = _restore_rewritten_path(scope)
    assert out["path"] == "/mcp/caniuse"
    assert out["query_string"] == b"x=1"


def test_scope_without_rewrite_is_untouched():
    scope = {"path": "/mcp/caniuse", "query_string": b""}
    assert _restore_rewritten_path(scope) is scope


def test_all_examples_are_hosted():
    assert {"bundlephobia", "caniuse"} <= set(SCHEMAS)
