"""
Antigravity Agent Package

Autonomous code generation agent using LangGraph, Ably real-time events,
and Redis persistence. Generates Next.js applications from API manifests.
"""

# Core graph functions (main entry points)
from agent.graph_logic import (
    create_antigravity_graph,
    run_antigravity_agent,
    resume_antigravity_agent,
    get_agent_state,
)

# State management
from agent.state_engine import (
    AgentState,
    create_redis_saver,
    get_graph_config,
    get_initial_state,
)

# Conversation memory
from agent.memory import ConversationMemory, get_memory

__all__ = [
    "create_antigravity_graph",
    "run_antigravity_agent",
    "resume_antigravity_agent",
    "get_agent_state",
    "AgentState",
    "create_redis_saver",
    "get_graph_config",
    "get_initial_state",
    "ConversationMemory",
    "get_memory",
]

__version__ = "0.8.0"
