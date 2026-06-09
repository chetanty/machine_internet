# Build Report

Full technical documentation: architecture, design decisions, sample output, and build log.

---

## Architecture

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
            Caps input at 40 endpoints to fit LLM output budget
            Tag filter (--tags) applied before cap to focus large specs
            Sends to Gemini 2.5 Flash: max 15 tools, verb_noun names,
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
      Card grid of wrapped services, per-card call count + uptime display,
      sidebar (Log / Info / Auth tabs), live call log polled via /api/events,
      light/dark mode, wiki modal, MCP URL footer, hero wrap bar
```

---

## Stack

| Original Plan | Actual | Reason |
|---|---|---|
| Claude API | Gemini 2.5 Flash + OpenAI fallback | Access and cost |
| Next.js dashboard | FastAPI + vanilla JS, single file | No Node.js dependency |
| AWS ECS Fargate | Render (Docker) | Simpler, no AWS setup needed |
| PostgreSQL + pgvector | JSON files in `schemas/` | No database needed for local operation |
| Docker Compose | venv (local) + Dockerfile (Render) | Simpler on Windows locally |
| Terraform | Not built | Render handles infrastructure |

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
    client.py            FallbackAIClient — Gemini (multi-model) + OpenAI fallback chain

  condensation/
    condenser.py         Semantic condensation, string-aware JSON extractor
    eval.py              Eval runner: re-condenses ground truth schemas, scores coverage

  serving/
    mcp_server.py        Pure ASGI SSE MCP server (no Starlette routing); on_call callback for telemetry
    executor.py          Smart endpoint selector + httpx request builder

  auth/
    vault.py             Fernet-encrypted credential store at ~/.uaa/vault/
    injector.py          Auth injection per request (Bearer, API key, OAuth2, Basic)

  config.py              Settings via .env

schemas/
  bundlephobia.json      6 tools: size, tree-shaking, history, similar, exports, recent
  caniuse.json           4 tools: search, browser support, feature details, news
  npm_trends.json        4 tools: registry info, downloads, trend range, GitHub stats
  regex101.json          3 tools: browse library, get regex, list versions
  algolia_api_hacker_news.json  2 tools: search, status
  github_v3_rest_api.json       15 tools: issues CRUD, labels, comments
  httpbin_service.json          15 tools: inspect, auth, redirect
  open_library.json             2 tools: books, affiliate links
  pokeapi.json                  1 tool: get Pokemon
  .running.json          (runtime) list of live servers; restored on dashboard startup
  .stats.json            (runtime) per-schema total call counts; persisted across restarts

evals/
  score.py               CLI eval runner
  ground_truth/
    demo_store.json           6 endpoints, 6 expected tool concepts
    github_api.json           ~40 curated GitHub endpoints, 11 expected concepts
    github_repos_issues.json  40 filtered GitHub endpoints, 12 expected concepts
    httpbin.json              73 endpoints, 13 expected tool concepts
```

---

## How Each Part Works

### Path A — OpenAPI Discovery

`discover_openapi(base_url)` fires GET requests at 20 predictable paths in sequence: `/openapi.json`, `/openapi.yaml`, `/swagger.json`, `/swagger.yaml`, `/api-docs`, `/v2/openapi.json`, `/v2/swagger.json`, `/v3/openapi.json`, `/spec.json`, `/api/openapi.json`, `/.well-known/openapi.json`, and several more.

The first 200-OK response whose body contains `"paths"` or `"openapi"` or `"swagger"` is accepted as the spec.

The parser then:
1. Reads `info.title`, `info.version`, `info.description`
2. Resolves the `servers[0].url`, handling both absolute (`https://api.example.com`) and relative (`/api/v3`) server URLs against the original base
3. Walks every `(path, method)` combination in `paths`
4. For each operation: collects path/query/header parameters, expands `requestBody` JSON properties into body parameters, resolves all `$ref` pointers recursively
5. Detects auth schemes from `components.securitySchemes` / `securityDefinitions`

For services with large specs (GitHub: 1184 endpoints), `--spec <raw-url>` fetches directly from a CDN URL, and `--tags repos,issues` pre-filters before the condenser's 40-endpoint cap.

### Path B — Traffic Sniffing

`discover_via_traffic(url)` runs this sequence:

1. **Playwright launch** — headless Chromium, new context and page
2. **Response listener** — `page.on("response", ...)` fires for every response. Filters to `xhr`/`fetch` with JSON content-type, then applies two filters:
   - **Brand filter** — extracts the second-to-last domain label from both the base URL and the response URL (e.g. `algolia` from `uj5wyc0l7x-dsn.algolia.net`). Only responses whose brand matches the target are kept. This handles multi-TLD API architectures (`algolia.com` and `algolia.net`) while discarding all third-party calls.
   - **Tracker blocklist** — regex-blocks known analytics/ad domains (`google-analytics`, `doubleclick`, `mixpanel`, `amplitude`, `segment.io`, `moatads`, `pubmatic`, `adnxs`, etc.)
3. **Navigation** — `page.goto(url, wait_until="networkidle")` then three scroll steps to trigger lazy-loaded content
4. **Interaction** — clicks up to 5 elements each matching `nav a`, `header a`, `[role="tab"]`, `aside a`, `[data-tab]`, `button:not([type=submit])`, `a[href^="/"]`. Each click waits 500ms for new traffic
5. **Deduplication** — normalises path params (`/pokemon/ditto` to `/pokemon/{id}`) and deduplicates by `METHOD:normalised_path`, keeping the first 40 unique entries
6. **AI inference** — sends the deduplicated traffic summary to the LLM to produce a clean schema JSON

When 0 entries pass the filter, the output reports whether the page made no XHR calls at all (server-rendered) or made XHR calls that all failed the brand/tracker filter.

### Semantic Condensation

The condenser sends to Gemini 2.5 Flash:

```
Service: <title>
Base URL: <base_url>
Endpoint count: <N>
Endpoints: [JSON array: path, method, summary, parameters]

Return condensed schema as JSON only:
{ "service_name": ..., "tools": [ { "name": "verb_noun", "description": "...",
  "parameters": [...], "endpoint_mappings": [...] } ] }

Maximum 15 tools.
```

The response passes through `_extract_json()` which strips markdown fences, tries direct `json.loads`, and if that fails walks character-by-character via `_find_json_end()` tracking in-string state so `{id}` path templates inside string values are not counted as open braces.

### Smart Endpoint Selection

Gemini sometimes collapses multiple HTTP operations into one tool. `_select_mapping()` picks the right one at call time:

1. **Path param filter** — mappings whose path contains `{param}` where `param` is absent from the call arguments are eliminated
2. **Scoring** — remaining candidates are scored by how many declared parameter mappings have a non-null value in the arguments. Highest score wins. Ties broken by array order (GET before PATCH/DELETE)
3. **Action tie-break** — if the caller passes `action="delete"` or `action="update"`, a lookup table maps that to the preferred HTTP method

### MCP Server

Pure async ASGI function, no Starlette routing (Starlette's `Route` returns `None` to the ASGI caller after handler completion, triggering `TypeError: 'NoneType' is not callable`).

```
GET  /mcp         opens SSE connection, runs server.run() loop
POST /messages/   receives client messages (tool calls)
*                 404
```

`create_mcp_app` accepts an optional `on_call(tool_name, ok, duration_ms)` callback. The dashboard passes a closure that writes to a capped ring buffer (`_events`, 500 entries). The frontend polls `/api/events?stem=X&since=N` every 2 seconds using sequence numbers for incremental updates, showing live tool calls in the Log tab.

### Eval Suite

`evals/score.py` re-runs LLM condensation on each ground truth schema and scores coverage and conciseness. Ground truth schemas are in `evals/`.

---

## Sample Output

### Path A — httpbin

```
$ python discover.py --url http://httpbin.org

Discovering http://httpbin.org ...
  [A] Checking for OpenAPI spec ...
  [OK] Found spec - 73 endpoints

Condensing to agent tools ...
  [OK] 15 tools:
     inspect_request, decode_base64_string, simulate_redirect,
     authenticate_basic, authenticate_bearer, authenticate_digest,
     get_all_cookies, set_single_cookie, set_multiple_cookies_from_query,
     delete_multiple_cookies_from_query, get_delayed_response,
     get_random_bytes, drip_data, test_caching, get_special_response

Saved -> schemas\httpbin_service.json
```

### Path B — Bundlephobia (no public API, 6 tools from one site)

```
$ python discover.py --url https://bundlephobia.com --traffic

Discovering https://bundlephobia.com ...
  [B] Traffic sniffing via headless browser ...
  [traffic] 6 same-domain XHR calls captured
  [OK] Captured - 6 endpoints inferred

Condensing to agent tools ...
  [OK] 6 tools:
     get_package_size, check_tree_shaking, get_package_history,
     get_recent_packages, get_similar_packages, get_exports_sizes

Saved -> schemas\bundlephobia.json
```

`get_package_size` and `check_tree_shaking` both map to `/api/size` but use `response_fields` to filter the response to the subset relevant to each question: size/gzip/dependencyCount vs hasJSModule/hasSideEffects/isModuleType.

### Path B — HN Algolia (React SPA, API on different TLD, no public spec)

```
$ python discover.py --url https://hn.algolia.com --traffic

Discovering https://hn.algolia.com ...
  [B] Traffic sniffing via headless browser ...
  [traffic] 5 same-domain XHR calls captured
  [OK] Captured - 2 endpoints inferred

Condensing to agent tools ...
  [OK] 2 tools:
     search_articles, check_service_status

Saved -> schemas\algolia_api_hacker_news.json
```

### MCP tool call — demo store

Input to `create_item`:
```json
{ "name": "Widget", "price": 9.99, "stock": 50 }
```

Output:
```json
{ "id": 4, "name": "Widget", "price": 9.99, "stock": 50 }
```

Input to `place_order`:
```json
{ "item_id": 4, "quantity": 2 }
```

Output:
```json
{ "order_id": 1, "item_id": 4, "quantity": 2, "total": 19.98, "status": "confirmed" }
```

---

## Limitations

**Path B and server-rendered pages.** Traffic sniffing works on SPAs (PokéAPI, HN Algolia). Server-rendered pages make no XHR calls during Playwright load and yield 0 captures. The tool needs to be pointed at a page that fetches data dynamically. A future improvement: follow links into sub-pages to accumulate more traffic per discovery run.

**Schema registry.** Originally planned as PostgreSQL + pgvector for similarity search. Currently a `schemas/` directory of JSON files. `src/registry/db.py` is a stub.

**Auth scope.** Bearer, API key, Basic, OAuth2 client-credentials only. No SSO or SAML.

---

## Bugs Fixed

| Date | Bug | Root Cause | Fix |
|---|---|---|---|
| pre-2026-05 | Multi-mapping executor always hit endpoint[0] | `mapping = tool.endpoint_mappings[0]` hardcoded | `_select_mapping()` scores by matched path params; action keyword breaks method ties |
| pre-2026-05 | MCP server `TypeError: 'NoneType' is not callable` | Starlette `Route` returns `None` to ASGI caller after handler completes | Rewrote as pure ASGI function, no Starlette routing |
| pre-2026-05 | Gemini JSON truncated / `{id}` in strings broke parser | Simple brace counter incremented on `{` inside quoted strings | `_find_json_end()` tracks in-string state and escape sequences |
| pre-2026-05 | `manage_repository` rejected as "action is required" | Gemini set `action` as `required: true`; MCP validates before executor runs | Patched schema to `required: false` |
| pre-2026-05 | Path B crashed on `"body_json"` parameter location | Gemini returned non-standard location strings | Normalisation map: `body_json/json/form/formdata` to `body`, unknown to `query` |
| pre-2026-05 | Windows `UnicodeEncodeError` on output | cp1252 terminal cannot encode Unicode box-drawing characters | Replaced all Unicode output symbols with ASCII |
| pre-2026-05 | OpenAPI relative server URL ignored | Parser only handled absolute server URLs | Extract scheme+host from base URL, combine with relative path |
| pre-2026-05 | Tool calls hung indefinitely | SSE session drops silently; `session.call_tool` never resolves | Wrapped every call in `asyncio.wait_for(timeout=8.0)` |
| pre-2026-05 | GitHub spec not found at `api.github.com` | GitHub hosts its spec on raw.githubusercontent.com | Added `--spec <url>` flag |
| pre-2026-05 | Dashboard lost server state on restart | `_servers` dict is in-memory only | `_save_state()` writes `schemas/.running.json`; `_restore_state()` relaunches on startup |
| pre-2026-05 | Gemini 429 quota exhausted mid-session | Free tier 20 calls/day per model | `FallbackAIClient` chains primary model, fallback models, secondary keys |
| 2026-05-21 | FastAPI `on_event` deprecation warning | `@app.on_event("startup")` deprecated in modern FastAPI | Replaced with `@asynccontextmanager` lifespan handler |
| 2026-05-21 | MCP servers crashed immediately after start | `_BASE_PORT = 8001` conflicted with existing process | Changed `_BASE_PORT` to 8100 |
| 2026-05-21 | Path B captured ad trackers | All same-domain XHR/JSON calls kept; analytics passed filter | Added `_TRACKER_RE` blocklist and brand-name filter |
| 2026-05-21 | Path B on `hn.algolia.com` dropped all real API calls | API on `algolia.net` rejected by old root-domain filter | Switched to brand-name matching: second-to-last label only |
| 2026-05-21 | Railway `$PORT` not expanding in start command | `startCommand` in `railway.toml` does not shell-expand env vars | Moved to Dockerfile `CMD` with `sh -c` |
| 2026-05-22 | JS used stale CSS class names after HTML redesign | Old class names left in `renderGrid` and `renderAuth` | Updated all template strings to match current CSS |
| 2026-05-22 | Favicon and logo were a placeholder emoji | No image asset wired up | Logo base64-encoded at module load, injected as `<img>` and `<link rel="icon">` |
| 2026-06-09 | Evals tab still visible after removal | Tab button, panel HTML, CSS classes, JS constant, and `renderEvals()` all survived the partial removal | Removed all five artefacts; updated `showTab` array |
| 2026-06-09 | `liveMap` refactor broke footer and auth URLs | `liveMap` changed to return `{stem, calls, uptime}` objects; callers still did `const stem = lm[file]` expecting a string, producing `[object Object]` in template literals | Updated callers to destructure `const info = lm[file]` |
| 2026-06-09 | Info tab did not deactivate Log/Auth on click | `showTab` array was `['log','auth']`; Info tab added but array not updated | Fixed to `['log','info','auth']` |
| 2026-06-09 | Tool search broken after tools became objects | Search filter called `t.toLowerCase()` after tools changed from strings to `{name, description}` | Fixed to `(t.name\|\|t).toLowerCase()` |
| 2026-06-09 | npm Trends traffic sniff captured zero endpoints | Proxy API lives on `uidotdev.workers.dev`; brand filter discarded it as a different domain | Called proxy API directly; it is publicly accessible without auth |
| 2026-06-09 | Bundlephobia `/api/exports` timed out | Endpoint computes bundle on-demand; only fast when Playwright pre-warms the cache | Excluded from schema; used `/api/exports-sizes` (pre-cached) instead |
