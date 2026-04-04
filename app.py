"""
Entry point for the Antigravity Agent API.

Run locally:
    python app.py

Production (Azure App Service):
    gunicorn -w 4 -k uvicorn.workers.UvicornWorker app:app --bind 0.0.0.0:8000
"""

# Re-export the FastAPI app so gunicorn can locate it as `app:app`
from api.webhook import app  # noqa: F401

if __name__ == "__main__":
    import uvicorn
    import os

    uvicorn.run(
        "api.webhook:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8001)),
        reload=os.environ.get("RELOAD", "false").lower(
        ) == "true",  # Disable reload by default
    )
