#!/usr/bin/env python3
"""Start an MCP server for a condensed schema."""
import json
from pathlib import Path

import click

from src.discovery.schema import CondensedSchema
from src.auth.vault import CredentialVault


@click.command()
@click.option("--schema", required=True, help="Path to condensed schema JSON")
@click.option("--host", default="0.0.0.0", show_default=True, help="Bind host")
@click.option("--port", default=8000, show_default=True, help="Bind port")
@click.option("--set-creds", is_flag=True, default=False, help="Interactively configure credentials")
def main(schema: str, host: str, port: int, set_creds: bool):
    """Serve a condensed schema as an SSE-based MCP endpoint."""
    path = Path(schema)
    if not path.exists():
        raise click.ClickException(f"Schema file not found: {schema}")

    condensed = CondensedSchema(**json.loads(path.read_text()))
    vault = CredentialVault()

    if set_creds:
        _setup_credentials(condensed, vault)

    click.echo(f"\nService : {condensed.service_name}")
    click.echo(f"Tools   : {[t.name for t in condensed.tools]}")
    click.echo(f"Auth    : {condensed.auth_type.value}")
    click.echo(f"MCP URL : http://{host}:{port}/mcp\n")

    from src.serving.mcp_server import serve
    serve(condensed, vault, host=host, port=port)


def _setup_credentials(schema: CondensedSchema, vault: CredentialVault) -> None:
    auth = schema.auth_type.value
    creds: dict = {}

    if auth == "api_key":
        creds["value"] = click.prompt("API Key", hide_input=True)
        creds["name"] = click.prompt("Header/param name", default="X-API-Key")
        creds["location"] = click.prompt("Location", type=click.Choice(["header", "query"]), default="header")
    elif auth == "bearer":
        creds["token"] = click.prompt("Bearer token", hide_input=True)
    elif auth == "oauth2":
        creds["token_url"] = click.prompt("Token URL")
        creds["client_id"] = click.prompt("Client ID")
        creds["client_secret"] = click.prompt("Client Secret", hide_input=True)
        creds["scope"] = click.prompt("Scope", default="")
    elif auth == "basic":
        creds["username"] = click.prompt("Username")
        creds["password"] = click.prompt("Password", hide_input=True)

    if creds:
        vault.store(schema.service_name, creds)
        click.echo(f"✓ Credentials saved for '{schema.service_name}'")


if __name__ == "__main__":
    main()
