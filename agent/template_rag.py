"""
Antigravity Agent - Template RAG (Retrieval-Augmented Generation)

Local FAISS-based retrieval using sentence-transformers embeddings.
Indexes template chunks and retrieves the most relevant ones for a given query.

No external API calls — runs entirely locally using all-MiniLM-L6-v2.

Usage:
    rag = get_template_rag()
    chunks = rag.retrieve(query="dashboard with charts", top_k=5)
    prompt_section = rag.format_for_prompt(chunks)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import pickle
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

from agent.template_chunker import TemplateChunk, chunk_template
from agent.template_loader import get_template_context_for_planner, load_template

logger = logging.getLogger(__name__)

# Embedding model config
_DEFAULT_MODEL_NAME = "all-MiniLM-L6-v2"
_EMBEDDING_DIM = 384  # Dimension for all-MiniLM-L6-v2

# Cache directory relative to template root
_INDEX_DIR_NAME = ".rag_index"


class TemplateRAG:
    """
    FAISS-based template retrieval with sentence-transformers embeddings.

    Builds and caches an index of template chunks, then retrieves the most
    relevant ones for a given query using cosine similarity.
    """

    def __init__(self, model_name: str = _DEFAULT_MODEL_NAME):
        self._model_name = model_name
        self._model = None
        self._index = None
        self._chunks: List[TemplateChunk] = []
        self._initialized = False

    def _load_model(self):
        """Lazy-load the sentence-transformers model."""
        if self._model is not None:
            return
        try:
            from sentence_transformers import SentenceTransformer
            self._model = SentenceTransformer(self._model_name)
            logger.info(f"Loaded sentence-transformers model: {self._model_name}")
        except ImportError:
            raise RuntimeError(
                "sentence-transformers is required for RAG. "
                "Install with: pip install sentence-transformers"
            )

    def _get_index_dir(self, template_name: str) -> Path:
        """Get the cache directory for a template's RAG index."""
        template_dir = Path(__file__).parent.parent / "templates" / template_name
        return template_dir / _INDEX_DIR_NAME

    def _compute_template_hash(self, template_name: str) -> str:
        """Compute a hash of all template files for cache invalidation."""
        file_system = load_template(template_name)
        content_hash = hashlib.sha256()
        for path in sorted(file_system.keys()):
            content_hash.update(path.encode())
            content_hash.update(file_system[path].encode())
        return content_hash.hexdigest()[:16]

    def build_index(self, template_name: str = "nextjs-app") -> None:
        """
        Build the FAISS index for a template.

        Chunks the template files, embeds them, and creates a FAISS IndexFlatIP
        for cosine similarity search.
        """
        import faiss

        self._load_model()

        # Chunk the template
        self._chunks = chunk_template(template_name)
        if not self._chunks:
            logger.warning(f"No chunks generated for template '{template_name}'")
            return

        # Generate embeddings
        texts = [self._chunk_to_embedding_text(c) for c in self._chunks]
        logger.info(f"Embedding {len(texts)} chunks for template '{template_name}'...")

        embeddings = self._model.encode(texts, normalize_embeddings=True, show_progress_bar=False)
        embeddings = np.array(embeddings, dtype=np.float32)

        # Build FAISS index (inner product = cosine similarity when normalized)
        self._index = faiss.IndexFlatIP(embeddings.shape[1])
        self._index.add(embeddings)

        self._initialized = True
        logger.info(
            f"Built FAISS index for '{template_name}': "
            f"{len(self._chunks)} chunks, dim={embeddings.shape[1]}"
        )

    def save_index(self, template_name: str = "nextjs-app") -> None:
        """Save the FAISS index and chunk metadata to disk."""
        if not self._initialized:
            logger.warning("Cannot save: index not built yet")
            return

        import faiss

        index_dir = self._get_index_dir(template_name)
        index_dir.mkdir(parents=True, exist_ok=True)

        # Save FAISS index
        faiss.write_index(self._index, str(index_dir / "index.faiss"))

        # Save chunk metadata
        chunk_data = [c.to_dict() for c in self._chunks]
        with open(index_dir / "chunks.json", "w") as f:
            json.dump(chunk_data, f, indent=2)

        # Save template hash for cache invalidation
        template_hash = self._compute_template_hash(template_name)
        with open(index_dir / "hash.txt", "w") as f:
            f.write(template_hash)

        logger.info(f"Saved RAG index to {index_dir}")

    def load_index(self, template_name: str = "nextjs-app") -> bool:
        """
        Load the FAISS index from disk cache.

        Returns:
            True if loaded successfully, False if cache miss or invalid.
        """
        import faiss

        index_dir = self._get_index_dir(template_name)

        required_files = [
            index_dir / "index.faiss",
            index_dir / "chunks.json",
            index_dir / "hash.txt",
        ]
        if not all(f.exists() for f in required_files):
            logger.info(f"No cached index found for '{template_name}'")
            return False

        # Check hash for staleness
        current_hash = self._compute_template_hash(template_name)
        cached_hash = (index_dir / "hash.txt").read_text().strip()
        if current_hash != cached_hash:
            logger.info(f"Template '{template_name}' changed since last index build, rebuilding")
            return False

        # Load FAISS index
        self._index = faiss.read_index(str(index_dir / "index.faiss"))

        # Load chunk metadata
        with open(index_dir / "chunks.json", "r") as f:
            chunk_dicts = json.load(f)
        self._chunks = [
            TemplateChunk(**{k: v for k, v in cd.items()})
            for cd in chunk_dicts
        ]

        self._initialized = True
        logger.info(f"Loaded cached RAG index for '{template_name}': {len(self._chunks)} chunks")
        return True

    def ensure_index(self, template_name: str = "nextjs-app", force_rebuild: bool = False) -> None:
        """
        Ensure the FAISS index is ready — load from cache or build fresh.

        Args:
            template_name: Template to index.
            force_rebuild: If True, always rebuild even if cache exists.
        """
        if self._initialized and not force_rebuild:
            return

        try:
            import faiss  # noqa: F401
        except ImportError:
            raise RuntimeError(
                "faiss-cpu is required for RAG. Install with: pip install faiss-cpu"
            )

        if not force_rebuild and self.load_index(template_name):
            return

        self.build_index(template_name)
        self.save_index(template_name)

    def retrieve(
        self,
        query: str,
        task_description: str = "",
        top_k: int = 5,
        template_name: str = "nextjs-app",
    ) -> List[Tuple[TemplateChunk, float]]:
        """
        Retrieve the most relevant template chunks for a query.

        Args:
            query: The user's prompt or search query.
            task_description: Optional task-specific description for better matching.
            top_k: Number of chunks to return.
            template_name: Template to search.

        Returns:
            List of (chunk, similarity_score) tuples, sorted by relevance.
        """
        self.ensure_index(template_name)

        if not self._initialized or not self._chunks:
            logger.warning("Index not available, returning empty results")
            return []

        self._load_model()

        # Combine query and task description
        search_text = query
        if task_description:
            search_text = f"{query} | {task_description}"

        # Embed the query
        query_embedding = self._model.encode(
            [search_text], normalize_embeddings=True, show_progress_bar=False
        )
        query_embedding = np.array(query_embedding, dtype=np.float32)

        # Search FAISS
        k = min(top_k, len(self._chunks))
        scores, indices = self._index.search(query_embedding, k)

        results = []
        for score, idx in zip(scores[0], indices[0]):
            if idx < 0 or idx >= len(self._chunks):
                continue
            results.append((self._chunks[idx], float(score)))

        return results

    def format_for_prompt(self, results: List[Tuple[TemplateChunk, float]]) -> str:
        """
        Format retrieved chunks into a prompt section.

        Args:
            results: List of (chunk, score) from retrieve().

        Returns:
            Formatted string for injection into planning/generation prompts.
        """
        if not results:
            return ""

        sections = ["## Retrieved Template Context\n"]
        sections.append("The following template snippets are most relevant to your task:\n")

        for chunk, score in results:
            header = f"### {chunk.file_path}"
            if chunk.metadata.get("declaration"):
                header += f" — {chunk.metadata['declaration']}"
            header += f" (relevance: {score:.2f})"
            sections.append(header)
            sections.append(f"Type: {chunk.chunk_type}")
            sections.append(f"```\n{chunk.content}\n```\n")

        return "\n".join(sections)

    @staticmethod
    def _chunk_to_embedding_text(chunk: TemplateChunk) -> str:
        """Convert a chunk into text suitable for embedding."""
        parts = [f"File: {chunk.file_path}", f"Type: {chunk.chunk_type}"]
        if chunk.metadata.get("declaration"):
            parts.append(f"Declaration: {chunk.metadata['declaration']}")
        if chunk.metadata.get("json_key"):
            parts.append(f"JSON key: {chunk.metadata['json_key']}")
        if chunk.metadata.get("section"):
            parts.append(f"CSS section: {chunk.metadata['section']}")
        parts.append(chunk.content)
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Singleton
# ---------------------------------------------------------------------------

_rag_instance: Optional[TemplateRAG] = None


def get_template_rag() -> TemplateRAG:
    """Get or create the singleton TemplateRAG instance."""
    global _rag_instance
    if _rag_instance is None:
        _rag_instance = TemplateRAG()
    return _rag_instance


def retrieve_relevant_chunks(
    query: str,
    task_description: str = "",
    top_k: int = 5,
    template_name: str = "nextjs-app",
) -> List[Tuple[TemplateChunk, float]]:
    """
    Convenience function: retrieve relevant template chunks.

    Falls back to empty list on any error (never crashes the caller).
    """
    try:
        rag = get_template_rag()
        return rag.retrieve(query, task_description, top_k, template_name)
    except Exception as e:
        logger.warning(f"RAG retrieval failed: {e}")
        return []


def get_rag_context_for_prompt(
    query: str,
    task_description: str = "",
    top_k: int = 5,
) -> Tuple[str, Dict[str, Any]]:
    """
    Get RAG context formatted for prompt injection, with metadata.

    Returns:
        (formatted_context, retrieval_metadata) tuple.
        Falls back to static TEMPLATE_INFO on failure.
    """
    try:
        rag = get_template_rag()
        results = rag.retrieve(query, task_description, top_k)

        if not results:
            return get_template_context_for_planner(), {
                "fallback_used": True,
                "reason": "no_results",
            }

        context = rag.format_for_prompt(results)
        metadata = {
            "chunks_retrieved": len(results),
            "file_paths_matched": list({r[0].file_path for r in results}),
            "similarity_scores": [round(r[1], 3) for r in results],
            "fallback_used": False,
        }
        return context, metadata

    except Exception as e:
        logger.warning(f"RAG context generation failed, using static fallback: {e}")
        return get_template_context_for_planner(), {
            "fallback_used": True,
            "error": str(e),
        }
