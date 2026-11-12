# Machine Internet

**Turn any website into an MCP server. No API needed.**

```bash
uvx machine-internet wrap https://bundlephobia.com/package/react
```

Machine Internet opens the site in a headless browser, watches the hidden API calls the page makes, and turns them into a small set of clean tools that Claude, Cursor or any MCP client can call directly.

<!-- TODO: demo GIF here. Show: wrap a URL -> paste config -> Claude answers a question with the new tools. Keep it under 30 seconds. -->

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-orange)

---

## Try it in 30 seconds (no API key)

Five sites with no public API come pre-wrapped. Add one to your Claude Desktop or Cursor config:

```json
{
  "mcpServers": {
    "bundlephobia": {
      "command": "uvx",
      "args": ["machine-internet", "serve", "bundlephobia"]
    }
  }
}
```

Restart your client and ask: *"How big is react-dom gzipped, and is lodash-es tree-shakeable?"*

| Example | Site | Tools |
|---|---|---|
| `bundlephobia` | [bundlephobia.com](https://bundlephobia.com) | 6: package size, tree-shaking check, size history, similar packages, per-export sizes, recent |
| `caniuse` | [caniuse.com](https://caniuse.com) | 4: search features, browser support, feature details, news |
| `npm_trends` | [npmtrends.com](https://npmtrends.com) | 4: package info, weekly downloads, trend comparison, GitHub stats |
| `regex101` | [regex101.com](https://regex101.com) | 3: browse the community library, get a saved regex, list versions |
| `hacker_news` | [hn.algolia.com](https://hn.algolia.com) | 2: search stories and comments, status |

You need [uv](https://docs.astral.sh/uv/) for `uvx`. Or `pip install machine-internet` and use `machine-internet` as the command.

**Hosted, nothing to install:** every example is also live at [machine-internet.vercel.app](https://machine-internet.vercel.app) as a remote MCP endpoint, e.g. `https://machine-internet.vercel.app/mcp/bundlephobia`. Clients with remote MCP support take the URL directly; others can use `npx mcp-remote <url>`.

---

## Wrap your own site

Set an LLM key (any one of these works):

```bash
export ANTHROPIC_API_KEY=...   # or OPENAI_API_KEY, or GEMINI_API_KEY
```

Then point it at a page that loads data as you use it:

```bash
uvx machine-internet wrap https://bundlephobia.com/package/react
```

```
Wrapping https://bundlephobia.com/package/react
  Checking for an OpenAPI spec ...
  Sniffing network traffic in a headless browser ...
  [OK] Inferred 4 endpoints
  Condensing into agent tools ...
  [OK] 4 tools:
       get_package_size: Use this tool to get detailed size information for a JavaScript packag
       get_package_history: Use this tool to retrieve the historical versions of a specific JavaSc
       ...

Add this to your MCP client config (Claude Desktop, Cursor, ...):
{ "mcpServers": { "bundlephobia_api": { "command": "uvx", "args": ["machine-internet", "serve", "bundlephobia_api"] } } }
```

The first run downloads headless Chromium once (about 150 MB). Wrapping takes 1 to 3 minutes and costs a few cents of LLM usage. Serving afterwards needs no LLM at all.

**Tips**
- Pick a page that fetches data: a search results page or an item page works better than a static landing page.
- If the site publishes an OpenAPI spec, it is used automatically and nothing is sniffed. Point at one directly with `--spec <url>`.
- Large specs: keep only some areas with `--tags repos,issues`.

---

## How it works

1. **Spec check.** Probes about 20 standard locations (`/openapi.json`, `/swagger.json`, ...). If a real spec exists, it is parsed directly.
2. **Traffic sniffing.** Otherwise, launches headless Chromium, loads the page, scrolls and clicks through it, and records every same-site JSON request. Analytics and ad trackers are filtered out.
3. **Inference.** An LLM reads the captured requests and responses and infers each endpoint's path, parameters and types.
4. **Condensation.** An LLM collapses the endpoints into at most 15 `verb_noun` tools, with descriptions written so an agent knows when to use each one.
5. **Serving.** The result is a small JSON file. `machine-internet serve` exposes it as a standard MCP server (stdio by default, SSE with `--sse`) that makes real calls to the real site.

---

## Commands

```bash
machine-internet wrap <url> [--name NAME] [--spec URL] [--tags a,b] [--sniff] [-o FILE]
machine-internet serve <name-or-file.json> [--sse --port 8000]
machine-internet list                  # bundled examples and servers you have wrapped
machine-internet config <name>         # print the MCP client config block
```

Wrapped servers are saved to `~/.machine-internet/schemas/`.

**APIs that need auth:** credentials are read from the environment when the server runs: `MI_TOKEN` (bearer), `MI_API_KEY` (plus `MI_API_KEY_NAME`, `MI_API_KEY_IN=header|query`), `MI_USERNAME` / `MI_PASSWORD`, or `MI_TOKEN_URL` / `MI_CLIENT_ID` / `MI_CLIENT_SECRET` for OAuth2 client credentials. Put them in the `env` block of your MCP client config.

---

## Limitations

- **Internal APIs change without notice.** A wrapped site can break when it ships a new frontend. Re-run `wrap` to refresh it.
- **It only sees what the page does while loading and being clicked.** Actions behind logins, multi-step forms or heavy anti-bot protection usually are not captured.
- **LLM output varies.** If the tools look wrong, run `wrap` again or narrow the page you point it at.

## Use responsibly

Undocumented APIs are still someone else's service. Check the site's terms before wrapping it, keep request volume reasonable, and do not use this to get around access controls or rate limits.

---

## How it compares

| | Machine Internet | OpenAPI-to-MCP converters | Browser agents |
|---|---|---|---|
| Works without an API spec | Yes | No | Yes |
| Agent calls the API directly (fast, cheap per call) | Yes | Yes | No, drives a browser every time |
| One command from URL to MCP server | Yes | Needs a spec | No |

Related work worth knowing: [Integuru](https://github.com/Integuru-AI/Integuru) generates integration code from captured traffic, and [mitmproxy2swagger](https://github.com/alufers/mitmproxy2swagger) builds OpenAPI specs from recorded traffic. Machine Internet's focus is going straight from a URL to a running MCP server.

---

## Development

```bash
git clone https://github.com/chetanty/machine_internet
cd machine_internet
pip install -e ".[dev]"
pytest
python evals/score.py      # condensation quality evals (needs an LLM key)
```

## License

Apache 2.0
