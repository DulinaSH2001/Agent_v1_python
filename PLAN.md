# Agent Performance Improvement Plan

## Overview

This plan improves generation speed from ~8–12s to ~3s by:

- Replacing local FAISS + sentence-transformers RAG with Pinecone (free hosted embeddings)
- Caching graph compilation, Redis connections, and RAG query results
- Parallelizing MCP tool calls
- Fixing Ably WebSocket SSL errors

---

## Current Bottlenecks (from logs)

```
INFO:api.webhook:Received generation request
  ↓ ~1-2s  ← Graph compilation (per-request)
INFO:api.webhook:Building graph with HITL enabled
  ↓ ~300ms ← Template file loading (35 files, synchronous)
INFO:agent.template_manager:Loaded 35 files from nextjs-app
  ↓ ~1-3s  ← MCP servers connecting sequentially (stdio startup)
INFO:agent.execution_layer:Configuring MCP server 'next-devtools'
INFO:agent.execution_layer:Configuring MCP server 'shadcn'
INFO:agent.execution_layer:Configuring MCP server 'github'
INFO:agent.execution_layer:Configuring MCP server 'brave-search'
INFO:agent.execution_layer:Configuring MCP server 'puppeteer'
  ↓ ~500ms-2s ← Ably WebSocket SSL failure + retry cycle
WARNING: [SSL: CERTIFICATE_VERIFY_FAILED]
  ↓ ~2-3s  ← Local sentence-transformers model cold start
INFO:agent.execution_layer:Successfully connected to 5 MCP servers
```

---

## Phase 1 — Pinecone RAG (Replaces FAISS + sentence-transformers)

**Files:** `agent/template_rag.py`, `scripts/build_rag_index.py`, `requirements.txt`

### What changes

**`requirements.txt`:**

```diff
- faiss-cpu>=1.8.0
- sentence-transformers>=3.0.0
- numpy>=1.24.0
+ pinecone>=5.0.0
```

**`agent/template_rag.py` — full rewrite of `TemplateRAG` class:**

- **Embedding model**: `multilingual-e5-large` via Pinecone Inference API — free, cloud-hosted, 1024-dim, no local download
  ```python
  pc.inference.embed(
      model="multilingual-e5-large",
      inputs=[text],
      parameters={"input_type": "passage"}  # or "query" for retrieval
  )
  ```
- **Index**: Pinecone Serverless (`metric=cosine`, `dimension=1024`, `cloud=aws`, `region=us-east-1`)
- **Namespaces**: one namespace per template (e.g. `nextjs-app`) — all templates share one index
- `build_index()`: chunk with existing `TemplateChunker` → embed in batches of 96 → upsert with metadata `{file_path, chunk_type, template}`
- `retrieve()`: embed query with `"input_type": "query"` → `index.query()` → return `(TemplateChunk, score)` pairs
- **Keep identical public API**: `retrieve_relevant_chunks()`, `get_rag_context_for_prompt()`, `get_template_rag()` — zero changes to callers
- **LRU cache**: keyed on `(query_hash, template_name, top_k)` — avoids re-embedding duplicate queries within a session

**`scripts/build_rag_index.py` — full rewrite:**

- Connect to Pinecone, create serverless index if missing
- Chunk + embed + upsert all templates into their namespaces
- `--force`: delete namespace before re-upsert
- `--template nextjs-app`: upsert a single template only

### New env vars

| Variable              | Example                 |
| --------------------- | ----------------------- |
| `PINECONE_API_KEY`    | `pcsk_abc123...`        |
| `PINECONE_INDEX_NAME` | `antigravity-templates` |

### Estimated savings

| Source                            | Saved                    |
| --------------------------------- | ------------------------ |
| Local model cold-start eliminated | **−2–3s**                |
| ~600MB dependency removed         | faster Docker/startup    |
| LRU cache (dedup queries)         | **−1–3s per generation** |

---

## Phase 2 — Graph Compilation Cache

**File:** `api/webhook.py`

### What changes

Add module-level `_compiled_graphs: Dict[str, Any] = {}` dict at the top of `webhook.py`.

In `run_generation_task()`:

```python
cache_key = f"reflexion={enable_reflexion}_skip_approval={skip_approval}"
if cache_key not in _compiled_graphs:
    _compiled_graphs[cache_key] = create_antigravity_graph(
        checkpointer=checkpointer,
        enable_reflexion=enable_reflexion,
        skip_approval=skip_approval,
    )
graph = _compiled_graphs[cache_key]
```

LangGraph compiled graphs are **stateless** — all state lives in the checkpointer, not the graph object. Safe to reuse across requests.

Same pattern for `_resume_approval_process()` and the modification graph.

### Estimated savings

- **−200–400ms per request** (graph compiled once per process lifetime, not per request)

---

## Phase 3 — Parallel MCP Tool Calls

**File:** `agent/execution_layer.py`

### What changes

In `gather_mcp_context()`, replace sequential tool invocations:

```python
# BEFORE (sequential)
for tool, payloads in tool_payload_pairs:
    result, error, used_args = await _invoke_tool_best_effort(tool, payloads)

# AFTER (parallel)
coros = [_invoke_tool_best_effort(t, p) for t, p in tool_payload_pairs]
raw_results = await asyncio.gather(*coros, return_exceptions=True)
for res in raw_results:
    if isinstance(res, Exception):
        # log as warning, skip
        continue
    result, error, used_args = res
```

### Estimated savings

- **−1–2s per file task** (3–5 tools × ~500ms sequential → ~500ms concurrent total)

---

## Phase 4 — Async Template File Loading

**File:** `agent/template_manager.py`

### What changes

Replace blocking `open()` loop in `_load_template_files()` with `aiofiles` (already in `requirements.txt`):

```python
# BEFORE (synchronous, sequential)
for file_path in template_dir.rglob("*"):
    with open(file_path, 'r', encoding='utf-8') as f:
        content = f.read()

# AFTER (async, parallel)
async def _read_file(path: Path) -> tuple[str, str]:
    async with aiofiles.open(path, 'r', encoding='utf-8') as f:
        content = await f.read()
    return str(path.relative_to(template_dir)).replace("\\", "/"), content

results = await asyncio.gather(*[_read_file(p) for p in file_paths], return_exceptions=True)
```

Also ensure `get_template_manager()` singleton is never re-instantiated per request.

### Estimated savings

- **−300–500ms** (35 template files read in parallel)

---

## Phase 5 — Redis Connection Singleton

**File:** `api/webhook.py`

### What changes

Use FastAPI `lifespan` context manager to create the checkpointer once at startup:

```python
from contextlib import asynccontextmanager

_global_checkpointer = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _global_checkpointer
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        _global_checkpointer = await AsyncRedisSaver.from_conn_string(redis_url)
    else:
        _global_checkpointer = _memory_checkpointer
    yield
    # cleanup on shutdown if needed

app = FastAPI(..., lifespan=lifespan)
```

`run_generation_task()` and `_resume_approval_process()` use `_global_checkpointer` directly.

### Estimated savings

- **−100–300ms per request** (no new TCP handshake to Redis per request)

---

## Phase 6 — Ably SSL Fix + HTTP-Only Publishing

**Files:** `api/webhook.py`, `agent/execution_layer.py`

### Problem

`publish_tool_use()` in `execution_layer.py` creates a new `AblyRealtime` (WebSocket) client per tool event:

```
→ WebSocket connect attempt
→ SSL: CERTIFICATE_VERIFY_FAILED  (macOS cert store issue)
→ Connection closing → closed
→ Falls back to HTTP anyway
```

This happens 5+ times per generation.

### What changes

**Replace `AblyRealtime` with `AblyRest`** for all server-side publishes:

```python
# BEFORE (WebSocket, fails on SSL)
client = AblyRealtime(api_key)
await channel.publish("status", payload)
await client.close()

# AFTER (HTTP/2, already working)
client = AblyRest(api_key, use_binary_protocol=False)
await channel.publish("status", payload)
```

**Add `_ably_rest` module-level singleton** in `execution_layer.py`:

```python
_ably_rest: Optional[AblyRest] = None

def get_ably_rest() -> Optional[AblyRest]:
    global _ably_rest
    if _ably_rest is None:
        api_key = os.getenv("ABLY_API_KEY")
        if api_key:
            _ably_rest = AblyRest(api_key, use_binary_protocol=False)
    return _ably_rest
```

**Add `certifi` SSL fix** to all Ably client initializations:

```python
import certifi
import os
os.environ.setdefault("REQUESTS_CA_BUNDLE", certifi.where())
os.environ.setdefault("SSL_CERT_FILE", certifi.where())
```

### Estimated savings

- **−500ms–2s** (eliminates 5+ WebSocket connect/SSL-fail/close cycles per generation)

---

## Phase 7 — Per-Request RAG Query Cache

**File:** `agent/execution_layer.py`

### What changes

Add a local dict cache inside `generation_node()` before the task loop:

```python
import hashlib

_rag_cache: Dict[str, str] = {}

for i, task in enumerate(plan):
    description = task.get("description", "")
    cache_key = hashlib.md5(description.encode()).hexdigest()[:12]

    if cache_key in _rag_cache:
        task_rag_context = _rag_cache[cache_key]
    else:
        task_rag_results = retrieve_relevant_chunks(
            query=description,
            task_description=f"{task_type} {file_path}: {description}",
            top_k=3,
        )
        # ... format context ...
        _rag_cache[cache_key] = task_rag_context
```

### Estimated savings

- **−1–3s per generation** (avoids 20–30 duplicate Pinecone queries when file descriptions overlap)

---

## Summary: Before vs After

| Bottleneck                       | Before     | After             | Saved      |
| -------------------------------- | ---------- | ----------------- | ---------- |
| RAG model cold start             | 2–3s       | 0s                | **~2.5s**  |
| Graph compilation (per-request)  | 200–400ms  | 0ms (cached)      | **~300ms** |
| MCP tool calls (sequential)      | 1.5–2.5s   | ~500ms (parallel) | **~1.5s**  |
| Template file loading            | 300–500ms  | ~50ms (async)     | **~400ms** |
| Redis new connection per request | 100–300ms  | 0ms (singleton)   | **~200ms** |
| Ably WebSocket SSL failures      | 500ms–2s   | 0ms (HTTP only)   | **~1s**    |
| Duplicate RAG queries (35 files) | 1–3s       | ~0ms (cached)     | **~2s**    |
| **Total**                        | **~8–12s** | **~2–3s**         | **~7–9s**  |

---

## Files Changed

| File                         | Change                                                                       |
| ---------------------------- | ---------------------------------------------------------------------------- |
| `requirements.txt`           | Remove `faiss-cpu`, `sentence-transformers`, `numpy` → Add `pinecone>=5.0.0` |
| `agent/template_rag.py`      | Full rewrite — FAISS → Pinecone Inference API                                |
| `scripts/build_rag_index.py` | Full rewrite — upsert to Pinecone namespaces                                 |
| `api/webhook.py`             | Graph cache + Redis singleton + lifespan + Ably REST fix                     |
| `agent/execution_layer.py`   | Parallel MCP + Ably REST singleton + RAG per-request cache                   |
| `agent/template_manager.py`  | Async file loading with `aiofiles`                                           |

---

## Implementation Order (recommended)

| Order | Phase                            | Reason                                                                                  |
| ----- | -------------------------------- | --------------------------------------------------------------------------------------- |
| 1     | Phase 1 — Pinecone RAG           | Biggest impact; run `build_rag_index.py` first to populate index before starting server |
| 2     | Phase 6 — Ably SSL fix           | Quick win, visible immediately in logs                                                  |
| 3     | Phase 2 — Graph cache            | Easy 3-line change, immediate benefit                                                   |
| 4     | Phase 5 — Redis singleton        | Easy change, reduces per-request overhead                                               |
| 5     | Phase 3 — Parallel MCP           | Medium change, significant savings per file                                             |
| 6     | Phase 7 — RAG cache              | Small change, large savings for multi-file generations                                  |
| 7     | Phase 4 — Async template loading | Requires async refactor in template_manager                                             |
