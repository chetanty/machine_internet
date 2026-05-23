# Machine Internet UAA

Point it at any URL. Get an MCP server any AI agent can call.

**Live:** https://machine-internet.onrender.com/

---

## The problem

Most APIs on the internet are not accessible to AI agents. They speak HTTP, not MCP. They have no agent SDK, no tool definitions, and often no public documentation at all. Every new integration is a custom build.

Machine Internet UAA eliminates that. Give it a URL and it produces a working MCP endpoint in seconds — one that any agent, Claude, Cursor, or any MCP client can call against the real service with no additional code.

---

## How it works

**Step 1: Discovery.** It tries to find an OpenAPI spec automatically by probing 20 standard paths. If it finds one, it parses it. If there is no spec, it launches a headless browser, observes the XHR traffic the page makes, and uses an LLM to infer the schema from what was captured. Every API on the internet is a target.

**Step 2: Condensation.** Raw API specs can have hundreds of endpoints. An LLM collapses them into 10-15 clean, agent-friendly tools with `verb_noun` names and descriptions that tell agents exactly when to use each one. A 400-endpoint CRM becomes `get_customer`, `create_deal`, `log_interaction`.

**Step 3: Serving.** The condensed schema is saved locally and served as a standard MCP SSE endpoint. Paste the URL into any MCP client. The agent makes real calls against the real service.

---

## Quickstart

```bash
git clone https://github.com/chetanty/machine_internet
cd machine_internet
pip install -r requirements.txt
playwright install chromium
cp .env.example .env   # add GEMINI_API_KEY or OPENAI_API_KEY
```

Wrap any API with a public spec:

```bash
python discover.py --url https://api.github.com \
  --spec https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json \
  --tags repos,issues
python serve.py --schema schemas/github_v3_rest_api.json --port 8100
```

Add `http://localhost:8100/mcp` to your Claude MCP config. Done.

Wrap an API with no spec using traffic sniffing:

```bash
python discover.py --url https://hn.algolia.com --traffic
python serve.py --schema schemas/algolia_api_hacker_news.json --port 8101
```

Run the dashboard to manage everything from a browser:

```bash
python dashboard.py   # http://localhost:7000
```

---

## What it can wrap

| Service | Method | Tools generated |
|---|---|---|
| GitHub REST API | OpenAPI spec | 14 tools: repos, issues, branches |
| httpbin.org | OpenAPI spec (auto-detected) | 15 tools |
| Stripe, Petstore, any OpenAPI service | OpenAPI spec | up to 15 tools |
| PokéAPI | Traffic sniffing (no spec) | `get_pokemon` |
| HN Algolia search | Traffic sniffing (React SPA, API on different TLD) | `search_articles`, `check_service_status` |
| Open Library | Traffic sniffing (partial, SSR-limited) | `search_books_authors`, `get_search_facets` |

Traffic sniffing works on SPAs that load data via XHR. Server-rendered pages yield fewer captures. The right target is any page that fetches data dynamically after load.

---

## Claude MCP config

```json
{
  "mcpServers": {
    "github": {
      "url": "http://localhost:8100/mcp"
    },
    "algolia": {
      "url": "http://localhost:8101/mcp"
    }
  }
}
```

---

## Configuration

Copy `.env.example` to `.env` and fill in at least one key:

```
GEMINI_API_KEY=your-key        # free tier: 20 req/day on flash
OPENAI_API_KEY=sk-...          # no daily quota, gpt-4o-mini default
```

The AI client tries OpenAI first, then Gemini, then fallback Gemini models, then secondary Gemini keys. Add more keys to `GEMINI_API_KEY_2` through `GEMINI_API_KEY_5` to increase capacity.

---

## Auth

Credentials are stored encrypted via Fernet in `~/.uaa/vault/`. The server injects them per request as Bearer token, API key header, Basic auth, or OAuth2 client credentials flow.

```bash
python serve.py --schema schemas/github_v3_rest_api.json --set-creds
```

---

## Deployment

Deployed on Railway via Docker. The live dashboard at https://machineinternet-production.up.railway.app runs discovery and wrapping in the cloud. MCP servers launched from the deployed instance are accessible within the container network.

To deploy your own instance:

```bash
railway up
```

---

## Project structure

```
discover.py       discover any API and save a condensed schema
serve.py          serve a schema as an MCP endpoint
dashboard.py      web UI: wrap, start, stop services

src/
  discovery/      Path A (OpenAPI) and Path B (traffic sniffing)
  condensation/   LLM-based schema condensation and eval runner
  serving/        MCP server (pure ASGI SSE) and executor
  auth/           encrypted credential vault and request injector
  ai/             Gemini + OpenAI fallback client

evals/            ground truth schemas and scoring
```

For a full technical breakdown of the architecture, design decisions, eval results, and build log, see [BUILD_REPORT.md](BUILD_REPORT.md).
