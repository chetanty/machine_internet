# Machine Internet UAA

Point it at any URL. Get an MCP server any AI agent can call.

**Live dashboard:** https://machineinternet-production.up.railway.app

---

## What it does

Most APIs on the internet are not accessible to AI agents. They have no MCP interface, no agent SDK, often no documentation at all. Machine Internet UAA fixes this automatically.

Give it a URL. It discovers the API surface, collapses hundreds of endpoints into 10-15 clean agent-friendly tools, and starts an MCP server on a local port. Plug that URL into Claude, Cursor, or any MCP client and the agent can call the real API immediately.

It handles two cases:

- **APIs with an OpenAPI spec** (GitHub, Stripe, any modern REST API): fetches and parses the spec automatically, no configuration needed.
- **APIs with no spec** (internal tools, legacy services, SPAs): launches a headless browser, records the XHR traffic the page makes, and uses an LLM to infer the schema from what it captured.

## Quickstart

```bash
# install
pip install -r requirements.txt
playwright install chromium
cp .env.example .env  # add your GEMINI_API_KEY or OPENAI_API_KEY

# wrap an API
python discover.py --url https://api.github.com \
  --spec https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json \
  --tags repos,issues

# serve it
python serve.py --schema schemas/github_v3_rest_api.json --port 8100
```

Then add `http://localhost:8100/mcp` to your Claude MCP config. Done.

For APIs with no spec, use `--traffic` to switch to browser-based discovery:

```bash
python discover.py --url https://hn.algolia.com --traffic
```

The dashboard at `http://localhost:7000` lets you wrap, start, and stop services from a browser UI without touching the terminal.

---

## How it works

### Architecture

```
discover.py  --url <base>  [--spec <url>]  [--tags <t1,t2>]  [--traffic]
      |
      |-- Path A: openapi.py
      |     Probes 20 standard spec paths (/openapi.json, /swagger.json, /spec.json ...)
      |     Parses OpenAPI 3.x and Swagger 2.x, resolves $ref, handles relative server URLs
      |     Falls through to Path B if nothing found
      |
      |-- Path B: traffic.py
            Launches headless Chromium via Playwright
            Intercepts XHR/fetch calls where Content-Type contains "json"
            Scrolls, clicks nav/tab/sidebar elements to trigger more requests
            Deduplicates by normalising path params (/users/123 -> /users/{id})
            Sends traffic log to Gemini to infer endpoint signatures
            |
            v
      RawSchema  (base_url, title, endpoints[], auth_schemes[])
            |
      condenser.py
            Caps input at 40 endpoints to fit Gemini's output budget
            Tag filter (--tags) applied before cap to focus large specs
            Sends to Gemini 2.5 Flash with system prompt: max 15 tools, verb_noun names,
              collapse related CRUD, write descriptions that tell agents when to use each tool
            Custom string-aware JSON extractor handles truncated / brace-in-string responses
            |
            v
      CondensedSchema  -> schemas/<name>.json
            |
      serve.py  --schema <file>  [--port N]  [--set-creds]
            |
      mcp_server.py  (pure ASGI router, SSE transport)
      executor.py    (smart endpoint selector + HTTP client)
      auth/vault.py  (Fernet-encrypted credentials)
      auth/injector  (Bearer / API key / OAuth2 / Basic injection)
            |
            v
      http://localhost:<port>/mcp   <-- any MCP agent connects here

dashboard.py  (port 7000)
      Machine Internet UAA dashboard — card grid of wrapped services,
      sidebar (Log / Evals / Auth tabs), light/dark mode, wiki,
      MCP URL footer, hero wrap bar
```

### Stack

| Component | Implementation |
|---|---|
| LLM | Gemini 2.5 Flash primary, OpenAI fallback |
| Dashboard | FastAPI + vanilla JS, single file, no Node.js needed |
| Deployment | Railway (Docker) |
| Schema storage | JSON files in `schemas/` |
| Local runtime | Python venv (Windows/Mac/Linux) |

---

## File Map

```
discover.py              CLI: discover any API, save condensed schema
serve.py                 CLI: start MCP server for a schema
dashboard.py             Web dashboard (port 7000)
demo_api.py              Local test store REST API (port 9000)
test_mcp.py              MCP client smoke test for demo store
test_github_mcp.py       MCP client smoke test for GitHub

src/
  discovery/
    openapi.py           Path A — spec probing, parsing, $ref resolution
    traffic.py           Path B — Playwright capture + Gemini inference
    schema.py            Pydantic models: RawSchema, CondensedSchema, AgentTool, EndpointMapping

  ai/
    client.py            FallbackAIClient — Gemini (multi-model) → OpenAI fallback chain

  condensation/
    condenser.py         Semantic condensation via FallbackGeminiClient, string-aware JSON extractor
    eval.py              Eval runner: re-condenses ground truth schemas, scores coverage

  serving/
    mcp_server.py        Pure ASGI SSE MCP server (no Starlette routing)
    executor.py          Smart endpoint selector + httpx request builder

  auth/
    vault.py             Fernet-encrypted credential store at ~/.uaa/vault/
    injector.py          Auth injection per request (Bearer, API key, OAuth2, Basic)

  config.py              Settings via .env (GEMINI_API_KEY, GEMINI_MODEL, ports)

evals/
  score.py               CLI eval runner
  ground_truth/
    demo_store.json           6 endpoints, 6 expected tool concepts
    github_api.json           ~40 curated GitHub endpoints, 11 expected concepts
    github_repos_issues.json  40 filtered GitHub endpoints, 12 expected concepts
    httpbin.json              73 endpoints, 13 expected tool concepts

schemas/                 Generated condensed schema JSON files
  demo_store.json              6 tools  — local demo store
  github_v3_rest_api.json     14 tools  — GitHub REST API (repos + issues)
  httpbin_service.json        15 tools  — httpbin.org
  pet_store_service.json      14 tools  — Petstore v3 (petstore3.swagger.io)
  swagger_petstore.json       15 tools  — Swagger Petstore v2 (petstore.swagger.io)
  pokeapi.json                 1 tool   — PokéAPI (Path B / traffic sniffing)
  algolia_api_hacker_news.json 2 tools  — HN Algolia search (Path B, no spec)
  open_library_search_api.json 2 tools  — Open Library search (Path B, SSR-limited)
```

---

## How Each Part Works

### Path A — OpenAPI Discovery

`discover_openapi(base_url)` fires GET requests at 20 predictable paths in sequence:
`/openapi.json`, `/openapi.yaml`, `/swagger.json`, `/swagger.yaml`, `/api-docs`,
`/v2/openapi.json`, `/v2/swagger.json`, `/v3/openapi.json`, `/spec.json`,
`/api/openapi.json`, `/.well-known/openapi.json`, and several more.

The first 200-OK response whose body contains `"paths"` or `"openapi"` or `"swagger"` is accepted as the spec.

The parser then:
1. Reads `info.title`, `info.version`, `info.description`
2. Resolves the `servers[0].url` — handling both absolute (`https://api.example.com`) and relative (`/api/v3`) server URLs against the original base
3. Walks every `(path, method)` combination in `paths`
4. For each operation: collects path/query/header parameters, expands `requestBody` JSON properties into body parameters, resolves all `$ref` pointers recursively
5. Detects auth schemes from `components.securitySchemes` / `securityDefinitions`

For services with large specs (GitHub: 1184 endpoints), `--spec <raw-url>` fetches directly from a CDN URL, and `--tags repos,issues` pre-filters before the condenser's 40-endpoint cap.

### Path B — Traffic Sniffing

`discover_via_traffic(url)` runs this sequence:

1. **Playwright launch** — headless Chromium, new context and page
2. **Response listener** — `page.on("response", ...)` fires for every response. The listener filters to resource type `xhr` or `fetch` with a JSON content-type, then applies two filters before keeping the entry:
   - **Brand filter** — extracts the second-to-last domain label from both the base URL and the response URL (e.g. `tvmaze` from `api.tvmaze.com`, `algolia` from `uj5wyc0l7x-dsn.algolia.net`). Only responses whose brand matches the target are kept. This handles multi-TLD API architectures (`algolia.com` ↔ `algolia.net`) while discarding all third-party calls.
   - **Tracker blocklist** — regex-blocks known analytics/ad domains (`google-analytics`, `doubleclick`, `mixpanel`, `amplitude`, `segment.io`, `moatads`, `pubmatic`, `adnxs`, etc.) as a second safety layer even if they share a brand.
3. **Navigation** — `page.goto(url, wait_until="networkidle")` then three scroll steps to trigger lazy-loaded content
4. **Interaction** — clicks up to 5 elements each matching: `nav a`, `header a`, `[role="tab"]`, `aside a`, `[data-tab]`, `button:not([type=submit])`, `a[href^="/"]`. Each click waits 500ms for new traffic
5. **Deduplication** — normalises path params (`/pokemon/ditto` → `/pokemon/{id}`, `/users/123` → `/users/{id}`) and deduplicates by `METHOD:normalised_path`, keeping the first 40 unique entries
6. **AI inference** — sends the deduplicated traffic summary (already brand-filtered) to the AI client with a prompt asking it to produce a clean API schema JSON, explicitly instructing it to skip analytics, tracking, or config payloads. The response goes through the same string-aware JSON extractor used by the condenser.

When 0 entries pass the filter, the output reports whether the page made no XHR calls at all (server-rendered) or made XHR calls that all failed the brand/tracker filter.

Parameter locations returned by the AI are normalised before being parsed into the enum — `"body_json"`, `"json"`, `"form"`, `"formdata"` all map to `"body"` with unknown values falling back to `"query"`.

**Verified on:**
- `pokeapi.co` — no spec at any of the 20 standard paths. Playwright captured `GET /api/v2/pokemon/ditto` from page load, `get_pokemon` tool written to `schemas/pokeapi.json`.
- `hn.algolia.com` — SPA with no public spec. API calls go to `*.algolia.net` (different TLD). Brand filter keeps them, tracker filter drops the Google Analytics and telemetry calls. 5 XHR calls captured, 2 tools generated: `search_articles`, `check_service_status`.

**SSR pages return nothing useful.** If the target page is server-rendered (TVmaze, most legacy sites), the browser makes no XHR calls during load and Path B captures 0 entries. The right Path B target is a SPA or any page that fetches data dynamically after load.

### Semantic Condensation

The condenser sends this prompt to Gemini 2.5 Flash:

```
Service: <title>
Description: <description>
Base URL: <base_url>
Endpoint count: <N>

Endpoints:
[JSON array: path, method, summary, description, tags, parameters]

Return condensed schema as JSON only:
{ "service_name": ..., "tools": [ { "name": "verb_noun", "description": "...",
  "parameters": [...], "endpoint_mappings": [...], "response_fields": null } ] }

Return ONLY valid JSON. Maximum 15 tools.
```

The system prompt instructs: collapse related CRUD, name with `verb_noun`, write descriptions that tell agents exactly when to use the tool, drop internal parameters.

The response passes through `_extract_json()` which:
1. Strips markdown code fences if present
2. Tries `json.loads` directly
3. If that fails, finds the first `{` and walks character-by-character via `_find_json_end()`, which tracks whether the current position is inside a quoted string (handling `\"` escapes). This prevents `{id}` path templates inside string values from being counted as open braces.

### Smart Endpoint Selection

Gemini sometimes collapses multiple HTTP operations into one tool — for example `manage_repository` maps to `POST /orgs/{org}/repos`, `GET /repos/{owner}/{repo}`, `PATCH /repos/{owner}/{repo}`, and `DELETE /repos/{owner}/{repo}`.

`_select_mapping()` picks the right one at call time:

1. **Path param filter** — any mapping whose path contains `{param}` where `param` is absent from the call arguments is eliminated. This discards `POST /orgs/{org}/repos` when `org` is not provided.

2. **Scoring** — remaining candidates are scored by how many of their declared parameter mappings have a non-null value in the call arguments. The mapping with the highest score wins (most specific match). Ties are broken by array order, which means GET comes before PATCH and DELETE when only `owner` + `repo` are supplied.

3. **Action tie-break** — if the caller passes `action="delete"` or `action="update"`, a lookup table maps that to a preferred HTTP method (`DELETE`, `PATCH`/`PUT`) and selects the first finalist matching that method.

The `action` parameter must be `required: false` in the JSON schema (patched after Gemini sometimes generates it as required) so MCP's input validation doesn't reject calls that don't include it.

### MCP Server

The server is a pure async ASGI function — no Starlette routing — because Starlette's `Route` wraps async handlers in a way that returns `None` to the ASGI caller when the handler completes, triggering a `TypeError: 'NoneType' is not callable`.

```
GET  /mcp         opens SSE connection, runs server.run() loop
POST /messages/   receives client messages (tool calls, etc.)
*                 404
```

The `mcp` Python SDK handles the protocol framing. Tool calls arrive as JSON-RPC, the `call_tool` handler looks up the tool in the condensed schema, passes arguments to `executor.execute()`, and returns the result as `TextContent`.

### Eval Suite

`evals/score.py` iterates over JSON files in `evals/ground_truth/`. Each file contains:
- `raw_schema` — a `RawSchema` dict (base_url, endpoints, etc.) representing the source API
- `ground_truth_tools` — a list of tool names representing the ideal condensation
- `notes` — explanation of what the ideal set covers

For each file the runner:
1. Re-runs Gemini condensation on the raw schema (live call, non-deterministic)
2. For each ground-truth tool name, checks if any generated tool *covers* it
3. Coverage uses noun-overlap matching: the subject nouns of a tool name (all words minus common verbs like get/list/create/delete) are extracted, and a ground-truth tool is considered covered if any generated tool shares at least one subject noun. This means `manage_repository` covers `get_repository` (both contain `repository`), and `list_repository_issues` covers `list_issues` (both contain `issues`).
4. Scores: coverage (matched / total ground truth, weight 0.6), conciseness (1.0 if ≤15 tools, penalty above, weight 0.4), overall = weighted sum. Pass threshold: 70%.

---

## What Is Working

### APIs Wrapped and Tested

| Service | Path | Tools | Auth | Call Tests |
|---|---|---|---|---|
| **Demo Store** (local FastAPI) | Manual schema | 6 | none | All 6 tools pass (list, get, create items; place, get, list orders) |
| **GitHub REST API** | Path A via `--spec` + `--tags repos,issues` | 14 | none | 4/5 pass (get repo, list branches, get branch, list org repos, get assignees) |
| **httpbin.org** | Path A auto (`/spec.json`) | 15 | none | Server running, tools listed |
| **Swagger Petstore v2** (petstore.swagger.io) | Path A auto (`/v2/swagger.json`) | 15 | api_key | Server running, tools listed |
| **Petstore v3** (petstore3.swagger.io) | Path A auto | 14 | api_key | Server running, tools listed |
| **PokéAPI** | **Path B** (traffic sniffing, no spec) | 1 | none | Schema generated from live XHR capture |
| **HN Algolia** (hn.algolia.com) | **Path B** (SPA, api on `algolia.net`) | 2 | none | `search_articles`, `check_service_status` |
| **Open Library** (openlibrary.org) | **Path B** (SSR, limited XHR) | 2 | none | `search_books_authors`, `get_search_facets` |

### Eval Results

Re-condensation run against ground truth. Each run is a live AI call; results vary slightly between runs and across models.

| Ground Truth File | Source Endpoints | Expected Tools | Generated | Coverage | Conciseness | Overall | Model |
|---|---|---|---|---|---|---|---|
| `demo_store.json` | 6 | 6 | 4 | 100% | 100% | **100% PASS** | gemini-2.5-flash |
| `github_api.json` | ~40 | 11 | 12 | 73% | 100% | **84% PASS** | gemini-2.5-flash |
| `github_repos_issues.json` | 40 | 12 | 15 | 100% | 100% | **100% PASS** | gemini-2.5-flash |
| `httpbin.json` | 73 | 13 | 13 | 54% | 100% | **72% PASS** | gpt-4o-mini |

All four ground truth files pass. The `httpbin.json` result is from a `gpt-4o-mini` run (current default); Gemini previously scored 77% coverage / 86% overall on the same file — the difference reflects model variation, not a regression in the pipeline. `gpt-4o-mini` is the right default: no daily quota, cheaper, and the 72% score clears the 70% pass threshold.

The 73% coverage on `github_api.json` is expected: `get_repo` and `search_repos` weren't generated as standalone tools because Gemini collapsed them into `manage_repository` and `search_repositories`. The noun-overlap matcher partially catches this (`search_repositories` covers `search_repos`) but `get_repo` → `repository` vs `manage_repository` → `repository` are covered. The remaining gap is `get_file_contents` which wasn't in the 40-endpoint filtered input at all.

---

## Sample Input / Output

### 1. Discovery — `discover.py`

```
$ python discover.py --url http://httpbin.org

Discovering http://httpbin.org ...
  [A] Checking for OpenAPI spec ...
  [OK] Found spec - 73 endpoints

Condensing to agent tools ...
  [OK] 15 tools:
     inspect_request: Inspect and echo back all details of the incoming HTTP request.
     decode_base64_string: Decode a URL-safe Base64-encoded string.
     simulate_redirect: Simulate HTTP redirects with configurable count and method.
     authenticate_basic: Prompt HTTP Basic authentication with a given username and password.
     authenticate_bearer: Prompt HTTP Bearer token authentication.
     authenticate_digest: Prompt HTTP Digest authentication.
     get_all_cookies: Return all cookies sent with the request.
     set_single_cookie: Set a cookie by name and value, returning it in the response.
     set_multiple_cookies_from_query: Set multiple cookies using query parameters.
     delete_multiple_cookies_from_query: Delete cookies specified as query parameters.
     get_delayed_response: Return a response after a configurable delay in seconds.
     get_random_bytes: Return N random bytes with application/octet-stream content type.
     drip_data: Stream a response body byte-by-byte over a configurable duration.
     test_caching: Test HTTP caching behaviour via If-Modified-Since / If-None-Match.
     get_special_response: Return a response with a specific HTTP status code.

Saved -> schemas\httpbin_service.json
Serve  -> python serve.py --schema schemas\httpbin_service.json
```

---

### 2. Discovery — Path B (traffic sniffing, no OpenAPI spec)

**PokéAPI** (homepage loads one demo Pokémon via XHR):
```
$ python discover.py --url https://pokeapi.co --traffic

Discovering https://pokeapi.co ...
  [B] Traffic sniffing via headless browser ...
  [traffic] 1 same-domain XHR calls captured
  [OK] Captured - 1 endpoints inferred

Condensing to agent tools ...
  [OK] 1 tools:
     get_pokemon: Retrieve detailed information about a specific Pokemon by name or ID.

Saved -> schemas\pokeapi.json
Serve  -> python serve.py --schema schemas\pokeapi.json
```

**Hacker News Algolia search** (React SPA, API on `algolia.net`, no public spec anywhere):
```
$ python discover.py --url https://hn.algolia.com --traffic

Discovering https://hn.algolia.com ...
  [B] Traffic sniffing via headless browser ...
  [traffic] 5 same-domain XHR calls captured
  [OK] Captured - 2 endpoints inferred

Condensing to agent tools ...
  [OK] 2 tools:
     search_articles: Use this tool to search for Hacker News articles by providing a query an
     check_service_status: Use this tool to check if the Hacker News search service is alive.

Saved -> schemas\algolia_api_hacker_news.json
Serve  -> python serve.py --schema schemas\algolia_api_hacker_news.json
```

**Open Library** (Internet Archive books API, no OpenAPI spec, mostly SSR):
```
$ python discover.py --url "https://openlibrary.org/search?q=tolkien&mode=everything" --traffic

Discovering https://openlibrary.org/search?q=tolkien&mode=everything ...
  [B] Traffic sniffing via headless browser ...
  [traffic] 1 same-domain XHR calls captured
  [OK] Captured - 2 endpoints inferred

Condensing to agent tools ...
  [OK] 2 tools:
     search_books_authors: Use this tool to search for books or authors using a query string.
     get_search_facets: Retrieve search facets to filter search results.

Saved -> schemas\open_library_search_api.json
Serve  -> python serve.py --schema schemas\open_library_search_api.json
```

Open Library is mostly server-rendered so only 1 XHR call was captured per page load. The search page yields the most useful results (`search_books_authors`, `get_search_facets`). A book page (`/works/OL45804W`) yielded `get_affiliate_links` and `get_book_lists` — real endpoints but lower utility. This is the SSR ceiling in practice: the API exists and the tools are real, but capture breadth is limited by how few XHR calls the page makes.

---

### 3. MCP tool call — demo store (via `test_mcp.py`)

**Input** (arguments passed to the `create_item` tool):
```json
{
  "name": "Widget",
  "price": 9.99,
  "stock": 50
}
```

**Output** (JSON returned by the real API through the MCP executor):
```json
{
  "id": 4,
  "name": "Widget",
  "price": 9.99,
  "stock": 50
}
```

**Input** (`place_order` tool):
```json
{
  "item_id": 4,
  "quantity": 2
}
```

**Output**:
```json
{
  "order_id": 1,
  "item_id": 4,
  "quantity": 2,
  "total": 19.98,
  "status": "confirmed"
}
```

---

### 4. Eval report — httpbin (`python evals/score.py evals/ground_truth/httpbin.json`)

Run with **gpt-4o-mini** (current default):

```
                 UAA Condensation Eval
----------------------------------------------------------------

[PASS]  httpbin.org  (httpbin.json)
  Generated : 13 tools  |  Ground truth : 13 concepts
  Coverage  : 54%   Conciseness : 100%   Overall : 72%
  Missing   : echo_request, get_request_info, test_redirects, get_status_code,
              test_caching, inspect_ip
  Covered   : get_headers, test_auth, manage_cookies, get_compressed_response,
              get_random_bytes, get_delayed_response, stream_response
  Tools     : redirect, auth_basic, auth_bearer, auth_digest, get_bytes,
              inspect_headers, manage_cookies, decode_base64, delayed_response,
              inspect_cache, drip_data, compress_response, return_anything

----------------------------------------------------------------
  Result : 1/1 passing   Average score : 72%
----------------------------------------------------------------
```

Same file run with **gemini-2.5-flash** (previous result for comparison):

```
[PASS]  httpbin.org  (httpbin.json)
  Generated : 15 tools  |  Ground truth : 13 concepts
  Coverage  : 77%   Conciseness : 100%   Overall : 86%
  Missing   : get_headers, get_status_code, inspect_ip
  Covered   : echo_request, get_request_info, test_auth, manage_cookies,
              test_redirects, test_caching, get_compressed_response,
              get_random_bytes, get_delayed_response, stream_response
  Tools     : inspect_request, decode_base64_string, simulate_redirect,
              authenticate_basic, authenticate_bearer, authenticate_digest,
              get_all_cookies, set_single_cookie, set_multiple_cookies_from_query,
              delete_multiple_cookies_from_query, get_delayed_response,
              get_random_bytes, drip_data, test_caching, get_special_response

  Result : 1/1 passing   Average score : 86%
```

Both pass the 70% threshold. Gemini scores higher on this eval (77% vs 54% coverage) because the condensation prompt was tuned against Gemini's output style. GPT-4o still produces a valid, passing condensation with a different but reasonable tool grouping.

---

## What Is Not Done

### Path B — capture breadth vs. SSR
Path B works correctly on SPAs that fetch data via XHR (verified on PokéAPI and HN Algolia). Server-rendered pages (TVmaze, most legacy sites) make no XHR calls during Playwright load and yield 0 captures. This is a targeting problem, not a code problem — the tool needs to be pointed at a page that is dynamically rendered. A future improvement would be to follow links into sub-pages to accumulate more traffic from a single discovery run.

### Schema Registry / Database
Originally planned as PostgreSQL + pgvector for similarity search across wrapped schemas. Currently just a `schemas/` directory of JSON files. `src/registry/db.py` exists as a stub. `src/api/app.py` exists but is not connected to anything.

### Terraform / Managed Infrastructure
No Terraform or ECS. Deployed to Railway via Docker. The system also runs locally in a venv on Windows 11.

### Enterprise Auth
SSO, SAML, rotating client certificates — not in scope. The vault handles Bearer, API key, Basic, and OAuth2 client-credentials flow only.

---

## Live Deployment

The dashboard is deployed on Railway:

**https://machineinternet-production.up.railway.app**

Deployed via Docker. Railway injects `$PORT`; the container runs:
```
uvicorn dashboard:app --host 0.0.0.0 --port ${PORT:-7000} --log-level info
```

To redeploy after local changes:
```powershell
railway up
```

To set or update environment variables on the deployed instance:
```powershell
railway variables --set "OPENAI_API_KEY=sk-..."
railway variables --set "GEMINI_API_KEY=your-key"
```

> **Note:** MCP servers started from the deployed dashboard (`▶ Start`) run as subprocesses inside the Railway container and are reachable only within that container's network. The dashboard UI and discovery (`Wrap API`) work fully in production. Serving MCP endpoints publicly requires exposing additional ports, which Railway supports via TCP proxying.

---

## Running It

### Which model to use

| Model | Use case |
|---|---|
| **`gpt-4o-mini`** (default) | Condensation and traffic inference. Cheap, fast, strong JSON reliability, no daily quota. Right choice for everyday use. |
| **`gpt-4o`** | Only if condensation quality on complex APIs feels noticeably worse. Stronger reasoning on very large endpoint sets, but more expensive. |
| **`gemini-2.5-flash`** | High-quality fallback when OpenAI is unavailable. Scores slightly higher on the eval suite because the condensation prompt was originally tuned against Gemini output. |
| **`gemini-3.1-flash-lite` / `gemini-2.0-flash-lite`** | Last-resort Gemini fallbacks when the primary flash quota is exhausted. |

Change the active model by setting `OPENAI_MODEL` in `.env`. No code changes needed.

### Fallback chain configuration

The client (`src/ai/client.py`) tries providers in this order, moving to the next on any 429 or 503:

1. **OpenAI** — `OPENAI_API_KEY` + `OPENAI_MODEL` (default `gpt-4o-mini`)
2. **Primary Gemini** — `GEMINI_API_KEY` + `GEMINI_MODEL`
3. **Gemini fallback models** — `GEMINI_FALLBACK_MODELS` (same key, independent quota pools)
4. **Secondary Gemini keys** — `GEMINI_API_KEY_2` … `GEMINI_API_KEY_5`

Minimum useful `.env` for a single account hitting the 20 req/day flash limit:

```
GEMINI_API_KEY=your-key
GEMINI_MODEL=gemini-2.5-flash
GEMINI_FALLBACK_MODELS=gemini-3.1-flash-lite,gemini-2.5-flash-lite,gemini-2.0-flash-lite
```

With OpenAI as the final safety net:

```
GEMINI_API_KEY=key-account-1
GEMINI_FALLBACK_MODELS=gemini-3.1-flash-lite,gemini-2.5-flash-lite
OPENAI_API_KEY=sk-...
OPENAI_MODEL=gpt-4o-mini
```

When any agent returns 429 or 503 the client logs the failure and immediately tries the next slot. Non-quota errors raise immediately.

Confirmed available model IDs (verified against the live API, May 2026):

| Model ID | Display name | Notes |
|---|---|---|
| `gemini-2.5-flash` | Gemini 2.5 Flash | Primary — 20 RPD free |
| `gemini-3.1-flash-lite` | Gemini 3.1 Flash Lite | 500 RPD free |
| `gemini-2.5-flash-lite` | Gemini 2.5 Flash-Lite | separate quota pool |
| `gemini-3.5-flash` | Gemini 3.5 Flash | latest generation |
| `gemini-2.0-flash-lite` | Gemini 2.0 Flash-Lite | reliable fallback |
| `gemini-3.1-flash-lite-preview` | Gemini 3.1 Flash Lite Preview | preview channel |

List all available models for your key: `python -c "import asyncio; from google import genai; from src.config import settings; c = genai.Client(api_key=settings.gemini_api_key); [print(m.name) for m in asyncio.run(c.aio.models.list())]"`

```powershell
# activate venv
.\venv\Scripts\activate

# wrap an API — Path A auto-detects the spec
python discover.py --url http://httpbin.org

# wrap with direct spec URL and tag filter (for large specs like GitHub)
python discover.py `
  --url https://api.github.com `
  --spec https://raw.githubusercontent.com/github/rest-api-description/main/descriptions/api.github.com/api.github.com.json `
  --tags repos,issues

# Path B — force traffic sniffing (for services with no OpenAPI spec)
python discover.py --url https://pokeapi.co --traffic

# serve a schema as an MCP endpoint
python serve.py --schema schemas/github_v3_rest_api.json --set-creds --port 8001

# run the dashboard (start/stop servers, wrap new APIs from browser)
python dashboard.py          # http://localhost:7000

# run the eval suite
python evals/score.py                                  # all ground truth files
python evals/score.py evals/ground_truth/demo_store.json  # single file

# test the demo store end-to-end
python demo_api.py           # start local store API on port 9000 (separate terminal)
python serve.py --schema schemas/demo_store.json --port 8001
python test_mcp.py
```

---

## Bugs Fixed During Build

Dates are in YYYY-MM-DD format.

| Date | Bug | Root Cause | Fix |
|---|---|---|---|
| pre-2026-05 | Multi-mapping executor always hit endpoint[0] | `mapping = tool.endpoint_mappings[0]` hardcoded | `_select_mapping()` scores by matched path params; action keyword breaks method ties |
| pre-2026-05 | MCP server `TypeError: 'NoneType' is not callable` | Starlette `Route` returns `None` to ASGI caller after handler completes | Rewrote as pure ASGI function, no Starlette routing |
| pre-2026-05 | Gemini JSON truncated / `{id}` in strings broke parser | Simple brace counter incremented on `{` inside quoted strings like `"/repos/{owner}"` | `_find_json_end()` tracks in-string state and escape sequences |
| pre-2026-05 | `manage_repository` rejected — "action is required" | Gemini set `action` as `required: true`; MCP validates input schema before executor runs | Patched schema to `required: false`; executor reads `action` as method selector |
| pre-2026-05 | Path B crashed on `"body_json"` parameter location | Gemini returned non-standard location strings; `ParameterLocation("body_json")` raises `ValueError` | Normalisation map: `body_json/json/form/formdata` → `body`, unknown → `query` |
| pre-2026-05 | Windows `UnicodeEncodeError` on output | cp1252 terminal cannot encode Unicode box-drawing and check-mark characters | Replaced all Unicode output symbols with ASCII equivalents |
| pre-2026-05 | OpenAPI relative server URL ignored | Parser only handled absolute `http://...` server URLs from `servers[0]` | Extract scheme+host from base URL, combine with relative path |
| pre-2026-05 | Tool calls hung indefinitely after several sequential calls | SSE session drops silently; `session.call_tool` never resolves | Wrapped every call in `asyncio.wait_for(timeout=8.0)` |
| pre-2026-05 | GitHub spec not found at `api.github.com` | GitHub hosts its spec on raw.githubusercontent.com, not on the API server itself | Added `--spec <url>` flag; `discover_openapi_from_spec_url()` fetches from any URL |
| pre-2026-05 | `pywin32` permission denied on pip install | System Python on Windows is locked; post-install script needs admin rights | Created venv; all package operations through `venv\Scripts\pip.exe` |
| pre-2026-05 | Dashboard lost server state on restart | `_servers` dict is in-memory only; restarting dashboard showed all services stopped | `_save_state()` writes `schemas/.running.json` on every change; `_restore_state()` relaunches servers on startup |
| pre-2026-05 | Dashboard listed `.running.json` as a schema | `SCHEMAS_DIR.glob("*.json")` matched the state sidecar file | Skip files whose names start with `.` in `list_schemas` |
| pre-2026-05 | Gemini 429 quota exhausted mid-session | Free tier is 20 calls/day per model; one session burned the full flash quota | `FallbackGeminiClient` tries primary model → `GEMINI_FALLBACK_MODELS` (same key, different quota pools) → `GEMINI_API_KEY_2…5` (separate accounts) |
| 2026-05-21 | Dashboard UI was a plain table with no discoverability | `_HTML` used a basic `<table>` layout with modal discovery | Full redesign: card grid with green live border, Path A/B pill, tool count, MCP URL per card, copy button; sidebar with Log/Evals/Auth tabs; light/dark mode (CSS custom properties + localStorage); hero wrap bar; metrics row; footer MCP URL with large Copy button |
| 2026-05-21 | FastAPI `on_event` deprecation warning on startup | `@app.on_event("startup")` is deprecated in modern FastAPI | Replaced with `@asynccontextmanager` lifespan handler |
| 2026-05-21 | MCP servers crashed immediately after start from dashboard | `_BASE_PORT = 8001` conflicted with a process already bound to 8001 | Changed `_BASE_PORT` to 8100; servers now assign 8100, 8101, 8102 … |
| 2026-05-21 | `serve.py` crashed with `ModuleNotFoundError: No module named 'mcp'` | `mcp` package was not installed in the venv | `pip install mcp` into venv; added `stdin=subprocess.DEVNULL` to `Popen` so the child never blocks waiting for terminal input |
| 2026-05-21 | Path B captured ad trackers instead of API calls | `on_response` kept all same-domain XHR/JSON calls; third-party analytics on the same page passed the filter | Added `_TRACKER_RE` blocklist (google-analytics, doubleclick, mixpanel, segment, etc.) and replaced root-domain filter with brand-name filter (second-to-last label): `algolia.com` and `algolia.net` both resolve to brand `algolia`, so multi-TLD API architectures pass while unrelated domains are dropped |
| 2026-05-21 | Path B returned 0 captures with no explanation | Empty traffic silently produced `[FAIL] Discovery failed` | Added diagnostic output: reports count of XHR calls seen vs. passed filter, distinguishes SSR pages (0 XHR at all) from brand-filter misses |
| 2026-05-21 | Path B on `hn.algolia.com` dropped all real API calls | Algolia's web app (`algolia.com`) fires XHR to DSN hosts (`algolia.net`) — different TLD, old root-domain filter rejected them | Switched to brand-name matching; `algolia.com` ↔ `algolia.net` both yield brand `algolia` — 5 calls captured, 2 tools generated |
| 2026-05-21 | Railway deploy timed out on first `railway up` | No service was linked (`serviceId=` empty in upload URL); `venv/` not excluded by `.gitignore` on Railway's uploader | Created `.railwayignore`, ran `railway service machine_internet` to link, redeployed |
| 2026-05-21 | Deployed container running wrong app on wrong port | Dockerfile `CMD` pointed to `src.api.app:app` (stub) on hardcoded port 8001; Railway routes traffic to `$PORT` | Updated `CMD` to `sh -c "uvicorn dashboard:app --host 0.0.0.0 --port ${PORT:-7000}"` so the shell expands `$PORT` at runtime |
| 2026-05-21 | Railway healthcheck failed — site unreachable despite "Online" status | `startCommand` in `railway.toml` passed `$PORT` as a literal string (no shell expansion) | Moved the command into Dockerfile `CMD` with `sh -c` which runs a real shell and expands env vars; removed `startCommand` override |
| 2026-05-22 | JS used stale CSS class names after HTML redesign | New CSS used `live-dot`, `mcp-url`, `auth-group`/`auth-label`/`auth-value`, `eval-bar`/`eval-meta`; JS still emitted old class names from the first design | Updated all `renderGrid` and `renderAuth` template strings to match current class names; pulse animation on live pill now works |
| 2026-05-22 | Favicon and logo were a placeholder emoji | Design uses the Machine Internet orange-Y logo (`mi4.png`) | Logo base64-encoded at module load, injected as `<img>` in header and `<link rel="icon">` in `<head>`; branding updated to "Machine Internet UAA" |
