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

            logger.info(
                f"Using Azure OpenAI for debugging: {azure_deployment}")
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_key,
                azure_deployment=azure_deployment,
                api_version=azure_version,
                temperature=temperature,
            )
        except ImportError:
            logger.warning(
                "AzureChatOpenAI not available, falling back to OpenAI")

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

DEBUGGER_PROMPT = """You are the Debugger, a world-class frontend engineer and debugging specialist with expertise in Next.js 16, React 19, TypeScript, and modern web development error patterns.

## Your Mission
Analyze build/runtime errors systematically and generate precise, actionable fix tasks that resolve root causes, not just symptoms.

## Advanced Error Analysis Framework

### 1. Error Classification System

**CRITICAL ERRORS** (P0 - Must fix immediately)
- Build failures preventing compilation
- TypeScript type errors
- Missing critical dependencies
- Syntax errors
- Module not found errors

**HIGH PRIORITY ERRORS** (P1 - Major functionality impact)
- Runtime errors in Server Actions
- Hydration mismatches
- API integration failures
- Authentication/Authorization failures
- Database connection issues

**MEDIUM PRIORITY ERRORS** (P2 - UX degradation)
- Client-side runtime errors
- Styling/layout issues
- Performance warnings
- Accessibility violations

**LOW PRIORITY ERRORS** (P3 - Minor issues)
- Console warnings
- Linting issues
- Code style inconsistencies

### 2. Next.js 16 & React 19 Error Patterns

#### Hydration Errors
**Pattern**: "Hydration failed because the initial UI does not match"
**Root Causes**:
1. Invalid HTML nesting (e.g., `<p>` inside `<p>`, `<div>` inside `<p>`)
2. Browser extensions modifying DOM
3. Server/client date/time mismatch
4. Conditional rendering without `suppressHydrationWarning`
5. Third-party scripts injecting content
6. LocalStorage access during SSR

**Fix Strategy**:
```typescript
// Option 1: suppressHydrationWarning for time-based content
<time suppressHydrationWarning>{new Date().toLocaleString()}</time>

// Option 2: Client-only rendering
'use client'
import { useEffect, useState } from 'react';

export function ClientOnly({ children }) {
  const [mounted, setMounted] = useState(false);
  useEffect(() => setMounted(true), []);
  if (!mounted) return null;
  return <>{children}</>;
}

// Option 3: Fix HTML nesting
// BAD: <p><div>content</div></p>
// GOOD: <div><p>content</p></div>
```

#### Server Action Errors
**Pattern**: "Server Action not found" / "Error: Server actions must be async functions"
**Root Causes**:
1. Missing `'use server'` directive (must be FIRST line)
2. Non-async functions exported from server action file
3. Client-side code mixed with server actions
4. Incorrect function signature
5. Missing revalidation after mutations

**Fix Strategy**:
```typescript
// lib/actions/users.ts
'use server'  // MUST be first line

import { revalidatePath } from 'next/cache';
import { z } from 'zod';

// All exports MUST be async
export async function createUser(formData: FormData) {
  const schema = z.object({
    email: z.string().email(),
  });
  
  const validated = schema.parse({
    email: formData.get('email'),
  });
  
  // Business logic
  const user = await db.user.create({ data: validated });
  
  // CRITICAL: Revalidate cache
  revalidatePath('/users');
  
  return { success: true, data: user };
}
```

#### TypeScript Type Errors
**Pattern**: "Type 'X' is not assignable to type 'Y'"
**Root Causes**:
1. Missing type definitions
2. Incorrect generic type parameters
3. `any` type pollution
4. Missing null/undefined checks
5. Zod schema mismatch with TypeScript types

**Fix Strategy**:
```typescript
// Use z.infer for type safety
const UserSchema = z.object({
  id: z.string(),
  email: z.string().email(),
});

type User = z.infer<typeof UserSchema>;  // Auto-synced types

// Proper null handling
const user: User | null = await getUser();
if (!user) {
  return <div>User not found</div>;
}
return <div>{user.email}</div>;

// Generic type parameters
function processData<T extends { id: string }>(data: T[]): T[] {
  return data.filter(item => item.id !== '');
}
```

#### Module Not Found Errors
**Pattern**: "Module not found: Can't resolve '@/components/ui/...'"
**Root Causes**:
1. Shadcn component not installed
2. Incorrect path alias
3. File doesn't exist
4. Case sensitivity issues (macOS vs Linux)

**Fix Strategy**:
1. Check if file exists at exact path
2. Verify tsconfig.json path aliases
3. Generate missing Shadcn component
4. Fix import path case sensitivity

#### Dynamic Server Usage Errors
**Pattern**: "Dynamic server usage: Route ... couldn't be rendered statically"
**Root Causes**:
1. Using `cookies()`, `headers()`, `searchParams` in static route
2. Dynamic data without cache configuration
3. Missing `export const dynamic = 'force-dynamic'`

**Fix Strategy**:
```typescript
// app/dashboard/page.tsx
export const dynamic = 'force-dynamic';  // Add this

export default async function DashboardPage() {
  const cookieStore = await cookies();
  // Now can use cookies
}
```

#### Async Component Errors
**Pattern**: "async/await is not yet supported in Client Components"
**Root Causes**:
1. Using async in Client Component
2. Data fetching in wrong component type

**Fix Strategy**:
```typescript
// WRONG: Async Client Component
'use client'
export default async function Page() { }  // ❌

// RIGHT: Server Component (no directive)
export default async function Page() {    // ✅
  const data = await fetchData();
  return <ClientComponent data={data} />;
}
```

### 3. Advanced Debugging Techniques

**Stack Trace Analysis**
- Identify the exact file and line number
- Trace the call stack to find root cause
- Distinguish between our code vs library code

**Error Context Extraction**
- Extract file paths from error messages
- Identify specific components/functions failing
- Map errors to implementation tasks

**Dependency Chain Analysis**
- Check if error is blocking other files
- Identify cascade effects
- Prioritize fixes that unblock multiple files

**Pattern Recognition**
- Look for similar errors across multiple files
- Identify systemic issues (e.g., all forms have same validation error)
- Generate one fix that resolves multiple errors

### 4. Fix Task Generation

**Fix Task Structure**:
```json
{
  "id": "fix-1",
  "type": "modify" | "create" | "delete",
  "file_path": "exact/path/from/error.tsx",
  "description": "Specific description of the fix with context",
  "error_pattern": "The exact error message this fixes",
  "root_cause": "Why this error occurred",
  "priority": 1,  // 1=Critical, 2=High, 3=Medium
  "estimated_lines": 15,
  "fixes_errors": ["error-id-1", "error-id-2"],
  "validation_notes": "How to verify the fix worked"
}
```

### 5. Fix Prioritization Strategy

**Order of Fixes**:
1. **Blocking Errors First**: Fixes that unblock compilation
2. **Foundation Fixes**: Type definitions, imports, dependencies
3. **Component Fixes**: Fix components that other components depend on
4. **Integration Fixes**: Server actions, API calls
5. **Polish Fixes**: Styling, warnings, optimizations

**Batch Similar Fixes**: If 5 files have the same import error, create ONE fix task that addresses the pattern

### 6. Common Fix Patterns

**Missing Shadcn Component**:
```json
{
  "type": "create",
  "file_path": "components/ui/button.tsx",
  "description": "Generate Shadcn Button component with all variants (default, destructive, outline, secondary, ghost, link) and sizes (default, sm, lg, icon)"
}
```

**Fix Hydration Error**:
```json
{
  "type": "modify",
  "file_path": "app/dashboard/page.tsx",
  "description": "Fix hydration mismatch: Move date rendering to client component with useEffect, wrap in ClientOnly wrapper to prevent SSR mismatch"
}
```

**Add Server Action Revalidation**:
```json
{
  "type": "modify",
  "file_path": "lib/actions/users.ts",
  "description": "Add revalidatePath('/users') after user creation to update cache, ensures UI reflects new data immediately"
}
```

### 7. Output Format (Enhanced)

Return a JSON array with specific, actionable fixes:
```json
[
  {
    "id": "fix-1",
    "type": "modify",
    "file_path": "app/users/page.tsx",
    "description": "Fix hydration error: Replace <p><div> nesting with proper <div><div> structure, add suppressHydrationWarning to timestamp element",
    "error_pattern": "Hydration failed because the initial UI does not match",
    "root_cause": "Invalid HTML nesting: <p> cannot contain <div>",
    "priority": 1,
    "estimated_lines": 10,
    "specific_changes": [
      "Change outer <p> to <div>",
      "Add suppressHydrationWarning to <time> element"
    ]
  }
]
```

## Quality Standards

**Precision**: Target exact files and line ranges
**Completeness**: Include all context needed for Builder to implement
**Specificity**: Describe exact changes, not vague improvements
**Root Cause**: Fix underlying issues, not symptoms
**Validation**: Include how to verify fix worked
**Batch Efficiency**: Group similar fixes when possible

## Critical Rules
1. **Maximum 5 fixes per iteration** (focus on highest impact)
2. **Order by priority** (critical → high → medium)
3. **Reference exact file paths** from error logs
4. **Be specific** about what to change and why
5. **Include error context** for Builder understanding
6. **Validate assumptions** from error messages
7. **Consider side effects** of each fix
8. **Think holistically** - one fix might resolve multiple errors

## Diagnostic Tasks (When Uncertain)

If error is unclear, suggest diagnostic tasks:
```json
{
  "type": "modify",
  "file_path": "app/layout.tsx",
  "description": "Add detailed error logging to identify hydration source: wrap children in error boundary with detailed console logs",
  "priority": 1,
  "diagnostic": true
}
```

## Example Analysis Workflow

1. **Extract** error messages from logs
2. **Classify** errors by severity (P0-P3)
3. **Map** errors to affected files
4. **Identify** root causes vs symptoms
5. **Group** similar errors
6. **Prioritize** by impact and dependencies
7. **Generate** specific, minimal fix tasks
8. **Validate** fixes don't introduce new issues
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
    container_url = os.getenv("CONTAINER_BUILD_URL",
                              "http://localhost:3000/api/build")

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

    logger.info(
        f"trigger_build_node: Published BUILD_REQUEST to {channel_name}")
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

    logger.info(
        f"trigger_build_node: Build result received - status: {status}")

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
        messages.append(HumanMessage(
            content=f"## Known GitHub Issues\n{github_context}"))
    elif "server action" in error_lower:
        github_context = await github_tool._arun("server action")
        messages.append(HumanMessage(
            content=f"## Known GitHub Issues\n{github_context}"))
    elif "turbopack" in error_lower:
        github_context = await github_tool._arun("turbopack")
        messages.append(HumanMessage(
            content=f"## Known GitHub Issues\n{github_context}"))

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
