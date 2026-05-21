#!/usr/bin/env python3
"""Wrap any API with a single command."""
import asyncio
import json
import sys
from pathlib import Path

import click

from src.discovery.openapi import discover_openapi, discover_openapi_from_spec_url
from src.discovery.traffic import discover_via_traffic
from src.condensation.condenser import condense


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
    if spec_url:
        click.echo(f"  [A] Fetching spec from {spec_url} ...")
        raw = await discover_openapi_from_spec_url(spec_url, url)
        if raw:
            click.echo(f"  [OK] Found spec - {len(raw.endpoints)} endpoints")
        else:
            click.echo("  [FAIL] Could not fetch/parse spec", err=True)
            sys.exit(1)
    elif not force_traffic:
        click.echo("  [A] Checking for OpenAPI spec ...")
        raw = await discover_openapi(url)
        if raw:
            click.echo(f"  [OK] Found spec - {len(raw.endpoints)} endpoints")

    if raw is None:
        click.echo("  [B] Traffic sniffing via headless browser ...")
        raw = await discover_via_traffic(url)
        if raw:
            click.echo(f"  [OK] Captured - {len(raw.endpoints)} endpoints inferred")
        else:
            click.echo("  [FAIL] Discovery failed", err=True)
            sys.exit(1)

    if filter_tags:
        before = len(raw.endpoints)
        tags_lower = {t.lower() for t in filter_tags}
        raw.endpoints = [ep for ep in raw.endpoints if any(t.lower() in tags_lower for t in ep.tags)]
        click.echo(f"  [filter] {before} -> {len(raw.endpoints)} endpoints (tags: {', '.join(filter_tags)})")

    if no_condense:
        data = raw.model_dump()
        name = raw.title.lower().replace(" ", "_")
    else:
        click.echo("\nCondensing to agent tools ...")
        condensed = await condense(raw, source_url=url)
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


if __name__ == "__main__":
    main()
