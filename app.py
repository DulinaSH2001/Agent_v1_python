"""
Entry point for the Antigravity Agent API.

Run:
    python app.py
"""

import uvicorn

if __name__ == "__main__":
    uvicorn.run(
        "api.webhook:app",
        host="0.0.0.0",
        port=8001,
        reload=True,
    )
