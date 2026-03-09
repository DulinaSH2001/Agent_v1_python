"""
Antigravity Agent - Plan Pre-Validator

Rule-based validation of the implementation plan BEFORE any LLM generation calls.
Catches common mistakes early: protected file edits, duplicate tasks, tasks that
should use pre-built components instead of creating new ones, wrong path conventions.

Usage:
    from agent.plan_validator import validate_plan
    plan, warnings = validate_plan(plan, file_system, template_files)
"""

from __future__ import annotations

import logging
import re
from typing import Dict, List, Tuple

logger = logging.getLogger(__name__)

# Pre-built components: tasks mentioning these should IMPORT, not create
_PREBUILT_COMPONENT_HINTS: dict[str, str] = {
    "create sidebar": "components/layout/Sidebar.tsx",
    "build sidebar": "components/layout/Sidebar.tsx",
    "create header": "components/layout/Header.tsx",
    "build header": "components/layout/Header.tsx",
    "create datatable": "components/data/DataTable.tsx",
    "create data table": "components/data/DataTable.tsx",
    "build data table": "components/data/DataTable.tsx",
    "create statcard": "components/data/StatCard.tsx",
    "create stat card": "components/data/StatCard.tsx",
    "create emptystate": "components/data/EmptyState.tsx",
    "create empty state": "components/data/EmptyState.tsx",
    "create pagecontainer": "components/layout/PageContainer.tsx",
    "create page container": "components/layout/PageContainer.tsx",
}



def validate_plan(
    plan: List[dict],
    file_system: Dict[str, str],
    template_files: Dict[str, str],
) -> Tuple[List[dict], List[str]]:
    """
    Run rule-based checks on the plan before any LLM generation.

    Rules applied (in order):
    1. Duplicate file guard — merge duplicate file_path tasks into one
    2. Template component suggestion — suggest pre-built components when relevant
    3. Path convention check — warn if page files use wrong flat path
    4. Missing loading.tsx — auto-add loading.tsx for async page tasks without one

    Args:
        plan: List of task dicts from the architect.
        file_system: Current virtual FS (may be empty for first generation).
        template_files: Template file contents keyed by relative path.

    Returns:
        (corrected_plan, warnings) tuple.
    """
    warnings: List[str] = []
    corrected: List[dict] = list(plan)

    # ── Rule 1: Duplicate file guard ──────────────────────────────────────────
    seen: dict[str, int] = {}  # file_path → first occurrence index
    merged_indices: set[int] = set()
    for i, task in enumerate(corrected):
        fp = task.get("file_path", "")
        if not fp:
            continue
        if fp in seen:
            first_idx = seen[fp]
            first_task = corrected[first_idx]
            # Merge description
            first_task["description"] = (
                first_task.get("description", "") + " | " + task.get("description", "")
            )
            merged_indices.add(i)
            warnings.append(
                f"Merged duplicate task for '{fp}' into a single task."
            )
        else:
            seen[fp] = i
    corrected = [t for i, t in enumerate(corrected) if i not in merged_indices]

    # ── Rule 2: Template component suggestion ────────────────────────────────
    for task in corrected:
        desc_lower = task.get("description", "").lower()
        for hint, pre_built_path in _PREBUILT_COMPONENT_HINTS.items():
            if hint in desc_lower and pre_built_path in template_files:
                component_name = pre_built_path.split("/")[-1].replace(".tsx", "")
                import_path = pre_built_path.replace(".tsx", "")
                task["description"] += (
                    f"\nNOTE: A pre-built {component_name} is available at "
                    f"'@/{import_path}'. Consider importing it instead of "
                    f"creating a new one."
                )
                warnings.append(
                    f"Suggested pre-built {component_name} for task "
                    f"'{task.get('file_path', '?')}'."
                )
                break  # Only apply the first matching hint per task

    # ── Rule 2b: Correct component import paths in task descriptions ─────────
    # Prevents the LLM from generating '@/components/Header' when the correct
    # path is '@/components/layout/Header' (template components live in subdirs).
    _FLAT_IMPORT_FIXES = {
        "@/components/Header": "@/components/layout/Header",
        "@/components/Sidebar": "@/components/layout/Sidebar",
        "@/components/PageContainer": "@/components/layout/PageContainer",
        "@/components/DataTable": "@/components/data/DataTable",
        "@/components/StatCard": "@/components/data/StatCard",
        "@/components/EmptyState": "@/components/data/EmptyState",
        # Kebab-case variants
        "@/components/data-table": "@/components/data/DataTable",
        "@/components/stat-card": "@/components/data/StatCard",
        "@/components/empty-state": "@/components/data/EmptyState",
        "@/components/page-container": "@/components/layout/PageContainer",
    }
    for task in corrected:
        desc = task.get("description", "")
        for wrong, correct in _FLAT_IMPORT_FIXES.items():
            if wrong in desc:
                desc = desc.replace(wrong, correct)
                warnings.append(
                    f"Corrected import path in task '{task.get('file_path', '?')}': "
                    f"'{wrong}' → '{correct}'"
                )
        task["description"] = desc

    # ── Rule 3: Path convention check ────────────────────────────────────────
    for task in corrected:
        fp = task.get("file_path", "")
        # page.tsx files must be inside a subdirectory: app/*/page.tsx not app/name.tsx
        if fp.startswith("app/") and fp.endswith(".tsx") and fp.count("/") == 1:
            base = fp[4:].replace(".tsx", "")
            if base not in ("layout", "page", "loading", "error", "not-found", "global-error"):
                suggested = f"app/{base}/page.tsx"
                warnings.append(
                    f"Path convention warning: '{fp}' should be '{suggested}' "
                    f"(page files must be inside route subdirectories)."
                )
                task["file_path"] = suggested

    # ── Rule 4: Missing loading.tsx for async pages ───────────────────────────
    page_paths = {
        t["file_path"]
        for t in corrected
        if t.get("file_path", "").endswith("/page.tsx")
        and ("fetch" in t.get("description", "").lower()
             or "async" in t.get("description", "").lower()
             or "api" in t.get("description", "").lower())
    }
    existing_loading = {
        t["file_path"]
        for t in corrected
        if t.get("file_path", "").endswith("/loading.tsx")
    }
    for page_path in page_paths:
        route_dir = page_path.rsplit("/page.tsx", 1)[0]
        loading_path = f"{route_dir}/loading.tsx"
        if loading_path not in existing_loading and loading_path not in file_system:
            corrected.append({
                "id": f"auto-loading-{route_dir.replace('/', '-')}",
                "type": "create",
                "file_path": loading_path,
                "description": (
                    "Create a loading.tsx skeleton for this route. "
                    "Use Skeleton components from @/components/ui/skeleton. "
                    "Export default function Loading() with appropriate skeleton layout."
                ),
                "requires_shadcn": ["skeleton"],
            })
            warnings.append(
                f"Auto-added loading.tsx task for async route '{route_dir}'."
            )

    # ── Rule 5: Route group path conflict detection ───────────────────────────
    # Next.js strips (group)/ from URLs, so app/(admin)/orders/page.tsx and
    # app/(shop)/orders/page.tsx both resolve to /orders — a fatal build error.
    # Fix: insert the group name as a real path segment for conflicting files.
    resolved_map: dict[str, list[int]] = {}
    for i, task in enumerate(corrected):
        fp = task.get("file_path", "")
        if not fp.startswith("app/"):
            continue
        # Compute URL-resolved path by stripping all (group)/ segments
        resolved = re.sub(r"\([^)]+\)/", "", fp)
        resolved_map.setdefault(resolved, []).append(i)

    for resolved, indices in resolved_map.items():
        if len(indices) < 2:
            continue
        # Keep the first task as-is; rename the rest
        for i in indices[1:]:
            fp = corrected[i]["file_path"]
            grp_match = re.search(r"\(([^)]+)\)", fp)
            grp_name = grp_match.group(1) if grp_match else "section"
            # Insert group name as a real path segment after the first (group)/
            fixed = re.sub(r"(\([^)]+\)/)", rf"\1{grp_name}/", fp, count=1)
            warnings.append(
                f"Route conflict: '{fp}' resolves to same URL as another page. "
                f"Renamed to '{fixed}'."
            )
            corrected[i]["file_path"] = fixed

    # ── Rule 6: Dependency deduplication ────────────────────────────────────
    all_deps: set[str] = set()
    for task in corrected:
        deps = task.get("dependencies", [])
        if isinstance(deps, list):
            unique = [d for d in deps if d not in all_deps]
            dupes = [d for d in deps if d in all_deps]
            if dupes:
                warnings.append(
                    f"Deduplicated dependencies in task '{task.get('id', '?')}': "
                    f"{', '.join(dupes)}"
                )
            all_deps.update(deps)
            task["dependencies"] = unique

    # ── Rule 7: Project type required pages check ──────────────────────────
    # Warn if the plan is missing required pages for the detected project type.
    try:
        from agent.project_classifier import classify_project
        all_descriptions = " ".join(
            t.get("description", "") for t in corrected
        )
        all_file_paths = " ".join(
            t.get("file_path", "") for t in corrected
        )
        combined_text = f"{all_descriptions} {all_file_paths}"
        project_info = classify_project(combined_text)

        if project_info["type"] != "general":
            required = project_info["config"].get("required_pages", [])
            combined_lower = combined_text.lower()
            for page in required:
                # Check each term in "hero/landing" style entries
                page_terms = [t.strip() for t in page.lower().split("/")]
                if not any(term in combined_lower for term in page_terms):
                    warnings.append(
                        f"Project type '{project_info['type']}' typically "
                        f"includes a '{page}' page. Consider adding it."
                    )
    except Exception as e:
        logger.debug("plan_validator: Project type check skipped: %s", e)

    # ── Rule 8: Template file operation correction ───────────────────────
    # If architect says type="create" for a known template file, correct
    # to "modify". Otherwise the generator will SKIP the task entirely
    # (execution_layer.py skips "create" tasks for template files).
    _TEMPLATE_FILES_TO_MODIFY = {
        "app/layout.tsx", "app/page.tsx", "app/loading.tsx", "app/error.tsx",
        "lib/data.ts", "lib/actions.ts", "lib/utils.ts",
        "styles/globals.css", "package.json", "tailwind.config.js",
        "postcss.config.js", "next.config.js", "tsconfig.json",
    }
    for task in corrected:
        fp = task.get("file_path", "")
        if fp in _TEMPLATE_FILES_TO_MODIFY and task.get("type") == "create":
            task["type"] = "modify"
            warnings.append(
                f"Corrected task type for '{fp}': "
                f"'create' -> 'modify' (file exists in template)."
            )

    # ── Rule 9: Auto-add lib/data.ts task if missing ───────────────────
    # Most projects need sample data. Ensure lib/data.ts is modified to
    # include project-specific data constants.
    has_data_task = any(
        t.get("file_path") == "lib/data.ts" for t in corrected
    )
    if not has_data_task and len(corrected) > 1:
        corrected.insert(0, {
            "id": "auto-data-ts",
            "type": "modify",
            "file_path": "lib/data.ts",
            "description": (
                "Add sample data constants for all entities in this project. "
                "Export typed arrays with realistic names, dates, and amounts. "
                "Import types from @/types/index.ts. Keep existing navLinks "
                "and socialLinks exports."
            ),
            "priority": 0,
        })
        warnings.append(
            "Auto-added lib/data.ts task for centralized sample data."
        )

    logger.info(
        "plan_validator: %d correction(s) applied. "
        "Plan: %d -> %d tasks.",
        len(warnings), len(plan), len(corrected),
    )
    return corrected, warnings
