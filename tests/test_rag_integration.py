"""Integration tests for RAG-enhanced generation flow."""

import asyncio
import pytest
from unittest.mock import patch, AsyncMock, MagicMock


def _make_test_state():
    """Create a minimal test state dict."""
    return {
        "manifest": {},
        "user_prompt": "Create a dashboard",
        "file_system": {},
        "implementation_plan": [],
        "build_logs": [],
        "iteration_count": 0,
        "messages": [],
        "approved": False,
        "build_ready": False,
        "build_status": "pending",
        "conversation_history": [],
        "project_context": {},
        "org_slug": "test-org",
        "project_slug": "test-proj",
        "visual_context": None,
        "data_mode": "sample_data",
        "retrieval_metadata": {},
    }


def _mock_deps():
    """Create mock patches for plan_node dependencies."""
    mock_response = MagicMock()
    mock_response.content = (
        '[{"id": "1", "type": "create", "file_path": "app/page.tsx", '
        '"description": "test", "dependencies": [], "priority": 1}]'
    )
    mock_llm = AsyncMock()
    mock_llm.ainvoke = AsyncMock(return_value=mock_response)

    mock_memory = MagicMock()
    mock_memory.load_project_context = AsyncMock(return_value={})
    mock_memory.load_history = AsyncMock(return_value=[])
    mock_memory.build_context_prompt = MagicMock(return_value="")

    return mock_llm, mock_memory


class TestPlanNodeRAGIntegration:
    """Test that plan_node correctly uses RAG context."""

    def test_plan_node_populates_retrieval_metadata(self):
        """plan_node should populate retrieval_metadata when RAG works."""
        mock_rag_context = "## Retrieved Template Context\n### button.tsx\n```\nexport const Button...\n```"
        mock_metadata = {
            "chunks_retrieved": 3,
            "file_paths_matched": ["components/ui/button.tsx"],
            "similarity_scores": [0.85, 0.72, 0.65],
            "fallback_used": False,
        }

        mock_llm, mock_memory = _mock_deps()

        # Patch at the source module (local import in plan_node uses agent.template_rag)
        with patch("agent.template_rag.get_rag_context_for_prompt", return_value=(mock_rag_context, mock_metadata)), \
             patch("agent.graph_logic.get_planning_llm", return_value=mock_llm), \
             patch("agent.graph_logic.get_memory", return_value=mock_memory), \
             patch("agent.execution_layer.gather_mcp_context", new_callable=AsyncMock, return_value={"references": [], "tools_used": [], "warnings": []}):

            from agent.graph_logic import plan_node

            state = _make_test_state()
            config = {"configurable": {"thread_id": "test-123"}}

            result = asyncio.get_event_loop().run_until_complete(plan_node(state, config))

            assert "retrieval_metadata" in result
            assert result["retrieval_metadata"]["chunks_retrieved"] == 3
            assert result["retrieval_metadata"]["fallback_used"] is False

    def test_plan_node_falls_back_without_rag(self):
        """plan_node should use static TEMPLATE_INFO when RAG fails."""
        mock_llm, mock_memory = _mock_deps()

        # Patch at the source module so the local import picks it up
        with patch("agent.template_rag.get_rag_context_for_prompt", side_effect=RuntimeError("FAISS not installed")), \
             patch("agent.graph_logic.get_planning_llm", return_value=mock_llm), \
             patch("agent.graph_logic.get_memory", return_value=mock_memory), \
             patch("agent.execution_layer.gather_mcp_context", new_callable=AsyncMock, return_value={"references": [], "tools_used": [], "warnings": []}):

            from agent.graph_logic import plan_node

            state = _make_test_state()
            config = {"configurable": {"thread_id": "test-456"}}

            result = asyncio.get_event_loop().run_until_complete(plan_node(state, config))

            assert "retrieval_metadata" in result
            assert result["retrieval_metadata"]["fallback_used"] is True
