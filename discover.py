#!/usr/bin/env python3
"""Wrap any API with a single command."""
import asyncio
import json
import sys
from pathlib import Path
from urllib.parse import urlparse

import click

from src.condensation.condenser import condense
from src.discovery.errors import (
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
from src.discovery.openapi import discover_openapi, discover_openapi_from_spec_url
from src.discovery.traffic import discover_via_traffic

_CONDENSER_CAP = 40
_WIKI_HINT = "  Open the ? wiki on the dashboard for more help."


def _lines(*parts: str) -> None:
    """Print each string as a separate line."""
    for p in parts:
        click.echo(p)


def _fail(*parts: str) -> None:
    _lines(*parts)
    sys.exit(1)


def _is_api_domain(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host.startswith("api.") or host.startswith("api-")


@click.command()
@click.option("--url", required=True, help="Target service base URL")
@click.option("--spec", "spec_url", default=None, help="Direct URL to an OpenAPI spec file (skips probing)")
@click.option("--tags", default=None, help="Comma-separated tags to include (e.g. repos,issues)")
@click.option("--output", "-o", default=None, help="Output path (default: schemas/<name>.json)")
@click.option("--traffic", "force_traffic", is_flag=True, default=False, help="Skip OpenAPI check, use traffic sniffing")
@click.option("--raw", "no_condense", is_flag=True, default=False, help="Save raw schema without condensation")
def main(url: str, spec_url: str, tags: str, output: str, force_traffic: bool, no_condense: bool):
    """Discover a service and save its condensed MCP schema."""
    filter_tags = [t.strip() for t in tags.split(",")] if tags else None
    asyncio.run(_run(url, spec_url, filter_tags, output, force_traffic, no_condense))


async def _run(url: str, spec_url: str, filter_tags, output: str, force_traffic: bool, no_condense: bool):
    click.echo(f"\nDiscovering {url} ...")

    raw = None

    # -- Path A: user-supplied spec URL --
    if spec_url:
        click.echo(f"  [A] Fetching spec from {spec_url} ...")
        try:
            raw = await discover_openapi_from_spec_url(spec_url, url)
            click.echo(f"  [OK] Found spec - {len(raw.endpoints)} endpoints")
        except SpecNetworkError:
            _fail(
                f"[FAIL] Could not reach {spec_url}",
                "",
                "  Check that the URL is correct and publicly accessible.",
                "  If the spec requires authentication it cannot be fetched automatically.",
                _WIKI_HINT,
            )
        except SpecParseError as exc:
            _fail(
                f"[FAIL] Found a file at {exc.spec_path} but could not parse it.",
                "",
                "  Supported formats: OpenAPI 3.x, Swagger 2.x (JSON or YAML).",
                "  The file may be malformed or a different format entirely.",
                _WIKI_HINT,
            )
        except SpecEmptyError:
            _fail(
                f"[FAIL] Spec fetched from {spec_url} but contained no parseable endpoints.",
                "",
                "  The spec may be empty, reference external files that cannot be resolved,",
                "  or use an unsupported schema format.",
                _WIKI_HINT,
            )

    # -- Path A: probe standard spec paths --
    elif not force_traffic:
        click.echo("  [A] Checking for OpenAPI spec ...")
        try:
            raw = await discover_openapi(url)
            if raw:
                click.echo(f"  [OK] Found spec - {len(raw.endpoints)} endpoints")
            else:
                _hint_no_spec(url)
        except SpecNetworkError:
            _fail(
                f"[FAIL] Could not reach {url}",
                "",
                "  Check that the URL is correct and publicly accessible.",
                "  If the service requires authentication, it cannot be auto-discovered.",
                _WIKI_HINT,
            )
        except SpecParseError as exc:
            _fail(
                f"[FAIL] Found a spec at {exc.spec_path} but could not parse it.",
                "",
                "  Supported formats: OpenAPI 3.x, Swagger 2.x (JSON or YAML).",
                "  Try providing the spec URL directly with --spec <url>",
                _WIKI_HINT,
            )
        except SpecEmptyError as exc:
            _fail(
                f"[FAIL] Spec found at {exc.spec_path} but contained no parseable endpoints.",
                "",
                "  The spec may be empty or reference external files that cannot be resolved.",
                "  Try --spec <direct-url> to fetch a different version of the spec.",
                _WIKI_HINT,
            )

    # -- Path B: traffic sniffing --
    if raw is None:
        click.echo("  [B] Traffic sniffing via headless browser ...")
        try:
            raw = await discover_via_traffic(url)
            click.echo(f"  [OK] Captured - {len(raw.endpoints)} endpoints inferred")
        except PlaywrightNotInstalledError:
            _fail(
                "[FAIL] Playwright is not installed. Path B traffic sniffing is unavailable.",
                "",
                "  Install it with:",
                "    playwright install chromium",
                "",
                "  Path A (OpenAPI spec detection) still works without Playwright.",
                _WIKI_HINT,
            )
        except TrafficNetworkError:
            _fail(
                f"[FAIL] Could not reach {url}",
                "",
                "  Check that the URL is correct and publicly accessible.",
                "  If the service requires authentication, it cannot be auto-discovered.",
                _WIKI_HINT,
            )
        except NoXHRCapturedError:
            _fail(
                f"[FAIL] No OpenAPI spec found and no browser traffic captured.",
                "",
                f"  {url} appears to be a server-rendered page or a bare API endpoint.",
                "  The page loaded but made no XHR or fetch calls.",
                "",
                "  Try one of these:",
                f"    A search results page:  --url \"{url.rstrip('/')}/search?q=test\" --traffic",
                f"    A specific item page:   --url {url.rstrip('/')}/items/1 --traffic",
                f"    Provide spec directly:  --url {url} --spec <spec-url>",
                _WIKI_HINT,
            )
        except AllTrackersFilteredError:
            _fail(
                "[FAIL] Page made XHR calls but all were filtered as third-party trackers.",
                "",
                "  The page may load only analytics and ad calls, not real data.",
                "  Try a more dynamic page:",
                f"    Dashboard or app page: --url {url.rstrip('/')}/app --traffic",
                f"    Search results:        --url \"{url.rstrip('/')}/search?q=test\" --traffic",
                f"    A specific item page:  --url {url.rstrip('/')}/items/1 --traffic",
                _WIKI_HINT,
            )
        except BrandFilteredError as exc:
            domains_str = ", ".join(exc.seen_brands[:5])
            _fail(
                f"[FAIL] Page made XHR calls but none matched the service brand \"{exc.base_brand}\".",
                "",
                f"  Detected calls to: {domains_str}",
                "  The API may be hosted on a different domain than the website.",
                "",
                "  Try pointing directly at the API domain:",
                f"    --url https://api.{exc.base_brand}.com --traffic",
                f"  Or provide a spec URL:",
                f"    --url {url} --spec <spec-url>",
                _WIKI_HINT,
            )
        except DiscoveryError:
            _fail(
                "[FAIL] Discovery failed",
                _WIKI_HINT,
            )

    # -- endpoint cap warning --
    if raw and len(raw.endpoints) > _CONDENSER_CAP:
        total = len(raw.endpoints)
        click.echo(f"  [WARN] Spec has {total} endpoints, capped at {_CONDENSER_CAP} for condensation.")
        click.echo(f"         Use --tags to focus on specific areas:")
        click.echo(f"           --tags repos,issues      (filter to repos and issues)")
        click.echo(f"           --tags users,posts       (filter to users and posts)")
        click.echo(f"         Or use --spec with a filtered spec URL.")

    # -- tag filter --
    if filter_tags:
        before = len(raw.endpoints)
        tags_lower = {t.lower() for t in filter_tags}
        raw.endpoints = [ep for ep in raw.endpoints if any(t.lower() in tags_lower for t in ep.tags)]
        click.echo(f"  [filter] {before} -> {len(raw.endpoints)} endpoints (tags: {', '.join(filter_tags)})")

    # -- condense or dump raw --
    if no_condense:
        data = raw.model_dump()
        name = raw.title.lower().replace(" ", "_")
    else:
        click.echo("\nCondensing to agent tools ...")
        try:
            condensed = await condense(raw, source_url=url)
        except LLMQuotaError:
            _fail(
                "[FAIL] AI provider quota exhausted.",
                "",
                "  All configured models and API keys are rate-limited.",
                "  Options:",
                "    Add a backup key: GEMINI_API_KEY_2=your-key in .env",
                "    Add OpenAI:       OPENAI_API_KEY=your-key in .env",
                "    Wait and retry:   Gemini free tier resets daily at midnight PST",
                _WIKI_HINT,
            )
        except LLMInvalidResponseError:
            _fail(
                "[FAIL] AI condensation returned invalid output.",
                "",
                "  The discovered endpoints may be too complex or ambiguous.",
                "  Try:",
                "    Filtering with --tags to reduce endpoint count",
                "    Using --raw to inspect the raw discovered schema",
                "    Running again (LLM output is non-deterministic)",
                _WIKI_HINT,
            )
        except LLMEmptySchemaError:
            _fail(
                "[FAIL] AI condensation produced no tools.",
                "",
                "  The discovered endpoints may be too ambiguous to generate tools from.",
                "  Try:",
                "    Filtering with --tags to reduce endpoint count",
                "    Using --raw to inspect the raw discovered schema",
                "    Running again (LLM output is non-deterministic)",
                _WIKI_HINT,
            )

        click.echo(f"  [OK] {len(condensed.tools)} tools:")
        for t in condensed.tools:
            click.echo(f"     {t.name}: {t.description[:72]}")
        data = json.loads(condensed.model_dump_json())
        data["discovery_method"] = raw.discovery_method
        name = condensed.service_name

    out = Path(output) if output else Path("schemas") / f"{name}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(data, indent=2))

    click.echo(f"\nSaved -> {out}")
    click.echo(f"Serve  -> python serve.py --schema {out}")


def _hint_no_spec(url: str) -> None:
    """Print a helpful hint when Path A finds no spec, before moving to Path B."""
    if _is_api_domain(url):
        host = urlparse(url).hostname or url
        click.echo(f"  [info] No spec at standard locations for {host}.")
        click.echo(f"         Some services host their spec on a CDN, not on the API domain.")
        click.echo(f"         Try providing the spec URL directly with --spec <url>")
        click.echo(f"         Common locations:")
        click.echo(f"           GitHub:   raw.githubusercontent.com/<owner>/<repo>/main/openapi.json")
        click.echo(f"           Postman:  raw.githubusercontent.com/<owner>/<repo>/main/swagger.json")


if __name__ == "__main__":
    main()
