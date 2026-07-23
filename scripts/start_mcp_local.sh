#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

if [ -f "$REPO_ROOT/.venv/bin/activate" ]; then
    source "$REPO_ROOT/.venv/bin/activate"
fi

PIDS=()

cleanup() {
    echo "Stopping MCP servers..."
    for pid in "${PIDS[@]}"; do
        kill "$pid" 2>/dev/null || true
    done
}
trap cleanup EXIT INT TERM

PORT="${K8S_MCP_PORT:-8080}" python3 "$SCRIPT_DIR/mcp_servers/k8s_mcp_server/server.py" &
PIDS+=("$!")
echo "k8s-mcp-server started (pid $!, port ${K8S_MCP_PORT:-8080})"

PORT="${PROMETHEUS_MCP_PORT:-8081}" python3 "$SCRIPT_DIR/mcp_servers/prometheus_mcp_server/server.py" &
PIDS+=("$!")
echo "prometheus-mcp-server started (pid $!, port ${PROMETHEUS_MCP_PORT:-8081})"

wait
