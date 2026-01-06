"""
Antigravity Agent - Ably Utilities

This module centralizes Ably real-time messaging logic to avoid circular dependencies
and ensure consistent messaging across API and execution layers.
"""

from __future__ import annotations

import logging
import os
from typing import Any, Dict, List, Optional

from ably import AblyRest
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

# Configure logging
logger = logging.getLogger(__name__)


def get_ably_client() -> Optional[AblyRest]:
    """Get configured Ably REST client for publishing status updates."""
    api_key = os.getenv("ABLY_API_KEY")
    if not api_key:
        logger.warning("ABLY_API_KEY not configured")
        return None
    return AblyRest(api_key)


def get_ably_channel_prefix() -> str:
    """Get Ably channel prefix from environment."""
    return os.getenv("ABLY_CHANNEL_PREFIX", "ai-backend-generation")


async def publish_to_ably(job_id: str, status: str, message: str, **extra_data):
    """Publish status update to Ably channel."""
    try:
        ably = get_ably_client()
        if not ably:
            return
        
        channel_name = f"{get_ably_channel_prefix()}:{job_id}"
        channel = ably.channels.get(channel_name)
        
        data = {
            "status": status,
            "message": message,
            "job_id": job_id,
            **extra_data
        }
        
        # AblyRest.publish is async in modern versions
        await channel.publish("status", data)
        
    except Exception as e:
        logger.error(f"Failed to publish to Ably: {e}")


async def publish_plan_to_ably(job_id: str, plan: List[Dict[str, Any]], awaiting_approval: bool = True):
    """
    Publish implementation plan to Ably for display in chat.
    
    This sends the plan details so the frontend can show what will be generated
    and optionally display approve/reject buttons.
    """
    try:
        ably = get_ably_client()
        if not ably:
            return
        
        channel_name = f"{get_ably_channel_prefix()}:{job_id}"
        channel = ably.channels.get(channel_name)
        
        # Format plan for display
        plan_summary = []
        for i, task in enumerate(plan, 1):
            plan_summary.append({
                "index": i,
                "type": task.get("type", "create"),
                "file_path": task.get("file_path", "unknown"),
                "description": task.get("description", "")[:100],
            })
        
        data = {
            "status": "plan_ready",
            "message": f"Implementation plan ready with {len(plan)} tasks",
            "job_id": job_id,
            "plan": plan_summary,
            "awaiting_approval": awaiting_approval,
            "task_count": len(plan),
        }
        
        await channel.publish("plan_ready", data)
        # logger.info(f"Published plan to Ably: {channel_name} - {len(plan)} tasks")
        
    except Exception as e:
        logger.error(f"Failed to publish plan to Ably: {e}")


async def publish_task_progress(job_id: str, task_index: int, total_tasks: int, file_path: str, status: str = "generating"):
    """
    Publish per-task progress updates to Ably.
    
    This enables the frontend to show real-time generation progress.
    """
    try:
        ably = get_ably_client()
        if not ably:
            return
        
        channel_name = f"{get_ably_channel_prefix()}:{job_id}"
        channel = ably.channels.get(channel_name)
        
        progress = int((task_index / total_tasks) * 100) if total_tasks > 0 else 0
        
        data = {
            "status": status,
            "message": f"Generating {file_path}... ({task_index}/{total_tasks})",
            "job_id": job_id,
            "task_index": task_index,
            "total_tasks": total_tasks,
            "file_path": file_path,
            "progress": 50 + (progress // 2),  # Scale 50-100
        }
        
        await channel.publish("task_progress", data)
        # logger.info(f"Published task progress: {file_path} ({task_index}/{total_tasks})")
        
    except Exception as e:
        logger.error(f"Failed to publish task progress: {e}")


async def publish_tool_usage(job_id: str, tool_name: str, args: Any):
    """
    Publish tool usage event to Ably.
    
    This allows the chat UI to display reasoning/action steps (e.g., "Scanning folder", "Reading docs").
    """
    try:
        ably = get_ably_client()
        if not ably:
            return

        channel_name = f"{get_ably_channel_prefix()}:{job_id}"
        channel = ably.channels.get(channel_name)

        message = f"Using tool: {tool_name}"
        data = {
            "status": "tool_use",
            "message": message,
            "job_id": job_id,
            "tool": tool_name,
            "args": str(args)
        }

        # Send as a status update so standard UI picks it up
        await channel.publish("status", data)
        # logger.info(f"Published tool usage: {tool_name}")

    except Exception as e:
        logger.error(f"Failed to publish tool usage: {e}")
