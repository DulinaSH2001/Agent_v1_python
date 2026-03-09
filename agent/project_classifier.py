"""
Antigravity Agent - Project Type Classifier

Keyword-based classifier that maps user prompts to project types.
No LLM call required — runs in microseconds, no Azure content filter risk.

Used by:
- graph_logic.py (plan_node) to inject project type context into architect prompt
- plan_validator.py to check required pages
- template_nodes.py to filter template files by relevance
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ─── Project Type Definitions ────────────────────────────────────────────────

PROJECT_TYPES: Dict[str, Dict[str, Any]] = {
    "portfolio": {
        "keywords": [
            "portfolio", "personal site", "personal website",
            "resume", "cv", "showcase", "my work", "freelance",
            "developer site", "designer site", "creative portfolio",
        ],
        "required_pages": [
            "hero/landing", "about", "projects/work", "contact",
        ],
        "layout_style": "minimal",
        "nav_style": "header_only",
        "relevant_components": {
            "sidebar": False,
            "data_table": False,
            "stat_card": False,
        },
    },
    "ecommerce": {
        "keywords": [
            "ecommerce", "e-commerce", "shop", "store", "product",
            "cart", "checkout", "catalog", "marketplace", "retail",
            "order management", "inventory",
        ],
        "required_pages": [
            "storefront", "products", "product detail",
            "cart", "checkout",
        ],
        "layout_style": "header_with_categories",
        "nav_style": "header_with_search",
        "relevant_components": {
            "sidebar": False,
            "data_table": True,
            "stat_card": False,
        },
    },
    "dashboard": {
        "keywords": [
            "dashboard", "admin panel", "admin", "analytics",
            "management", "crm", "cms", "back office",
            "control panel", "monitoring", "reporting",
        ],
        "required_pages": [
            "dashboard overview", "data views", "settings",
        ],
        "layout_style": "sidebar",
        "nav_style": "sidebar_with_header",
        "relevant_components": {
            "sidebar": True,
            "data_table": True,
            "stat_card": True,
        },
    },
    "blog": {
        "keywords": [
            "blog", "article", "post", "writing", "publication",
            "magazine", "journal", "content site", "news",
        ],
        "required_pages": [
            "home/feed", "article page", "about",
        ],
        "layout_style": "content_focused",
        "nav_style": "header_only",
        "relevant_components": {
            "sidebar": False,
            "data_table": False,
            "stat_card": False,
        },
    },
    "landing": {
        "keywords": [
            "landing page", "saas", "startup", "marketing",
            "waitlist", "coming soon", "launch page",
            "product page", "promo", "promotional",
        ],
        "required_pages": [
            "hero", "features", "pricing", "cta/signup",
        ],
        "layout_style": "single_page",
        "nav_style": "sticky_header",
        "relevant_components": {
            "sidebar": False,
            "data_table": False,
            "stat_card": False,
        },
    },
}


def classify_project(user_prompt: str) -> Dict[str, Any]:
    """
    Classify a user prompt into a project type.

    Returns a dict with:
      - type: project type string (e.g. "portfolio") or "general"
      - config: the full config dict for the matched type
      - confidence: float 0.0-1.0

    Examples:
        >>> classify_project("Build me a portfolio website")
        {"type": "portfolio", "config": {...}, "confidence": 0.8}
        >>> classify_project("Build something cool")
        {"type": "general", "config": {...}, "confidence": 0.0}
    """
    if not user_prompt:
        return _general_result()

    prompt_lower = user_prompt.lower()

    best_type = None
    best_score = 0.0
    best_config = None

    for project_type, config in PROJECT_TYPES.items():
        score = _score_keywords(prompt_lower, config["keywords"])
        if score > best_score:
            best_score = score
            best_type = project_type
            best_config = config

    # Require a minimum confidence threshold
    if best_score < 0.3 or best_type is None:
        logger.debug(
            "project_classifier: No confident match (best=%.2f), "
            "returning 'general'", best_score
        )
        return _general_result()

    logger.info(
        "project_classifier: Classified as '%s' (confidence=%.2f)",
        best_type, best_score,
    )
    return {
        "type": best_type,
        "config": best_config,
        "confidence": best_score,
    }


def _score_keywords(prompt_lower: str, keywords: List[str]) -> float:
    """
    Score how well a prompt matches a list of keywords.

    A single strong keyword match should be sufficient (score >= 0.4).
    Multiple matches increase confidence further.
    """
    matched_weight = 0.0

    for keyword in keywords:
        if keyword not in prompt_lower:
            continue

        # Base weight: 1.0 per keyword, multi-word gets bonus
        weight = 1.0 + 0.5 * keyword.count(" ")

        # Bonus for whole-word match (not substring)
        pattern = rf"\b{re.escape(keyword)}\b"
        if re.search(pattern, prompt_lower):
            weight *= 1.5

        matched_weight += weight

    if matched_weight == 0:
        return 0.0

    # Scale: 1 keyword match ~0.4-0.6, 2+ matches ~0.7-1.0
    # Cap at 3.0 matched weight for normalization
    return min(1.0, matched_weight / 3.0)


def _general_result() -> Dict[str, Any]:
    """Return a generic/fallback classification result."""
    return {
        "type": "general",
        "config": {
            "keywords": [],
            "required_pages": [],
            "layout_style": "flexible",
            "nav_style": "header_only",
            "relevant_components": {
                "sidebar": True,
                "data_table": True,
                "stat_card": True,
            },
        },
        "confidence": 0.0,
    }


def get_relevant_components(project_type: str) -> Dict[str, bool]:
    """
    Get which template components are relevant for a project type.

    Returns a dict like {"sidebar": True, "data_table": False, ...}
    Used by template_nodes.py for selective upload.
    """
    if project_type in PROJECT_TYPES:
        return PROJECT_TYPES[project_type].get(
            "relevant_components",
            {"sidebar": True, "data_table": True, "stat_card": True},
        )
    # General/unknown: include everything
    return {"sidebar": True, "data_table": True, "stat_card": True}
