#!/usr/bin/env bash
# Avvio: build del frontend (se serve) e backend su http://localhost:8000
set -e
cd "$(dirname "$0")"
python3 -m pip install -q -r backend/requirements.txt
if [ ! -d frontend/dist ] || [ "$1" == "--build" ]; then
  (cd frontend && npm install && npm run build)
fi
cd backend && exec python3 -m uvicorn app.main:app --host 127.0.0.1 --port 8000
