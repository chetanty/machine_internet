"""machine-internet: turn a website into an MCP server.

    machine-internet wrap https://bundlephobia.com
    machine-internet serve bundlephobia
"""
from __future__ import annotations

import asyncio
import json
import sys
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
                "[FAIL] No LLM API key found.",
                "",
                "  Set any one of these (environment variable or a .env file in this folder):",
                "    ANTHROPIC_API_KEY=...",
                "    OPENAI_API_KEY=...",
                "    GEMINI_API_KEY=...",
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
        base = url.rstrip("/")
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
                "  It is probably server-rendered. Try a page that loads data as you use it:",
                f"    machine-internet wrap \"{base}/search?q=test\"",
                f"    machine-internet wrap {base}/items/1",
            )
        except AllTrackersFilteredError:
            _fail(
                "[FAIL] The only API calls were analytics and ad trackers.",
                f"  Try a more dynamic page, e.g. machine-internet wrap \"{base}/search?q=test\"",
            )
        except BrandFilteredError as exc:
            _fail(
                f"[FAIL] API calls went to other domains ({', '.join(exc.seen_brands[:5])}).",
                f"  Try the API domain directly: machine-internet wrap https://api.{exc.base_brand}.com",
            )
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
            click.echo(f"       {t.name}: {t.description[:70]}")
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


@main.command()
@click.argument("schema")
def config(schema):
    """Print the MCP client config block for SCHEMA."""
    _resolve_schema(schema)
    click.echo(_client_config(schema))


if __name__ == "__main__":
    main()
