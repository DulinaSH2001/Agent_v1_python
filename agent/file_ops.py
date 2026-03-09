"""
Antigravity Agent - File Operation Classification & Validation

Provides deterministic file operation handling:
- classify_file_operation: Validates and potentially reclassifies operations
- validate_plan_operations: Validates all operations in a plan

Rules:
- "modify" on non-existent file -> reclassify to "create" with warning
- "create" on existing file -> reclassify to "modify" with warning
- "delete" on non-existent file -> skip with warning
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Tuple

logger = logging.getLogger(__name__)


# No file protection — agent decides what to modify
PROTECTED_FILES = frozenset()


def classify_file_operation(
    task: Dict[str, Any],
    file_system: Dict[str, str],
) -> Tuple[Dict[str, Any], List[str]]:
    """
    Validate and potentially reclassify a file operation.

    Args:
        task: A plan task dict with at least 'type' and 'file_path'.
        file_system: Current virtual filesystem.

    Returns:
        (corrected_task, warnings) — corrected_task may have a different 'type',
        and warnings lists any reclassifications or blocks.
    """
    task = dict(task)  # Don't mutate the original
    warnings: List[str] = []

    file_path = task.get("file_path", "")
    task_type = task.get("type", "create")

    if not file_path:
        return task, warnings

    file_exists = file_path in file_system

    if task_type == "modify" and not file_exists:
        warnings.append(
            f"Reclassified '{file_path}': modify -> create (file does not exist)"
        )
        task["type"] = "create"

    elif task_type == "create" and file_exists:
        warnings.append(
            f"Reclassified '{file_path}': create -> modify (file already exists)"
        )
        task["type"] = "modify"

    elif task_type == "delete" and not file_exists:
        warnings.append(
            f"Skipping delete of '{file_path}': file does not exist"
        )
        task["_skip"] = True

    return task, warnings


def validate_plan_operations(
    plan: List[Dict[str, Any]],
    file_system: Dict[str, str],
) -> Tuple[List[Dict[str, Any]], List[str]]:
    """
    Validate all operations in a plan.

    Returns:
        (corrected_plan, all_warnings) — plan with corrected operations
        and accumulated warning messages.
    """
    corrected_plan: List[Dict[str, Any]] = []
    all_warnings: List[str] = []

    for task in plan:
        corrected_task, warnings = classify_file_operation(task, file_system)
        all_warnings.extend(warnings)

        # Skip blocked or skippable tasks
        if corrected_task.get("_blocked") or corrected_task.get("_skip"):
            continue

        corrected_plan.append(corrected_task)

    if all_warnings:
        logger.info(f"Plan validation: {len(all_warnings)} corrections/warnings")
        for w in all_warnings:
            logger.warning(f"  {w}")

    return corrected_plan, all_warnings
