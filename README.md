# Antigravity Agent (Python)

LangGraph + FastAPI service that generates Next.js code, supports human approval, and streams status updates.

## What This Service Does

- Accepts a generation request (`query`, `job_id`, `user_id`, optional `manifest`).
- Builds an implementation plan and pauses for approval.
- On approval, generates project files and sends them to your backend webhook.
- Publishes realtime events to Ably.
- Accepts build-error feedback and attempts automatic code fixes.

## Project Location

`/Users/dulina/Documents/Research Project/Platform/Agent_v1_python`

## Prerequisites

- Python `3.11+`
- `pip`
- Network access to your model provider (Azure OpenAI or OpenAI)

## Quick Start (Local)

```bash
cd "/Users/dulina/Documents/Research Project/Platform/Agent_v1_python"

python3 -m venv .venv
source .venv/bin/activate

pip install --upgrade pip
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your real values
```

Start the API server (recommended):

```bash
uvicorn api.webhook:app --host 0.0.0.0 --port 8000 --reload
```

Health check:

```bash
curl http://localhost:8000/health
```

Expected response:

```json
{"status":"healthy","version":"0.5.0"}
```

## Alternative Start Command

You can also run:

```bash
python app.py
```

Note: `app.py` is currently hardcoded to port `8001`, while `api/webhook.py` defaults to `API_PORT` (default `8000`).

## Environment Variables

Copy from `.env.example` and set these values.

### Required For Normal Platform Flow

- `AZURE_OPENAI_ENDPOINT`
- `AZURE_OPENAI_API_KEY`
- `AZURE_OPENAI_DEPLOYMENT_NAME`
- `AZURE_OPENAI_API_VERSION`
- `ABLY_API_KEY`
- `NODE_BACKEND_URL`
- `NODE_BACKEND_WEBHOOK_SECRET`

### Required For Persistent Checkpointing Across Restarts

- `REDIS_URL` (recommended, native Redis URL)

Example:

```env
REDIS_URL=redis://default:password@your-redis-host:6379
```

Without `REDIS_URL`, the agent falls back to in-memory checkpointing (state is lost on restart).

### Optional

- `ABLY_CHANNEL_PREFIX` (default: `ai-backend-generation`)
- `API_HOST` (default: `0.0.0.0`)
- `API_PORT` (default: `8000`)
- `WEBHOOK_SECRET` (validates legacy callback signatures)
- `CONTAINER_BUILD_URL`
- `BACKEND_URL`
- `FASTAPI_WEBHOOK_SECRET`
- `PINECONE_API_KEY`, `PINECONE_INDEX_NAME` (template RAG index)
- `MCP_DOCS_SERVER_URL` or `MCP_DOCS_SERVER_COMMAND`
- `AZURE_STORAGE_CONNECTION_STRING`
- `OPENAI_API_KEY` (fallback if not using Azure OpenAI)

## Main API Endpoints

- `GET /health`
- `POST /api/v1/generate`
- `GET /api/v1/generate/{job_id}`
- `GET /api/v1/generate/{job_id}/files`
- `POST /api/v1/generate/{job_id}/approve`
- `POST /api/v1/generate/{job_id}/reject`
- `POST /api/v1/generate/{job_id}/build-error`
- `POST /api/v1/chat/{job_id}`
- `POST /api/v1/chat/{job_id}/modify`

Legacy endpoints:

- `POST /callbacks/build-status/{thread_id}`
- `POST /callbacks/human-response/{thread_id}`

## API Usage Examples

### 1) Start Generation

```bash
curl -X POST "http://localhost:8000/api/v1/generate" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "Create a modern dashboard with KPI cards and table",
    "user_id": "user-001",
    "job_id": "job-001",
    "manifest": {
      "name": "sample-backend",
      "endpoints": [
        {"path": "/api/users", "method": "GET"},
        {"path": "/api/users", "method": "POST"}
      ]
    },
    "data_mode": "real_api",
    "api_base_url": "http://localhost:8080"
  }'
```

### 2) Check Job Status

```bash
curl "http://localhost:8000/api/v1/generate/job-001"
```

### 3) Approve Generated Plan

```bash
curl -X POST "http://localhost:8000/api/v1/generate/job-001/approve" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001"
  }'
```

### 4) Reject Plan With Feedback

```bash
curl -X POST "http://localhost:8000/api/v1/generate/job-001/reject" \
  -H "Content-Type: application/json" \
  -d '{
    "user_id": "user-001",
    "feedback": "Use tabs instead of side navigation"
  }'
```

### 5) Report Build Errors For Auto-Fix

```bash
curl -X POST "http://localhost:8000/api/v1/generate/job-001/build-error" \
  -H "Content-Type: application/json" \
  -d '{
    "phase": "dev",
    "errors": [
      "Module not found: Cannot resolve ./components/Button"
    ],
    "fullOutput": "full build log text here",
    "retries_used": 0
  }'
```

## Realtime Status Channel

Ably channel name format:

`{ABLY_CHANNEL_PREFIX}:{job_id}`

Default prefix:

`ai-backend-generation`

Common statuses you will receive include:

- `started`
- `specs_extracted`
- `approved`
- `rejected`
- `completed`
- `failed`
- `reflexion_progress`
- `reflexion_escalate`

## Validate Your Setup

Run the setup test script:

```bash
python tests/test_setup.py
```

Run full tests:

```bash
pytest -q
```

## Common Issues

### Port confusion (`8000` vs `8001`)

- `uvicorn api.webhook:app ...` default is `8000` (or `.env` `API_PORT`).
- `python app.py` is currently hardcoded to `8001`.

### Job not found (404)

- Confirm you are querying the same `job_id` you submitted.
- Server restart clears in-memory `_active_jobs`.
- Use `REDIS_URL` if you need durable checkpointing.

### No realtime events

- Verify `ABLY_API_KEY` is set and valid.
- Confirm frontend subscribes to `ai-backend-generation:{job_id}` or your custom prefix.

### State does not resume after restart

- Set a valid `REDIS_URL`.
- Upstash REST credentials alone may not provide full LangGraph checkpoint persistence.

## Related Docs

- `DOCUMENTATION.md` (full architecture and flow)
- `USER_GUIDE.md` (workflow guide)
- `PINECONE_SETUP.md` (RAG index setup)
- `SSL_CERTIFICATE_FIX.md` (SSL troubleshooting)
