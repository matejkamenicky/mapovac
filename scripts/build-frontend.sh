#!/usr/bin/env bash
# Sestaví frontend do frontend/dist, který pak servíruje FastAPI (jeden proces).
# Spusť jednou — a znovu po každé změně frontendu (frontend/src).
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$HERE/frontend"

echo "▶ npm install…"
npm install

echo "▶ vite build → frontend/dist…"
npm run build

echo "✓ Hotovo: $HERE/frontend/dist"
