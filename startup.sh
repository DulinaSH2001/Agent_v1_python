#!/bin/bash
# Azure App Service startup script for FastAPI (ASGI) application.
# Set this as the startup command in Azure App Service:
#   /home/site/wwwroot/startup.sh

exec gunicorn \
    -w 4 \
    -k uvicorn.workers.UvicornWorker \
    api.webhook:app \
    --bind 0.0.0.0:8000 \
    --timeout 600 \
    --access-logfile - \
    --error-logfile -
