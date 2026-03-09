# Antigravity Agent - Complete Documentation

**Autonomous Next.js Code Generation Agent**

A LangGraph-powered agent that generates production-ready Next.js 16 code with human-in-the-loop approval, self-correction capabilities, and real-time event streaming.

---

## Table of Contents

1. [Overview](#1-overview)
2. [Architecture](#2-architecture)
3. [Installation](#3-installation)
4. [Configuration](#4-configuration)
5. [Core Concepts](#5-core-concepts)
6. [Integration Guide](#6-integration-guide)
7. [API Reference](#7-api-reference)
8. [Webhook Integration](#8-webhook-integration)
9. [Testing](#9-testing)
10. [Troubleshooting](#10-troubleshooting)

---

## 1. Overview

### What is Antigravity Agent?

The Antigravity Agent is an autonomous code generation system that:
- Takes a **backend API manifest** (endpoints, auth, database schema)
- Accepts a **user prompt** describing frontend requirements
- Generates a complete **Next.js 16 application** with proper structure
- Supports **human approval** before code generation
- Includes **self-correction** (Reflexion Loop) for build errors
- Streams events in **real-time** via Ably

### Key Features

| Feature | Description |
|---------|-------------|
| **HITL (Human-in-the-Loop)** | Pause for human approval before generating code |
| **Delta Mode** | Modify existing files without overwriting |
| **Reflexion Loop** | Automatically fix build errors (up to 3 attempts) |
| **MCP Tools** | Access Next.js docs and Shadcn UI patterns |
| **Azure Blob Storage** | Persist generated files to the cloud |
| **Redis Checkpointing** | Resume workflows across sessions |

---

## 2. Architecture

### System Flow

```
┌─────────────────────────────────────────────────────────────────┐
│                        YOUR SYSTEM                               │
│  ┌──────────┐    ┌──────────────┐    ┌──────────────────────┐  │
│  │ Frontend │───▶│ Your Backend │───▶│ Antigravity Agent     │  │
│  │   App    │    │   (API)      │    │ (Python + LangGraph) │  │
│  └──────────┘    └──────────────┘    └───────────┬──────────┘  │
│       ▲                                           │              │
│       │              Ably Events                  │              │
│       └───────────────────────────────────────────┘              │
└─────────────────────────────────────────────────────────────────┘
```

### Agent Graph

```
START
  │
  ▼
┌─────────────┐
│   planner   │◀──────────────────┐
│ (Architect) │                   │
└──────┬──────┘                   │
       │                          │
       ▼                          │ (edit feedback)
┌─────────────┐                   │
│  approval   │───────────────────┘
│ (Gatekeeper)│──── PAUSE (Human Review)
└──────┬──────┘
       │ (approved)
       ▼
┌─────────────┐
│  generator  │◀──────────────────┐
│  (Builder)  │                   │
└──────┬──────┘                   │
       │                          │ (fix tasks)
       ▼                          │
┌─────────────┐                   │
│ persistence │                   │
│ (Uploader)  │                   │
└──────┬──────┘                   │
       │                          │
       ▼                          │
       │
       ▼
      END

Default runtime build-fix path (side-channel):
frontend build error -> POST /api/v1/generate/{job_id}/build-error
                   -> run_build_fix_task -> file_generated/reflexion_progress

Legacy/manual interrupt path:
persistence -> trigger_build -> /callbacks/build-status/{thread_id}
            -> reflexion/escalation loop
```

---

## 3. Installation

### Requirements
- Python 3.11+
- pip3

### Quick Install

```bash
# Clone/navigate to project
cd Agent_v1_python

# Create virtual environment (recommended)
python3 -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip3 install -r requirements.txt
```

### Dependencies Installed

| Package | Purpose |
|---------|---------|
| `langgraph` | Agent graph orchestration |
| `langchain-openai` | Azure/OpenAI LLM integration |
| `langgraph-checkpoint-redis` | State persistence |
| `ably` | Real-time event streaming |
| `azure-storage-blob` | File storage |
| `fastapi` + `uvicorn` | Webhook server |
| `pydantic` | Data validation |

---

## 4. Configuration

### Environment Variables

Create a `.env` file:

```env
# REQUIRED: Azure OpenAI (GPT-4o)
AZURE_OPENAI_ENDPOINT=https://your-resource.cognitiveservices.azure.com
AZURE_OPENAI_API_KEY=your_api_key_here
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o
AZURE_OPENAI_API_VERSION=2025-01-01-preview

# REQUIRED: Redis (for state persistence)
UPSTASH_REDIS_REST_URL=https://your-redis.upstash.io
UPSTASH_REDIS_REST_TOKEN=your_token_here

# REQUIRED: Ably (real-time events)
ABLY_API_KEY=your_ably_key_here

# OPTIONAL: Azure Blob Storage (file upload)
AZURE_STORAGE_CONNECTION_STRING=DefaultEndpointsProtocol=https;AccountName=...

# OPTIONAL: Webhook
WEBHOOK_SECRET=your_secret_here
API_PORT=8000
```

---

## 5. Core Concepts

### AgentState

The central state object that flows through the graph:

```python
class AgentState(TypedDict):
    manifest: Dict[str, Any]          # Backend API definition
    user_prompt: str                   # User's requirements
    file_system: Dict[str, str]        # Generated files (path → code)
    implementation_plan: List[Dict]    # Task list
    build_logs: List[str]              # Error logs
    iteration_count: int               # Fix attempt counter
    messages: Sequence[BaseMessage]    # Chat history
    approved: bool                     # Human approval flag
    build_ready: bool                  # Upload complete flag
    build_status: str                  # "pending"|"success"|"failed"|"escalate"
```

### Manifest Format

```python
manifest = {
    "name": "my-app",
    "endpoints": [
        {
            "path": "/api/users",
            "method": "GET",
            "response": {"type": "User[]"},
            "auth": True
        },
        {
            "path": "/api/users",
            "method": "POST",
            "body": {"type": "CreateUserInput"},
            "response": {"type": "User"}
        }
    ],
    "auth": {
        "type": "jwt",
        "provider": "clerk"  # or "auth0", "supabase"
    },
    "database": {
        "type": "postgresql",
        "orm": "prisma"
    }
}
```

---

## 6. Integration Guide

### Option A: Python Integration

```python
import asyncio
from agent import run_antigravity_agent, resume_antigravity_agent

async def generate_frontend(manifest, requirements, session_id):
    # Step 1: Start planning
    result = await run_antigravity_agent(
        manifest=manifest,
        user_prompt=requirements,
        thread_id=session_id,
    )
    
    # Step 2: Check plan and approve
    plan = result.get("implementation_plan", [])
    # ... show plan to user ...
    
    # Step 3: Approve and generate
    result = await resume_antigravity_agent(
        thread_id=session_id,
        action="APPROVE",  # or "EDIT" with feedback
        feedback=None,     # Required if action="EDIT"
    )
    
    # Step 4: Get generated files
    files = result.get("file_system", {})
    return files

# Usage
files = asyncio.run(generate_frontend(
    manifest=my_manifest,
    requirements="Create a dashboard with dark mode",
    session_id="project-123"
))
```

### Option B: REST API Integration

Start the webhook server:
```bash
uvicorn api.webhook:app --host 0.0.0.0 --port 8000
```

Then call from your backend:

```javascript
// Your Node.js/Express backend
const axios = require('axios');

// Start agent (you'd need to create this endpoint)
async function startAgent(manifest, prompt, sessionId) {
    // Run Python agent via subprocess or HTTP bridge
}

// Receive frontend build errors and forward for side-channel auto-fix
app.post('/api/build-error', async (req, res) => {
    const { jobId, phase, errors, fullOutput } = req.body;

    await axios.post(
        `http://localhost:8000/api/v1/generate/${jobId}/build-error`,
        { phase, errors, fullOutput, retries_used: 0 }
    );

    res.json({ success: true });
});
```

### Option C: Ably Real-time Integration

```javascript
// Frontend: Listen for agent events
const ably = new Ably.Realtime('your-ably-key');
const channel = ably.channels.get(`agent:control:${sessionId}`);

channel.subscribe('message', (message) => {
    const data = message.data;
    
    switch (data.type) {
        case 'PLAN_GENERATED':
            showPlanForApproval(data.plan);
            break;
        case 'CODE_GENERATED':
            updateFileTree(data.files);
            break;
        case 'BUILD_REQUEST':
            triggerBuildInContainer(data);
            break;
        case 'ESCALATION':
            showHumanInterventionDialog(data);
            break;
    }
});
```

---

## 7. API Reference

### Main Functions

#### `run_antigravity_agent()`
Start a new agent session.

```python
result = await run_antigravity_agent(
    manifest: Dict[str, Any],      # Required: API manifest
    user_prompt: str,              # Required: User requirements
    thread_id: str,                # Required: Session identifier
    file_system: Dict[str, str],   # Optional: Existing files (delta mode)
    redis_url: str,                # Optional: Custom Redis URL
)
```

#### `resume_antigravity_agent()`
Resume a paused session.

```python
result = await resume_antigravity_agent(
    thread_id: str,                # Required: Session identifier
    action: "APPROVE" | "EDIT",    # Required: Human decision
    feedback: str,                 # Required if action="EDIT"
    redis_url: str,                # Optional: Custom Redis URL
)
```

#### `get_agent_state()`
Retrieve current session state.

```python
state = await get_agent_state(thread_id: str)
```

### Node Functions

| Function | Role | Description |
|----------|------|-------------|
| `plan_node` | Architect | Generates implementation plan |
| `approval_node` | Gatekeeper | Pauses for human approval |
| `generation_node` | Builder | Generates code files |
| `persistence_node` | Uploader | Uploads to Azure Blob |
| `trigger_build_node` | - | Triggers external build (legacy interrupt flow) |
| `reflexion_node` | Debugger | Analyzes errors, generates fixes |
| `escalation_node` | - | Requests human help |

---

## 8. Webhook Integration

### Build Error Auto-Fix (Default Runtime)

**Endpoint:** `POST /api/v1/generate/{job_id}/build-error`

**Request Body:**
```json
{
    "phase": "preflight" | "install" | "dev",
    "errors": ["Build output line 1", "Error message..."],
    "fullOutput": "Full terminal output (optional)",
    "retries_used": 0
}
```

**Response:**
```json
{
    "status": "fixing",
    "message": "Auto-fix started (iteration 1)",
    "job_id": "project-123",
    "iteration": 1
}
```

### Build Status Webhook (Legacy/Compatibility)

**Endpoint:** `POST /callbacks/build-status/{thread_id}`

**Request Body:**
```json
{
    "status": "success" | "failed",
    "logs": ["Build output line 1", "Error message..."],
    "error_message": "Primary error (optional)",
    "duration_ms": 1234
}
```

**Response:**
```json
{
    "message": "Build status received and agent resumed",
    "thread_id": "project-123",
    "status_received": "success"
}
```

### Human Escalation Webhook

**Endpoint:** `POST /callbacks/human-response/{thread_id}`

**Request Body:**
```json
{
    "action": "RETRY" | "ABORT" | "ACCEPT",
    "feedback": "Additional instructions..."
}
```

---

## 9. Testing

### Quick Test (No Redis)
```bash
python3 test_simple.py
```

### Full Test Suite
```bash
python3 test_setup.py
```

### Test with Webhook
```bash
# Terminal 1: Start webhook server
uvicorn api.webhook:app --port 8000

# Terminal 2: Run workflow
python3 test_full_workflow.py

# Terminal 3: Send build result (legacy interrupt flow only)
curl -X POST http://localhost:8000/callbacks/build-status/test-session-001 \
  -H "Content-Type: application/json" \
  -d '{"status": "success", "logs": ["Build completed"]}'
```

---

## 10. Troubleshooting

| Issue | Solution |
|-------|----------|
| `No module named 'langchain_openai'` | `pip3 install langchain-openai` |
| `No module named 'langgraph_checkpoint_redis'` | Use virtual env or `pip3 install --force-reinstall langgraph-checkpoint-redis` |
| `AZURE_OPENAI_ENDPOINT not set` | Check `.env` file exists and is loaded |
| Graph pauses but never resumes | Ensure same `thread_id` when calling resume |
| Empty `file_system` | Check `implementation_plan` has tasks |
| Build auto-fix not running | Verify frontend sends `/api/v1/generate/{job_id}/build-error` |
| Legacy build webhook not working | Verify server running and use `/callbacks/build-status/{thread_id}` |

---

## Project Structure

```
Agent_v1_python/
├── agent/
│   ├── __init__.py          # Package exports (v0.4.0)
│   ├── state_engine.py      # State schema, Redis, Ably
│   ├── graph_logic.py       # Graph assembly, nodes
│   ├── execution_layer.py   # Code generation, MCP tools
│   └── reflexion.py         # Self-correction loop
├── api/
│   ├── __init__.py
│   └── webhook.py           # FastAPI webhook handlers
├── requirements.txt         # Dependencies
├── .env                     # Your credentials
├── .env.example             # Template
├── USER_GUIDE.md            # Quick start guide
├── DOCUMENTATION.md         # This file
├── test_setup.py            # Verification tests
├── test_simple.py           # Core workflow test
└── test_full_workflow.py    # Full workflow with Redis
```

---

## Version History

| Version | Changes |
|---------|---------|
| 0.1.0 | Core infrastructure (AgentState, Ably, Redis) |
| 0.2.0 | Graph logic (planner, approval, HITL) |
| 0.3.0 | Execution layer (generation, persistence, MCP) |
| 0.4.0 | Reflexion loop (trigger_build, reflexion, escalation) |

---

## License

MIT License - See LICENSE file for details.
