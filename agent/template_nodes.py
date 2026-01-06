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
    
    user_query = state.get("user_query", "")
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
    
    # Import Azure upload logic (reuse from persistence_node)
    try:
        import os
        from azure.storage.blob.aio import BlobServiceClient
        from azure.core.exceptions import ResourceExistsError
        
        # Import SAS token helper
        import sys
        from pathlib import Path
        agent_root = Path(__file__).parent
        sys.path.insert(0, str(agent_root))
        from execution_layer import request_sas_token
        
        # Get thread_id for container naming
        thread_id = config.get("configurable", {}).get("thread_id", "unknown")
        container_name = f"project-{thread_id}"
        container_name = "".join(c if c.isalnum() or c == "-" else "-" for c in container_name.lower())
        
        # Request SAS token
        sas_data = await request_sas_token(container_name)
        
        if not sas_data:
            # Fallback to connection string
            logger.warning("SAS token unavailable for template upload, using connection string")
            connection_string = os.getenv("AZURE_STORAGE_CONNECTION_STRING")
            
            if not connection_string:
                logger.error("No Azure credentials available")
                build_logs.append("Error: Cannot upload template - no Azure credentials")
                return {"build_logs": build_logs}
        
        # Create BlobServiceClient
        if sas_data:
            sas_url = sas_data.get("sasUrl")
            blob_service = BlobServiceClient(account_url=sas_url)
        else:
            blob_service = BlobServiceClient.from_connection_string(connection_string)
        
        container_client = blob_service.get_container_client(container_name)
        
        async with blob_service:
            # Ensure container exists
            try:
                await container_client.create_container()
                logger.info(f"Created container: {container_name}")
            except ResourceExistsError:
                logger.info(f"Container exists: {container_name}")
            except Exception as e:
                logger.info(f"Container check: {e}")
            
            # Upload each template file
            upload_count = 0
            for file_path, content in template_files.items():
                try:
                    blob_client = container_client.get_blob_client(file_path)
                    await blob_client.upload_blob(
                        content.encode("utf-8"),
                        overwrite=True,
                    )
                    upload_count += 1
                    logger.debug(f"Uploaded template file: {file_path}")
                except Exception as e:
                    error_msg = f"Failed to upload {file_path}: {e}"
                    logger.error(error_msg)
                    build_logs.append(f"Error: {error_msg}")
            
            build_logs.append(f"Phase 1 Complete: Uploaded {upload_count}/{len(template_files)} template files")
            logger.info(f"Template upload complete: {upload_count} files")
        
        return {"build_logs": build_logs}
        
    except ImportError as e:
        logger.error(f"Azure SDK not available: {e}")
        build_logs.append("Error: Azure SDK not installed")
        return {"build_logs": build_logs}
    except Exception as e:
        error_msg = f"Template upload failed: {e}"
        logger.error(error_msg)
        build_logs.append(f"Error: {error_msg}")
        return {"build_logs": build_logs}
