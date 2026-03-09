"""
Antigravity Agent - Template Nodes

This module provides LangGraph nodes for template management:
- template_selection_node: Analyzes user query to select appropriate template
- template_upload_node: Uploads base template to Azure before custom generation
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict

from langchain_core.runnables import RunnableConfig

from agent.state_engine import AgentState
from agent.template_manager import get_template_manager

logger = logging.getLogger(__name__)


async def template_selection_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    Select an appropriate code generation template based on user query.

    This node analyzes the user's request and selects the best matching
    template (Next.js, Vite, etc.) to initialize the project with.

    Args:
        state: Current agent state with user_query
        config: Runnable configuration

    Returns:
        State update with selected_template and template_files
    """
    logger.info("template_selection_node: Selecting template")

    user_query = state.get("user_prompt", "")
    if not user_query:
        logger.warning("No user query available for template selection")
        return {}

    # Get template manager
    template_manager = get_template_manager()

    # Select template based on query
    template = template_manager.select_template_from_query(user_query)

    if not template:
        logger.error("Failed to select template")
        return {"selected_template": None, "template_files": {}}

    logger.info(
        f"Selected template: {template.name} ({template.framework}, {template.language})")
    logger.info(f"Template contains {len(template.files)} files")

    # Store template info in state
    return {
        "selected_template": template.name,
        "template_files": template.files,
    }


async def template_upload_node(
    state: AgentState,
    config: RunnableConfig,
) -> Dict[str, Any]:
    """
    Upload the selected template files to Azure Blob Storage.

    This is Phase 1 of the two-phase generation workflow. It uploads
    the base template structure before custom file generation begins.

    Args:
        state: Current agent state with template_files
        config: Runnable configuration with thread_id

    Returns:
        State update with build_logs
    """
    logger.info("template_upload_node: Starting template upload (Phase 1)")

    # Get mutable build logs
    build_logs: list[str] = list(state.get("build_logs", []))

    # Get template files
    template_files = dict(state.get("template_files", {}))
    template_name = state.get("selected_template", "unknown")

    if not template_files:
        logger.warning("No template files to upload")
        build_logs.append("Warning: No template selected")
        return {"build_logs": build_logs}

    # ── Filter template files by project type relevance ──────────────
    # Prevents irrelevant components (e.g. DataTable for a portfolio)
    # from reaching the LLM context and biasing generation.
    try:
        from agent.project_classifier import (
            classify_project,
            get_relevant_components,
        )
        user_query = state.get("user_prompt", "")
        project_info = classify_project(user_query)
        project_type = project_info["type"]
        relevant = get_relevant_components(project_type)

        # Always include these paths (config, styles, ui, utils, types)
        _ALWAYS_INCLUDE = (
            "package.json", "tailwind.config.js", "postcss.config.js",
            "tsconfig.json", "next.config.js", "next-env.d.ts",
            "styles/", "app/layout.tsx", "app/loading.tsx", "app/error.tsx",
            "app/page.tsx", "lib/", "components/ui/",
            "components/theme-provider.tsx", "types/",
            "components/layout/Header.tsx",
            "components/layout/Footer.tsx",
            "components/layout/PageContainer.tsx",
            "components/data/EmptyState.tsx",
            "template.json", "README.md",
        )

        # Conditionally included based on project type
        _CONDITIONAL = {
            "components/layout/Sidebar.tsx": relevant.get("sidebar", True),
            "components/data/DataTable.tsx": relevant.get("data_table", True),
            "components/data/StatCard.tsx": relevant.get("stat_card", True),
        }

        original_count = len(template_files)
        filtered = {}
        for path, content in template_files.items():
            # Check always-include prefixes
            if any(
                path == prefix or path.startswith(prefix)
                for prefix in _ALWAYS_INCLUDE
            ):
                filtered[path] = content
            # Check conditional files
            elif path in _CONDITIONAL:
                if _CONDITIONAL[path]:
                    filtered[path] = content
                else:
                    logger.debug(
                        "template_upload_node: Filtered out '%s' "
                        "(not relevant for %s)", path, project_type,
                    )
            else:
                # Include unknown files by default
                filtered[path] = content

        template_files = filtered
        if len(filtered) < original_count:
            removed = original_count - len(filtered)
            logger.info(
                "template_upload_node: Filtered %d irrelevant files "
                "for project type '%s'", removed, project_type,
            )
            build_logs.append(
                f"Filtered {removed} irrelevant template files "
                f"for {project_type} project"
            )
    except Exception as e:
        logger.warning(
            "template_upload_node: Template filtering skipped: %s", e
        )

    logger.info("Uploading %d template files", len(template_files))
    build_logs.append(
        f"Phase 1: Uploading {template_name} template ({len(template_files)} files)")

    # Get org_slug and project_slug from state
    org_slug = state.get("org_slug")
    project_slug = state.get("project_slug")

    if not org_slug or not project_slug:
        logger.error(
            "template_upload_node: Missing org_slug or project_slug in state")
        build_logs.append(
            "Error: Missing organization or project slugs for template upload")
        return {"build_logs": build_logs}

    # Get backend URL
    import os
    backend_url = os.getenv("BACKEND_URL", "http://localhost:8080")
    webhook_secret = os.getenv("FASTAPI_WEBHOOK_SECRET", "")

    # Get thread_id so we can also stream template files to the WebContainer via Ably
    thread_id = config.get("configurable", {}).get("thread_id", "")

    # Prepare files array for backend
    files_payload = [
        {"path": path, "content": content}
        for path, content in template_files.items()
    ]

    logger.info(
        f"template_upload_node: Sending {len(files_payload)} template files to backend for {org_slug}/{project_slug}")
    build_logs.append(
        f"Phase 1: Uploading {len(files_payload)} template files to {org_slug}/{project_slug}")

    try:
        import aiohttp

        BATCH_SIZE = 10  # Files per request — keeps each request well under typical 1 MB limits
        endpoint = f"{backend_url}/api/v1/agents/upload-files"
        headers = {
            "Authorization": f"Bearer {webhook_secret}",
            "Content-Type": "application/json",
        }

        total_files = len(files_payload)
        success_count = 0
        batches = [files_payload[i:i + BATCH_SIZE]
                   for i in range(0, total_files, BATCH_SIZE)]
        logger.info(
            f"template_upload_node: Uploading {total_files} files in "
            f"{len(batches)} batches of up to {BATCH_SIZE}"
        )

        async with aiohttp.ClientSession() as session:
            for batch_idx, batch in enumerate(batches, 1):
                payload = {
                    "orgSlug": org_slug,
                    "projectSlug": project_slug,
                    "files": batch,
                }
                async with session.post(
                    endpoint, json=payload, headers=headers, timeout=120
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        result = data.get("data", {})
                        for file_res in result.get("results", []):
                            if file_res.get("success"):
                                success_count += 1
                            else:
                                error = file_res.get("error", "Unknown error")
                                logger.error(
                                    f"Failed to upload {file_res.get('path')}: {error}"
                                )
                        logger.info(
                            f"template_upload_node: Batch {batch_idx}/{len(batches)} uploaded "
                            f"({len(batch)} files)"
                        )
                    else:
                        error_text = await response.text()
                        logger.error(
                            f"Backend upload failed on batch {batch_idx} "
                            f"(status {response.status}): {error_text}"
                        )
                        build_logs.append(
                            f"Error: Template upload batch {batch_idx} failed "
                            f"(Status {response.status})"
                        )
                        # Continue uploading remaining batches even if one fails

        if success_count == total_files:
            logger.info(
                f"Successfully uploaded all {success_count} template files")
            build_logs.append(
                f"Phase 1 Complete: All {success_count} template files uploaded")
        else:
            logger.warning(
                f"Uploaded {success_count}/{total_files} template files")
            build_logs.append(
                f"Phase 1 Warning: Uploaded {success_count}/{total_files} template files"
            )

        # --- Stream template files directly to WebContainer via Ably ---
        # Generated files are already streamed to the WebContainer via file_generated
        # Ably events, but template files only go to Azure Blob Storage — the WebContainer
        # never sees them. Publish each template file as a file_generated event so that
        # package.json (and the rest of the base template) land in the WebContainer FS
        # before npm install runs.
        if thread_id:
            try:
                from agent.execution_layer import publish_file_generated

                # Prioritise package.json first so WebContainer detects it early
                ordered_files = sorted(
                    files_payload,
                    key=lambda f: (0 if f["path"] == "package.json" else 1),
                )
                total = len(ordered_files)
                logger.info(
                    f"template_upload_node: Streaming {total} template files "
                    "to WebContainer via Ably"
                )
                for idx, file_entry in enumerate(ordered_files):
                    try:
                        await publish_file_generated(
                            thread_id=thread_id,
                            file_path=file_entry["path"],
                            content=file_entry["content"],
                            task_index=idx,
                            total_tasks=total,
                        )
                    except Exception as pub_err:
                        logger.debug(
                            f"template_upload_node: Failed to stream "
                            f"{file_entry['path']} to Ably: {pub_err}"
                        )
                logger.info(
                    "template_upload_node: Template Ably streaming complete")
            except Exception as stream_err:
                logger.warning(
                    "template_upload_node: Ably streaming of template files "
                    f"failed (non-fatal): {stream_err}"
                )

        return {
            "build_logs": build_logs,
            "template_files": template_files,
        }

    except Exception as e:
        error_msg = f"Template upload failed: {e}"
        logger.error(error_msg)
        build_logs.append(f"Error: {error_msg}")
        return {
            "build_logs": build_logs,
            "template_files": template_files,
        }
