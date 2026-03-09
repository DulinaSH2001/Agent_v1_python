#!/bin/bash
# Azure App Service startup script for FastAPI (ASGI) application.
# Set this as the startup command in Azure App Service:
#   /home/site/wwwroot/startup.sh

exec uvicorn \
    api.webhook:app \
    --host 0.0.0.0 \
    --port 8000 \
    --timeout-keep-alive 1200 \
    --log-level info
