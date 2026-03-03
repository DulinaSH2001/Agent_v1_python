#!/usr/bin/env python3
"""
Pre-compute template RAG index for faster startup.

Usage:
    python scripts/build_rag_index.py [--template nextjs-app] [--force]
"""

import argparse
import logging
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from agent.template_rag import get_template_rag


def main():
    parser = argparse.ArgumentParser(description="Build RAG index for template files")
    parser.add_argument("--template", default="nextjs-app", help="Template name to index")
    parser.add_argument("--force", action="store_true", help="Force rebuild even if cached")
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )

    rag = get_template_rag()
    rag.ensure_index(args.template, force_rebuild=args.force)

    # Print summary
    print(f"\nRAG index ready for template '{args.template}':")
    print(f"  Chunks indexed: {len(rag._chunks)}")
    print(f"  Index location: {rag._get_index_dir(args.template)}")

    # Quick test retrieval
    results = rag.retrieve("dashboard with cards and navigation", top_k=3, template_name=args.template)
    print(f"\nTest retrieval ('dashboard with cards and navigation'):")
    for chunk, score in results:
        print(f"  [{score:.3f}] {chunk.file_path} ({chunk.chunk_type})")


if __name__ == "__main__":
    main()
