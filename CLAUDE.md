# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
# Setup (first time)
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

# Run the API server (recommended, port 8000)
uvicorn api.webhook:app --host 0.0.0.0 --port 8000 --reload

# Alternative (port 8001 hardcoded in app.py)
python app.py

# Run tests
pytest
pytest tests/test_plan_validator_rules.py   # single test file
pytest tests/ -k "rag"                      # filter by name
```

## Architecture

**FastAPI + LangGraph** autonomous code generation agent. Accepts generation requests from the Node.js backend, runs an agentic graph, and streams events to Ably.

### Entry Point
`api/webhook.py` — FastAPI app with lifespan hooks that pre-warm templates, Redis checkpointer, and Ably REST client. Main endpoints: `POST /generate`, `POST /generate/{job_id}/resume`, `POST /generate/{job_id}/build-result`.

### LangGraph Graph (`agent/graph_logic.py`)
Nodes execute in this order:
```
template_selection → plan → approval (HITL interrupt) → generation → code_review
→ persistence → trigger_build → reflexion (loop up to MAX_REFLEXION_ITERATIONS=5)
→ escalation (if unfixable)
```
- **`plan_node`** (Architect): GPT-4o generates a structured implementation plan
- **`generation_node`** (Builder): Executes plan tasks, writes to virtual `file_system` dict using MCP tools
- **`code_review_node`**: Runs `CodeQualityReviewer`, auto-fixes, then single LLM correction if 1–3 errors remain
- **`persistence_node`**: Uploads `file_system` files to Azure Blob Storage, notifies Node.js backend
- **`reflexion_node`** (Debugger): Analyzes build errors and patches specific files

### State (`agent/state_engine.py`)
`AgentState` TypedDict is the graph's shared memory across all nodes. Key fields:
- `file_system: Dict[str, str]` — virtual FS mapping path → code content
- `implementation_plan: List[Dict]` — task list from Architect
- `iteration_count: int` — guards the build-fix loop (incremented in `code_review_node`)
- `visual_context: Optional[Dict]` — injected for modification requests from Visual Edit mode
- `build_status: str` — `"pending" | "success" | "failed" | "escalate"`

Checkpointing: Redis (via `REDIS_URL`) for persistence across restarts; falls back to in-memory `MemorySaver`.

### Code Quality System (`agent/code_quality.py`, `agent/plan_validator.py`)
- `plan_validator.py` runs 5 rule checks **before** any LLM call: protected file guard, duplicate task merger, template component rewriter, path convention checker, auto-add `loading.tsx`
- `CodeQualityReviewer` runs after each file is generated; auto-fixes common issues
- `template_corrections.json` — seed correction patterns (C001–C005); self-improves when `reflexion.py` sees recurring fixes 2+ times

### RAG System (`agent/template_rag.py`, `agent/template_chunker.py`)
- Pre-built template components in `templates/nextjs-app/components/` have JSDoc `@component`/`@description`/`@example` blocks
- RAG score threshold: 0.60; `usage_example` chunks boosted 1.2×; top 6 results injected into prompts
- Template injection and correction injection are currently **disabled** in `execution_layer.py` (~lines 1977–1991) due to Azure content filter jailbreak detection — do not re-enable without testing against Azure OpenAI

### Code Generation Rules (from `BUILDER_PROMPT`)
- **Notifications**: `import { toast } from 'sonner'` only — never `react-hot-toast`/`react-toastify`
- **Tables**: `import { DataTable } from '@/components/data/DataTable'` — never direct `@tanstack/react-table`
- **Sidebar**: use pre-built with `navLinks` prop, not custom implementations
- **Paths**: pages → `app/page-name/page.tsx`, not `app/page-name.tsx`

### MCP Tools (`agent/execution_layer.py` — `MCPWrapper`)
Tools with `args_schema` (JSON schema validation) require dict arguments — string payloads are skipped automatically. Do not pass bare strings to tools that have `args_schema`.

### Environment Variables
Required: `AZURE_OPENAI_ENDPOINT`, `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_DEPLOYMENT_NAME`, `AZURE_OPENAI_API_VERSION`, `ABLY_API_KEY`, `NODE_BACKEND_URL`, `NODE_BACKEND_WEBHOOK_SECRET`

Optional: `REDIS_URL` (recommended for persistence), `CONTAINER_BUILD_URL`, `FASTAPI_WEBHOOK_SECRET`, `ABLY_CHANNEL_PREFIX` (default: `ai-backend-generation`)
