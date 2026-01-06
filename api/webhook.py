"""
Antigravity Agent - API Webhook Handler

FastAPI webhook handler for receiving external build status callbacks.
This enables the agent to receive build results from the external
container and resume the paused Reflexion Loop.

Run with: uvicorn api.webhook:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import logging
import os
from typing import Any, Dict, List, Literal, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from langgraph.types import Command
from langgraph.checkpoint.memory import MemorySaver
from ably import AblyRest

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global Memory Checkpointer (Fallback)
# Used when Redis is not configured, to enable HITL within the same process
_memory_checkpointer = MemorySaver()

# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="Antigravity Agent Webhook API",
    description="Receive build status callbacks from external containers",
    version="0.4.0",
)

# Add CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Configure appropriately in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# =============================================================================
# Request/Response Models
# =============================================================================

class BuildStatusPayload(BaseModel):
    """Payload for build status webhook."""
    status: Literal["success", "failed"] = Field(
        description="Build result status"
    )
    logs: List[str] = Field(
        default_factory=list,
        description="Build output logs"
    )
    error_message: Optional[str] = Field(
        default=None,
        description="Primary error message if build failed"
    )
    duration_ms: Optional[int] = Field(
        default=None,
        description="Build duration in milliseconds"
    )


class WebhookResponse(BaseModel):
    """Response for webhook endpoints."""
    message: str
    thread_id: str
    status_received: str


class HealthResponse(BaseModel):
    """Response for health check."""
    status: str
    version: str


# =============================================================================
# Generation API Models
# =============================================================================

class GenerateRequest(BaseModel):
    """Request payload for starting code generation."""
    query: str = Field(description="User's prompt for code generation")
    user_id: str = Field(description="User identifier")
    job_id: str = Field(description="Unique job identifier from backend")
    max_revisions: int = Field(
        default=1, description="Maximum revision iterations")
    manifest: Optional[Dict[str, Any]] = Field(
        default=None, description="Backend API manifest")
    org_id: Optional[str] = Field(default=None, description="Organization ID")
    project_id: Optional[str] = Field(default=None, description="Project ID")
    org_slug: Optional[str] = Field(
        default=None, description="Organization slug for file storage")
    project_slug: Optional[str] = Field(
        default=None, description="Project slug for file storage")


class GenerateResponse(BaseModel):
    """Response for generation endpoints."""
    success: bool
    message: str
    job_id: str
    status: Optional[str] = None


class ChatRequest(BaseModel):
    """Request payload for chat messages."""
    message: str = Field(description="Chat message from user")
    user_id: str = Field(description="User identifier")


class ChatModifyRequest(BaseModel):
    """Request payload for modification requests."""
    message: str = Field(description="Modification request message")
    user_id: str = Field(description="User identifier")
    modification_id: Optional[str] = Field(
        default=None, description="Unique modification ID")


class JobStatusResponse(BaseModel):
    """Response for job status queries."""
    success: bool
    job_id: str
    status: str
    progress: Optional[int] = None
    message: Optional[str] = None
    files_generated: Optional[int] = None
    error: Optional[str] = None


class FilesResponse(BaseModel):
    """Response for generated files."""
    success: bool
    job_id: str
    files: List[Dict[str, Any]]
    file_count: int


# =============================================================================
# Ably Client Configuration
# =============================================================================

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
        logger.info(f"Published to Ably: {channel_name} - {status}")

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
        logger.info(
            f"Published plan to Ably: {channel_name} - {len(plan)} tasks")

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

        progress = int((task_index / total_tasks) *
                       100) if total_tasks > 0 else 0

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
        logger.info(
            f"Published task progress: {file_path} ({task_index}/{total_tasks})")

    except Exception as e:
        logger.error(f"Failed to publish task progress: {e}")


# =============================================================================
# Webhook Security
# =============================================================================

def verify_webhook_signature(
    payload: bytes,
    signature: Optional[str],
    secret: str,
) -> bool:
    """
    Verify webhook signature using HMAC-SHA256.

    Args:
        payload: Raw request body bytes.
        signature: Signature from X-Webhook-Signature header.
        secret: Shared webhook secret.

    Returns:
        True if signature is valid.
    """
    if not signature:
        return False

    expected = hmac.new(
        secret.encode(),
        payload,
        hashlib.sha256,
    ).hexdigest()

    return hmac.compare_digest(f"sha256={expected}", signature)


# =============================================================================
# Graph Resume Helper
# =============================================================================

async def resume_agent_with_build_status(
    thread_id: str,
    status: str,
    logs: List[str],
) -> bool:
    """
    Resume a paused agent with build status.

    Args:
        thread_id: The thread ID of the paused agent.
        status: Build status ("success" or "failed").
        logs: Build output logs.

    Returns:
        True if successful.
    """
    try:
        # Import here to avoid circular imports
        from agent.graph_logic import create_antigravity_graph
        from agent.state_engine import create_redis_saver, get_graph_config

        # Create graph with checkpointer
        checkpointer = create_redis_saver()
        graph = create_antigravity_graph(checkpointer=checkpointer)

        # Get config
        config = get_graph_config(thread_id)

        # Resume with build result
        resume_payload = {
            "status": status,
            "logs": logs,
        }

        logger.info(f"Resuming agent {thread_id} with status: {status}")

        await graph.ainvoke(
            Command(resume=resume_payload),
            config=config,
        )

        logger.info(f"Successfully resumed agent {thread_id}")
        return True

    except Exception as e:
        logger.error(f"Failed to resume agent {thread_id}: {e}")
        raise


# =============================================================================
# API Routes
# =============================================================================

@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint."""
    return HealthResponse(
        status="healthy",
        version="0.5.0",
    )


# =============================================================================
# Generation API Routes (Platform Integration)
# =============================================================================

# In-memory job storage (use Redis in production for persistence across restarts)
_active_jobs: Dict[str, Dict[str, Any]] = {}


async def run_generation_task(
    job_id: str,
    query: str,
    user_id: str,
    manifest: Optional[Dict[str, Any]],
    org_id: Optional[str],
    project_id: Optional[str],
    org_slug: Optional[str],
    project_slug: Optional[str],
):
    """Background task to run the agent and publish status updates."""
    try:
        # Import here to avoid circular imports
        from agent.graph_logic import create_antigravity_graph, run_antigravity_agent
        from agent.state_engine import create_redis_saver, get_graph_config, get_initial_state

        # Update job status
        _active_jobs[job_id] = {
            "status": "started",
            "user_id": user_id,
            "query": query,
            "org_id": org_id,
            "project_id": project_id,
            "file_system": {},
        }

        # Publish started status
        await publish_to_ably(job_id, "started", "AI service processing request", progress=10)

        # Create checkpointer (Redis or Global Memory Fallback)
        checkpointer = None
        redis_url = os.getenv("UPSTASH_REDIS_REST_URL")

        try:
            # Check for Redis URL (env var REDIS_URL preferred for standard redis)
            redis_conn_url = os.getenv("REDIS_URL")

            if redis_conn_url:
                from langgraph.checkpoint.redis.aio import AsyncRedisSaver
                checkpointer = AsyncRedisSaver.from_conn_info(
                    url=redis_conn_url)
                logger.info("Using Redis checkpointer for persistence")
            else:
                # Fallback to Memory implementation for session-based HITL
                # This enables approval flow without external Redis
                logger.warning(
                    "No REDIS_URL found. Using In-Memory Checkpointer (session-only persistence).")
                checkpointer = _memory_checkpointer

        except Exception as e:
            logger.warning(
                f"Failed to create Redis saver: {e}. Using MemorySaver.")
            checkpointer = _memory_checkpointer

        # Create graph - Do NOT skip approval since we have a checkpointer (either Redis or Memory)
        # This enables the HITL flow
        skip_approval = checkpointer is None

        # Log mode
        if skip_approval:
            logger.warning(
                "Building graph with skip_approval=True (NO Persistence/HITL)")
        else:
            logger.info(
                f"Building graph with HITL enabled (Checkpointer: {type(checkpointer).__name__})")

        graph = create_antigravity_graph(
            checkpointer=checkpointer,
            enable_reflexion=False,
            skip_approval=skip_approval
        )

        # Prepare initial state
        initial_state = get_initial_state(
            manifest=manifest or {},
            user_prompt=query,
            org_slug=org_slug,
            project_slug=project_slug,
        )

        # Get config using job_id as thread_id
        config = get_graph_config(job_id)

        # Publish specs_extracted status
        await publish_to_ably(job_id, "specs_extracted", "Requirements analyzed, generating plan", progress=30)

        logger.info(f"Starting agent for job {job_id}")

        if checkpointer:
            # Full HITL flow with checkpointing (Redis or Memory)
            # First run - generates the plan and pauses at 'approval'
            result = await graph.ainvoke(initial_state, config=config)

            # Check if we have an implementation plan
            plan = result.get("implementation_plan", [])
            logger.info(
                f"Generated plan with {len(plan)} tasks for job {job_id}")

            # Publish the plan to chat (awaiting_approval=True)
            # The agent is PAUSED at the approval node
            _active_jobs[job_id]["status"] = "awaiting_approval"
            logger.info(f"Job {job_id} paused for approval")

            await publish_plan_to_ably(job_id, plan, awaiting_approval=True)
            return

        else:
            # Fallback (Auto-approve mode) - Only if checkpointer creation failed completely
            initial_state["approved"] = True

            # Run the full graph in one pass
            result = await graph.ainvoke(initial_state, config=config)

            plan = result.get("implementation_plan", [])
            logger.info(
                f"Generated plan with {len(plan)} tasks for job {job_id}")

            # Publish the plan to chat (awaiting_approval=False)
            await publish_plan_to_ably(job_id, plan, awaiting_approval=False)

            await publish_to_ably(
                job_id,
                "generated",
                f"Plan generated with {len(plan)} tasks, generating code...",
                progress=50,
                task_count=len(plan)
            )

        # Get generated files
        file_system = result.get("file_system", {})

        # Update job storage
        _active_jobs[job_id]["file_system"] = file_system
        _active_jobs[job_id]["status"] = "completed"
        _active_jobs[job_id]["files_generated"] = len(file_system)

        logger.info(f"Generated {len(file_system)} files for job {job_id}")

        # Publish completion
        await publish_to_ably(
            job_id,
            "completed",
            f"Code generation completed with {len(file_system)} files",
            progress=90,
            filesGenerated=len(file_system),
        )

        # Send files to backend webhook
        await send_files_to_backend(
            job_id=job_id,
            files=file_system,
            org_id=org_id,
            project_id=project_id,
        )

    except Exception as e:
        logger.error(f"Generation failed for job {job_id}: {e}")
        import traceback
        traceback.print_exc()

        _active_jobs[job_id] = {
            "status": "failed",
            "error": str(e),
        }

        await publish_to_ably(
            job_id,
            "failed",
            f"Generation failed: {str(e)}",
            progress=0,
            error=str(e),
        )


async def send_files_to_backend(
    job_id: str,
    files: Dict[str, str],
    org_id: Optional[str],
    project_id: Optional[str],
):
    """Send generated files to the backend webhook."""
    backend_url = os.getenv("NODE_BACKEND_URL", "http://localhost:8080")
    webhook_secret = os.getenv(
        "NODE_BACKEND_WEBHOOK_SECRET", "your-webhook-secret-change-this")

    # Convert file_system dict to list format expected by backend
    files_list = [
        {"path": path, "content": content, "content_type": "text/plain"}
        for path, content in files.items()
    ]

    payload = {
        "job_id": job_id,
        "files": files_list,
        "project_name": f"generated-{job_id[:8]}",
        "metadata": {
            "org_id": org_id,
            "project_id": project_id,
            "file_count": len(files_list),
        }
    }

    try:
        async with httpx.AsyncClient() as client:
            response = await client.post(
                f"{backend_url}/api/v1/generate/webhook/completion",
                json=payload,
                headers={
                    "Authorization": f"Bearer {webhook_secret}",
                    "Content-Type": "application/json",
                },
                timeout=30.0,
            )

            if response.status_code == 200:
                logger.info(
                    f"Successfully sent {len(files_list)} files to backend for job {job_id}")
                logger.info(
                    f"Backend will publish files_saved and upload_complete events after saving to Azure")
                # NOTE: Don't publish files_saved here - backend will do it after actually saving files to Azure
            else:
                logger.error(
                    f"Backend webhook returned {response.status_code}: {response.text}")

    except Exception as e:
        logger.error(f"Failed to send files to backend: {e}")


@app.post("/api/v1/generate", response_model=GenerateResponse)
async def start_generation(
    request: GenerateRequest,
    background_tasks: BackgroundTasks,
):
    """
    Start code generation.

    This endpoint accepts a generation request from the platform backend,
    starts the agent in a background task, and returns immediately with
    the job ID. Status updates are sent via Ably.
    """
    logger.info(
        f"Received generation request: job_id={request.job_id}, query={request.query[:50]}...")

    # Store initial job state
    _active_jobs[request.job_id] = {
        "status": "queued",
        "user_id": request.user_id,
        "query": request.query,
    }

    # Start background task
    background_tasks.add_task(
        run_generation_task,
        job_id=request.job_id,
        query=request.query,
        user_id=request.user_id,
        manifest=request.manifest,
        org_id=request.org_id,
        project_id=request.project_id,
        org_slug=request.org_slug,
        project_slug=request.project_slug,
    )

    return GenerateResponse(
        success=True,
        message="Generation started",
        job_id=request.job_id,
        status="queued",
    )


@app.get("/api/v1/generate/{job_id}", response_model=JobStatusResponse)
async def get_job_status(job_id: str):
    """Get the current status of a generation job."""
    job = _active_jobs.get(job_id)

    if not job:
        # Try to get from Redis checkpointer
        try:
            from agent.graph_logic import get_agent_state
            state = await get_agent_state(job_id)

            if state:
                return JobStatusResponse(
                    success=True,
                    job_id=job_id,
                    status=state.get("build_status", "unknown"),
                    files_generated=len(state.get("file_system", {})),
                )
        except Exception as e:
            logger.warning(f"Could not get agent state: {e}")

        raise HTTPException(status_code=404, detail="Job not found")

    return JobStatusResponse(
        success=True,
        job_id=job_id,
        status=job.get("status", "unknown"),
        files_generated=job.get("files_generated"),
        error=job.get("error"),
    )


@app.get("/api/v1/generate/{job_id}/files", response_model=FilesResponse)
async def get_generated_files(job_id: str):
    """Get the generated files for a completed job."""
    job = _active_jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("status") != "completed":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not completed. Current status: {job.get('status')}"
        )

    file_system = job.get("file_system", {})
    files_list = [
        {"path": path, "content": content, "size": len(content)}
        for path, content in file_system.items()
    ]

    return FilesResponse(
        success=True,
        job_id=job_id,
        files=files_list,
        file_count=len(files_list),
    )


@app.post("/api/v1/chat/{job_id}")
async def send_chat_message(job_id: str, request: ChatRequest):
    """Send a chat message for follow-up interactions."""
    job = _active_jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    logger.info(f"Chat message for job {job_id}: {request.message[:50]}...")

    # For now, just acknowledge - full implementation would integrate with agent
    return {
        "success": True,
        "job_id": job_id,
        "message": "Message received",
    }


@app.post("/api/v1/chat/{job_id}/modify", response_model=GenerateResponse)
async def request_modification(
    job_id: str,
    request: ChatModifyRequest,
    background_tasks: BackgroundTasks,
):
    """Request modifications to a generated project."""
    job = _active_jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    logger.info(
        f"Modification request for job {job_id}: {request.message[:50]}...")

    # Start a new generation with the modification as the query
    # and the existing file_system for delta mode
    modification_job_id = request.modification_id or f"{job_id}-mod"

    _active_jobs[modification_job_id] = {
        "status": "queued",
        "user_id": request.user_id,
        "query": request.message,
        "parent_job_id": job_id,
        "file_system": job.get("file_system", {}),
    }

    background_tasks.add_task(
        run_generation_task,
        job_id=modification_job_id,
        query=request.message,
        user_id=request.user_id,
        manifest=None,  # Inherit from parent
        org_id=job.get("org_id"),
        project_id=job.get("project_id"),
    )

    return GenerateResponse(
        success=True,
        message="Modification started",
        job_id=modification_job_id,
        status="queued",
    )


# =============================================================================
# Approval Endpoints (HITL Flow)
# =============================================================================

class ApprovalRequest(BaseModel):
    """Request payload for plan approval."""
    user_id: str = Field(description="User identifier")
    feedback: Optional[str] = Field(
        default=None, description="Optional feedback message")


@app.post("/api/v1/generate/{job_id}/approve", response_model=GenerateResponse)
async def approve_plan(
    job_id: str,
    request: ApprovalRequest,
    background_tasks: BackgroundTasks,
):
    """
    Approve a pending implementation plan.

    This endpoint is called by the frontend when the user approves the plan
    shown in the chat. It resumes the agent to continue with code generation.
    """
    job = _active_jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("status") != "awaiting_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not awaiting approval. Current status: {job.get('status')}"
        )

    logger.info(f"Plan approved for job {job_id} by user {request.user_id}")

    # Update status
    _active_jobs[job_id]["status"] = "approved"

    # Publish approval status
    await publish_to_ably(
        job_id,
        "approved",
        "Plan approved! Starting code generation...",
        progress=50,
    )

    # Resume generation
    background_tasks.add_task(
        _resume_approval_process,
        job_id=job_id,
        action="APPROVE"
    )

    return GenerateResponse(
        success=True,
        message="Plan approved, generation continuing",
        job_id=job_id,
        status="approved",
    )


@app.post("/api/v1/generate/{job_id}/reject", response_model=GenerateResponse)
async def reject_plan(
    job_id: str,
    request: ApprovalRequest,
    background_tasks: BackgroundTasks,
):
    """
    Reject a pending implementation plan.

    This endpoint is called when the user rejects the plan. 
    The agent will be notified to regenerate with the feedback.
    """
    job = _active_jobs.get(job_id)

    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    if job.get("status") != "awaiting_approval":
        raise HTTPException(
            status_code=400,
            detail=f"Job is not awaiting approval. Current status: {job.get('status')}"
        )

    logger.info(
        f"Plan rejected for job {job_id} by user {request.user_id}. Feedback: {request.feedback}")

    # Update status
    _active_jobs[job_id]["status"] = "rejected"
    _active_jobs[job_id]["rejection_feedback"] = request.feedback

    # Publish rejection status
    await publish_to_ably(
        job_id,
        "rejected",
        f"Plan rejected. {request.feedback or 'Please provide new requirements.'}",
        progress=30,
        feedback=request.feedback,
    )

    # Resume generation with rejection
    background_tasks.add_task(
        _resume_approval_process,
        job_id=job_id,
        action="REJECT",
        feedback=request.feedback
    )

    return GenerateResponse(
        success=True,
        message="Plan rejected",
        job_id=job_id,
        status="rejected",
    )


async def _resume_approval_process(job_id: str, action: str, feedback: Optional[str] = None):
    """Internal helper to resume graph from approval state."""
    try:
        # Import here to avoid circular imports
        from agent.graph_logic import create_antigravity_graph
        from agent.state_engine import get_graph_config

        # 1. Get checkpointer (Redis or Memory)
        checkpointer = None
        try:
            redis_conn_url = os.getenv("REDIS_URL")
            if redis_conn_url:
                from langgraph.checkpoint.redis.aio import AsyncRedisSaver
                checkpointer = AsyncRedisSaver.from_conn_info(
                    url=redis_conn_url)
            else:
                checkpointer = _memory_checkpointer
        except:
            checkpointer = _memory_checkpointer

        # 2. Rebuild graph
        graph = create_antigravity_graph(
            checkpointer=checkpointer,
            enable_reflexion=False,
            skip_approval=False  # Must be False for HITL
        )

        # 3. Resume
        config = get_graph_config(job_id)

        resume_data = {"action": action}
        if feedback:
            resume_data["feedback"] = feedback

        logger.info(f"Resuming job {job_id} with action {action}")

        result = await graph.ainvoke(
            Command(resume=resume_data),
            config=config,
        )

        # 4. Handle result (Publish completion/updates)
        # Check if we have a new plan (retry case)
        plan = result.get("implementation_plan", [])

        if action == "REJECT":
            # If rejected, we expect a NEW plan
            logger.info(
                f"Regenerated plan with {len(plan)} tasks for job {job_id}")
            _active_jobs[job_id]["status"] = "awaiting_approval"
            await publish_plan_to_ably(job_id, plan, awaiting_approval=True)

        elif action == "APPROVE":
            # If approved, files generated
            file_system = result.get("file_system", {})
            _active_jobs[job_id]["file_system"] = file_system
            _active_jobs[job_id]["status"] = "completed"
            _active_jobs[job_id]["files_generated"] = len(file_system)

            logger.info(f"Generated {len(file_system)} files for job {job_id}")

            await publish_to_ably(
                job_id,
                "completed",
                f"Code generation completed with {len(file_system)} files",
                progress=90,
                filesGenerated=len(file_system),
            )

            # Send files to backend webhook - this will publish files_saved status
            job = _active_jobs.get(job_id, {})
            await send_files_to_backend(
                job_id=job_id,
                files=file_system,
                org_id=job.get("org_id"),
                project_id=job.get("project_id"),
            )

    except Exception as e:
        logger.error(f"Failed to resume process for {job_id}: {e}")
        await publish_to_ably(job_id, "failed", f"Error resuming generation: {str(e)}")


# =============================================================================
# Build Status Callback Routes
# =============================================================================


@app.post(
    "/callbacks/build-status/{thread_id}",
    response_model=WebhookResponse,
)
async def receive_build_status(
    thread_id: str,
    payload: BuildStatusPayload,
    request: Request,
    x_webhook_signature: Optional[str] = Header(default=None),
):
    """
    Receive build status from external container.

    This endpoint is called by the build container after completing
    a build. It resumes the paused agent with the build result.

    Args:
        thread_id: The agent thread ID.
        payload: Build status payload.
        x_webhook_signature: Optional HMAC signature for verification.

    Returns:
        Webhook response confirming receipt.
    """
    logger.info(
        f"Received build status for thread {thread_id}: {payload.status}")

    # Verify signature if secret is configured
    webhook_secret = os.getenv("WEBHOOK_SECRET")
    if webhook_secret:
        body = await request.body()
        if not verify_webhook_signature(body, x_webhook_signature, webhook_secret):
            logger.warning(f"Invalid webhook signature for thread {thread_id}")
            raise HTTPException(
                status_code=401, detail="Invalid webhook signature")

    # Prepare logs
    logs = list(payload.logs)
    if payload.error_message:
        logs.insert(0, f"ERROR: {payload.error_message}")
    if payload.duration_ms:
        logs.append(f"Build duration: {payload.duration_ms}ms")

    # Resume the agent
    try:
        await resume_agent_with_build_status(
            thread_id=thread_id,
            status=payload.status,
            logs=logs,
        )
    except Exception as e:
        logger.error(f"Failed to resume agent: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to resume agent: {str(e)}",
        )

    return WebhookResponse(
        message="Build status received and agent resumed",
        thread_id=thread_id,
        status_received=payload.status,
    )


@app.post("/callbacks/human-response/{thread_id}")
async def receive_human_response(
    thread_id: str,
    request: Request,
):
    """
    Receive human response for escalated issues.

    This endpoint is called when a human provides guidance for
    an escalated build issue.

    Args:
        thread_id: The agent thread ID.
        request: Raw request with human guidance.

    Returns:
        Confirmation of receipt.
    """
    body = await request.json()

    action = body.get("action", "ACCEPT")
    feedback = body.get("feedback", "")

    logger.info(f"Received human response for thread {thread_id}: {action}")

    try:
        from agent.graph_logic import create_antigravity_graph
        from agent.state_engine import create_redis_saver, get_graph_config

        checkpointer = create_redis_saver()
        graph = create_antigravity_graph(checkpointer=checkpointer)
        config = get_graph_config(thread_id)

        resume_payload = {
            "action": action,
            "feedback": feedback,
        }

        await graph.ainvoke(
            Command(resume=resume_payload),
            config=config,
        )

        return {
            "message": "Human response received",
            "thread_id": thread_id,
            "action": action,
        }

    except Exception as e:
        logger.error(f"Failed to process human response: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Failed to process human response: {str(e)}",
        )


# =============================================================================
# Startup/Shutdown Events
# =============================================================================

@app.on_event("startup")
async def startup_event():
    """Run on application startup."""
    logger.info("Antigravity Webhook API starting...")
    logger.info(
        f"Webhook secret configured: {bool(os.getenv('WEBHOOK_SECRET'))}")


@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown."""
    logger.info("Antigravity Webhook API shutting down...")


# =============================================================================
# Main Entry Point
# =============================================================================

if __name__ == "__main__":
    import uvicorn

    host = os.getenv("API_HOST", "0.0.0.0")
    port = int(os.getenv("API_PORT", "8000"))

    uvicorn.run(
        "api.webhook:app",
        host=host,
        port=port,
        reload=True,
    )
