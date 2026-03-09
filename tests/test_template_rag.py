"""Tests for RAG retrieval module."""

import pytest
from unittest.mock import patch, MagicMock
from agent.template_rag import (
    TemplateRAG,
    get_template_rag,
    get_rag_context_for_prompt,
    retrieve_relevant_chunks,
)


def _can_import(module_name: str) -> bool:
    """Check if a module can be imported."""
    try:
        __import__(module_name)
        return True
    except ImportError:
        return False


class TestTemplateRAG:
    """Test the TemplateRAG class."""

    def test_singleton_pattern(self):
        """get_template_rag should return the same instance."""
        rag1 = get_template_rag()
        rag2 = get_template_rag()
        assert rag1 is rag2

    def test_chunk_to_embedding_text(self):
        """Embedding text should include file path and content."""
        from agent.template_chunker import TemplateChunk
        chunk = TemplateChunk(
            chunk_id="test:file.tsx:Button",
            file_path="components/ui/button.tsx",
            content="export const Button = () => <button>Click</button>",
            chunk_type="component",
            metadata={"declaration": "Button"},
            template_name="nextjs-app",
        )
        text = TemplateRAG._chunk_to_embedding_text(chunk)
        assert "components/ui/button.tsx" in text
        assert "component" in text
        assert "Button" in text
        assert "export const" in text

    @pytest.mark.skipif(
        not _can_import("faiss"),
        reason="faiss-cpu not installed"
    )
    @pytest.mark.skipif(
        not _can_import("sentence_transformers"),
        reason="sentence-transformers not installed"
    )
    def test_build_and_retrieve(self):
        """Build index and retrieve should return results."""
        rag = TemplateRAG()
        rag.build_index("nextjs-app")
        assert rag._initialized
        assert len(rag._chunks) > 0

        results = rag.retrieve("button component with variants", top_k=3)
        assert len(results) > 0
        assert len(results) <= 3

        # Results should be (chunk, score) tuples
        for chunk, score in results:
            assert isinstance(score, float)
            assert chunk.content

    @pytest.mark.skipif(
        not _can_import("faiss"),
        reason="faiss-cpu not installed"
    )
    @pytest.mark.skipif(
        not _can_import("sentence_transformers"),
        reason="sentence-transformers not installed"
    )
    def test_format_for_prompt(self):
        """format_for_prompt should produce readable text."""
        rag = TemplateRAG()
        rag.build_index("nextjs-app")
        results = rag.retrieve("card component", top_k=2)
        formatted = rag.format_for_prompt(results)
        assert "Retrieved Template Context" in formatted
        assert len(formatted) > 100

    def test_format_for_prompt_empty(self):
        """Empty results should return empty string."""
        rag = TemplateRAG()
        assert rag.format_for_prompt([]) == ""


class TestConvenienceFunctions:
    """Test module-level convenience functions."""

    def test_retrieve_relevant_chunks_no_crash(self):
        """retrieve_relevant_chunks should not crash even if deps missing."""
        # This will either work (deps installed) or gracefully return []
        results = retrieve_relevant_chunks("test query")
        assert isinstance(results, list)

    def test_get_rag_context_for_prompt_fallback(self):
        """Should fall back to static TEMPLATE_INFO on failure."""
        with patch("agent.template_rag.get_template_rag", side_effect=RuntimeError("no deps")):
            context, metadata = get_rag_context_for_prompt("test")
            assert metadata["fallback_used"] is True
            assert "Pre-loaded Template Files" in context
