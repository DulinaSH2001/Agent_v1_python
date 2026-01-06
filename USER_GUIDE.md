# Antigravity Agent - User Guide

Complete guide to testing and running the Antigravity agent.

---

## 1. Prerequisites

### Install Dependencies
```bash
cd /Users/dulina/Documents/Research\ Project/Platform/Agent_v1_python
pip install -r requirements.txt
```

### Verify Environment
Ensure your `.env` file has:
```bash
# Required
AZURE_OPENAI_ENDPOINT=https://frontend-agent-resource.cognitiveservices.azure.com
AZURE_OPENAI_API_KEY=<your-key>
AZURE_OPENAI_DEPLOYMENT_NAME=gpt-4o

# Required for state persistence
UPSTASH_REDIS_REST_URL=<your-url>
UPSTASH_REDIS_REST_TOKEN=<your-token>

# Required for real-time events
ABLY_API_KEY=<your-key>

# Optional
AZURE_STORAGE_CONNECTION_STRING=<for-file-upload>
```

---

## 2. Quick Test (No External Dependencies)

Run this to verify the agent works without Redis/Ably:

```python
# test_basic.py
import asyncio
from agent.graph_logic import create_antigravity_graph, get_planning_llm

async def test_llm():
    """Test Azure OpenAI connection."""
    llm = get_planning_llm()
    response = await llm.ainvoke("Say hello in one word")
    print(f"✓ LLM Response: {response.content}")

async def test_graph_creation():
    """Test graph compiles without errors."""
    graph = create_antigravity_graph(enable_reflexion=False)
    print(f"✓ Graph created with nodes: {list(graph.nodes.keys())}")

if __name__ == "__main__":
    asyncio.run(test_llm())
    asyncio.run(test_graph_creation())
```

Run:
```bash
python test_basic.py
```

---

## 3. Full Workflow Test

### Step 1: Start the Webhook Server
```bash
# Terminal 1
uvicorn api.webhook:app --host 0.0.0.0 --port 8000 --reload
```

### Step 2: Run the Agent
```python
# test_full_workflow.py
import asyncio
from agent import run_antigravity_agent, resume_antigravity_agent, get_agent_state

async def main():
    # Sample manifest (describes your backend API)
    manifest = {
        "name": "my-app",
        "endpoints": [
            {
                "path": "/api/users",
                "method": "GET",
                "response": {"type": "User[]"}
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
            "provider": "clerk"
        },
        "database": {
            "type": "postgresql",
            "orm": "prisma"
        }
    }
    
    user_prompt = """
    Create a modern user management dashboard with:
    - Dark mode design
    - User list table with search
    - Add user form with validation
    - User profile cards
    Use Shadcn UI components.
    """
    
    thread_id = "test-session-001"
    
    print("=" * 60)
    print("STEP 1: Starting Planning Phase")
    print("=" * 60)
    
    try:
        result = await run_antigravity_agent(
            manifest=manifest,
            user_prompt=user_prompt,
            thread_id=thread_id,
        )
        
        # The agent will pause at approval_node
        print("\n📋 Implementation Plan Generated!")
        plan = result.get("implementation_plan", [])
        for i, task in enumerate(plan, 1):
            print(f"  {i}. [{task.get('type')}] {task.get('file_path')}")
            print(f"     {task.get('description')}")
        
        print("\n" + "=" * 60)
        print("STEP 2: Approving Plan")
        print("=" * 60)
        
        # Approve the plan
        result = await resume_antigravity_agent(
            thread_id=thread_id,
            action="APPROVE",
        )
        
        print("\n✅ Plan approved! Code generation started.")
        
        # Check generated files
        file_system = result.get("file_system", {})
        print(f"\n📦 Generated {len(file_system)} files:")
        for path in file_system.keys():
            print(f"  - {path}")
        
        # Show build logs
        print("\n📜 Build Logs:")
        for log in result.get("build_logs", []):
            print(f"  {log}")
        
        print("\n" + "=" * 60)
        print("STEP 3: Build Status (if reflexion enabled)")
        print("=" * 60)
        
        # If reflexion is enabled, the agent is now waiting at trigger_build_node
        # You can simulate a build result with:
        # curl -X POST http://localhost:8000/callbacks/build-status/test-session-001 \
        #   -H "Content-Type: application/json" \
        #   -d '{"status": "success", "logs": ["Build completed successfully"]}'
        
        print("Agent is waiting for build status webhook at:")
        print(f"  POST http://localhost:8000/callbacks/build-status/{thread_id}")
        
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
```

Run:
```bash
# Terminal 2
python test_full_workflow.py
```

---

## 4. Simulate Build Results

### Success (completes workflow):
```bash
curl -X POST http://localhost:8000/callbacks/build-status/test-session-001 \
  -H "Content-Type: application/json" \
  -d '{"status": "success", "logs": ["Build completed in 3.2s"]}'
```

### Failure (triggers reflexion):
```bash
curl -X POST http://localhost:8000/callbacks/build-status/test-session-001 \
  -H "Content-Type: application/json" \
  -d '{
    "status": "failed",
    "logs": [
      "Error: Module not found: @/components/ui/button",
      "at app/page.tsx:5:0"
    ],
    "error_message": "Shadcn Button component missing"
  }'
```

---

## 5. Test Individual Components

### Test Planning LLM
```python
import asyncio
from agent.graph_logic import get_planning_llm
from langchain_core.messages import HumanMessage, SystemMessage

async def test_planner():
    llm = get_planning_llm()
    response = await llm.ainvoke([
        SystemMessage(content="You are a helpful assistant."),
        HumanMessage(content="What is Next.js 16?")
    ])
    print(response.content)

asyncio.run(test_planner())
```

### Test MCP Tools
```python
from agent.execution_layer import get_mcp_wrapper

mcp = get_mcp_wrapper()
print(f"Tools available: {[t.name for t in mcp.get_tools()]}")

# Get Shadcn component docs
import asyncio
for tool in mcp.get_tools():
    if tool.name == "get_shadcn_component":
        result = asyncio.run(tool._arun("button"))
        print(result)
```

### Test Debugger
```python
import asyncio
from agent.reflexion import get_debugger_llm, DEBUGGER_PROMPT
from langchain_core.messages import HumanMessage, SystemMessage

async def test_debugger():
    llm = get_debugger_llm()
    response = await llm.ainvoke([
        SystemMessage(content=DEBUGGER_PROMPT),
        HumanMessage(content="Error: Hydration failed at app/page.tsx")
    ])
    print(response.content)

asyncio.run(test_debugger())
```

---

## 6. Inspect State

```python
import asyncio
from agent import get_agent_state

async def check_state():
    state = await get_agent_state("test-session-001")
    if state:
        print(f"Approved: {state.get('approved')}")
        print(f"Build Ready: {state.get('build_ready')}")
        print(f"Build Status: {state.get('build_status')}")
        print(f"Iterations: {state.get('iteration_count')}")
        print(f"Files: {len(state.get('file_system', {}))}")
    else:
        print("No state found for this thread")

asyncio.run(check_state())
```

---

## 7. Expected Flow

```
1. run_antigravity_agent()
   └── plan_node (generates implementation plan)
   └── approval_node (PAUSES - waiting for human)

2. resume_antigravity_agent(action="APPROVE")
   └── generation_node (generates code files)
   └── persistence_node (uploads to Azure Blob)
   └── trigger_build_node (PAUSES - waiting for webhook)

3. POST /callbacks/build-status/{thread_id}
   └── If success → END
   └── If failed → reflexion_node → generation_node (retry loop)
   └── If max retries → escalation_node (PAUSES - human help)
```

---

## 8. Troubleshooting

| Issue | Solution |
|-------|----------|
| `AZURE_OPENAI_ENDPOINT not set` | Check `.env` file is loaded |
| `Redis connection failed` | Verify Upstash credentials |
| `Graph not resuming` | Ensure same `thread_id` used |
| `No files generated` | Check `implementation_plan` is not empty |
| `Build never completes` | Send webhook to `/callbacks/build-status/{thread_id}` |

---

## 9. File Outputs

After successful run:
- **In Memory**: `state['file_system']` contains all generated files
- **Azure Blob**: Container `project-{thread_id}` (if configured)
- **Redis**: Full state persisted for resume capability
