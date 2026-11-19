#!/usr/bin/env bash
# Starts all 3 local services (Toolbox, Web Console, Streamlit) pointing to BigQuery in GCP.
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

mkdir -p .data .bin

# 1. Environment & Variables
if [ -f .env ]; then set -a; . .env; set +a; fi
PROJECT="${GOOGLE_CLOUD_PROJECT:-}"
[ -n "$PROJECT" ] || { echo "ERROR: Set GOOGLE_CLOUD_PROJECT in .env"; exit 1; }
PY="$ROOT/.venv/bin/python"

echo "==> Deploying local services pointing to GCP BigQuery ($PROJECT:$BQ_DATASET)..."

# Kill any existing processes on ports 5000, 8080, 8501
fuser -k 5000/tcp 2>/dev/null || true
fuser -k 8080/tcp 2>/dev/null || true
fuser -k 8501/tcp 2>/dev/null || true
sleep 1

# 2. Render Toolbox configuration
echo "==> Rendering Toolbox YAML..."
"$PY" scripts/render_toolbox.py --out toolbox/tools.rendered.yaml

# 3. Start MCP Toolbox on 127.0.0.1:5000
echo "==> Starting MCP Toolbox on 127.0.0.1:5000..."
VERSION="${TOOLBOX_VERSION:-v1.17.0}"
BIN="$ROOT/.bin/toolbox-$VERSION"
if [ ! -x "$BIN" ]; then
  OS="$(uname -s | tr '[:upper:]' '[:lower:]')"
  ARCH="$(uname -m | sed 's/x86_64/amd64/; s/aarch64/arm64/')"
  curl -fsSL -o "$BIN" "https://storage.googleapis.com/mcp-toolbox-for-databases/$VERSION/$OS/$ARCH/toolbox"
  chmod +x "$BIN"
fi
nohup "$BIN" --config "$ROOT/toolbox/tools.rendered.yaml" --address 127.0.0.1 --port 5000 > .data/toolbox.log 2>&1 &
echo $! > .data/toolbox.pid

# Wait for Toolbox to be ready
echo "    Waiting for Toolbox to be healthy..."
for i in {1..30}; do
  if curl -s http://127.0.0.1:5000/tools >/dev/null 2>&1; then
    echo "    ✓ MCP Toolbox is ready on http://127.0.0.1:5000"
    break
  fi
  sleep 0.5
done

# 4. Start Heat Dispatch Web Console (FastAPI + React) on :8080
echo "==> Starting Heat Dispatch Web Console on http://0.0.0.0:8080..."
nohup "$PY" -m uvicorn heat_console.main:app --app-dir "$ROOT/web/backend" --port 8080 --host 0.0.0.0 > .data/web_console.log 2>&1 &
echo $! > .data/web_console.pid

# Wait for Web Console
echo "    Waiting for Web Console to be healthy..."
for i in {1..30}; do
  if curl -s http://127.0.0.1:8080/api/health >/dev/null 2>&1; then
    echo "    ✓ Web Console is ready on http://localhost:8080"
    break
  fi
  sleep 0.5
done

# 5. Start Streamlit client on :8501
echo "==> Starting Streamlit Dispatch & 311 Desk on http://0.0.0.0:8501..."
nohup "$ROOT/.venv/bin/streamlit" run client/app.py --server.port 8501 --server.headless true > .data/streamlit.log 2>&1 &
echo $! > .data/streamlit.pid

# Wait for Streamlit
echo "    Waiting for Streamlit to be healthy..."
for i in {1..30}; do
  if curl -s http://127.0.0.1:8501 >/dev/null 2>&1; then
    echo "    ✓ Streamlit client is ready on http://localhost:8501"
    break
  fi
  sleep 0.5
done

echo ""
echo "=================================================================="
echo "✓ All services successfully deployed locally against GCP BigQuery!"
echo "  • MCP Toolbox (BigQuery):   http://127.0.0.1:5000"
echo "  • Heat Dispatch Console:    http://localhost:8080"
echo "  • Streamlit Client / 311:   http://localhost:8501"
echo "=================================================================="
