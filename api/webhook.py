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
from ably import AblyRest

# Load environment variables
load_dotenv()

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

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
    max_revisions: int = Field(default=1, description="Maximum revision iterations")
    manifest: Optional[Dict[str, Any]] = Field(default=None, description="Backend API manifest")
    org_id: Optional[str] = Field(default=None, description="Organization ID")
    project_id: Optional[str] = Field(default=None, description="Project ID")


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
    modification_id: Optional[str] = Field(default=None, description="Unique modification ID")


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
        
        await asyncio.to_thread(channel.publish, "status", data)
        logger.info(f"Published to Ably: {channel_name} - {status}")
        
    except Exception as e:
        logger.error(f"Failed to publish to Ably: {e}")


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
        
        # Create checkpointer
        try:
            checkpointer = create_redis_saver()
        except ValueError as e:
            logger.warning(f"Redis not configured, running without persistence: {e}")
            checkpointer = None
        
        # Create graph
        graph = create_antigravity_graph(checkpointer=checkpointer, enable_reflexion=False)
        
        # Prepare initial state
        initial_state = get_initial_state(
            manifest=manifest or {},
            user_prompt=query,
        )
        
        # Get config using job_id as thread_id
        config = get_graph_config(job_id)
        
        # Publish specs_extracted status
        await publish_to_ably(job_id, "specs_extracted", "Requirements analyzed, generating plan", progress=30)
        
        # Run the agent (this will pause at approval_node with HITL)
        # For simplicity, we auto-approve in this flow
        logger.info(f"Starting agent for job {job_id}")
        
        # First run - generates the plan
        result = await graph.ainvoke(initial_state, config=config)
        
        # Check if we have an implementation plan (paused at approval)
        plan = result.get("implementation_plan", [])
        logger.info(f"Generated plan with {len(plan)} tasks for job {job_id}")
        
        # Auto-approve the plan and continue
        if plan and not result.get("approved", False):
            from langgraph.types import Command
            
            await publish_to_ably(
                job_id, 
                "generated", 
                f"Plan generated with {len(plan)} tasks, generating code...", 
                progress=50,
                task_count=len(plan)
            )
            
            # Resume with approval
            result = await graph.ainvoke(
                Command(resume={"action": "APPROVE"}),
                config=config,
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
    webhook_secret = os.getenv("NODE_BACKEND_WEBHOOK_SECRET", "your-webhook-secret-change-this")
    
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
                logger.info(f"Successfully sent {len(files_list)} files to backend for job {job_id}")
                
                # Publish files_saved status
                await publish_to_ably(
                    job_id,
                    "files_saved",
                    f"Successfully saved {len(files_list)} files to project",
                    progress=100,
                    file_count=len(files_list),
                )
            else:
                logger.error(f"Backend webhook returned {response.status_code}: {response.text}")
                
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
    logger.info(f"Received generation request: job_id={request.job_id}, query={request.query[:50]}...")
    
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
    
    logger.info(f"Modification request for job {job_id}: {request.message[:50]}...")
    
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
    logger.info(f"Received build status for thread {thread_id}: {payload.status}")
    
    # Verify signature if secret is configured
    webhook_secret = os.getenv("WEBHOOK_SECRET")
    if webhook_secret:
        body = await request.body()
        if not verify_webhook_signature(body, x_webhook_signature, webhook_secret):
            logger.warning(f"Invalid webhook signature for thread {thread_id}")
            raise HTTPException(status_code=401, detail="Invalid webhook signature")
    
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
    logger.info(f"Webhook secret configured: {bool(os.getenv('WEBHOOK_SECRET'))}")


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
