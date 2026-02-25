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

MAX_REFLEXION_ITERATIONS = 5


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

DEBUGGER_PROMPT = """You are the Debugger, a senior frontend engineer specialized in analyzing and fixing Next.js 15 build errors.

## Your Role
Analyze build error logs and generate specific fix tasks that can be executed by the code generation system.

## CRITICAL: Available Shadcn UI components
ONLY these files exist in @/components/ui/: button, card, input, label, badge, dialog, skeleton, table.
If an import references ANY other path like @/components/ui/header, @/components/ui/footer,
@/components/ui/navbar, @/components/ui/dropdown-menu, @/components/ui/select, @/components/ui/tabs,
@/components/ui/sheet, @/components/ui/avatar, @/components/ui/toast — the fix is to MODIFY the file
that contains the import to remove it and create a custom component in components/ instead.
Never try to generate a missing Shadcn component — only these 8 exist.

## Common Next.js 15 Error Patterns

### 1. Missing UI module (MOST COMMON)
**Pattern**: "Module not found: Can't resolve '@/components/ui/<anything-not-in-list>'"
**Fix**: Modify the file that has the bad import — replace the import with either:
  a) A valid Shadcn import from the list above, or
  b) An inline implementation using only valid Shadcn components

### 2. Missing custom component
**Pattern**: "Module not found: Can't resolve '@/components/<name>'" (not in ui/)
**Fix**: Create the missing component file at that path with a basic implementation

### 3. error.tsx syntax/structure issues
**Pattern**: WebpackError or NonErrorEmittedError on an error.tsx file
**Fix**: Rewrite the file with EXACTLY this pattern (must have 'use client'):
```typescript
'use client';
import { useEffect } from 'react';
import { Button } from '@/components/ui/button';
interface ErrorProps { error: Error & { digest?: string }; reset: () => void; }
export default function Error({ error, reset }: ErrorProps) {
  useEffect(() => { console.error(error); }, [error]);
  return (
    <div className="flex flex-col items-center justify-center min-h-[400px] gap-4">
      <h2 className="text-xl font-semibold">Something went wrong</h2>
      <Button onClick={reset}>Try again</Button>
    </div>
  );
}
```

### 4. Hydration Errors
**Pattern**: "Hydration failed because the initial UI does not match"
**Fix**: Check component structure, wrap dynamic content with `suppressHydrationWarning` or use `useEffect` for client-only data

### 5. Server Action Errors
**Pattern**: "Server Action not found" or "Error: Server actions must be async functions"
**Fix**: Ensure `'use server'` is at the very top of the action file, before any imports

### 6. Client/Server Directive Conflicts
**Pattern**: "'use client' and 'use server' cannot be used together"
**Fix**: Separate client and server code into different files

### 7. Dynamic Server Usage
**Pattern**: "Dynamic server usage: Route ... couldn't be rendered statically"
**Fix**: Add `export const dynamic = 'force-dynamic'` to the page

### 8. Type Errors
**Pattern**: "Type '...' is not assignable to type '...'"
**Fix**: Update type definitions or fix type mismatches

### 9. Async Component Errors
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
4. Fix the root cause: NEVER create a @/components/ui/<custom> file — fix the import instead
5. Never suggest more than 5 fixes at once
6. PROTECTED FILES — NEVER suggest modification:
   - `app/layout.tsx` — If error references this, the issue is with generated code that imports from wrong path. Fix the generated file instead.
   - `app/page.tsx` — If error references this, fix the generated code that modified it. Suggest reverting or fixing to use route-specific pages.
   - `styles/globals.css` — NEVER suggest changes
   - `tailwind.config.js` — NEVER suggest changes
   - `next.config.js` — NEVER suggest changes
   - `tsconfig.json` — NEVER suggest changes
"""


# =============================================================================
# Phase 4: Error Categorization + Category-Specific Prompts
# =============================================================================

# Error category → regex patterns to match in log output
ERROR_PATTERNS: Dict[str, List[str]] = {
    "import_errors": [
        r"Module not found",
        r"Cannot find module",
        r"Cannot find name",
        r"has no exported member",
        r"does not provide an export",
        r"is not a module",
    ],
    "type_errors": [
        r"Type '.+' is not assignable to type",
        r"Property '.+' does not exist on type",
        r"Argument of type '.+' is not assignable",
        r"Expected \d+ arguments",
        r"Parameter '.+' implicitly has",
        r"TS\d{4}",
    ],
    "runtime_errors": [
        r"Hydration failed",
        r"hydration mismatch",
        r"Cannot read properties of",
        r"is not a function",
        r"is not defined",
        r"ReferenceError",
        r"TypeError",
    ],
    "syntax_errors": [
        r"SyntaxError",
        r"Unexpected token",
        r"Unexpected end of input",
        r"Expected ';'",
        r"Unterminated string",
        r"Parsing error",
    ],
    "dependency_errors": [
        r"peer dep missing",
        r"npm ERR!",
        r"ENOENT",
        r"Cannot find package",
        r"missing dependency",
        r"Failed to resolve",
    ],
    "directive_errors": [
        r"'use client' and 'use server' cannot be used together",
        r"Server Action not found",
        r"async/await is not yet supported in Client Components",
        r"Dynamic server usage",
        r"You're importing a component that needs",
    ],
}

# Category-specific system prompt additions focused on the relevant fix domain
CATEGORY_PROMPTS: Dict[str, str] = {
    "import_errors": """
## Focus: Import / Module Resolution Errors
- Check exact import paths (case-sensitive on Linux/Mac)
- Verify the file exists at the expected path
- Ensure barrel exports (`index.ts`) re-export the symbol
- Use `@/` alias for src-relative imports
- Shadcn imports must follow: `from '@/components/ui/<component>'`
""",
    "type_errors": """
## Focus: TypeScript Type Errors
- Identify the mismatched types and fix the narrower definition
- Add `as const` for literal narrowing if needed
- Prefer updating the interface over type assertions (`as`)
- For missing properties, add them to the interface or make them optional (`?:`)
- For function arguments, check if the call-site or the signature is wrong
""",
    "runtime_errors": """
## Focus: Runtime / Hydration Errors
- Wrap components that use browser-only APIs with `use client`
- Hydration: ensure server and client render the same HTML
- Add `suppressHydrationWarning` for timestamps/dates if needed
- Avoid `window`, `document`, `localStorage` outside `useEffect`
- Check for HTML nesting violations (`<p>` inside `<p>` etc.)
""",
    "syntax_errors": """
## Focus: Syntax Errors
- Fix the exact character position reported in the error
- Check unclosed brackets, braces, parentheses, or template literals
- Ensure JSX expressions are properly wrapped in `{}`
- Verify that `async`/`await` is only used inside `async` functions
""",
    "dependency_errors": """
## Focus: Dependency / Package Errors
- Check that all `import` statements reference installed packages
- Missing `shadcn` components must be created at `components/ui/<name>.tsx`
- Do NOT generate `npm install` commands — only generate files
- Provide fallback stubs for missing third-party packages if needed
""",
    "directive_errors": """
## Focus: Next.js Directive Errors
- `'use client'` must be the FIRST line of the file
- `'use server'` must be at the top of action files (before imports)
- Server Components cannot import Client Components that use hooks directly
- Add `export const dynamic = 'force-dynamic'` for routes using cookies/headers
- Separate client/server boundaries into different files
""",
}

# Progressive fix strategy per iteration band
PROGRESSIVE_STRATEGIES: Dict[str, str] = {
    "minimal": """
## Strategy: Minimal Targeted Fix (Iterations 1-2)
- Fix ONLY the specific error(s) reported
- Do not restructure or refactor code unnecessarily
- Prefer the smallest possible change that resolves the error
- Maximum 3 fix tasks
""",
    "restructure": """
## Strategy: Component Restructuring (Iterations 3-4)
- The minimal fix approach has failed — try a broader fix
- Consider splitting components across client/server boundaries
- Restructure data fetching patterns if needed
- Merge or split files if the current structure causes the error
- Maximum 5 fix tasks
""",
    "rewrite": """
## Strategy: Full Rewrite of Affected Files (Iteration 5)
- Previous fix attempts have not resolved the issue
- Completely rewrite the affected files from scratch
- Use the simplest possible approach that satisfies the requirement
- Avoid advanced patterns that may cause edge-case errors
- Generate replacement files with type "modify" (or "create" for new files)
""",
}


def categorize_errors(logs: List[str]) -> Dict[str, List[str]]:
    """
    Categorize build log entries by error type.

    Args:
        logs: Build log lines.

    Returns:
        Dict mapping category name → list of matching log lines.
    """
    import re

    log_text = "\n".join(logs)
    categories: Dict[str, List[str]] = {cat: [] for cat in ERROR_PATTERNS}

    for line in logs:
        for category, patterns in ERROR_PATTERNS.items():
            for pattern in patterns:
                if re.search(pattern, line, re.IGNORECASE):
                    categories[category].append(line)
                    break  # Only classify each line once per category

    # Remove empty categories
    return {cat: lines for cat, lines in categories.items() if lines}


def get_progressive_strategy(iteration: int) -> str:
    """
    Get the fix strategy prompt addition for a given iteration number.

    Args:
        iteration: Current iteration (0-indexed, so iteration 0 = first attempt).

    Returns:
        Strategy prompt section.
    """
    if iteration <= 1:
        return PROGRESSIVE_STRATEGIES["minimal"]
    elif iteration <= 3:
        return PROGRESSIVE_STRATEGIES["restructure"]
    else:
        return PROGRESSIVE_STRATEGIES["rewrite"]


def build_categorized_prompt(
    error_text: str,
    categories: Dict[str, List[str]],
    iteration: int,
    existing_files: str,
) -> str:
    """
    Build an enhanced user prompt for the LLM based on categorized errors.

    Args:
        error_text: Raw error text (last N lines of logs).
        categories: Categorized error dict from categorize_errors().
        iteration: Current iteration number.
        existing_files: Formatted list of existing files.

    Returns:
        Enriched user prompt string.
    """
    # Build category summary section
    cat_summary = ""
    if categories:
        cat_summary = "\n## Detected Error Categories\n"
        for cat, lines in categories.items():
            label = cat.replace("_", " ").title()
            cat_summary += f"\n### {label} ({len(lines)} occurrences)\n"
            for line in lines[:3]:  # show first 3 per category
                cat_summary += f"  - `{line.strip()[:120]}`\n"

    # Progressive strategy for this iteration
    strategy = get_progressive_strategy(iteration)

    prompt = f"""## Build Error Logs (Last 20 entries)
```
{error_text}
```
{cat_summary}
## Current Iteration
{iteration + 1} of {MAX_REFLEXION_ITERATIONS} allowed attempts

## Existing Files
{existing_files}
{strategy}
## Task
Analyze the error logs and generate a JSON array of fix tasks.
Focus on the root cause. Return ONLY the JSON array, no markdown.
"""
    return prompt


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
## Known Issue: Hydration Mismatch in Next.js 15

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
    The Debugger node - analyzes build errors and generates targeted fix plans.

    Phase 4 enhancements:
    - Categorizes errors into: import_errors, type_errors, runtime_errors,
      syntax_errors, dependency_errors, directive_errors
    - Applies category-specific focused prompts
    - Uses progressive fix strategies per iteration:
        Iterations 1-2: Minimal targeted fixes
        Iterations 3-4: Component restructuring
        Iteration 5:    Full rewrite of affected files
    - Escalates to human after MAX_REFLEXION_ITERATIONS (now 5) attempts

    Args:
        state: Current agent state with build_logs.
        config: Runnable configuration.

    Returns:
        State update with fix tasks and incremented iteration_count.
    """
    current_iteration = state.get("iteration_count", 0)
    build_logs = state.get("build_logs", [])
    thread_id = config.get("configurable", {}).get("thread_id", "") if config else ""

    logger.info(
        f"reflexion_node: Analyzing build errors "
        f"(iteration {current_iteration + 1}/{MAX_REFLEXION_ITERATIONS})"
    )

    # Check escalation threshold
    if current_iteration >= MAX_REFLEXION_ITERATIONS:
        logger.warning(
            f"reflexion_node: Max iterations ({MAX_REFLEXION_ITERATIONS}) reached. "
            "Escalating to human intervention."
        )
        if thread_id:
            channel_prefix = os.getenv("ABLY_CHANNEL_PREFIX", "ai-backend-generation")
            await publish_to_ably(
                f"{channel_prefix}:{thread_id}",
                {
                    "status": "reflexion_escalate",
                    "iteration": current_iteration,
                    "max_iterations": MAX_REFLEXION_ITERATIONS,
                    "message": f"Auto-fix limit reached ({MAX_REFLEXION_ITERATIONS} attempts). Escalating."
                }
            )
        return {
            "build_status": "escalate",
            "build_logs": build_logs + [
                f"ESCALATION: Max fix attempts ({MAX_REFLEXION_ITERATIONS}) reached. "
                "Human intervention required."
            ],
        }

    # --- Error categorization ---
    error_text = "\n".join(build_logs[-20:])  # Last 20 log entries
    categories = categorize_errors(build_logs[-50:])  # Analyse more context for categorisation

    logger.info(f"reflexion_node: Error categories detected: {list(categories.keys())}")

    # Notify frontend of reflexion progress
    if thread_id:
        strategy_label = (
            "minimal fix" if current_iteration <= 1
            else "component restructuring" if current_iteration <= 3
            else "full rewrite"
        )
        channel_prefix = os.getenv("ABLY_CHANNEL_PREFIX", "ai-backend-generation")
        await publish_to_ably(
            f"{channel_prefix}:{thread_id}",
            {
                "status": "reflexion_progress",
                "iteration": current_iteration + 1,
                "max_iterations": MAX_REFLEXION_ITERATIONS,
                "categories": list(categories.keys()),
                "strategy": strategy_label,
                "message": (
                    f"Auto-fix attempt {current_iteration + 1}/{MAX_REFLEXION_ITERATIONS}: "
                    f"{strategy_label.title()} — "
                    f"fixing {', '.join(categories.keys()) if categories else 'unknown errors'}"
                )
            }
        )

    # --- Build enhanced LLM prompt ---
    file_system = state.get("file_system", {})
    existing_files = "\n".join(f"- {path}" for path in sorted(file_system.keys()))

    # Build category-specific system prompt additions
    category_addons = ""
    for cat in categories:
        if cat in CATEGORY_PROMPTS:
            category_addons += CATEGORY_PROMPTS[cat]

    system_prompt = DEBUGGER_PROMPT + category_addons

    # Build user content with progressive strategy
    user_content = build_categorized_prompt(
        error_text=error_text,
        categories=categories,
        iteration=current_iteration,
        existing_files=existing_files,
    )

    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    # Augment with known GitHub issues for specific error patterns
    github_tool = MockGithubIssuesTool()
    error_lower = error_text.lower()
    github_queries = []
    if "hydration" in error_lower or "runtime_errors" in categories:
        github_queries.append("hydration")
    if "server action" in error_lower or "directive_errors" in categories:
        github_queries.append("server action")
    if "turbopack" in error_lower:
        github_queries.append("turbopack")

    for query in github_queries[:2]:  # Max 2 lookups to avoid excessive context
        github_context = await github_tool._arun(query)
        messages.append(HumanMessage(content=f"## Known GitHub Issues ({query})\n{github_context}"))

    # --- Generate fix plan ---
    llm = get_debugger_llm()

    try:
        response = await llm.ainvoke(messages, config=config)

        # Parse JSON response (strip markdown fences if present)
        content = response.content.strip()
        if content.startswith("```"):
            lines = content.split("\n")
            lines = lines[1:]
            if lines and lines[-1].strip() == "```":
                lines = lines[:-1]
            content = "\n".join(lines)

        fix_tasks = json.loads(content)

        # Enforce iteration-based task limit
        max_tasks = 3 if current_iteration <= 1 else 5
        fix_tasks = fix_tasks[:max_tasks]

        logger.info(
            f"reflexion_node: Generated {len(fix_tasks)} fix tasks "
            f"(strategy: {get_progressive_strategy(current_iteration)[:30].strip()})"
        )

        # Append fix tasks to implementation plan
        current_plan = list(state.get("implementation_plan", []))
        for task in current_plan:
            if "status" not in task:
                task["status"] = "completed"
        current_plan.extend(fix_tasks)

        return {
            "implementation_plan": current_plan,
            "iteration_count": current_iteration + 1,
            "build_status": "pending",
            "build_logs": build_logs + [
                f"Reflexion iteration {current_iteration + 1}: "
                f"Generated {len(fix_tasks)} fix tasks "
                f"(categories: {', '.join(categories.keys()) if categories else 'unknown'})"
            ],
        }

    except json.JSONDecodeError as e:
        logger.error(f"reflexion_node: Failed to parse fix plan: {e}")
        return {
            "iteration_count": current_iteration + 1,
            "build_status": "pending",
            "build_logs": build_logs + [f"Reflexion error: Failed to parse fix plan — {e}"],
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
