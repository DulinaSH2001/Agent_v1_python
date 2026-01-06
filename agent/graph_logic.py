"""
Antigravity Agent - Graph Logic

Core decision-making graph implementation providing:
- plan_node: The Architect - generates implementation plans using GPT-4o
- approval_node: The Gatekeeper - HITL interrupt for human approval
- check_approval: Conditional edge for the Antigravity loop
- generation_node: The Builder - generates code with MCP tools
- persistence_node: The Uploader - uploads to Azure Blob Storage
- trigger_build_node: Triggers external build and waits for result
- reflexion_node: The Debugger - analyzes errors and generates fixes
- Graph assembly with Redis checkpointing

This module implements the Human-in-the-Loop (HITL) workflow using
LangGraph's interrupt/Command pattern for pause/resume capability.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Literal, Optional

from dotenv import load_dotenv
from langchain_core.messages import HumanMessage, SystemMessage
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI
from langgraph.graph import StateGraph, END
from langgraph.types import interrupt, Command

from agent.state_engine import (
    AgentState,
    AblyCallbackHandler,
    create_redis_saver,
    get_graph_config,
)

# Import template loader
from agent.template_loader import (
    load_template,
    merge_with_template,
    get_template_context_for_planner,
    TEMPLATE_INFO,
)

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# System Prompts
# =============================================================================

ARCHITECT_PROMPT = """You are the Architect, a senior frontend engineer specializing in Next.js 16 applications.

## Your Role
Generate detailed, actionable implementation plans for Next.js projects based on:
1. A backend API manifest (endpoints, schemas, authentication)
2. User requirements for styling and functionality

## STRICT Next.js 16 Rules
You MUST follow these rules without exception:

1. **App Router Only**: Use the `app/` directory structure. Never use `pages/`.
   - Route: `app/dashboard/page.tsx`
   - Layout: `app/dashboard/layout.tsx`
   - Loading: `app/dashboard/loading.tsx`
   - Error: `app/dashboard/error.tsx`

2. **Server Actions**: Use Server Actions in `lib/actions.ts` instead of API Routes.
   - Define actions with `"use server"` directive
   - Call actions directly from components
   - Example:
     ```typescript
     // lib/actions.ts
     "use server"
     export async function createUser(formData: FormData) { ... }
     ```

3. **Shadcn UI Components**: Use Shadcn UI component names:
   - Button, Card, Input, Label, Dialog, Sheet
   - Table, Tabs, Badge, Avatar, Dropdown
   - Form (with react-hook-form + zod)
   - Toast (via sonner)

4. **TypeScript Strict Mode**: All files must use TypeScript with strict types.

5. **File Naming Conventions**:
   - Components: `components/ui/*.tsx` (Shadcn), `components/*.tsx` (custom)
   - Actions: `lib/actions.ts` or `lib/actions/*.ts`
   - Types: `types/*.ts` or co-located `*.types.ts`
   - Utilities: `lib/utils.ts`

## Output Format
Return a JSON array of implementation tasks:
```json
[
  {
    "id": "task-1",
    "type": "create" | "modify" | "delete",
    "file_path": "app/page.tsx",
    "description": "Create main landing page with hero section",
    "dependencies": [],
    "priority": 1,
    "estimated_lines": 50
  }
]
```

## Important
- Order tasks by dependency (independent tasks first)
- Include all necessary files (components, types, actions)
- Be specific about Shadcn components to use
- Consider responsive design requirements
"""

DELTA_PLANNING_INSTRUCTION = """
## CRITICAL: UPDATE MODE ACTIVE

This is an UPDATE request, not a fresh build. You must:

1. **Analyze Existing Files**: Review the file_system to understand current implementation
2. **Generate DELTA Plan Only**: Specify only files that need modification or addition
3. **Preserve Existing Work**: Do NOT destroy or recreate existing files unless explicitly requested
4. **Reference Existing Paths**: When modifying, use exact existing file paths
5. **Merge Logic**: For modifications, describe what to ADD or CHANGE, not full replacements

Mark tasks appropriately:
- `"type": "modify"` - Update existing file
- `"type": "create"` - New file only
- `"type": "delete"` - Remove file (rare, only if requested)
"""


# =============================================================================
# LLM Configuration
# =============================================================================

def get_planning_llm(
    temperature: float = 0.1,
    streaming: bool = True,
) -> ChatOpenAI:
    """
    Get a configured LLM instance for planning.
    
    Supports both Azure OpenAI and standard OpenAI based on environment variables.
    Checks for Azure config first, then falls back to standard OpenAI.
    
    Args:
        temperature: Sampling temperature. Low for deterministic planning.
        streaming: Whether to enable streaming. Required for token callbacks.
    
    Returns:
        Configured ChatOpenAI or AzureChatOpenAI instance.
    """
    # Check for Azure OpenAI configuration
    azure_endpoint = os.getenv("AZURE_OPENAI_ENDPOINT")
    azure_key = os.getenv("AZURE_OPENAI_API_KEY")
    azure_deployment = os.getenv("AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-4o")
    azure_version = os.getenv("AZURE_OPENAI_API_VERSION", "2025-01-01-preview")
    
    if azure_endpoint and azure_key:
        try:
            from langchain_openai import AzureChatOpenAI
            
            logger.info(f"Using Azure OpenAI: {azure_deployment}")
            return AzureChatOpenAI(
                azure_endpoint=azure_endpoint,
                api_key=azure_key,
                azure_deployment=azure_deployment,
                api_version=azure_version,
                temperature=temperature,
                streaming=streaming,
            )
        except ImportError:
            logger.warning("AzureChatOpenAI not available, falling back to OpenAI")
    
    # Fall back to standard OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "Neither Azure OpenAI nor OpenAI API key is configured. "
            "Set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY or OPENAI_API_KEY."
        )
    
    logger.info("Using standard OpenAI API")
    return ChatOpenAI(
        model="gpt-4o",
        temperature=temperature,
        streaming=streaming,
        api_key=api_key,
    )


# =============================================================================
# Node: plan_node (The Architect)
# =============================================================================

async def plan_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    The Architect node - generates implementation plans using GPT-4o.
    
    This node analyzes the manifest and user prompt to create a detailed
    implementation plan. When file_system is not empty, it switches to
    DELTA mode to preserve existing work (Antigravity pattern).
    
    Args:
        state: Current agent state containing manifest, user_prompt, file_system.
        config: Runnable configuration with thread_id and callbacks.
    
    Returns:
        State update with implementation_plan.
    """
    logger.info("plan_node: Starting plan generation")
    
    # Get the LLM
    llm = get_planning_llm()
    
    # Build the system prompt
    system_prompt = ARCHITECT_PROMPT
    
    # Check if this is an update request (Antigravity pattern)
    if state.get("file_system"):
        logger.info("plan_node: DELTA mode - existing files detected")
        system_prompt += DELTA_PLANNING_INSTRUCTION
    
    # Build the user message with context
    manifest_str = json.dumps(state.get("manifest", {}), indent=2)
    file_system = state.get("file_system", {})
    
    user_content = f"""## Backend API Manifest
```json
{manifest_str}
```

## User Requirements
{state.get("user_prompt", "No specific requirements provided.")}
"""
    
    # Add existing files context if in delta mode
    if file_system:
        existing_files = "\n".join(f"- {path}" for path in file_system.keys())
        user_content += f"""
## Existing Files (DO NOT recreate unless modifying)
{existing_files}
"""
    else:
        # No existing files - include template info so planner knows what's available
        user_content += get_template_context_for_planner()
    
    user_content += """
## Task
Generate the implementation plan as a JSON array. Return ONLY the JSON array, no markdown formatting.
"""
    
    # Prepare messages
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]
    
    # Invoke the LLM
    try:
        response = await llm.ainvoke(messages, config=config)
        
        # Parse the JSON response
        content = response.content.strip()
        
        # Handle potential markdown code blocks
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        
        implementation_plan = json.loads(content)
        
        logger.info(f"plan_node: Generated {len(implementation_plan)} tasks")
        
        return {
            "implementation_plan": implementation_plan,
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
        
    except json.JSONDecodeError as e:
        logger.error(f"plan_node: Failed to parse LLM response as JSON: {e}")
        # Return a fallback plan indicating the error
        return {
            "implementation_plan": [{
                "id": "error-1",
                "type": "error",
                "file_path": "",
                "description": f"Failed to generate plan: {str(e)}",
                "dependencies": [],
                "priority": 0,
            }],
            "iteration_count": state.get("iteration_count", 0) + 1,
        }
    except Exception as e:
        logger.error(f"plan_node: Unexpected error: {e}")
        raise


# =============================================================================
# Node: approval_node (The Gatekeeper)
# =============================================================================

async def approval_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    The Gatekeeper node - pauses execution for human approval.
    
    This node publishes the generated plan to an Ably channel for UI rendering,
    then calls interrupt() to pause execution. When resumed via Command,
    it processes the human's decision.
    
    Resume payloads:
        - {"action": "APPROVE"} -> Sets approved=True, continues to scaffold
        - {"action": "EDIT", "feedback": "..."} -> Updates user_prompt, loops back
    
    Args:
        state: Current agent state with implementation_plan.
        config: Runnable configuration with thread_id.
    
    Returns:
        State update with approved status and potentially updated user_prompt.
    """
    logger.info("approval_node: Awaiting human approval")
    
    # Extract thread_id from config for Ably channel
    thread_id = config.get("configurable", {}).get("thread_id", "unknown")
    
    # Prepare the plan summary for the interrupt payload
    plan = state.get("implementation_plan", [])
    plan_summary = {
        "thread_id": thread_id,
        "task_count": len(plan),
        "tasks": plan,
        "iteration": state.get("iteration_count", 0),
        "awaiting_action": ["APPROVE", "EDIT"],
    }
    
    # Note: In a real implementation, you would publish to Ably here
    # For now, the interrupt payload contains the plan for the caller
    logger.info(f"approval_node: Publishing plan with {len(plan)} tasks to channel")
    
    # Interrupt execution and wait for human input
    # The payload becomes available to the caller and will be returned
    # when they query the graph state
    human_response = interrupt(plan_summary)
    
    # Process the human's response (after resume via Command)
    action = human_response.get("action", "").upper()
    
    if action == "APPROVE":
        logger.info("approval_node: Plan APPROVED by human")
        return {"approved": True}
    
    elif action == "EDIT":
        feedback = human_response.get("feedback", "")
        logger.info(f"approval_node: Plan EDIT requested with feedback: {feedback[:100]}...")
        
        # Append feedback to user_prompt for next iteration
        original_prompt = state.get("user_prompt", "")
        updated_prompt = f"{original_prompt}\n\n[REVISION FEEDBACK]: {feedback}"
        
        return {
            "approved": False,
            "user_prompt": updated_prompt,
        }
    
    else:
        # Unknown action, treat as rejection
        logger.warning(f"approval_node: Unknown action '{action}', treating as rejection")
        return {"approved": False}


# =============================================================================
# Execution Layer Import
# =============================================================================

# Import execution nodes from execution_layer module
from agent.execution_layer import (
    generation_node,
    persistence_node,
    BUILDER_PROMPT,
    get_mcp_wrapper,
    list_generated_files,
)

# Import template nodes
from agent.template_nodes import template_selection_node, template_upload_node

# Import reflexion nodes from reflexion module
from agent.reflexion import (
    trigger_build_node,
    reflexion_node,
    escalation_node,
    should_fix,
    DEBUGGER_PROMPT,
    MAX_REFLEXION_ITERATIONS,
)


# =============================================================================
# Conditional Edge: check_approval
# =============================================================================

def check_approval(state: AgentState) -> Literal["template_upload", "planner"]:
    """
    Conditional edge that routes based on approval status.
    
    This implements the Antigravity loop with two-phase generation:
    - If approved: proceed to template upload (Phase 1)
    - If not approved: loop back to planning with updated feedback
    
    Args:
        state: Current agent state with approved flag.
    
    Returns:
        Next node name: "template_upload" or "planner"
    """
    if state.get("approved", False):
        logger.info("check_approval: Approved -> template_upload (Phase 1)")
        return "template_upload"
    else:
        logger.info("check_approval: Not approved -> planner (Antigravity loop)")
        return "planner"


# =============================================================================
# Graph Assembly
# =============================================================================

def create_antigravity_graph(
    checkpointer: Optional[Any] = None,
    enable_reflexion: bool = True,
    skip_approval: bool = False,
) -> Any:
    """
    Create and compile the Antigravity agent graph.
    
    The graph implements the following flow:
    1. planner (plan_node) - Generate implementation plan
    2. approval (approval_node) - HITL interrupt for approval (skipped if skip_approval=True)
    3. Conditional: approved? -> generator : loop to planner
    4. generator (generation_node) - Generate code with MCP tools
    5. persistence (persistence_node) - Upload to Azure Blob Storage
    6. trigger_build (trigger_build_node) - Request external build
    7. Conditional: build_status? -> end/reflexion/escalate
    8. reflexion (reflexion_node) - Analyze errors and generate fixes
    9. escalation (escalation_node) - Request human help if max retries
    
    Args:
        checkpointer: Optional checkpointer (e.g., AsyncRedisSaver) for persistence.
            Required for interrupt/resume to work across sessions.
        enable_reflexion: Whether to include the reflexion loop. Defaults to True.
        skip_approval: Whether to skip the HITL approval step. Defaults to False.
            Set to True when running without checkpointer.
    
    Returns:
        Compiled graph ready for execution.
    
    Example:
        >>> from agent.state_engine import create_redis_saver
        >>> checkpointer = create_redis_saver()
        >>> graph = create_antigravity_graph(checkpointer=checkpointer)
        >>> 
        >>> # Run the graph
        >>> config = {"configurable": {"thread_id": "session-123"}}
        >>> result = await graph.ainvoke(initial_state, config=config)
    """
    # Create the graph with AgentState schema
    builder = StateGraph(AgentState)
    
    # Add core nodes
    builder.add_node("template_selection", template_selection_node)
    builder.add_node("planner", plan_node)
    builder.add_node("generator", generation_node)
    builder.add_node("template_upload", template_upload_node)  # Phase 1: Upload template
    builder.add_node("persistence", persistence_node)  # Phase 2: Upload custom files
    
    if skip_approval:
        # Simple flow: template_selection -> planner -> generator (no HITL)
        logger.info("Building graph with skip_approval=True (no HITL)")
        builder.set_entry_point("template_selection")
        builder.add_edge("template_selection", "planner")
        builder.add_edge("planner", "template_upload")  # Upload template before generation
        builder.add_edge("template_upload", "generator")
    else:
        # Full HITL flow with approval node
        builder.add_node("approval", approval_node)
        builder.set_entry_point("template_selection")
        builder.add_edge("template_selection", "planner")
        builder.add_edge("planner", "approval")
        
        # Conditional edge from approval
        builder.add_conditional_edges(
            "approval",
            check_approval,
            {
                 "template_upload": "template_upload",  # Phase 1: Upload template after approval
                "planner": "planner",
            }
        )
        
        # Chain: template_upload -> generator (Phase 2)
        builder.add_edge("template_upload", "generator")
    
    # Chain: generator -> persistence
    builder.add_edge("generator", "persistence")
    
    if enable_reflexion:
        # Add reflexion nodes
        builder.add_node("trigger_build", trigger_build_node)
        builder.add_node("reflexion", reflexion_node)
        builder.add_node("escalation", escalation_node)
        
        # Chain: persistence -> trigger_build
        builder.add_edge("persistence", "trigger_build")
        
        # Conditional edge from trigger_build based on build status
        builder.add_conditional_edges(
            "trigger_build",
            should_fix,
            {
                "end": END,
                "reflexion": "reflexion",
                "escalate": "escalation",
            }
        )
        
        # Reflexion loops back to generator
        builder.add_edge("reflexion", "generator")
        
        # Escalation can loop back to planner (after human input)
        builder.add_edge("escalation", "planner")
    else:
        # Simple flow: persistence -> END
        builder.add_edge("persistence", END)
    
    # Compile with checkpointer if provided
    if checkpointer:
        logger.info("Compiling graph with checkpointer for persistence")
        return builder.compile(checkpointer=checkpointer)
    else:
        logger.info("Compiling graph without checkpointer (no persistence)")
        return builder.compile()


# =============================================================================
# Convenience Functions
# =============================================================================

async def run_antigravity_agent(
    manifest: Dict[str, Any],
    user_prompt: str,
    thread_id: str,
    file_system: Optional[Dict[str, str]] = None,
    redis_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Convenience function to run the Antigravity agent.
    
    This sets up the graph with Redis persistence and runs it with the
    provided inputs. The graph will pause at the approval node and
    return the interrupt data.
    
    Args:
        manifest: Backend API manifest.
        user_prompt: User's frontend requirements.
        thread_id: Unique thread identifier for persistence.
        file_system: Optional existing files (triggers delta mode).
        redis_url: Optional Redis URL (defaults to REDIS_URL env var).
    
    Returns:
        Graph execution result or interrupt state.
    
    Example:
        >>> result = await run_antigravity_agent(
        ...     manifest={"endpoints": ["/api/users"]},
        ...     user_prompt="Create a modern dashboard",
        ...     thread_id="session-123",
        ... )
    """
    # Create checkpointer
    checkpointer = create_redis_saver(redis_url)
    
    # Create graph
    graph = create_antigravity_graph(checkpointer=checkpointer)
    
    # Load template files if no existing file_system provided
    if not file_system:
        logger.info("Loading template files as base")
        file_system = load_template("nextjs-app")
    
    # Prepare initial state
    initial_state: AgentState = {
        "manifest": manifest,
        "user_prompt": user_prompt,
        "file_system": file_system,
        "implementation_plan": [],
        "build_logs": [],
        "iteration_count": 0,
        "messages": [],
        "approved": False,
        "build_ready": False,
        "build_status": "pending",
    }
    
    # Get config
    config = get_graph_config(thread_id)
    
    # Run the graph
    logger.info(f"Starting Antigravity agent for thread: {thread_id}")
    result = await graph.ainvoke(initial_state, config=config)
    
    return result


async def resume_antigravity_agent(
    thread_id: str,
    action: Literal["APPROVE", "EDIT"],
    feedback: Optional[str] = None,
    redis_url: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Resume the Antigravity agent after human review.
    
    This function resumes a paused graph execution with the human's
    decision (approve or edit with feedback).
    
    Args:
        thread_id: Thread identifier of the paused execution.
        action: Human action - "APPROVE" or "EDIT".
        feedback: Required feedback text if action is "EDIT".
        redis_url: Optional Redis URL (defaults to REDIS_URL env var).
    
    Returns:
        Graph execution result after resume.
    
    Example:
        >>> # Approve the plan
        >>> result = await resume_antigravity_agent(
        ...     thread_id="session-123",
        ...     action="APPROVE",
        ... )
        >>> 
        >>> # Request edits
        >>> result = await resume_antigravity_agent(
        ...     thread_id="session-123",
        ...     action="EDIT",
        ...     feedback="Add dark mode toggle to the header",
        ... )
    """
    # Create checkpointer
    checkpointer = create_redis_saver(redis_url)
    
    # Create graph
    graph = create_antigravity_graph(checkpointer=checkpointer)
    
    # Get config
    config = get_graph_config(thread_id)
    
    # Build resume payload
    resume_payload: Dict[str, Any] = {"action": action}
    if action == "EDIT" and feedback:
        resume_payload["feedback"] = feedback
    
    # Resume with Command
    logger.info(f"Resuming Antigravity agent for thread: {thread_id} with action: {action}")
    result = await graph.ainvoke(
        Command(resume=resume_payload),
        config=config,
    )
    
    return result


# =============================================================================
# Graph State Inspection
# =============================================================================

async def get_agent_state(
    thread_id: str,
    redis_url: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get the current state of an Antigravity agent execution.
    
    This is useful for checking if the agent is paused at an interrupt
    and retrieving the plan for UI display.
    
    Args:
        thread_id: Thread identifier to inspect.
        redis_url: Optional Redis URL (defaults to REDIS_URL env var).
    
    Returns:
        Current agent state or None if not found.
    """
    # Create checkpointer
    checkpointer = create_redis_saver(redis_url)
    
    # Create graph
    graph = create_antigravity_graph(checkpointer=checkpointer)
    
    # Get config
    config = get_graph_config(thread_id)
    
    # Get state
    try:
        state = await graph.aget_state(config)
        return state.values if state else None
    except Exception as e:
        logger.error(f"Failed to get agent state: {e}")
        return None
