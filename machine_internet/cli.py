"""machine-internet: turn a website into an MCP server.

    machine-internet wrap https://bundlephobia.com
    machine-internet serve bundlephobia
"""
from __future__ import annotations

import asyncio
import json
import sys
import textwrap
from pathlib import Path
from urllib.parse import urlparse

import click

from .discovery.errors import (
    AllTrackersFilteredError,
    BrandFilteredError,
    DiscoveryError,
    LLMEmptySchemaError,
    LLMInvalidResponseError,
    LLMQuotaError,
    NoXHRCapturedError,
    PlaywrightNotInstalledError,
    SpecEmptyError,
    SpecNetworkError,
    SpecParseError,
    TrafficNetworkError,
)
from .discovery.schema import CondensedSchema

EXAMPLES_DIR = Path(__file__).parent / "examples"
USER_DIR = Path.home() / ".machine-internet" / "schemas"
_CONDENSER_CAP = 40


def _fail(*lines: str) -> None:
    for line in lines:
        click.echo(line, err=True)
    sys.exit(1)


def _resolve_schema(name_or_path: str) -> Path:
    """Find a schema by file path, then by name in the user dir, then in bundled examples."""
    p = Path(name_or_path)
    if p.suffix == ".json" and p.exists():
        return p
    for d in (USER_DIR, EXAMPLES_DIR):
        candidate = d / f"{name_or_path}.json"
        if candidate.exists():
            return candidate
    names = sorted({f.stem for d in (USER_DIR, EXAMPLES_DIR) if d.exists() for f in d.glob("*.json")})
    raise click.ClickException(
        f"No schema named '{name_or_path}'. Available: {', '.join(names) or 'none'}. "
        "Run 'machine-internet wrap <url>' to create one."
    )


def _client_config(name: str, target: str | None = None) -> str:
    """MCP client config block. target is what 'serve' receives (a name or a file path)."""
    return json.dumps(
        {"mcpServers": {name: {"command": "uvx", "args": ["machine-internet", "serve", target or name]}}},
        indent=2,
    )


@click.group()
@click.version_option(package_name="machine-internet")
def main() -> None:
    """Turn any website into an MCP server. No API needed."""
    # Windows consoles default to cp1252 and crash on API responses containing non-ASCII text
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


# --------------------------------------------------------------------------- wrap


@main.command()
@click.argument("url")
@click.option("--name", default=None, help="Name for the server (default: inferred from the site)")
@click.option("--spec", "spec_url", default=None, help="Direct URL to an OpenAPI spec (skips sniffing)")
@click.option("--tags", default=None, help="Comma-separated OpenAPI tags to keep, e.g. repos,issues")
@click.option("--sniff", "force_traffic", is_flag=True, help="Skip the OpenAPI check and sniff traffic directly")
@click.option("--raw", "no_condense", is_flag=True, help="Save the raw discovered schema without condensing")
@click.option("-o", "--output", default=None, help="Output file (default: ~/.machine-internet/schemas/<name>.json)")
@click.option("--debug", is_flag=True, help="Show full tracebacks on errors")
def wrap(url, name, spec_url, tags, force_traffic, no_condense, output, debug):
    """Discover the API behind URL and save it as an MCP server."""
    if "://" not in url:
        url = "https://" + url
    filter_tags = [t.strip() for t in tags.split(",")] if tags else None

    if not no_condense:
        from .ai.client import NoAPIKeyError, get_ai_client

        try:
            get_ai_client()
        except NoAPIKeyError:
            _fail(
                "[FAIL] wrap needs an LLM API key (serving and the bundled examples do not).",
                "",
                "  Free option: get a Gemini key at https://aistudio.google.com/apikey",
                "  (free tier, no credit card), then run one of these and try again:",
                "",
                "    macOS / Linux:   export GEMINI_API_KEY=your-key",
                "    Windows (PS):    $env:GEMINI_API_KEY=\"your-key\"",
                "    Windows (cmd):   set GEMINI_API_KEY=your-key",
                "",
                "  ANTHROPIC_API_KEY or OPENAI_API_KEY work too. A .env file in this folder also works.",
            )

    try:
        asyncio.run(_wrap(url, name, spec_url, filter_tags, force_traffic, no_condense, output))
    except KeyboardInterrupt:
        _fail("", "Cancelled.")
    except Exception as exc:
        if debug:
            raise
        _fail(f"[FAIL] {type(exc).__name__}: {str(exc)[:300]}", "  Re-run with --debug for the full traceback.")


async def _wrap(url, name, spec_url, filter_tags, force_traffic, no_condense, output):
    from .discovery.openapi import discover_openapi, discover_openapi_from_spec_url
    from .discovery.traffic import discover_via_traffic

    click.echo(f"\nWrapping {url}")
    raw = None

    # Path A: OpenAPI spec, if one exists (free, no LLM needed for discovery)
    if spec_url or not force_traffic:
        try:
            if spec_url:
                click.echo(f"  Fetching spec from {spec_url} ...")
                raw = await discover_openapi_from_spec_url(spec_url, url)
            else:
                click.echo("  Checking for an OpenAPI spec ...")
                raw = await discover_openapi(url)
            if raw:
                click.echo(f"  [OK] Found spec: {len(raw.endpoints)} endpoints")
        except SpecNetworkError:
            _fail(f"[FAIL] Could not reach {spec_url or url}. Check the URL is correct and public.")
        except SpecParseError as exc:
            _fail(
                f"[FAIL] Found a spec at {exc.spec_path} but could not parse it.",
                "  Supported: OpenAPI 3.x and Swagger 2.x (JSON or YAML). Try --spec <url> or --sniff.",
            )
        except SpecEmptyError as exc:
            _fail(f"[FAIL] Spec at {exc.spec_path} has no usable endpoints. Try --sniff.")

    # Path B: sniff the API calls the site makes in a headless browser
    if raw is None:
        click.echo("  Sniffing network traffic in a headless browser ...")
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        try:
            raw = await discover_via_traffic(url)
            click.echo(f"  [OK] Inferred {len(raw.endpoints)} endpoints")
        except PlaywrightNotInstalledError:
            _fail("[FAIL] Could not install headless Chromium. Run: playwright install chromium")
        except TrafficNetworkError:
            _fail(f"[FAIL] Could not load {url}. Check the URL is correct and public.")
        except NoXHRCapturedError:
            _fail(
                "[FAIL] The page made no API calls while loading.",
                "  It is probably server-rendered, so there is no hidden API to find.",
                "  If the site has search or filters, try a page that loads data as you use it:",
                f"    machine-internet wrap \"{base}/search?q=test\"",
                f"    machine-internet wrap {base}/items/1",
            )
        except AllTrackersFilteredError:
            _fail(
                "[FAIL] The page made no API calls of its own, only analytics, ads or bot checks.",
                "  It is probably server-rendered or behind bot protection.",
                f"  If it has search or filters, try that page, e.g. machine-internet wrap \"{base}/search?q=test\"",
            )
        except BrandFilteredError as exc:
            _fail(
                f"[FAIL] The page's data calls all went to other services ({', '.join(exc.seen_brands[:5])}),",
                "  not to the site itself, so there was nothing of the site's own to wrap.",
                "  If one of those is really the site's API, wrap a page on that domain instead.",
            )
        except LLMEmptySchemaError:
            _fail(
                "[FAIL] The page made calls, but none of them returned real data (only page or config payloads).",
                "  The page is probably rendered on the server. Pages that load results as you search,",
                "  filter or scroll work best.",
            )
        except LLMInvalidResponseError:
            _fail(
                "[FAIL] Traffic was captured, but the LLM's answer could not be parsed.",
                "  Run again (LLM output varies), or point at a page with less going on.",
            )
        except LLMQuotaError:
            _fail("[FAIL] LLM quota exhausted on every configured provider. Add another key or retry later.")
        except DiscoveryError:
            _fail("[FAIL] Discovery failed.")

    if filter_tags:
        before = len(raw.endpoints)
        wanted = {t.lower() for t in filter_tags}
        raw.endpoints = [ep for ep in raw.endpoints if any(t.lower() in wanted for t in ep.tags)]
        click.echo(f"  Filtered {before} -> {len(raw.endpoints)} endpoints by tag")

    if len(raw.endpoints) > _CONDENSER_CAP:
        click.echo(f"  [WARN] {len(raw.endpoints)} endpoints; only the first {_CONDENSER_CAP} are condensed. Use --tags to focus.")

    if no_condense:
        data = raw.model_dump()
        default_name = raw.title.lower().replace(" ", "_")
    else:
        from .condensation.condenser import condense

        click.echo("  Condensing into agent tools ...")
        try:
            condensed = await condense(raw, source_url=url)
        except LLMQuotaError:
            _fail("[FAIL] LLM quota exhausted on every configured provider. Add another key or retry later.")
        except (LLMInvalidResponseError, LLMEmptySchemaError):
            _fail("[FAIL] The LLM did not return usable tools. Run again, or narrow it with --tags.")
        if name:
            condensed.service_name = name
        click.echo(f"  [OK] {len(condensed.tools)} tools:")
        for t in condensed.tools:
            click.echo(f"       {t.name}: {textwrap.shorten(t.description, 78, placeholder=' ...')}")
        data = json.loads(condensed.model_dump_json())
        data["discovery_method"] = raw.discovery_method
        default_name = condensed.service_name

    final_name = name or default_name
    out = Path(output) if output else USER_DIR / f"{final_name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2), encoding="utf-8")

    click.echo(f"\nSaved {out}")
    if not no_condense:
        click.echo("\nAdd this to your MCP client config (Claude Desktop, Cursor, ...):\n")
        target = str(out.resolve()) if output else final_name
        click.echo(_client_config(final_name, target))
        click.echo(f"\nOr try it now: machine-internet serve {target}")


# --------------------------------------------------------------------------- serve


@main.command()
@click.argument("schema")
@click.option("--sse", is_flag=True, help="Serve over HTTP/SSE instead of stdio")
@click.option("--host", default="127.0.0.1", show_default=True, help="Bind host (with --sse)")
@click.option("--port", default=8000, show_default=True, help="Bind port (with --sse)")
def serve(schema, sse, host, port):
    """Run an MCP server for SCHEMA (a name from 'list', or a .json path).

    Defaults to stdio, which is what Claude Desktop and Cursor launch.
    Credentials for APIs that need them are read from MI_TOKEN, MI_API_KEY,
    MI_USERNAME/MI_PASSWORD or MI_CLIENT_ID/MI_CLIENT_SECRET/MI_TOKEN_URL.
    """
    from .serving.mcp_server import serve_sse, serve_stdio

    path = _resolve_schema(schema)
    condensed = CondensedSchema(**json.loads(path.read_text(encoding="utf-8")))
    # stdout carries the MCP protocol in stdio mode, so all logging goes to stderr
    click.echo(f"{condensed.service_name}: {len(condensed.tools)} tools from {path}", err=True)
    if sse:
        click.echo(f"MCP endpoint: http://{host}:{port}/mcp", err=True)
        serve_sse(condensed, host=host, port=port)
    else:
        serve_stdio(condensed)


# --------------------------------------------------------------------------- list / config


@main.command("list")
def list_schemas():
    """List bundled examples and the servers you have wrapped."""
    for label, d in (("Your servers", USER_DIR), ("Bundled examples", EXAMPLES_DIR)):
        files = sorted(d.glob("*.json")) if d.exists() else []
        click.echo(f"\n{label}:")
        if not files:
            click.echo("  (none yet: machine-internet wrap <url>)")
        for f in files:
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
                src = data.get("source_url") or data.get("base_url", "")
                click.echo(f"  {f.stem:<20} {len(data.get('tools', [])):>2} tools  {src}")
            except Exception:
                click.echo(f"  {f.stem:<20} (unreadable)")
    click.echo()


def _coerce(value: str, typ: str):
    """Turn a key=value string from the command line into the tool parameter's type."""
    try:
        if typ == "integer":
            return int(value)
        if typ == "number":
            return float(value)
        if typ == "boolean":
            return value.lower() in ("1", "true", "yes", "y")
        if typ in ("object", "array"):
            return json.loads(value)
    except (ValueError, json.JSONDecodeError):
        pass
    return value


@main.command()
@click.argument("schema")
def tools(schema):
    """Show the tools in SCHEMA and their parameters."""
    s = CondensedSchema(**json.loads(_resolve_schema(schema).read_text(encoding="utf-8")))
    click.echo(f"\n{s.service_name}  ({s.base_url})\n")
    for t in s.tools:
        click.echo(click.style(t.name, bold=True))
        click.echo(f"  {t.description}")
        for p in t.parameters:
            req = "required" if p.required else "optional"
            click.echo(f"    {p.name} ({p.type}, {req}): {p.description}")
        click.echo()


@main.command()
@click.argument("schema")
@click.argument("tool")
@click.argument("args", nargs=-1)
def call(schema, tool, args):
    """Call one TOOL directly, e.g.:  machine-internet call bundlephobia get_package_size package=react"""
    from .auth.injector import AuthInjector, credentials_from_env
    from .serving.executor import ToolExecutor

    s = CondensedSchema(**json.loads(_resolve_schema(schema).read_text(encoding="utf-8")))
    t = next((x for x in s.tools if x.name == tool), None)
    if t is None:
        raise click.ClickException(f"No tool '{tool}'. Available: {', '.join(x.name for x in s.tools)}")

    types_by_name = {p.name: p.type for p in t.parameters}
    arguments = {}
    for a in args:
        if "=" not in a:
            raise click.ClickException(f"Arguments look like key=value, got '{a}'")
        k, v = a.split("=", 1)
        arguments[k] = _coerce(v, types_by_name.get(k, "string"))
    missing = [p.name for p in t.parameters if p.required and p.name not in arguments]
    if missing:
        raise click.ClickException(f"Missing required argument(s): {', '.join(f'{m}=...' for m in missing)}")

    injector = None
    creds = credentials_from_env(s.auth_type) if s.auth_type.value != "none" else None
    if creds:
        injector = AuthInjector(s.auth_type, creds)
    try:
        result = asyncio.run(ToolExecutor(s.base_url, injector, s.static_headers, s.static_query, s.source_url).execute(t, arguments))
    except Exception as exc:
        raise click.ClickException(f"{type(exc).__name__}: {exc}")
    click.echo(json.dumps(result, indent=2, ensure_ascii=False) if isinstance(result, (dict, list)) else str(result))


@main.command()
@click.argument("schema")
def config(schema):
    """Print the MCP client config block for SCHEMA."""
    _resolve_schema(schema)
    click.echo(_client_config(schema))


if __name__ == "__main__":
    main()
