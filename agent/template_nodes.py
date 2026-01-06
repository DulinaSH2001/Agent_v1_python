"""
Antigravity Agent - Template Nodes

This module provides LangGraph nodes for template management:
- template_selection_node: Analyzes user query to select appropriate template
- template_upload_node: Uploads base template to Azure before custom generation
"""

from __future__ import annotations

import logging
from typing import Any, Dict

from langchain_core.runnables import RunnableConfig

from agent.state_engine import AgentState
from agent.template_manager import get_template_manager
from agent.retry_utils import async_retry, format_error_context

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
    
    logger.info(f"Selected template: {template.name} ({template.framework}, {template.language})")
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
    template_files = state.get("template_files", {})
    template_name = state.get("selected_template", "unknown")
    
    if not template_files:
        logger.warning("No template files to upload")
        build_logs.append("Warning: No template selected")
        return {"build_logs": build_logs}
    
    logger.info(f"Uploading {len(template_files)} template files")
    build_logs.append(f"Phase 1: Uploading {template_name} template ({len(template_files)} files)")
    
    # Get org_slug and project_slug from state
    org_slug = state.get("org_slug")
    project_slug = state.get("project_slug")
    
    if not org_slug or not project_slug:
        logger.error("template_upload_node: Missing org_slug or project_slug in state")
        build_logs.append("Error: Missing organization or project slugs for template upload")
        return {"build_logs": build_logs}
    
    # Get backend URL
    import os
    backend_url = os.getenv("BACKEND_URL", "http://localhost:8080")
    webhook_secret = os.getenv("FASTAPI_WEBHOOK_SECRET", "")
    
    # Prepare files array for backend
    files_payload = [
        {"path": path, "content": content}
        for path, content in template_files.items()
    ]
    
    logger.info(f"template_upload_node: Sending {len(files_payload)} template files to backend for {org_slug}/{project_slug}")
    build_logs.append(f"Phase 1: Uploading {len(files_payload)} template files to {org_slug}/{project_slug}")
    
    try:
        import aiohttp
        
        endpoint = f"{backend_url}/api/v1/agents/upload-files"
        payload = {
            "orgSlug": org_slug,
            "projectSlug": project_slug,
            "files": files_payload
        }
        
        headers = {
            "Authorization": f"Bearer {webhook_secret}",
            "Content-Type": "application/json"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(endpoint, json=payload, headers=headers, timeout=120) as response:
                if response.status == 200:
                    data = await response.json()
                    result = data.get("data", {})
                    success_count = 0
                    
                    # Log individual file results
                    for file_res in result.get("results", []):
                        if file_res.get("success"):
                            success_count += 1
                        else:
                            error = file_res.get("error", "Unknown error")
                            logger.error(f"Failed to upload {file_res.get('path')}: {error}")
                    
                    if success_count == len(files_payload):
                        logger.info(f"Successfully uploaded all {success_count} template files")
                        build_logs.append(f"Phase 1 Complete: All {success_count} template files uploaded")
                    else:
                        logger.warning(f"Uploaded {success_count}/{len(files_payload)} template files")
                        build_logs.append(f"Phase 1 Warning: Uploaded {success_count}/{len(files_payload)} template files")
                
                else:
                    error_text = await response.text()
                    logger.error(f"Backend upload failed with status {response.status}: {error_text}")
                    build_logs.append(f"Error: Backend template upload failed (Status {response.status})")
                    
        return {"build_logs": build_logs}
        
    except Exception as e:
        error_msg = f"Template upload failed: {e}"
        logger.error(error_msg)
        build_logs.append(f"Error: {error_msg}")
        return {"build_logs": build_logs}
