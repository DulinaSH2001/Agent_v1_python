#!/usr/bin/env python3
"""
Build/rebuild the Pinecone RAG index for template files.

Chunks template files, embeds them via Pinecone Inference API
(multilingual-e5-large, free), and upserts into a Pinecone Serverless index.

Usage:
    python scripts/build_rag_index.py                        # build nextjs-app
    python scripts/build_rag_index.py --template nextjs-app  # same, explicit
    python scripts/build_rag_index.py --force                # delete + rebuild
    python scripts/build_rag_index.py --all                  # all templates
    python scripts/build_rag_index.py --verbose              # debug logging
"""

from agent.template_manager import get_template_manager
from agent.template_rag import get_template_rag, _get_index_name
from dotenv import load_dotenv
import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

load_dotenv()


def main():
    parser = argparse.ArgumentParser(
        description="Build Pinecone RAG index for template files"
    )
    parser.add_argument(
        "--template", default="nextjs-app",
        help="Template name to index (default: nextjs-app)"
    )
    parser.add_argument(
        "--all", action="store_true",
        help="Index all available templates"
    )
    parser.add_argument(
        "--force", action="store_true",
        help="Force rebuild: delete existing namespace and re-upsert"
    )
    parser.add_argument(
        "--verbose", action="store_true",
        help="Enable debug logging"
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)

    # Determine which templates to index
    if args.all:
        manager = get_template_manager()
        templates = [t["name"] for t in manager.list_templates()]
        if not templates:
            logger.error("No templates found in templates/ directory.")
            sys.exit(1)
        logger.info(f"Indexing {len(templates)} templates: {templates}")
    else:
        templates = [args.template]

    rag = get_template_rag()

    for template_name in templates:
        print(f"\n{'='*60}")
        print(f"Template: {template_name}")
        print(f"{'='*60}")

        rag.ensure_index(template_name, force_rebuild=args.force)

        # Show index stats
        stats = rag.get_index_stats()
        namespaces = stats.get("namespaces", {})
        ns_info = namespaces.get(template_name, {})
        vector_count = ns_info.get("vector_count", "unknown")

        print(f"\n=== Index Stats ===")
        print(f"  Index name  : {_get_index_name()}")
        print(f"  Namespace   : {template_name}")
        print(f"  Vectors     : {vector_count}")

        # Test retrieval
        print(f"\n=== Test Retrieval ===")
        test_queries = [
            "dashboard with cards and navigation",
            "e-commerce product listing page",
            "user authentication login form",
        ]
        for query in test_queries:
            results = rag.retrieve(query, top_k=3, template_name=template_name)
            print(f"\nQuery: \"{query}\"")
            if results:
                for chunk, score in results:
                    print(
                        f"  [{score:.3f}]  {chunk.file_path}  ({chunk.chunk_type})")
            else:
                print("  No results returned.")

    print(f"\n{'='*60}")
    print("Done. RAG index is ready.")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
