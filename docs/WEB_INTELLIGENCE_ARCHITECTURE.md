# Web Intelligence Architecture — M7

## Responsibilities

The Web Intelligence subsystem gives Pearl access to current external information
when the user's question cannot be answered from the local repository alone. It is
responsible for:

1. **Search** — Querying a search provider and collecting candidate URLs.
2. **Fetch** — Safely retrieving page content over HTTP with timeout and size limits.
3. **Extract** — Converting raw HTML into clean, structured text.
4. **Rank / Deduplicate** — Scoring results by relevance and removing near-duplicates.
5. **Bound** — Assembling a `WebContext` that respects the LLM token budget.
6. **Cite** — Preserving source attribution through to the final answer.

---

## Layer Placement

Web Intelligence lives in **Layer 3 (Tools)** — the same layer as
`src/tools/` and `src/repository/`. This matches the M3 pattern exactly:

```
src/web/           Layer 3 — Web Intelligence module
  __init__.py
  models.py        Data types (SearchResult, WebDocument, WebSource, WebContext)
  search.py        WebSearcher interface + DuckDuckGoSearcher
  fetch.py         Safe HTTP page fetcher
  extract.py       HTML → clean text extractor (BeautifulSoup4)
  rank.py          Relevance scoring + deduplication
  service.py       WebIntelligenceService — orchestrates the pipeline

src/tools/web_tools.py   Layer 3 — @tool functions that expose web_search,
                                    web_fetch, web_context to the planner
```

**Dependency rule (unchanged):** `src/web/` never imports from `src/agent/`,
`src/mcp/`, or `src/tools/`. `src/tools/web_tools.py` imports from `src/web/`
downward only. The planner/executor access web capabilities through the tool
registry — the same path every other tool takes.

---

## Data Flow

```
User request
    │
    ▼
Planner (Layer 4)
    │  decides web_context tool is needed
    ▼
Executor → ToolDispatcher → web_context(query)
    │
    ▼
WebIntelligenceService.build_context(query)
    │
    ├─ WebSearcher.search(query) → SearchResult[]
    │       DuckDuckGoSearcher (default, no API key)
    │
    ├─ PageFetcher.fetch(url) × N → RawPage[]
    │       httpx, timeout=15s, max 5 MB, security validation
    │
    ├─ ContentExtractor.extract(raw) → WebDocument[]
    │       BeautifulSoup4: strip nav/scripts/ads, keep headings+body
    │
    ├─ rank_and_deduplicate(docs, query) → WebDocument[]
    │       TF-IDF-like scoring, URL normalization dedup, top-K
    │
    └─ WebContext (query, sources[], evidence[], token budget)
            │
            ▼
        Formatted string → LLM context
            │
            ▼
        LLM answer with inline citations
```

---

## Interfaces

### Search

```python
class WebSearcher(Protocol):
    def search(self, query: str, max_results: int) -> list[SearchResult]: ...
```

`SearchResult` carries: `url`, `title`, `snippet`, `source` (domain).
Implementations: `DuckDuckGoSearcher` (no API key, uses HTML endpoint).
The provider is selected by `Settings.WEB_SEARCH_PROVIDER`; new providers
implement `WebSearcher` and are added to the factory without changing callers.

### Fetch

```python
class PageFetcher:
    def fetch(self, url: str) -> RawPage: ...
```

Returns `RawPage(url, content_type, body_bytes, final_url)`.
Raises `FetchError` for all failure modes: timeout, too large, bad content-type,
blocked IP, unsupported scheme. Never raises unclassified exceptions to callers.

### Extract

```python
class ContentExtractor:
    def extract(self, raw: RawPage) -> WebDocument: ...
```

`WebDocument` carries: `url`, `title`, `text`, `headings`, `retrieved_at`.
`text` is clean prose — no HTML tags, no script content, no navigation boilerplate.

### Rank

```python
def rank_and_deduplicate(
    docs: list[WebDocument], query: str, max_results: int
) -> list[WebDocument]: ...
```

Scores each document against the query using word-overlap TF-IDF.
Deduplicates by normalized URL (query-string stripping) and near-duplicate text
(first 200 chars Jaccard similarity ≥ 0.8 → drop the lower-scored copy).

### Context

```python
@dataclass
class WebContext:
    query: str
    sources: list[WebSource]     # URL + title + domain
    evidence: list[str]          # Formatted per-source evidence blocks
    total_chars: int             # Sum of evidence char lengths
```

`WebIntelligenceService.build_context()` returns a `WebContext`. The tool
function `web_context()` converts it to a formatted string for the LLM.

---

## Security Boundaries

**Private IP blocking.** `PageFetcher` rejects any URL whose resolved IP falls
in RFC 1918 / RFC 3927 / loopback ranges before sending the request. This is
enforced at DNS-resolution time on the resolved address. Failure mode: `FetchError`.

**Blocked schemes.** Only `http://` and `https://` are accepted. `file://`,
`ftp://`, `data:`, and all other schemes are rejected immediately.

**Redirect following.** httpx follows redirects by default (max 5). The final
URL after redirects is re-validated against the private-IP blocklist.

**Content-type validation.** Only `text/html`, `text/plain`, and
`application/xhtml+xml` responses are extracted. Binary/media responses
are rejected with `UnsupportedContentTypeError`.

**Response size cap.** Responses larger than `Settings.WEB_MAX_RESPONSE_BYTES`
(default 5 MB) are rejected. The Content-Length header is checked before
reading; streaming reads cut off at the limit.

**Timeout.** `Settings.WEB_FETCH_TIMEOUT` (default 15 s) applies to both
connect and read. No request can block indefinitely.

---

## Token / Character Budget

`WebIntelligenceService` enforces `Settings.WEB_EVIDENCE_CHARS` (default 2000)
per source. The total evidence string sent to the LLM is therefore bounded by
`WEB_MAX_RESULTS × WEB_EVIDENCE_CHARS` characters (default 5 × 2000 = 10 000).

The evidence for each source is extracted by taking the passage most relevant
to the query (first 2000 chars of extracted text if no better signal). The
full page is never sent to the LLM.

---

## Failure Handling

| Failure | Behaviour |
|---------|-----------|
| Search provider error | `web_search` returns empty list + error message; `web_context` surfaces "search unavailable" |
| Fetch timeout | Source skipped; remaining sources processed |
| Private IP blocked | Source skipped |
| Oversized response | Source skipped |
| Malformed HTML | ContentExtractor falls back to raw text stripping |
| All sources fail | `web_context` returns context with 0 sources; LLM is told no web info available |
| Search returns 0 results | Surfaces "no results found" in context |

Pearl never freezes, crashes, or fabricates citations when web retrieval fails.

---

## Provider Independence

Web search and page retrieval are completely independent of the LLM provider:

- `ModelRouter` is NOT consulted during web retrieval.
- `LLMClient` is NOT called during search/fetch/extract.
- The LLM receives extracted evidence as plain text — the same way it receives
  repository context from `SemanticContextBuilder`.

---

## Integration with SemanticContextBuilder

When both repository and web context are needed, the planner may call:
1. `repo_context(symbols=...)` → repository evidence string
2. `web_context(query=...)` → web evidence string

Both evidence strings arrive as separate tool results in the executor's result
set and are included in the LLM's context on the reflection step. No coupling
between `SemanticContextBuilder` and `WebIntelligenceService` is required.

---

## Citation Preservation

Each `WebSource` carries its `url` and `title` through the entire pipeline.
The formatted evidence block sent to the LLM is:

```
Source: <title>
URL: <url>
---
<evidence text>
```

The LLM is instructed (via the tool description) to cite sources by title
and URL when using web evidence. Pearl does not allow the LLM to invent URLs;
citations must reference only URLs that appear in the retrieved `WebContext`.

---

## When to Use the Web

The planner determines tool selection from the task description. The tool
descriptions deliberately steer towards appropriate use:

- `web_search` — "Use when current/external information is needed that is not
  in the local repository. Do NOT use for repository-specific questions."
- `web_context` — "Combines search + fetch + extract into ready-to-use evidence.
  Use for factual questions about external libraries, versions, documentation,
  or current events."

The planner is expected to:
- Route pure conversation to no tool.
- Route pure repository questions to repository tools.
- Route current-information questions to web tools.
- Route combined questions to both.

---

## Retries

Search: No retry (search provider failure is surfaced immediately).
Fetch: Single attempt; timeout failure skips the source.
Rationale: retrying a slow or overloaded server delays the user.
A skipped source is better than a frozen agent.

---

*Pearl Engineering Standards — docs/engineering/ applies.*
*Layer rule: src/web/ → Layer 3. Never import from Layer 4 (agent) or Layer 5 (mcp).*
