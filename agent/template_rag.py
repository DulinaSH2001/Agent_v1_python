"""
Antigravity Agent - Template RAG (Retrieval-Augmented Generation)

Pinecone-based retrieval using Pinecone's free hosted Inference API.
Indexes template chunks in Pinecone Serverless and retrieves the most
relevant ones for a given query.

Embedding model: multilingual-e5-large (free, cloud-hosted, 1024-dim)
No local model downloads — zero cold-start time.

Usage:
    rag = get_template_rag()
    chunks = rag.retrieve(query="dashboard with charts", top_k=5)
    prompt_section = rag.format_for_prompt(chunks)
"""

from __future__ import annotations

import hashlib
import logging
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dotenv import load_dotenv

from agent.template_chunker import TemplateChunk, chunk_template
from agent.template_loader import get_template_context_for_planner

load_dotenv()

logger = logging.getLogger(__name__)

# Pinecone config
_EMBEDDING_MODEL = "multilingual-e5-large"
_EMBEDDING_DIM = 1024
_PINECONE_METRIC = "cosine"
_PINECONE_CLOUD = "aws"
_PINECONE_REGION = "us-east-1"
_UPSERT_BATCH_SIZE = 96   # Pinecone inference batch limit
_QUERY_CACHE_SIZE = 128   # Max cached query results


def _get_pinecone_client():
    """Get a configured Pinecone client."""
    from pinecone import Pinecone
    api_key = os.getenv("PINECONE_API_KEY")
    if not api_key:
        raise RuntimeError(
            "PINECONE_API_KEY is not set. Add it to your .env file.\n"
            "See PINECONE_SETUP.md for instructions."
        )
    return Pinecone(api_key=api_key)


def _get_index_name() -> str:
    return os.getenv("PINECONE_INDEX_NAME", "antigravity-templates")


class TemplateRAG:
    """
    Pinecone-based template retrieval with free hosted inference embeddings.

    Builds and maintains a Pinecone Serverless index of template chunks,
    then retrieves the most relevant ones for a given query using cosine
    similarity. Uses multilingual-e5-large via Pinecone Inference API (free).
    """

    def __init__(self):
        self._pc = None          # Pinecone client (lazy)
        self._index = None       # Pinecone index object (lazy)
        self._initialized = False

    def _ensure_client(self):
        """Lazy-initialize the Pinecone client."""
        if self._pc is None:
            self._pc = _get_pinecone_client()

    def _ensure_index(self):
        """Connect to (or create) the Pinecone index."""
        self._ensure_client()
        if self._index is not None:
            return

        index_name = _get_index_name()
        existing = [i.name for i in self._pc.list_indexes()]

        if index_name not in existing:
            logger.info(
                f"Index '{index_name}' not found. Creating serverless index "
                f"(dim={_EMBEDDING_DIM}, metric={_PINECONE_METRIC}, "
                f"cloud={_PINECONE_CLOUD}, region={_PINECONE_REGION})..."
            )
            from pinecone import ServerlessSpec
            self._pc.create_index(
                name=index_name,
                dimension=_EMBEDDING_DIM,
                metric=_PINECONE_METRIC,
                spec=ServerlessSpec(cloud=_PINECONE_CLOUD,
                                    region=_PINECONE_REGION),
            )
            for _ in range(30):
                status = self._pc.describe_index(index_name).status
                if status.get("ready"):
                    break
                time.sleep(2)
            logger.info(f"Index '{index_name}' is ready.")
        else:
            logger.info(f"Connected to existing index '{index_name}'.")

        self._index = self._pc.Index(index_name)

    def _embed(self, texts: List[str], input_type: str = "passage") -> List[List[float]]:
        """
        Embed texts using Pinecone's free Inference API (multilingual-e5-large).

        Args:
            texts: List of texts to embed.
            input_type: "passage" for documents, "query" for search queries.
        """
        self._ensure_client()
        embeddings = self._pc.inference.embed(
            model=_EMBEDDING_MODEL,
            inputs=texts,
            parameters={"input_type": input_type, "truncate": "END"},
        )
        return [e["values"] for e in embeddings]

    def build_index(self, template_name: str = "nextjs-app") -> None:
        """
        Chunk a template and upsert all vectors to Pinecone.

        Args:
            template_name: Name of the template to index (used as namespace).
        """
        self._ensure_index()

        logger.info(f"Chunking template '{template_name}'...")
        chunks = chunk_template(template_name)
        if not chunks:
            logger.warning(
                f"No chunks generated for template '{template_name}'")
            return

        num_batches = (len(chunks) + _UPSERT_BATCH_SIZE -
                       1) // _UPSERT_BATCH_SIZE
        logger.info(
            f"Generated {len(chunks)} chunks. "
            f"Embedding via Pinecone Inference API ({num_batches} batches)..."
        )

        total_upserted = 0
        for batch_num, i in enumerate(range(0, len(chunks), _UPSERT_BATCH_SIZE), 1):
            batch = chunks[i: i + _UPSERT_BATCH_SIZE]
            texts = [self._chunk_to_embedding_text(c) for c in batch]

            logger.info(
                f"  Batch {batch_num}/{num_batches}: embedding {len(texts)} chunks...")
            vectors_data = self._embed(texts, input_type="passage")

            vectors = []
            for chunk, vector in zip(batch, vectors_data):
                metadata = {
                    "chunk_id": chunk.chunk_id,
                    "file_path": chunk.file_path,
                    "chunk_type": chunk.chunk_type,
                    "template_name": chunk.template_name,
                    "content": chunk.content[:2000],
                    **{k: str(v) for k, v in chunk.metadata.items()},
                }
                vectors.append({
                    "id": chunk.chunk_id,
                    "values": vector,
                    "metadata": metadata,
                })

            self._index.upsert(vectors=vectors, namespace=template_name)
            total_upserted += len(vectors)

        self._initialized = True
        logger.info(
            f"Upserted {total_upserted} vectors to namespace '{template_name}' "
            f"in index '{_get_index_name()}'."
        )

    def delete_namespace(self, template_name: str) -> None:
        """Delete all vectors in a namespace (used by --force rebuild)."""
        self._ensure_index()
        try:
            self._index.delete(delete_all=True, namespace=template_name)
            logger.info(f"Deleted namespace '{template_name}' from index.")
        except Exception as e:
            logger.warning(
                f"Could not delete namespace '{template_name}': {e}")

    def ensure_index(self, template_name: str = "nextjs-app", force_rebuild: bool = False) -> None:
        """
        Ensure the Pinecone index is ready.
        If force_rebuild=True, deletes the namespace and re-upserts everything.
        Otherwise, connects to the existing populated index.
        """
        self._ensure_index()

        if force_rebuild:
            logger.info(
                f"Force rebuild: deleting namespace '{template_name}'...")
            self.delete_namespace(template_name)
            self.build_index(template_name)
            return

        try:
            stats = self._index.describe_index_stats()
            ns_stats = stats.get("namespaces", {})
            vector_count = ns_stats.get(
                template_name, {}).get("vector_count", 0)
            if vector_count > 0:
                logger.info(
                    f"Namespace '{template_name}' already has {vector_count} vectors. "
                    f"Skipping rebuild. Use --force to re-index."
                )
                self._initialized = True
                return
        except Exception as e:
            logger.warning(f"Could not check index stats: {e}")

        logger.info(f"Namespace '{template_name}' is empty. Building index...")
        self.build_index(template_name)

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
            task_description: Optional task description for better matching.
            top_k: Number of chunks to return.
            template_name: Pinecone namespace (template) to search.

        Returns:
            List of (TemplateChunk, similarity_score) tuples, sorted by relevance.
        """
        self._ensure_index()

        cache_key = hashlib.md5(
            f"{query}|{task_description}|{top_k}|{template_name}".encode()
        ).hexdigest()
        cached = _query_cache_get(cache_key)
        if cached is not None:
            return cached

        search_text = f"{query} | {task_description}" if task_description else query
        query_vectors = self._embed([search_text], input_type="query")

        response = self._index.query(
            vector=query_vectors[0],
            top_k=top_k,
            namespace=template_name,
            include_metadata=True,
        )

        MIN_SIMILARITY = 0.60
        results: List[Tuple[TemplateChunk, float]] = []
        for match in response.get("matches", []):
            meta = match.get("metadata", {})
            score = float(match.get("score", 0.0))
            if score < MIN_SIMILARITY:
                continue
            known_keys = {"chunk_id", "file_path",
                          "chunk_type", "template_name", "content"}
            extra_meta = {k: v for k, v in meta.items() if k not in known_keys}
            chunk = TemplateChunk(
                chunk_id=meta.get("chunk_id", match["id"]),
                file_path=meta.get("file_path", "unknown"),
                content=meta.get("content", ""),
                chunk_type=meta.get("chunk_type", "full_file"),
                template_name=meta.get("template_name", template_name),
                metadata=extra_meta,
            )
            # Boost usage_example chunks for better ranking
            boosted = _boosted_score(chunk, score)
            results.append((chunk, boosted))

        results.sort(key=lambda x: x[1], reverse=True)
        _query_cache_set(cache_key, results)
        return results

    def format_for_prompt(self, results: List[Tuple[TemplateChunk, float]]) -> str:
        """Format retrieved chunks into a prompt section with usage examples prioritized."""
        if not results:
            return ""

        sections = ["## Reference Template Code\n"]
        sections.append(
            "The snippets below are existing source code from the project template, "
            "provided as reference material for context.\n"
        )

        for chunk, score in results[:6]:
            if chunk.chunk_type == "usage_example":
                component_name = chunk.metadata.get(
                    "component_name", chunk.file_path)
                header = f"### Reference — {component_name} (relevance={score:.2f})"
                lang = "tsx"
            else:
                header = f"### Reference — {chunk.file_path}"
                if chunk.metadata.get("declaration"):
                    header += f" ({chunk.metadata['declaration']})"
                header += f" [relevance={score:.2f}, type={chunk.chunk_type}]"
                lang = "tsx" if chunk.file_path.endswith(
                    (".tsx", ".ts")) else "text"
            sections.append(header)
            sections.append(f"```{lang}\n{chunk.content}\n```\n")

        return "\n".join(sections)

    def get_index_stats(self) -> Dict[str, Any]:
        """Return current index statistics."""
        self._ensure_index()
        try:
            return dict(self._index.describe_index_stats())
        except Exception as e:
            return {"error": str(e)}

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


def _boosted_score(chunk: TemplateChunk, score: float) -> float:
    """Apply a 1.2x boost to usage_example chunks to surface them above raw code chunks."""
    if chunk.chunk_type == "usage_example" or chunk.metadata.get("is_example"):
        return min(1.0, score * 1.2)
    return score


# ---------------------------------------------------------------------------
# In-process query cache (avoids re-embedding identical queries per session)
# ---------------------------------------------------------------------------

_query_cache: Dict[str, List[Tuple[TemplateChunk, float]]] = {}
_query_cache_keys: List[str] = []


def _query_cache_get(key: str) -> Optional[List[Tuple[TemplateChunk, float]]]:
    return _query_cache.get(key)


def _query_cache_set(key: str, value: List[Tuple[TemplateChunk, float]]) -> None:
    if key not in _query_cache:
        _query_cache_keys.append(key)
        if len(_query_cache_keys) > _QUERY_CACHE_SIZE:
            oldest = _query_cache_keys.pop(0)
            _query_cache.pop(oldest, None)
    _query_cache[key] = value


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
        Falls back to static template info on failure.
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
        logger.warning(
            f"RAG context generation failed, using static fallback: {e}")
        return get_template_context_for_planner(), {
            "fallback_used": True,
            "error": str(e),
        }
