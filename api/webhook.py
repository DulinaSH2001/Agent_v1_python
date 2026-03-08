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
from contextlib import asynccontextmanager
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

# Shared in-process checkpointer (fallback when Redis is unavailable)
_memory_checkpointer = MemorySaver()

# Compiled graph cache: avoids recompiling the LangGraph graph on every request
_compiled_graphs: Dict[str, Any] = {}

# Redis checkpointer singleton (initialised once in lifespan)
_redis_checkpointer: Optional[Any] = None

# Ably REST singleton
_ably_rest_client: Optional[AblyRest] = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Pre-warm expensive singletons at startup — not per request."""
    global _redis_checkpointer, _ably_rest_client

    # Pre-load template files (blocking I/O done once here, not on first request)
    try:
        from agent.template_manager import get_template_manager
        get_template_manager()
        logger.info("lifespan: templates pre-loaded")
    except Exception as exc:
        logger.warning(f"lifespan: template pre-load skipped — {exc}")

    # Redis checkpointer singleton
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        try:
            from langgraph.checkpoint.redis.aio import AsyncRedisSaver
            _redis_checkpointer = AsyncRedisSaver.from_conn_info(url=redis_url)
            logger.info("lifespan: Redis checkpointer ready")
        except Exception as exc:
            logger.warning(f"lifespan: Redis unavailable — {exc}")

    # Ably REST singleton
    api_key = os.getenv("ABLY_API_KEY")
    if api_key:
        try:
            _ably_rest_client = AblyRest(
                api_key, use_binary_protocol=False, log_level="WARNING"
            )
            logger.info("lifespan: Ably REST client ready")
        except Exception as exc:
            logger.warning(f"lifespan: Ably init skipped — {exc}")

    yield  # server is live
    logger.info("lifespan: shutdown")


# =============================================================================
# FastAPI Application
# =============================================================================

app = FastAPI(
    title="Antigravity Agent Webhook API",
    description="Receive build status callbacks from external containers",
    version="0.4.0",
    lifespan=lifespan,
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
    visual_context: Optional[Dict[str, Any]] = Field(
        default=None, description="Visual editor context: selected element info and style changes")
    data_mode: Optional[str] = Field(
        default="real_api", description="Data mode: 'real_api' or 'sample_data'")
    api_base_url: Optional[str] = Field(
        default=None, description="User-provided backend base URL for real_api mode (e.g. 'http://localhost:8080')")


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
    """Return the Ably REST singleton, initializing on first call."""
    global _ably_rest_client
    if _ably_rest_client is not None:
        return _ably_rest_client

    api_key = os.getenv("ABLY_API_KEY")
    if not api_key:
        logger.warning("ABLY_API_KEY not configured")
        return None

    try:
        _ably_rest_client = AblyRest(
            api_key,
            use_binary_protocol=False,
            log_level="WARNING",
        )
        return _ably_rest_client
    except Exception as e:
        logger.error(f"Failed to initialize Ably client: {e}")
        return None


def get_ably_channel_prefix() -> str:
    """Get Ably channel prefix from environment."""
    return os.getenv("ABLY_CHANNEL_PREFIX", "ai-backend-generation")


async def publish_to_ably(job_id: str, status: str, message: str, **extra_data):
    """Publish status update to Ably channel.

    Handles SSL certificate verification issues and provides proper error handling.
    """
    try:
        ably = get_ably_client()
        if not ably:
            logger.warning(
                f"Ably client not available, skipping publish for job {job_id}")
            return

        channel_name = f"{get_ably_channel_prefix()}:{job_id}"
        channel = ably.channels.get(channel_name)

        data = {
            "status": status,
            "message": message,
            "job_id": job_id,
            **extra_data
        }

        # AblyRest.publish is sync in httpx-based versions, wrapped as async
        try:
            await channel.publish("status", data)
        except TypeError:
            # Fallback for sync version
            channel.publish("status", data)

        logger.info(f"Published to Ably: {channel_name} - {status}")

    except Exception as e:
        logger.warning(
            f"Failed to publish to Ably: {e} (non-critical, continuing)")


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
            requires_shadcn = task.get("requires_shadcn", [])
            if not isinstance(requires_shadcn, list):
                requires_shadcn = []
            mcp_tools_used = task.get("mcp_tools_used", [])
            if not isinstance(mcp_tools_used, list):
                mcp_tools_used = []
            plan_summary.append({
                "index": task.get("index", i),
                "type": task.get("type", "create"),
                "file_path": task.get("file_path", "unknown"),
                "description": task.get("description", "")[:100],
                "requires_shadcn": [str(c) for c in requires_shadcn],
                "mcp_tools_used": [str(t) for t in mcp_tools_used],
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
    Resume a paused agent with build status (legacy interrupt flow).

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

# -------------------------------------------------------------------------
# Forbidden-SDK guardrail — strip payment SDKs and ORM imports from every
# generated file before it reaches the WebContainer / storage.
# -------------------------------------------------------------------------
_FORBIDDEN_SDK_PATTERNS = [
    # Payment SDKs — never allowed in generated code
    "@stripe/stripe-js",
    "@stripe/react-stripe-js",
    "loadStripe",
    "useStripe",
    "useElements",
    "PaymentElement",
    "CardElement",
    "@paypal/react-paypal-js",
    "braintree",
    # ORM / database clients — never allowed in generated code
    "@prisma/client",
    "prisma",
    "drizzle-orm",
    "typeorm",
    "sequelize",
    "mongoose",
]


def _scrub_payment_sdk(file_system: Dict[str, str]) -> Dict[str, str]:
    """Remove forbidden SDK imports and package.json entries from generated files."""
    import re
    import json as _json
    scrubbed: Dict[str, str] = {}
    for path, content in file_system.items():
        if not isinstance(content, str):
            scrubbed[path] = content
            continue
        # --- Clean package.json: remove forbidden entries + fix dev script ---
        if path.endswith("package.json"):
            try:
                pkg = _json.loads(content)
                for section in ("dependencies", "devDependencies"):
                    if section in pkg:
                        pkg[section] = {
                            k: v for k, v in pkg[section].items()
                            if k not in _FORBIDDEN_SDK_PATTERNS
                        }
                # Remove --turbo flag: not supported in all environments
                if "scripts" in pkg and "dev" in pkg["scripts"]:
                    pkg["scripts"]["dev"] = pkg["scripts"]["dev"].replace(" --turbo", "").replace("--turbo ", "").strip()
                scrubbed[path] = _json.dumps(pkg, indent=4)
            except Exception:
                scrubbed[path] = content
            continue
        # --- Clean source files: remove forbidden import lines ---
        cleaned = content
        for pkg in _FORBIDDEN_SDK_PATTERNS:
            cleaned = re.sub(
                rf"^.*import[^;]*['\"]({re.escape(pkg)})['\"][^;]*;?\s*\n?",
                "",
                cleaned,
                flags=re.MULTILINE,
            )
        scrubbed[path] = cleaned
    return scrubbed


async def run_generation_task(
    job_id: str,
    query: str,
    user_id: str,
    manifest: Optional[Dict[str, Any]],
    org_id: Optional[str],
    project_id: Optional[str],
    org_slug: Optional[str],
    project_slug: Optional[str],
    visual_context: Optional[Dict[str, Any]] = None,
    data_mode: Optional[str] = "real_api",
    api_base_url: Optional[str] = None,
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

        # Use the global singletons set during lifespan startup
        checkpointer = _redis_checkpointer if _redis_checkpointer is not None else _memory_checkpointer
        skip_approval = False
        # Default runtime flow uses /build-error side-channel auto-fix, not graph interrupts.
        enable_reflexion = False
        logger.info(f"Using checkpointer: {type(checkpointer).__name__}")

        # Cache compiled graphs — prevents recompiling the LangGraph graph on every request
        graph_key = (
            f"{type(checkpointer).__name__}"
            f"_approval={'on' if not skip_approval else 'off'}"
            f"_reflexion={'on' if enable_reflexion else 'off'}"
        )
        if graph_key not in _compiled_graphs:
            logger.info(f"Compiling LangGraph graph (key={graph_key})...")
            _compiled_graphs[graph_key] = create_antigravity_graph(
                checkpointer=checkpointer,
                enable_reflexion=enable_reflexion,
                skip_approval=skip_approval,
            )
        graph = _compiled_graphs[graph_key]

        # Prepare initial state
        initial_state = get_initial_state(
            manifest=manifest or {},
            user_prompt=query,
            org_slug=org_slug,
            project_slug=project_slug,
        )
        # Attach visual_context so the planning node can use it for targeted edits
        if visual_context:
            initial_state["visual_context"] = visual_context
        # Attach data mode (real_api or sample_data)
        if data_mode:
            initial_state["data_mode"] = data_mode
        # Attach user-provided API base URL (used in real_api mode)
        if api_base_url:
            initial_state["api_base_url"] = api_base_url

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
        file_system = _scrub_payment_sdk(result.get("file_system", {}))

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

                # Publish files_saved status
                await publish_to_ably(
                    job_id,
                    "files_saved",
                    f"Successfully saved {len(files_list)} files to project",
                    progress=100,
                    file_count=len(files_list),
                )
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
        visual_context=request.visual_context,
        data_mode=request.data_mode,
        api_base_url=request.api_base_url,
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
            # Keep runtime consistent with run_generation_task (no interrupt-based build loop).
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
            file_system = _scrub_payment_sdk(result.get("file_system", {}))
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

    except Exception as e:
        logger.error(f"Failed to resume process for {job_id}: {e}")
        await publish_to_ably(job_id, "failed", f"Error resuming generation: {str(e)}")


# =============================================================================
# Legacy Build Status Callback Routes
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
    Receive build status from external container (legacy callback path).

    This supports older interrupt-based reflexion runs where the graph paused
    at trigger_build_node. The default runtime flow now uses
    /api/v1/generate/{job_id}/build-error side-channel auto-fix.

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
    Receive human response for escalated issues (legacy interrupt flow).

    This endpoint is used when an interrupt-based reflexion run escalates.

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
# Build Error Auto-Fix (Reflexion Side-Channel)
# =============================================================================

# Per-job locks to prevent overlapping fix attempts
_build_fix_locks: Dict[str, asyncio.Lock] = {}

# Per-job reflexion iteration counters
_build_fix_iterations: Dict[str, int] = {}

MAX_BUILD_FIX_ITERATIONS = 5


class BuildErrorRequest(BaseModel):
    """Request body for build error auto-fix endpoint."""
    phase: str = Field(
        description="Build phase that failed: preflight, install, or dev")
    errors: List[str] = Field(
        default=[], description="Error messages from WebContainer")
    structured_errors: List[Dict[str, Any]] = Field(
        default=[], description="Structured error objects")
    fullOutput: Optional[str] = Field(
        default=None, description="Full build output log")
    orgSlug: Optional[str] = None
    projectSlug: Optional[str] = None
    retries_used: int = Field(
        default=0, description="Number of retries already attempted by WebContainer")


async def run_build_fix_task(
    job_id: str,
    errors: List[str],
    phase: str,
    full_output: Optional[str],
):
    """Background task: analyze build errors, generate fixes, re-persist."""
    from agent.reflexion import (
        categorize_errors,
        build_categorized_prompt,
        DEBUGGER_PROMPT,
        MAX_REFLEXION_ITERATIONS,
    )

    # Get or create per-job lock
    if job_id not in _build_fix_locks:
        _build_fix_locks[job_id] = asyncio.Lock()

    async with _build_fix_locks[job_id]:
        iteration = _build_fix_iterations.get(job_id, 0)

        if iteration >= MAX_BUILD_FIX_ITERATIONS:
            logger.warning(
                f"build_fix: Max iterations reached for job {job_id}")
            await publish_to_ably(
                job_id, "reflexion_escalate",
                f"Auto-fix reached max attempts ({MAX_BUILD_FIX_ITERATIONS}). Manual intervention needed.",
            )
            return

        job = _active_jobs.get(job_id)
        if not job:
            logger.warning(f"build_fix: Job {job_id} not found in active jobs")
            return

        file_system = job.get("file_system", {})
        if not file_system:
            logger.warning(f"build_fix: No file_system for job {job_id}")
            return

        logger.info(
            f"build_fix: Starting iteration {iteration + 1} for job {job_id} (phase={phase}, errors={len(errors)})")

        # Notify frontend
        await publish_to_ably(
            job_id, "reflexion_progress",
            f"Auto-fixing build errors (attempt {iteration + 1}/{MAX_BUILD_FIX_ITERATIONS})...",
            iteration=iteration + 1,
            max_iterations=MAX_BUILD_FIX_ITERATIONS,
            phase=phase,
        )

        try:
            # Categorize errors
            all_error_lines = list(errors)
            if full_output:
                all_error_lines.extend(full_output.strip().split("\n")[-30:])
            categories = categorize_errors(all_error_lines)

            # Build the error prompt
            error_text = "\n".join(all_error_lines[-20:])
            existing_files_list = "\n".join(
                f"- {fp}" for fp in sorted(file_system.keys()))
            user_prompt = build_categorized_prompt(
                error_text=error_text,
                categories=categories,
                iteration=iteration,
                existing_files=existing_files_list,
            )

            # Call LLM to generate fix plan
            from langchain_openai import ChatOpenAI
            from langchain_core.messages import SystemMessage, HumanMessage
            import json

            llm = ChatOpenAI(
                model=os.getenv("AZURE_OPENAI_DEPLOYMENT", "gpt-4o"),
                api_key=os.getenv("AZURE_OPENAI_API_KEY"),
                azure_endpoint=os.getenv("AZURE_OPENAI_ENDPOINT"),
                api_version=os.getenv(
                    "AZURE_OPENAI_API_VERSION", "2024-12-01-preview"),
                temperature=0.2,
                max_tokens=4096,
            )

            messages = [
                SystemMessage(content=DEBUGGER_PROMPT),
                HumanMessage(content=user_prompt),
            ]

            response = await llm.ainvoke(messages)
            response_text = response.content.strip()

            # Parse fix tasks
            if response_text.startswith("```"):
                response_text = response_text.split("```")[1]
                if response_text.startswith("json"):
                    response_text = response_text[4:]

            fix_tasks = json.loads(response_text)
            if not isinstance(fix_tasks, list):
                fix_tasks = [fix_tasks]

            logger.info(
                f"build_fix: LLM generated {len(fix_tasks)} fix tasks for job {job_id}")

            # Generate fixed code for each task
            fixed_files: Dict[str, str] = {}
            for task in fix_tasks:
                file_path = task.get("file_path", "")
                description = task.get("description", "")
                task_type = task.get("type", "modify")

                if not file_path or not description:
                    continue

                # Build a simple generation prompt
                existing_content = file_system.get(file_path, "")

                fix_prompt = f"""## Fix Task
{description}

## File Path
{file_path}
"""
                if existing_content:
                    fix_prompt += f"""
## Current File Content
```typescript
{existing_content}
```

Return the COMPLETE fixed file. Preserve all working code. Only fix the error described above.
"""
                else:
                    fix_prompt += "\nReturn ONLY the code for this new file. No markdown.\n"

                fix_messages = [
                    SystemMessage(
                        content="Generate Next.js 15 TypeScript code. Return ONLY code, no markdown."),
                    HumanMessage(content=fix_prompt),
                ]

                fix_response = await llm.ainvoke(fix_messages)
                fix_code = fix_response.content.strip()

                # Strip markdown fences
                if fix_code.startswith("```"):
                    lines = fix_code.split("\n")
                    lines = lines[1:]  # remove opening fence
                    if lines and lines[-1].strip() == "```":
                        lines = lines[:-1]
                    fix_code = "\n".join(lines)

                if fix_code:
                    fixed_files[file_path] = fix_code
                    file_system[file_path] = fix_code

            if not fixed_files:
                logger.warning(f"build_fix: No files fixed for job {job_id}")
                await publish_to_ably(
                    job_id, "reflexion_progress",
                    "Auto-fix could not determine fixes. Manual review may be needed.",
                )
                return

            # Update job storage
            _active_jobs[job_id]["file_system"] = file_system

            logger.info(
                f"build_fix: Fixed {len(fixed_files)} files for job {job_id}: {list(fixed_files.keys())}")

            # Publish fixed files as file_generated events (WebContainer will pick them up)
            ably = get_ably_client()
            if ably:
                channel_name = f"{get_ably_channel_prefix()}:{job_id}"
                channel = ably.channels.get(channel_name)
                for fp, content in fixed_files.items():
                    try:
                        file_event = {
                            "status": "file_generated",
                            "file_path": fp,
                            "content": content,
                            "job_id": job_id,
                            "is_fix": True,
                        }
                        try:
                            await channel.publish("file_generated", file_event)
                        except TypeError:
                            channel.publish("file_generated", file_event)
                    except Exception as pub_err:
                        logger.warning(
                            f"build_fix: Failed to publish fix for {fp}: {pub_err}")

            # Re-send all files to backend
            org_id = job.get("org_id")
            project_id = job.get("project_id")
            await send_files_to_backend(
                job_id=job_id,
                files=file_system,
                org_id=org_id,
                project_id=project_id,
            )

            # Notify frontend of fix completion
            await publish_to_ably(
                job_id, "reflexion_progress",
                f"Fixed {len(fixed_files)} file(s): {', '.join(fixed_files.keys())}. Rebuilding...",
                files_fixed=list(fixed_files.keys()),
                iteration=iteration + 1,
            )

            _build_fix_iterations[job_id] = iteration + 1

        except Exception as e:
            logger.error(
                f"build_fix: Error in auto-fix for job {job_id}: {e}", exc_info=True)
            await publish_to_ably(
                job_id, "reflexion_progress",
                f"Auto-fix error: {str(e)[:200]}",
            )


@app.post("/api/v1/generate/{job_id}/build-error")
async def handle_build_error(
    job_id: str,
    request: BuildErrorRequest,
    background_tasks: BackgroundTasks,
):
    """
    Receive WebContainer build errors and trigger auto-fix.

    The backend forwards build errors from the frontend here.
    This starts a background reflexion task that:
    1. Categorizes errors
    2. Generates fix tasks via LLM
    3. Re-persists fixed files
    4. Publishes file_generated events for WebContainer
    """
    job = _active_jobs.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    iteration = _build_fix_iterations.get(job_id, 0)
    if iteration >= MAX_BUILD_FIX_ITERATIONS:
        return {
            "status": "escalated",
            "message": f"Max auto-fix attempts ({MAX_BUILD_FIX_ITERATIONS}) reached",
            "job_id": job_id,
        }

    logger.info(
        f"Received build error for job {job_id}: phase={request.phase}, "
        f"errors={len(request.errors)}, retries_used={request.retries_used}"
    )

    background_tasks.add_task(
        run_build_fix_task,
        job_id=job_id,
        errors=request.errors,
        phase=request.phase,
        full_output=request.fullOutput,
    )

    return {
        "status": "fixing",
        "message": f"Auto-fix started (iteration {iteration + 1})",
        "job_id": job_id,
        "iteration": iteration + 1,
    }


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
