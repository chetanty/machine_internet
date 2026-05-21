"""Smoke-test for the GitHub MCP server.

Run the server first:
  python serve.py --schema schemas/github_v3_rest_api.json --port 8001
"""
import asyncio
import json
from mcp.client.sse import sse_client
from mcp import ClientSession

MCP_URL = "http://localhost:8001/mcp"


async def call(session: ClientSession, tool: str, args: dict, timeout: float = 15.0):
    return await asyncio.wait_for(session.call_tool(tool, args), timeout=timeout)


def parse(res) -> object:
    text = res.content[0].text if res.content else ""
    if not text:
        return {"error": "(empty response)"}
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text[:300]}


async def main():
    async with sse_client(MCP_URL) as (r, w):
        async with ClientSession(r, w) as session:
            await session.initialize()

            print("=== tools ===")
            tools = await session.list_tools()
            for t in tools.tools:
                print(f"  {t.name}: {t.description[:72]}")

            print()
            print("=== manage_repository (GET octocat/Hello-World) ===")
            res = await call(session, "manage_repository", {"owner": "octocat", "repo": "Hello-World"})
            data = parse(res)
            if isinstance(data, dict) and "error" not in data and "raw" not in data:
                print(json.dumps({k: data[k] for k in ("full_name", "description", "stargazers_count", "language", "open_issues_count") if k in data}, indent=2))
            else:
                print(data)

            print()
            print("=== list_branches (octocat/Hello-World) ===")
            res = await call(session, "list_branches", {"owner": "octocat", "repo": "Hello-World"})
            data = parse(res)
            if isinstance(data, list):
                for b in data[:5]:
                    print(f"  {b.get('name')}")
            else:
                print(data)

            print()
            print("=== get_branch_details (octocat/Hello-World master) ===")
            res = await call(session, "get_branch_details", {"owner": "octocat", "repo": "Hello-World", "branch": "master"})
            data = parse(res)
            if isinstance(data, dict) and "error" not in data and "raw" not in data:
                commit = data.get("commit", {})
                print(f"  branch: {data.get('name')}")
                print(f"  sha:    {commit.get('sha', '')[:10]}...")
                print(f"  commit: {commit.get('commit', {}).get('message', '')[:60]}")
            else:
                print(data)

            print()
            print("=== list_organization_repositories (github org) ===")
            res = await call(session, "list_organization_repositories", {"org": "github"})
            data = parse(res)
            if isinstance(data, list):
                print(f"  {len(data)} repos, first 5:")
                for r in data[:5]:
                    print(f"    {r.get('full_name', r.get('name'))}: {r.get('description', '')[:50]}")
            else:
                print(data)

            print()
            print("=== get_repository_assignees (octocat/Hello-World) ===")
            res = await call(session, "get_repository_assignees", {"owner": "octocat", "repo": "Hello-World"})
            data = parse(res)
            if isinstance(data, list):
                print(f"  {len(data)} assignees: {[u.get('login') for u in data[:5]]}")
            else:
                print(data)

            print()
            print("=== done ===")


asyncio.run(main())
