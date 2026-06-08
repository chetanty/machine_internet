# Machine Internet

> MCP endpoints for APIs that will never have documentation.

Most of the internet has no OpenAPI spec and never will. Every SPA, every internal tool, every API that only a browser has ever seen. Machine Internet connects AI agents to all of it. Paste a URL, get a working MCP endpoint.

[Bundlephobia](https://bundlephobia.com) has no public API. [caniuse.com](https://caniuse.com) has no public API. Machine Internet wraps both in under 30 seconds.

**Live:** [machine-internet.onrender.com](https://machine-internet.onrender.com)

![License](https://img.shields.io/badge/license-Apache%202.0-blue)
![Python](https://img.shields.io/badge/python-3.10%2B-blue)
![MCP](https://img.shields.io/badge/MCP-compatible-orange)

---

## Use a hosted endpoint right now

Works with Claude Desktop, Cursor, Cline, and any MCP-compatible client.

```json
{
  "mcpServers": {
    "github": {
      "command": "npx",
      "args": ["mcp-remote", "https://machine-internet.onrender.com/mcp/github_v3_rest_api", "--transport", "sse-only"]
    },
    "hn-search": {
      "command": "npx",
      "args": ["mcp-remote", "https://machine-internet.onrender.com/mcp/algolia_api_hacker_news", "--transport", "sse-only"]
    },
    "httpbin": {
      "command": "npx",
      "args": ["mcp-remote", "https://machine-internet.onrender.com/mcp/httpbin_service", "--transport", "sse-only"]
    }
  }
}
```

Or wrap any API yourself at [machine-internet.onrender.com](https://machine-internet.onrender.com). Paste a URL, get an endpoint.

---

## Available hosted endpoints

| API | Endpoint | Tools |
|---|---|---|
| [Bundlephobia](https://bundlephobia.com) | `/mcp/bundlephobia` | 6: size, tree-shaking check, history, similar, per-export sizes, recent |
| [caniuse.com](https://caniuse.com) | `/mcp/caniuse` | 4: search features, browser support, feature details, news |
| [GitHub Issues](https://docs.github.com/en/rest/issues/issues) | `/mcp/github_v3_rest_api` | 15: list, create, update, comment, label |
| [HN Algolia Search](https://hn.algolia.com/api) | `/mcp/algolia_api_hacker_news` | 2: search articles, status |
| [httpbin](https://httpbin.org) | `/mcp/httpbin_service` | 15: inspect, auth, redirect |
| [Open Library](https://openlibrary.org) | `/mcp/open_library` | 2: books, affiliate links |
| [PokéAPI](https://pokeapi.co) | `/mcp/pokeapi` | 1: get Pokemon |

All endpoints are live and free. Base URL: `https://machine-internet.onrender.com`

Want an API that isn't listed? Wrap it yourself in 30 seconds on the dashboard.

---

## Demo

<!-- GIF coming soon: terminal running discover.py against httpbin, 73 endpoints to 15 tools -->

![Machine Internet Dashboard](assets/dashboard.png)
*The Machine Internet dashboard. Wrapped services, live MCP endpoints, one-click URL copy.*

![Claude calling GitHub data via MCP](assets/claude_mcp.png)
*Claude calling live [GitHub](https://github.com/microsoft/vscode) data through a Machine Internet MCP endpoint. 17,938 open issues as of today.*

---

## How it works

**Path B: Traffic sniffing (the hard part)**
When no spec exists, launches headless Chromium, observes the XHR and fetch calls the page makes while navigating and scrolling, and uses an LLM to infer a clean schema from the captured traffic. Works on any SPA, any internal tool, any API that has no public documentation. This is the part nobody else does.

**Path A: OpenAPI spec detection**
Probes 20 standard locations for an OpenAPI or Swagger spec. If found, parses the entire API surface automatically. Zero LLM cost. Useful for documented APIs where you want an MCP endpoint without writing any code.

**Condensation**
Raw specs can have hundreds of endpoints. An LLM collapses them into 10-15 clean tools with `verb_noun` names and descriptions that tell agents exactly when to use each one. A 400-endpoint CRM becomes `get_customer`, `create_deal`, `log_interaction`.

**Serving**
The condensed schema is served as a standard SSE-based MCP server. Any MCP-compatible client connects immediately and makes real calls against the real service.

---

## Who this is for

**Agent developers** who need to connect to an API that has no MCP server, no OpenAPI spec, and no plans to publish either.

**Claude Desktop and Cursor users** who want to add any tool to their agent without writing integration code.

**Teams with internal APIs** behind a firewall, documented only in someone's head or a Confluence page nobody reads.

**Anyone building agents** who hits the wall where the tool they need has never heard of MCP.

---

## Wrap your own API

Using the hosted dashboard (no install):

1. Visit [machine-internet.onrender.com](https://machine-internet.onrender.com)
2. Paste any URL into the wrap bar
3. Copy the MCP endpoint URL
4. Add it to your agent config

Using the CLI (self-hosted):

```bash
# Any API with an OpenAPI spec
python discover.py --url http://httpbin.org

# Large specs: use --spec and --tags to focus
python discover.py \
  --url https://api.github.com \
  --spec https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json \
  --tags issues

# No spec: use traffic sniffing
python discover.py --url https://hn.algolia.com --traffic

# Serve the result
python serve.py --schema schemas/httpbin_service.json --port 8100
```

---

## Verified on

| Service | Method | Notes |
|---|---|---|
| [Bundlephobia](https://bundlephobia.com) | Path B traffic sniff | No public API. 6 tools: size, tree-shaking check, history, similar packages, per-export sizes, recent |
| [caniuse.com](https://caniuse.com) | Path B traffic sniff | No public API. 4 tools: search, browser support, feature details, news |
| [HN Algolia](https://hn.algolia.com/api) | Path B traffic sniff | SPA, API lives on a different TLD than the website |
| [PokéAPI](https://pokeapi.co) | Path B traffic sniff | No spec exists anywhere |
| [Open Library](https://openlibrary.org) | Path B traffic sniff | Mostly server-rendered, search page works |
| [GitHub Issues API](https://docs.github.com/en/rest/issues/issues) | Path A + `--spec --tags issues` | 1,186 endpoint spec, filtered to issues |
| [httpbin.org](https://httpbin.org) | Path A auto-detected | Spec at `/spec.json` |

---

## Pricing

Free tier: wrap public APIs, use hosted endpoints.

Pro ($29/month): private APIs, credential vault, higher rate limits.

Enterprise: self-hosted license for internal APIs behind a firewall.

---

## Self-hosted setup

```bash
git clone https://github.com/chetanty/machine_internet
cd machine_internet
pip install -r requirements.txt
playwright install chromium
cp .env.example .env
```

Add at least one key to `.env`:

```
OPENAI_API_KEY=sk-...        # recommended, no daily quota
GEMINI_API_KEY=your-key      # free tier, 20 req/day on flash
```

The AI client tries OpenAI first, then falls back through multiple Gemini models automatically on quota exhaustion.

Run the dashboard:

```bash
python dashboard.py   # http://localhost:7000
```

---

## Project structure

```
discover.py       discover any API, save condensed schema
serve.py          serve a schema as a live MCP endpoint
dashboard.py      web UI: wrap, start, stop, manage services
src/
  discovery/      Path A (OpenAPI) and Path B (traffic sniffing)
  condensation/   LLM condensation and eval runner
  serving/        pure ASGI SSE MCP server and smart executor
  auth/           Fernet-encrypted credential vault
  ai/             OpenAI + Gemini fallback client
evals/            ground truth schemas and scoring scripts
```

Full technical breakdown: architecture decisions, eval results, build log: [BUILD_REPORT.md](BUILD_REPORT.md)

---

## Contributing

Issues and PRs welcome. If you wrap an API that works well, open a PR to add the schema to the `schemas/` directory and the endpoint to this README.

---

## License

Apache 2.0
