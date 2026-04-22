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
from agent.reflexion import (
    trigger_build_node,
    reflexion_node,
    escalation_node,
    should_fix,
    DEBUGGER_PROMPT,
    MAX_REFLEXION_ITERATIONS,
)
from agent.template_nodes import template_selection_node, template_upload_node
from agent.execution_layer import (
    generation_node,
    persistence_node,
    code_review_node,
    BUILDER_PROMPT,
    get_mcp_wrapper,
    list_generated_files,
)

import json
import logging
import os
import re
from typing import Any, Dict, List, Literal, Optional, Union

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

# Import conversation memory
from agent.memory import get_memory

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)


# =============================================================================
# System Prompts
# =============================================================================

ARCHITECT_PROMPT = """You are a frontend engineer planning Next.js 15 implementation.

Return a JSON array with tasks like:
[
  {"id": "task-1", "type": "create|modify", "file_path": "app/dashboard/page.tsx", "description": "Page description", "priority": 1, "dependencies": ["recharts"]}
]

Task types:
- "create": New file that does not exist in the template.
- "modify": Update an existing template file (app/layout.tsx, app/page.tsx, lib/data.ts, etc.).

Use app/ directory. Use TypeScript. Import existing template components when suitable.
Use Shadcn components, sonner for toasts, Server Actions in lib/actions.ts.
You may modify any file including layout.tsx, package.json, etc. when needed.
Always include a "modify" task for app/page.tsx to replace the placeholder with your home page.
Always include a "modify" task for lib/data.ts to add project-specific sample data.
For each task, list any npm packages needed beyond what the template already provides.
Base template does NOT include: recharts, framer-motion,
  @tanstack/react-query, @tanstack/react-table, axios, zustand, mapbox-gl, react-pdf, react-markdown, socket.io-client, pusher-js.
Note: For tables, use the pre-built DataTable component (`@/components/data/DataTable`) — do NOT add @tanstack/react-table as a dependency.
Always list these in "dependencies" when any task file imports them.
For each task description, include styling notes: layout type (grid/flex/single-column), whether it needs a hero section, card grids, data tables, or forms. This helps the Builder generate polished, modern UI.
PAYMENT GUARDRAIL: NEVER add @stripe/stripe-js, @stripe/react-stripe-js, or any other
  external payment SDK to "dependencies". Payment pages must use simple HTML forms only.
ORM GUARDRAIL: NEVER add @prisma/client, prisma, drizzle-orm, typeorm, sequelize, or mongoose
  to "dependencies". Data must be defined as exported const arrays in lib/data.ts — no DB, no ORM.
"""

DELTA_PLANNING_INSTRUCTION = """
## Update Mode

This is an UPDATE request, not a fresh build. Please:

1. **Analyze Existing Files**: Review the file_system to understand current implementation
2. **Generate DELTA Plan Only**: Specify only files that need modification or addition
3. **Preserve Existing Work**: Avoid destroying or recreating existing files unless explicitly requested
4. **Reference Existing Paths**: When modifying, use exact existing file paths
5. **Merge Logic**: For modifications, describe what to ADD or CHANGE, not full replacements

Mark tasks appropriately:
- `"type": "modify"` - Update existing file
- `"type": "create"` - New file only
- `"type": "delete"` - Remove file (rare, only if requested)
"""

VISUAL_EDIT_INSTRUCTION = """
## VISUAL EDITOR CONTEXT

This modification was triggered from the visual editor. The user selected a specific UI element and requested changes to it.

**Visual context provided:**
{visual_context_str}

When applying this change:
1. **Find the React component** that renders the element matching the CSS classes and text content above
2. **Apply ONLY the specified style changes** — use Tailwind CSS utility classes (not inline styles) where possible
3. **Preserve all other code**, state management, props, and logic unchanged
4. **Modify the minimal number of files** — usually just 1 component file
5. If a Tailwind class equivalent exists for the style change, use it (e.g. `bg-blue-500` not `backgroundColor: '#3B82F6'`)
"""

SHADCN_KEYWORD_MAP = {
    "button": ["button", "cta", "submit", "click"],
    "card": ["card", "tile", "panel"],
    "input": ["input", "field", "textbox"],
    "label": ["label"],
    "badge": ["badge", "tag", "chip"],
    "dialog": ["dialog", "modal", "popup"],
    "skeleton": ["skeleton", "loading placeholder", "loading state"],
    "table": ["table", "grid", "list view"],
}


def infer_required_shadcn(text: str) -> List[str]:
    if not text:
        return []

    lower = text.lower()
    required: List[str] = []
    for component, terms in SHADCN_KEYWORD_MAP.items():
        if any(term in lower for term in terms):
            required.append(component)
    return sorted(set(required))


def enrich_plan_with_mcp_metadata(
    implementation_plan: List[Dict[str, Any]],
    mcp_tools_used: List[str],
) -> List[Dict[str, Any]]:
    enriched: List[Dict[str, Any]] = []
    tools = sorted(set(mcp_tools_used))

    for i, task in enumerate(implementation_plan, 1):
        if not isinstance(task, dict):
            continue
        task_copy = dict(task)
        task_copy.setdefault("index", i)

        combined_text = f"{task_copy.get('description', '')} {task_copy.get('file_path', '')}"
        existing_requires = task_copy.get("requires_shadcn")
        if isinstance(existing_requires, list):
            inferred = [str(x).strip().lower()
                        for x in existing_requires if str(x).strip()]
        else:
            inferred = infer_required_shadcn(combined_text)

        if inferred:
            task_copy["requires_shadcn"] = sorted(set(inferred))
        if tools:
            current_tools = task_copy.get("mcp_tools_used", [])
            if not isinstance(current_tools, list):
                current_tools = []
            task_copy["mcp_tools_used"] = sorted(set([*current_tools, *tools]))

        enriched.append(task_copy)

    return enriched


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
    azure_deployment = os.getenv(
        "AZURE_OPENAI_DEPLOYMENT_NAME", "gpt-5.2-chat")
    azure_version = os.getenv("AZURE_OPENAI_API_VERSION", "2024-05-01-preview")

    # GPT-5.2 only supports temperature=1 (default)
    if "gpt-5.2" in azure_deployment.lower():
        temperature = 1.0

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
            logger.warning(
                "AzureChatOpenAI not available, falling back to OpenAI")

    # Fall back to standard OpenAI
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        raise ValueError(
            "Neither Azure OpenAI nor OpenAI API key is configured. "
            "Set AZURE_OPENAI_ENDPOINT + AZURE_OPENAI_API_KEY or OPENAI_API_KEY."
        )

    logger.info("Using standard OpenAI API")
    return ChatOpenAI(
        model="gpt-5.2",
        temperature=temperature,
        streaming=streaming,
        api_key=api_key,
    )


# =============================================================================
# Utility: Detect dark background from hex color
# =============================================================================

def _is_dark_background(hex_color: str) -> bool:
    """Return True if the hex color has low luminance (dark background)."""
    h = hex_color.lstrip('#')
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    # Relative luminance approximation
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return luminance < 128


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

    Before planning, it loads conversation memory from the backend to
    provide context from prior generations and user preferences.

    Args:
        state: Current agent state containing manifest, user_prompt, file_system.
        config: Runnable configuration with thread_id and callbacks.

    Returns:
        State update with implementation_plan, conversation_history, project_context.
    """
    logger.info("plan_node: Starting plan generation")

    # Get the LLM
    llm = get_planning_llm()

    # Build the system prompt
    system_prompt = ARCHITECT_PROMPT

    # Classify project type and inject context (fresh builds only)
    project_type_info = {"type": "general", "config": {}, "confidence": 0.0}
    if not state.get("file_system") and not state.get("visual_context"):
        try:
            from agent.project_classifier import classify_project
            project_type_info = classify_project(
                state.get("user_prompt", "")
            )
            if project_type_info["type"] != "general":
                cfg = project_type_info["config"]
                system_prompt += (
                    f"\nProject type: {project_type_info['type']}. "
                    f"Required pages: {', '.join(cfg['required_pages'])}. "
                    f"Layout: {cfg['layout_style']}. "
                    f"Navigation: {cfg['nav_style']}.\n"
                )
                logger.info(
                    "plan_node: Classified as '%s' (confidence=%.2f)",
                    project_type_info["type"],
                    project_type_info["confidence"],
                )
        except Exception as e:
            logger.warning("plan_node: Project classification failed: %s", e)

    # Check if this is an update request (Antigravity pattern)
    if state.get("file_system"):
        logger.info("plan_node: DELTA mode - existing files detected")
        system_prompt += DELTA_PLANNING_INSTRUCTION

    # Check if this modification came from the visual editor
    visual_context = state.get("visual_context")
    if visual_context:
        logger.info(
            "plan_node: Visual editor context detected, adding targeted edit instructions")
        vc = visual_context
        changes_str = "\n".join(
            [f"  - {k}: {v}" for k, v in vc.get("changes", {}).items()])
        visual_context_str = (
            f"Element: <{vc.get('element_tag', 'unknown')}> "
            f"class=\"{vc.get('element_classes', '')}\" "
            f"text=\"{vc.get('element_text', '')}\"\n"
            f"Style changes:\n{changes_str if changes_str else '  (none — see NL request)'}"
        )
        system_prompt += VISUAL_EDIT_INSTRUCTION.format(
            visual_context_str=visual_context_str)

        # Fast path: when the visual editor stamped data-edit-id onto the clicked
        # element (via source_tagger), we know the exact file to modify. Skip the
        # architect LLM entirely — it would only re-derive what we already know,
        # at ~30s cost and with nonzero "wrong file" rate.
        source_file = vc.get("source_file")
        if source_file and state.get("file_system", {}).get(source_file) is not None:
            logger.info(
                "plan_node: Visual edit fast-path — targeting %s:%s (%s) without architect LLM",
                source_file,
                vc.get("source_line"),
                vc.get("component_name"),
            )
            desc_parts = [
                f"Apply the visual editor changes to component "
                f"{vc.get('component_name') or '(unknown)'} at "
                f"{source_file}:{vc.get('source_line', '?')}."
            ]
            if changes_str:
                desc_parts.append(
                    f"Required style changes (use Tailwind classes where possible):\n{changes_str}"
                )
            desc_parts.append(
                f"Selected element: <{vc.get('element_tag', 'unknown')}> "
                f"class=\"{vc.get('element_classes', '')}\" "
                f"text=\"{vc.get('element_text', '')}\""
            )
            desc_parts.append(
                "Preserve all other code, props, state, and logic. Modify only this file."
            )
            fast_plan = [{
                "id": "visual-edit-1",
                "type": "modify",
                "file_path": source_file,
                "description": "\n\n".join(desc_parts),
                "priority": 0,
                "visual_edit_source_line": vc.get("source_line"),
                "visual_edit_component": vc.get("component_name"),
            }]
            return {
                "implementation_plan": fast_plan,
                "iteration_count": state.get("iteration_count", 0) + 1,
                "conversation_history": state.get("conversation_history", []),
                "project_context": state.get("project_context", {}),
                "retrieval_metadata": {"visual_edit_fast_path": True},
            }

    # --- Load conversation memory ---
    memory = get_memory()
    org_slug = state.get("org_slug", "")
    project_slug = state.get("project_slug", "")
    thread_id = config.get("configurable", {}).get("thread_id", "")
    mcp_context: Dict[str, Any] = {
        "references": [],
        "tools_used": [],
        "warnings": [],
    }

    try:
        from agent.execution_layer import gather_mcp_context
        mcp_context = await gather_mcp_context(
            phase="planning",
            prompt=state.get("user_prompt", ""),
            manifest=state.get("manifest", {}),
            task={"description": state.get("user_prompt", "")},
            thread_id=thread_id,
            max_references=3,
        )
    except Exception as e:
        logger.warning(f"plan_node: MCP planning enrichment unavailable: {e}")

    conversation_history = state.get("conversation_history", [])
    project_context = state.get("project_context", {})

    # Load project context if not already loaded
    if not project_context and org_slug and project_slug:
        try:
            project_context = await memory.load_project_context(org_slug, project_slug)
            logger.info(
                f"plan_node: Loaded project context with {len(project_context.get('generations', []))} prior generations")
        except Exception as e:
            logger.warning(f"plan_node: Failed to load project context: {e}")

    # Load conversation history if not already loaded
    if not conversation_history and thread_id:
        try:
            conversation_history = await memory.load_history(thread_id)
            logger.info(
                f"plan_node: Loaded {len(conversation_history)} history messages")
        except Exception as e:
            logger.warning(
                f"plan_node: Failed to load conversation history: {e}")

    # Summarize conversation if needed
    conversation_summary = ""
    if conversation_history:
        try:
            conversation_summary = await memory.summarize_context(conversation_history)
        except Exception as e:
            logger.warning(f"plan_node: Failed to summarize context: {e}")

    # Build context prompt section from memory
    context_prompt = memory.build_context_prompt(
        project_context, conversation_summary)

    # Build the user message with context
    manifest_raw = state.get("manifest", {})
    manifest_str = manifest_raw if isinstance(manifest_raw, str) else json.dumps(manifest_raw, indent=2)
    file_system = state.get("file_system", {})

    user_content = ""

    # Inject memory context first if available
    if context_prompt:
        user_content += f"""{context_prompt}

---

"""

    user_content += f"""## Backend API Manifest
```json
{manifest_str}
```
"""

    # Add endpoint hint if manifest has endpoints
    if isinstance(manifest_raw, dict) and manifest_raw:
        endpoints = manifest_raw.get("endpoints", [])
        if endpoints:
            user_content += f"(Backend has {len(endpoints)} endpoints — use these for real data fetching, not mock data)\n"

    user_content += f"""
## User Requirements
{state.get("user_prompt", "No specific requirements provided.")}
"""

    # ── Variation seeds (fresh builds only) ──────────────────────────────
    # Inject random design direction so the same prompt generates different
    # projects each time. Skipped for delta/visual-edit modes.
    if not file_system and not state.get("visual_context"):
        user_color_palette = state.get("color_palette")
        if user_color_palette:
            # Use the user-selected color palette (deterministic)
            palette_name = user_color_palette.get("name", "Custom")
            primary = user_color_palette.get("primary", "#3b82f6")
            secondary = user_color_palette.get("secondary", "#6366f1")
            accent = user_color_palette.get("accent", "#8b5cf6")
            background = user_color_palette.get("background", "#ffffff")
            foreground = user_color_palette.get("foreground", "#171717")

            # Determine style automatically from background luminance
            is_dark = _is_dark_background(background)
            style_desc = "sleek dark-theme aesthetic with high contrast accents" if is_dark else "clean and modern with a light, airy feel"

            from agent.execution_layer import get_color_palette_css_instruction
            palette_css_instruction = get_color_palette_css_instruction(
                user_color_palette)

            user_content += f"""
## Design Direction (User-Selected Palette: {palette_name})
Style: {style_desc}.
Colors: primary={primary}, secondary={secondary}, accent={accent}, bg={background}.
{palette_css_instruction}

Apply modern polish to EVERY page and component:
- Hero/landing heading: font-bold tracking-tight text-5xl xl:text-7xl — use text-gradient class for color
- Primary CTAs: gradient-primary class + hover:shadow-glow hover:-translate-y-0.5 transition-all duration-150
- Feature/data cards: shadow-soft hover:shadow-elevated hover:-translate-y-0.5 transition-all duration-200 animate-slide-up
- Navigation bar: sticky top-0 z-50 glass class for frosted-glass blur effect
- Section backgrounds: alternate between bg-background and bg-muted/30 with subtle gradient overlays
- Typography: font-semibold for section headings, tracking-tight for large text, text-muted-foreground for body
"""
        else:
            # Fallback: random design direction for variety
            import random
            _DESIGN_STYLES = [
                "modern and clean with plenty of whitespace",
                "bold with vibrant accent colors and strong typography",
                "minimalist with subtle animations and smooth transitions",
                "professional with a structured layout and clear hierarchy",
                "creative with asymmetric layout and unique visual elements",
                "warm and approachable with rounded corners and soft shadows",
                "sleek dark-theme aesthetic with high contrast accents",
                "elegant with serif typography and refined spacing",
            ]
            _COLOR_PALETTES = [
                "blue and indigo tones",
                "emerald and teal accents",
                "purple and violet theme",
                "warm amber and orange highlights",
                "neutral grays with a single bright accent color",
                "slate and sky blue combination",
                "rose and pink accents on neutral base",
                "forest green with warm earth tones",
            ]
            style = random.choice(_DESIGN_STYLES)
            color_theme = random.choice(_COLOR_PALETTES)
            user_content += f"""
## Design Direction: {style}
Colors: {color_theme}.

Apply modern polish to EVERY page and component:
- Hero/landing heading: font-bold tracking-tight text-5xl xl:text-7xl with text-gradient class
- Primary CTAs: gradient-primary class + hover:shadow-glow hover:-translate-y-0.5 transition-all duration-150
- Feature/data cards: shadow-soft hover:shadow-elevated hover:-translate-y-0.5 transition-all duration-200 animate-slide-up
- Navigation bar: sticky top-0 z-50 glass class for frosted-glass blur effect
- Section backgrounds: alternate bg-background and bg-muted/30, use subtle gradient overlays
- Typography: font-semibold for section headings, tracking-tight for large text, text-muted-foreground for body
"""

    # ── Anti-repetition from conversation history ────────────────────────
    # When prior generations exist, tell the architect to vary the approach.
    if conversation_history and not file_system:
        prior_pages: set = set()
        for msg in conversation_history:
            msg_content = msg.get("content", "")
            if isinstance(msg_content, str):
                found = re.findall(r'app/[\w\-/]+/page\.tsx', msg_content)
                prior_pages.update(found)
        if prior_pages:
            pages_str = ", ".join(sorted(prior_pages)[:10])
            user_content += f"""
## Variation Requirement
Previous generations in this project created: {pages_str}.
Use a different page structure and layout approach this time.
"""

    if mcp_context.get("references"):
        mcp_lines = "\n".join(f"- {ref}" for ref in mcp_context["references"])
        user_content += f"""
## MCP References
{mcp_lines}
"""

    # --- Template Manifest (always injected for full template knowledge) ---
    try:
        from agent.template_manifest import get_manifest_for_prompt
        user_content += "\n" + get_manifest_for_prompt() + "\n"
    except Exception as e:
        logger.warning(f"plan_node: Template manifest injection failed: {e}")

    # --- RAG Template Retrieval ---
    retrieval_metadata: Dict[str, Any] = {}
    rag_context = ""

    # Add existing files context if in delta mode
    if file_system:
        existing_files = "\n".join(f"- {path}" for path in file_system.keys())
        user_content += f"""
## Existing Files (DO NOT recreate unless modifying)
{existing_files}
"""
    else:
        # No existing files - try RAG retrieval for template context
        try:
            from agent.template_rag import get_rag_context_for_prompt
            rag_context, retrieval_metadata = get_rag_context_for_prompt(
                query=state.get("user_prompt", ""),
                task_description=state.get("user_prompt", ""),
                top_k=8,
            )
            if not retrieval_metadata.get("fallback_used"):
                logger.info(
                    f"plan_node: RAG retrieved {retrieval_metadata.get('chunks_retrieved', 0)} chunks "
                    f"from {len(retrieval_metadata.get('file_paths_matched', []))} files"
                )
        except Exception as e:
            logger.warning(
                f"plan_node: RAG retrieval failed, using static template info: {e}")
            rag_context = get_template_context_for_planner()
            retrieval_metadata = {"fallback_used": True, "error": str(e)}

        # Use RAG context (or fallback static info)
        user_content += rag_context if rag_context else get_template_context_for_planner()

        # Emit retrieval_context Ably event for frontend visibility
        if thread_id and retrieval_metadata.get("chunks_retrieved", 0) > 0:
            try:
                from agent.reflexion import publish_to_ably
                channel_prefix = os.getenv(
                    "ABLY_CHANNEL_PREFIX", "ai-backend-generation")
                await publish_to_ably(
                    f"{channel_prefix}:{thread_id}",
                    {
                        "status": "retrieval_context",
                        "chunks_retrieved": retrieval_metadata["chunks_retrieved"],
                        "file_paths": retrieval_metadata.get("file_paths_matched", []),
                        "message": f"Retrieved {retrieval_metadata['chunks_retrieved']} relevant template snippets",
                    }
                )
            except Exception as e:
                logger.debug(
                    f"plan_node: Failed to publish retrieval_context event: {e}")

    user_content += """
## Task
Generate the implementation plan as a JSON array. Return ONLY the JSON array, no markdown formatting.
"""

    # Prepare messages
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=user_content),
    ]

    async def _invoke_and_parse(msgs: list) -> list:
        """Invoke LLM and parse JSON response. Returns the implementation_plan list."""
        resp = await llm.ainvoke(msgs, config=config)
        content = resp.content.strip()
        if content.startswith("```"):
            content = content.split("```")[1]
            if content.startswith("json"):
                content = content[4:]
            content = content.strip()
        return json.loads(content)

    def _build_minimal_user_content() -> str:
        """Fallback: build a minimal user message without RAG context or manifest."""
        minimal = f"""## User Requirements\n{state.get("user_prompt", "No specific requirements provided.")}

## Task
Generate the implementation plan as a JSON array. Return ONLY the JSON array, no markdown formatting.
"""
        return minimal

    # Invoke the LLM
    try:
        implementation_plan = await _invoke_and_parse(messages)

    except json.JSONDecodeError as e:
        logger.error(f"plan_node: Failed to parse LLM response as JSON: {e}")
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
            "conversation_history": conversation_history,
            "project_context": project_context,
            "retrieval_metadata": retrieval_metadata,
        }

    except Exception as e:
        # Check if this is an Azure OpenAI content filter (jailbreak false-positive)
        err_str = str(e)
        is_content_filter = (
            "content_filter" in err_str
            or "content management policy" in err_str
            or "ResponsibleAIPolicyViolation" in err_str
        )
        if not is_content_filter:
            logger.error(f"plan_node: Unexpected error: {e}")
            raise

        # --- Content filter retry: strip RAG context and manifest, use minimal prompt ---
        logger.warning(
            "plan_node: Content filter triggered (jailbreak false-positive). "
            "Retrying with minimal prompt (no RAG context)."
        )
        minimal_messages = [
            SystemMessage(content=ARCHITECT_PROMPT),
            HumanMessage(content=_build_minimal_user_content()),
        ]
        try:
            implementation_plan = await _invoke_and_parse(minimal_messages)
            logger.info("plan_node: Retry with minimal prompt succeeded.")
            retrieval_metadata = {**retrieval_metadata,
                                  "content_filter_retry": True}
        except Exception as retry_err:
            logger.error(f"plan_node: Retry also failed: {retry_err}")
            raise retry_err

    implementation_plan = enrich_plan_with_mcp_metadata(
        implementation_plan,
        mcp_context.get("tools_used", []),
    )

    logger.info(f"plan_node: Generated {len(implementation_plan)} tasks")

    return {
        "implementation_plan": implementation_plan,
        "iteration_count": state.get("iteration_count", 0) + 1,
        "conversation_history": conversation_history,
        "project_context": project_context,
        "retrieval_metadata": retrieval_metadata,
    }


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
    logger.info(
        f"approval_node: Publishing plan with {len(plan)} tasks to channel")

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
        logger.info(
            f"approval_node: Plan EDIT requested with feedback: {feedback[:100]}...")

        # Append feedback to user_prompt for next iteration
        original_prompt = state.get("user_prompt", "")
        updated_prompt = f"{original_prompt}\n\n[REVISION FEEDBACK]: {feedback}"

        return {
            "approved": False,
            "user_prompt": updated_prompt,
        }

    else:
        # Unknown action, treat as rejection
        logger.warning(
            f"approval_node: Unknown action '{action}', treating as rejection")
        return {"approved": False}


# =============================================================================
# Execution Layer Import
# =============================================================================

# Import execution nodes from execution_layer module

# Import template nodes

# Import reflexion nodes from reflexion module


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
        logger.info(
            "check_approval: Not approved -> planner (Antigravity loop)")
        return "planner"


# =============================================================================
# Conditional Edge: Quality Gate (code_review -> persistence or generator)
# =============================================================================

def should_proceed_after_review(state: AgentState) -> Literal["persistence", "generator"]:
    """
    Quality gate: if critical (unfixed) errors remain after code review,
    loop back to generator for one re-generation attempt.
    If iteration_count >= 2, proceed to persistence anyway (avoid infinite loops).
    """
    quality = state.get("quality_summary", {})
    iteration = state.get("iteration_count", 0)

    if quality.get("blocking") and iteration < 2:
        remaining = quality.get("remaining_critical_errors", 0)
        logger.info(
            f"should_proceed_after_review: {remaining} critical error(s) remain, "
            f"iteration={iteration} -> re-generating"
        )
        return "generator"

    return "persistence"


# =============================================================================
# Graph Assembly
# =============================================================================

def create_antigravity_graph(
    checkpointer: Optional[Any] = None,
    enable_reflexion: bool = False,
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
        enable_reflexion: Whether to include the reflexion loop. Defaults to False.
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
    # Phase 5: Quality review
    builder.add_node("code_review", code_review_node)
    # Phase 1: Upload template
    builder.add_node("template_upload", template_upload_node)
    # Phase 2: Upload custom files
    builder.add_node("persistence", persistence_node)

    if skip_approval:
        # Simple flow: template_selection -> planner -> generator (no HITL)
        logger.info("Building graph with skip_approval=True (no HITL)")
        builder.set_entry_point("template_selection")
        builder.add_edge("template_selection", "planner")
        # Upload template before generation
        builder.add_edge("planner", "template_upload")
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

    # Phase 5: generator -> code_review -> quality gate -> persistence (or re-generate)
    builder.add_edge("generator", "code_review")
    builder.add_conditional_edges(
        "code_review",
        should_proceed_after_review,
        {
            "persistence": "persistence",
            "generator": "generator",
        }
    )

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
# Phase 3: Modification Graph (Lightweight flow for targeted changes)
# =============================================================================

def create_modification_graph(
    checkpointer: Optional[Any] = None,
    enable_reflexion: bool = False,
) -> Any:
    """
    Create a lightweight graph for code modifications without regenerating everything.

    Flow:
    1. modification_analysis_node - Analyze which files are affected
    2. modification_planning_node - Create targeted modification plan
    3. generation_node - Generate only modified content (delta mode)
    4. persistence_node - Upload modified files
    5. Conditional: build -> end/reflexion

    Args:
        checkpointer: Optional checkpointer for persistence.
        enable_reflexion: Whether to include error recovery loop. Defaults to False.

    Returns:
        Compiled modification graph.
    """
    # Import here to avoid circular dependency
    from agent.execution_layer import modification_analysis_node, modification_planning_node
    from agent.execution_layer import generation_node, persistence_node, code_review_node
    from agent.reflexion import reflexion_node, escalation_node, should_fix

    builder = StateGraph(AgentState)

    # Add modification-specific nodes
    builder.add_node("analysis", modification_analysis_node)
    builder.add_node("modification_plan", modification_planning_node)
    builder.add_node("generator", generation_node)
    # Phase 5: quality check
    builder.add_node("code_review", code_review_node)
    builder.add_node("persistence", persistence_node)

    # Set entry and build the chain
    builder.set_entry_point("analysis")
    builder.add_edge("analysis", "modification_plan")
    builder.add_edge("modification_plan", "generator")
    # Phase 5: generator -> code_review -> quality gate -> persistence (or re-generate)
    builder.add_edge("generator", "code_review")
    builder.add_conditional_edges(
        "code_review",
        should_proceed_after_review,
        {
            "persistence": "persistence",
            "generator": "generator",
        }
    )

    if enable_reflexion:
        builder.add_node("trigger_build", trigger_build_node)
        builder.add_node("reflexion", reflexion_node)
        builder.add_node("escalation", escalation_node)

        builder.add_edge("persistence", "trigger_build")
        builder.add_conditional_edges(
            "trigger_build",
            should_fix,
            {
                "end": END,
                "reflexion": "reflexion",
                "escalate": "escalation",
            }
        )
        builder.add_edge("reflexion", "generator")
        builder.add_edge("escalation", "analysis")
    else:
        builder.add_edge("persistence", END)

    if checkpointer:
        logger.info("Compiling modification graph with checkpointer")
        return builder.compile(checkpointer=checkpointer)
    else:
        logger.info("Compiling modification graph without checkpointer")
        return builder.compile()


# =============================================================================
# Convenience Functions
# =============================================================================

async def run_antigravity_agent(
    manifest: Union[Dict[str, Any], str],
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
        "files_streamed": 0,
        "conversation_history": [],
        "project_context": {},
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
    logger.info(
        f"Resuming Antigravity agent for thread: {thread_id} with action: {action}")
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
