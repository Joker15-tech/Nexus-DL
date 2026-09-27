#!/bin/bash
set -e

echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║           NEXUSDL WEB - Starting Docker Container               ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
echo "🌐 Frontend:  http://localhost:${PORT}"
echo "🔌 Backend:   http://localhost:${PORT}/api/v1"
echo "📚 Docs:      http://localhost:${PORT}/docs"
echo "💾 Data:      /app/data"
echo ""

# Configurer le frontend
python /app/configure_static.py

# Démarrer le backend FastAPI
echo "🚀 Starting FastAPI backend..."
exec uvicorn nexusdl.interfaces.web.backend.main:app \
    --host ${HOST:-0.0.0.0} \
    --port ${PORT:-8000} \
    --workers 1 \
    --proxy-headers \
    --forwarded-allow-ips '*'
