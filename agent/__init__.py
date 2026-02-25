"""
Antigravity Agent Package

Core infrastructure for an autonomous agent that generates Next.js code
using LangGraph, Ably real-time events, and Redis persistence.

Phase 1: State Engine
- AgentState: Graph state schema
- AblyCallbackHandler: Real-time event streaming
- create_redis_saver: Redis checkpointer factory
- create_graph_builder: StateGraph builder

Phase 2: Graph Logic
- create_antigravity_graph: Compiled graph with HITL
- run_antigravity_agent: Convenience runner
- resume_antigravity_agent: Resume from interrupt
- ARCHITECT_PROMPT: System prompt for planning

Phase 3: Execution Layer
- generation_node: Code generation with MCP tools
- persistence_node: Azure Blob Storage upload
- MCPWrapper: Tool access layer
- BUILDER_PROMPT: System prompt for code generation

Phase 4: Reflexion Loop
- trigger_build_node: External build trigger
- reflexion_node: Error analysis and fix generation
- escalation_node: Human intervention for max retries
- DEBUGGER_PROMPT: System prompt for error analysis
"""

# Phase 1: State Engine
from agent.state_engine import (
    AgentState,
    AblyCallbackHandler,
    create_redis_saver,
    create_graph_builder,
    get_graph_config,
    get_initial_state,
)

# Phase 2: Graph Logic
from agent.graph_logic import (
    ARCHITECT_PROMPT,
    DELTA_PLANNING_INSTRUCTION,
    plan_node,
    approval_node,
    check_approval,
    create_antigravity_graph,
    run_antigravity_agent,
    resume_antigravity_agent,
    get_agent_state,
)

# Phase 3: Execution Layer
from agent.execution_layer import (
    BUILDER_PROMPT,
    DELTA_GENERATION_INSTRUCTION,
    MCPWrapper,
    get_mcp_wrapper,
    generation_node,
    persistence_node,
    code_review_node,
    list_generated_files,
    preview_generation,
    publish_file_generated,
    stream_file_to_backend,
)

# Phase 4: Reflexion Loop
from agent.reflexion import (
    DEBUGGER_PROMPT,
    MAX_REFLEXION_ITERATIONS,
    trigger_build_node,
    reflexion_node,
    escalation_node,
    should_fix,
    publish_to_ably,
    get_debugger_tools,
    format_error_summary,
    categorize_errors,
    get_progressive_strategy,
    build_categorized_prompt,
    ERROR_PATTERNS,
    CATEGORY_PROMPTS,
    PROGRESSIVE_STRATEGIES,
)

# Phase 5: Code Quality
from agent.code_quality import (
    CodeReviewer,
    QualityIssue,
    FileReviewResult,
    ReviewSummary,
)

# Phase 3: Smart Modifications
from agent.codebase_analyzer import (
    CodebaseAnalyzer,
    ComponentInfo,
    ImportGraph,
)

from agent.diff_engine import (
    DiffGenerator,
    TargetedModifier,
)

# Phase 5: Conversation Memory
from agent.memory import (
    ConversationMemory,
    get_memory,
)

# Template Loader
from agent.template_loader import (
    load_template,
    merge_with_template,
    get_template_context_for_planner,
    list_template_files,
)

__all__ = [
    # Phase 1: State Engine
    "AgentState",
    "AblyCallbackHandler",
    "create_redis_saver",
    "create_graph_builder",
    "get_graph_config",
    "get_initial_state",
    # Phase 2: Graph Logic
    "ARCHITECT_PROMPT",
    "DELTA_PLANNING_INSTRUCTION",
    "plan_node",
    "approval_node",
    "check_approval",
    "create_antigravity_graph",
    "run_antigravity_agent",
    "resume_antigravity_agent",
    "get_agent_state",
    # Phase 3: Execution Layer
    "BUILDER_PROMPT",
    "DELTA_GENERATION_INSTRUCTION",
    "MCPWrapper",
    "get_mcp_wrapper",
    "generation_node",
    "persistence_node",
    "code_review_node",
    "list_generated_files",
    "preview_generation",
    "publish_file_generated",
    "stream_file_to_backend",
    # Phase 5: Code Quality
    "CodeReviewer",
    "QualityIssue",
    "FileReviewResult",
    "ReviewSummary",
    # Phase 4: Reflexion Loop
    "DEBUGGER_PROMPT",
    "MAX_REFLEXION_ITERATIONS",
    "trigger_build_node",
    "reflexion_node",
    "escalation_node",
    "should_fix",
    "publish_to_ably",
    "get_debugger_tools",
    "format_error_summary",
    "categorize_errors",
    "get_progressive_strategy",
    "build_categorized_prompt",
    "ERROR_PATTERNS",
    "CATEGORY_PROMPTS",
    "PROGRESSIVE_STRATEGIES",
    # Phase 3: Smart Modifications
    "CodebaseAnalyzer",
    "ComponentInfo",
    "ImportGraph",
    "DiffGenerator",
    "TargetedModifier",
    # Phase 5: Conversation Memory
    "ConversationMemory",
    "get_memory",
]

__version__ = "0.7.0"
