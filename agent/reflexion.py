"""
Antigravity Agent - Reflexion Loop

This module implements the self-correction mechanism for the Antigravity agent:
- TriggerBuildNode: Publishes build request and waits for external container
- ReflexionNode: Analyzes build errors and generates fix plans
- should_fix: Conditional edge for routing based on build status

The Reflexion Loop enables autonomous error detection and fixing without
human intervention, up to a configurable threshold.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI
from langgraph.types import interrupt
from pydantic import BaseModel, Field

from agent.state_engine import AgentState

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================

MAX_REFLEXION_ITERATIONS = 3


# =============================================================================
# LLM Configuration for Debugging
# =============================================================================

def get_debugger_llm(
    temperature: float = 0.1,
) -> ChatOpenAI:
    """
    Get a configured LLM instance for error analysis.
    
    Supports both Azure OpenAI and standard OpenAI based on environment variables.
    
    Args:
        temperature: Sampling temperature. Low for precise analysis.
    
    Returns:
        Configured ChatOpenAI or AzureChatOpenAI instance.
    """
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    azure_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    
    if azure_endpoint and azure_key:
        try:
            from langchain_openai import AzureChatOpenAI
            
            logger.info(f"Using Azure OpenAI for debugging: {azure_deployment}")
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_key,
                azure_deployment=azure_deployment,
                api_version=azure_version,
                temperature=temperature,
            )
        except ImportError:
            logger.warning("AzureChatOpenAI not available, falling back to OpenAI")
    
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "Neither Azure OpenAI nor OpenAI API key is configured."
        )
    
    return ChatOpenAI(
        model="gpt-4o",
        temperature=temperature,
        api_key=api_key,
    )


# =============================================================================
# System Prompts
# =============================================================================

DEBUGGER_PROMPT = """You are the Debugger, a senior frontend engineer specialized in analyzing and fixing Next.js 16 build errors.

## Your Role
Analyze build error logs and generate specific fix tasks that can be executed by the code generation system.

## Common Next.js 16 Error Patterns

### 1. Hydration Errors
**Pattern**: "Hydration failed because the initial UI does not match"
**Causes**:
- HTML nesting issues (e.g., `<p>` inside `<p>`, `<div>` inside `<p>`)
- Browser extensions modifying DOM
- Date/time rendering differences between server and client
**Fix**: Check component structure, wrap dynamic content with `suppressHydrationWarning` or use `useEffect` for client-only data

### 2. Server Action Errors
**Pattern**: "Server Action not found" or "Error: Server actions must be async functions"
**Causes**:
- Missing `'use server'` directive at file top
- Mixing client and server code incorrectly
**Fix**: Ensure `'use server'` is at the very top of the action file, before any imports

### 3. Missing Components
**Pattern**: "Module not found: Can't resolve '@/components/ui/...'"
**Causes**:
- Shadcn component not installed
- Incorrect import path
**Fix**: Generate the missing Shadcn component file

### 4. Client/Server Directive Conflicts
**Pattern**: "'use client' and 'use server' cannot be used together"
**Fix**: Separate client and server code into different files

### 5. Dynamic Server Usage
**Pattern**: "Dynamic server usage: Route ... couldn't be rendered statically"
**Causes**:
- Using `cookies()` or `headers()` in a static page
**Fix**: Add `export const dynamic = 'force-dynamic'` to the page

### 6. Type Errors
**Pattern**: "Type '...' is not assignable to type '...'"
**Fix**: Update type definitions or fix type mismatches

### 7. Import Errors
**Pattern**: "Module not found" or "Cannot find module"
**Fix**: Check import paths, ensure files exist, verify package installation

### 8. Async Component Errors
**Pattern**: "async/await is not yet supported in Client Components"
**Fix**: Move async logic to Server Components or use `useEffect` with state

## Output Format
Return a JSON array of fix tasks. Each task should have:
```json
[
  {
    "id": "fix-1",
    "type": "modify" | "create",
    "file_path": "path/to/file.tsx",
    "description": "Specific fix description",
    "error_pattern": "The error pattern this fixes",
    "priority": 1
  }
]
```

## Important Rules
1. Be specific about what needs to change
2. Reference exact file paths from the error logs
3. Prioritize fixes that unblock other errors
4. If error is unclear, suggest diagnostic tasks
5. Never suggest more than 5 fixes at once
"""


# =============================================================================
# MCP Tool for GitHub Issues (Mock)
# =============================================================================

class SearchGithubIssuesInput(BaseModel):
    """Input schema for search_github_issues tool."""
    query: str = Field(description="Search query for GitHub issues")
    repo: str = Field(
        default="vercel/next.js",
        description="Repository to search (e.g., 'vercel/next.js')"
    )


class MockGithubIssuesTool(BaseTool):
    """Mock tool for searching GitHub issues for known bugs."""
    
    name: str = "search_github_issues"
    description: str = "Search GitHub issues for known bugs and workarounds"
    args_schema: type[BaseModel] = SearchGithubIssuesInput
    
    def _run(self, query: str, repo: str = "vercel/next.js") -> str:
        """Synchronous run."""
        return self._search(query, repo)
    
    async def _arun(self, query: str, repo: str = "vercel/next.js") -> str:
        """Async run."""
        return self._search(query, repo)
    
    def _search(self, query: str, repo: str) -> str:
        """Mock search results."""
        known_issues = {
            "hydration": """
## Known Issue: Hydration Mismatch in Next.js 16

**Issue #12345**: Hydration errors with dynamic content
**Status**: Open
**Workaround**: 
1. Use `suppressHydrationWarning` on elements with dynamic content
2. Wrap client-only code in `useEffect`
3. Check for browser extensions modifying DOM
""",
            "server action": """
## Known Issue: Server Actions not found

**Issue #54321**: Server Actions fail silently
**Status**: Fixed in 16.0.1
**Workaround**:
1. Ensure 'use server' is the FIRST line of the file
2. Don't export non-async functions from server action files
3. Clear .next cache and rebuild
""",
            "turbopack": """
## Known Issue: Turbopack compatibility

**Issue #67890**: Some packages incompatible with Turbopack
**Status**: In Progress
**Workaround**:
1. Add problematic packages to `transpilePackages` in next.config.js
2. Disable Turbopack with `--no-turbo` flag
""",
        }
        
        query_lower = query.lower()
        for key, issue in known_issues.items():
            if key in query_lower:
                return issue
        
        return f"No known issues found for '{query}' in {repo}. This may be a project-specific error."


# =============================================================================
# Ably Publishing Helper
# =============================================================================

async def publish_to_ably(
    channel_name: str,
    data: Dict[str, Any],
) -> bool:
    """
    Publish a message to an Ably channel.
    
    Args:
        channel_name: The Ably channel to publish to.
        data: The data payload to publish.
    
    Returns:
        True if successful, False otherwise.
    """
    try:
        from ably import AblyRealtime
        
        api_key = os.getenv("ABLY_API_KEY")
        if not api_key:
            logger.warning("ABLY_API_KEY not set, skipping publish")
            return False
        
        client = AblyRealtime(api_key)
        channel = client.channels.get(channel_name)
        await channel.publish("message", data)
        await client.close()
        
        logger.info(f"Published to Ably channel {channel_name}")
        return True
        
    except ImportError:
        logger.warning("Ably package not installed, skipping publish")
        return False
    except Exception as e:
        logger.error(f"Failed to publish to Ably: {e}")
        return False


# =============================================================================
# Node: TriggerBuildNode
# =============================================================================

async def trigger_build_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    Trigger external build and wait for result via webhook.
    
    This node publishes a BUILD_REQUEST event to Ably, then interrupts
    execution to wait for the external container to send a webhook
    with the build result.
    
    Args:
        state: Current agent state with file_system.
        config: Runnable configuration with thread_id.
    
    Returns:
        State update with build_status and build_logs from webhook.
    """
    logger.info("trigger_build_node: Requesting external build")
    
    # Extract thread_id
    thread_id = config.get("configurable", {}).get("thread_id", "unknown")
    
    # Get container URL
    container_url = os.getenv("CONTAINER_BUILD_URL", "http://localhost:3000/api/build")
    
    # Prepare build request payload
    build_request = {
        "type": "BUILD_REQUEST",
        "thread_id": thread_id,
        "container_url": container_url,
        "file_count": len(state.get("file_system", {})),
        "files": list(state.get("file_system", {}).keys()),
        "timestamp": __import__("datetime").datetime.utcnow().isoformat(),
    }
    
    # Publish to Ably control channel
    channel_name = f"agent:control:{thread_id}"
    await publish_to_ably(channel_name, build_request)
    
    logger.info(f"trigger_build_node: Published BUILD_REQUEST to {channel_name}")
    logger.info("trigger_build_node: Waiting for build result via webhook...")
    
    # Interrupt and wait for webhook to resume with build result
    # The webhook will call Command(resume={status, logs})
    build_result = interrupt({
        "awaiting": "build_status",
        "thread_id": thread_id,
        "request": build_request,
    })
    
    # Process the build result from webhook
    status = build_result.get("status", "failed")
    logs = build_result.get("logs", [])
    
    if isinstance(logs, str):
        logs = [logs]
    
    logger.info(f"trigger_build_node: Build result received - status: {status}")
    
    # Update build_logs with new logs
    current_logs = list(state.get("build_logs", []))
    current_logs.extend(logs)
    
    return {
        "build_status": status,
        "build_logs": current_logs,
    }


# =============================================================================
# Node: ReflexionNode (The Debugger)
# =============================================================================

async def reflexion_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    The Debugger node - analyzes build errors and generates fix plans.
    
    This node runs when a build fails. It uses an LLM to analyze error
    logs and generate specific fix tasks that are appended to the
    implementation plan for the next generation cycle.
    
    Escalation: If iteration_count exceeds MAX_REFLEXION_ITERATIONS,
    the node sets build_status to "escalate" for human intervention.
    
    Args:
        state: Current agent state with build_logs.
        config: Runnable configuration.
    
    Returns:
        State update with fix tasks and incremented iteration_count.
    """
    logger.info("reflexion_node: Analyzing build errors")
    
    current_iteration = state.get("iteration_count", 0)
    build_logs = state.get("build_logs", [])
    
    # Check escalation threshold
    if current_iteration >= MAX_REFLEXION_ITERATIONS:
        logger.warning(
            f"reflexion_node: Max iterations ({MAX_REFLEXION_ITERATIONS}) reached. "
            "Escalating to human intervention."
        )
        return {
            "build_status": "escalate",
            "build_logs": build_logs + [
                f"ESCALATION: Max fix attempts ({MAX_REFLEXION_ITERATIONS}) reached. "
                "Human intervention required."
            ],
        }
    
    # Get LLM for error analysis (using Azure-aware helper)
    llm = get_debugger_llm()
    
    # Prepare error context
    error_text = "\n".join(build_logs[-20:])  # Last 20 log entries
    
    # Get existing files for context
    file_system = state.get("file_system", {})
    existing_files = "\n".join(f"- {path}" for path in file_system.keys())
    
    # Build the analysis prompt
    user_content = f"""## Build Error Logs (Last 20 entries)
```
{error_text}
```

## Current Iteration
{current_iteration + 1} of {MAX_REFLEXION_ITERATIONS} allowed attempts

## Existing Files
{existing_files}

## Task
Analyze the error logs and generate a JSON array of fix tasks.
Focus on the root cause. Return ONLY the JSON array, no markdown.
"""
    
    messages = [
        SystemMessage(content=DEBUGGER_PROMPT),
        HumanMessage(content=user_content),
    ]
    
    # Check for library-related errors and query GitHub issues
    github_tool = MockGithubIssuesTool()
    error_lower = error_text.lower()
    
    if "hydration" in error_lower:
        github_context = await github_tool._arun("hydration")
        messages.append(HumanMessage(content=f"## Known GitHub Issues\n{github_context}"))
    elif "server action" in error_lower:
        github_context = await github_tool._arun("server action")
        messages.append(HumanMessage(content=f"## Known GitHub Issues\n{github_context}"))
    elif "turbopack" in error_lower:
        github_context = await github_tool._arun("turbopack")
        messages.append(HumanMessage(content=f"## Known GitHub Issues\n{github_context}"))
    
    try:
        # Generate fix plan
        response = await llm.ainvoke(messages, config=config)
        
        # Parse JSON response
        content = response.content.strip()
        
        # Handle potential markdown formatting
        if content.startswith("```"):
            lines = content.split("\n")
            lines = lines[1:]  # Remove first line
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)
        
        fix_tasks = json.loads(content)
        
        logger.info(f"reflexion_node: Generated {len(fix_tasks)} fix tasks")
        
        # Append fix tasks to implementation plan
        current_plan = list(state.get("implementation_plan", []))
        
        # Mark existing tasks as completed (they were already processed)
        for task in current_plan:
            if "status" not in task:
                task["status"] = "completed"
        
        # Add new fix tasks
        current_plan.extend(fix_tasks)
        
        return {
            "implementation_plan": current_plan,
            "iteration_count": current_iteration + 1,
            "build_status": "pending",  # Reset for retry
            "build_logs": build_logs + [
                f"Reflexion iteration {current_iteration + 1}: Generated {len(fix_tasks)} fix tasks"
            ],
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"reflexion_node: Failed to parse fix plan: {e}")
        return {
            "iteration_count": current_iteration + 1,
            "build_status": "pending",
            "build_logs": build_logs + [f"Reflexion error: Failed to parse fix plan - {e}"],
        }
    except Exception as e:
        logger.error(f"reflexion_node: Unexpected error: {e}")
        return {
            "iteration_count": current_iteration + 1,
            "build_status": "pending",
            "build_logs": build_logs + [f"Reflexion error: {e}"],
        }


# =============================================================================
# Conditional Edge: should_fix
# =============================================================================

def should_fix(state: AgentState) -> Literal["end", "reflexion", "escalate"]:
    """
    Conditional edge that routes based on build status.
    
    Routes:
    - "success" -> "end" (build succeeded, workflow complete)
    - "failed" -> "reflexion" (analyze errors and retry)
    - "escalate" -> "escalate" (max iterations, need human)
    
    Args:
        state: Current agent state with build_status.
    
    Returns:
        Next node: "end", "reflexion", or "escalate"
    """
    build_status = state.get("build_status", "pending")
    
    if build_status == "success":
        logger.info("should_fix: Build SUCCESS -> end")
        return "end"
    elif build_status == "escalate":
        logger.info("should_fix: ESCALATE -> human intervention")
        return "escalate"
    else:
        logger.info(f"should_fix: Build {build_status} -> reflexion")
        return "reflexion"


# =============================================================================
# Human Escalation Node
# =============================================================================

async def escalation_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    Escalation node - pauses for human intervention.
    
    This node is reached when the agent has exceeded the maximum
    number of fix attempts and needs human help.
    
    Args:
        state: Current agent state.
        config: Runnable configuration.
    
    Returns:
        State update after human provides guidance.
    """
    logger.info("escalation_node: Requesting human intervention")
    
    thread_id = config.get("configurable", {}).get("thread_id", "unknown")
    
    # Prepare escalation summary
    escalation_summary = {
        "type": "ESCALATION",
        "thread_id": thread_id,
        "iteration_count": state.get("iteration_count", 0),
        "error_summary": state.get("build_logs", [])[-10:],
        "files_affected": list(state.get("file_system", {}).keys()),
        "message": "Maximum auto-fix attempts reached. Please review errors and provide guidance.",
    }
    
    # Publish escalation to Ably
    channel_name = f"agent:control:{thread_id}"
    await publish_to_ably(channel_name, escalation_summary)
    
    # Interrupt for human input
    human_guidance = interrupt(escalation_summary)
    
    # Process human guidance
    if human_guidance.get("action") == "RETRY":
        # Human wants to retry with new prompt
        feedback = human_guidance.get("feedback", "")
        return {
            "user_prompt": state.get("user_prompt", "") + f"\n\n[HUMAN GUIDANCE]: {feedback}",
            "iteration_count": 0,  # Reset counter
            "build_status": "pending",
        }
    elif human_guidance.get("action") == "ABORT":
        return {
            "build_status": "aborted",
            "build_logs": state.get("build_logs", []) + ["Workflow aborted by human"],
        }
    else:
        # Default: accept current state
        return {
            "build_status": "human_resolved",
        }


# =============================================================================
# Utility Functions
# =============================================================================

def get_debugger_tools() -> List[BaseTool]:
    """Get available tools for the debugger."""
    return [MockGithubIssuesTool()]


def format_error_summary(build_logs: List[str], max_lines: int = 10) -> str:
    """
    Format build logs into a readable summary.
    
    Args:
        build_logs: List of log entries.
        max_lines: Maximum lines to include.
    
    Returns:
        Formatted error summary.
    """
    if not build_logs:
        return "No build logs available."
    
    recent_logs = build_logs[-max_lines:]
    return "\n".join(f"  {log}" for log in recent_logs)
