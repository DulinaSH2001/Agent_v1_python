"""
Antigravity Agent - API Webhook Handler

FastAPI webhook handler for receiving external build status callbacks.
This enables the agent to receive build results from the external
container and resume the paused Reflexion Loop.

Run with: uvicorn api.webhook:app --host 0.0.0.0 --port 8000
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from typing import List, Literal, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Header, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from langgraph.types import Command

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
        version="0.4.0",
    )


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
