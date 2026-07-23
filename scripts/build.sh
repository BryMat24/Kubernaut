#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

DOCKER_USER="brymat24"
TAG="${IMAGE_TAG:-latest}"

cd "$REPO_ROOT"

# name:dockerfile:context -- matches the repository names in helm/values.yaml exactly
IMAGES=(
    "api:api/Dockerfile:."
    "ui:ui/Dockerfile:ui"
    "k8s_mcp:mcp_servers/k8s_mcp_server/Dockerfile:mcp_servers/k8s_mcp_server"
    "prometheus_mcp:mcp_servers/prometheus_mcp_server/Dockerfile:mcp_servers/prometheus_mcp_server"
    "loki_mcp:mcp_servers/loki_mcp_server/Dockerfile:mcp_servers/loki_mcp_server"
)

for entry in "${IMAGES[@]}"; do
    IFS=':' read -r name dockerfile context <<< "$entry"
    image="${DOCKER_USER}/${name}:${TAG}"

    echo "======================================"
    echo "Building ${image}"
    echo "  Dockerfile: ${dockerfile}"
    echo "  Context:    ${context}"
    echo "======================================"
    docker build -f "${dockerfile}" -t "${image}" "${context}"

    echo "Pushing ${image}..."
    docker push "${image}"
    echo
done

echo "All images built and pushed:"
for entry in "${IMAGES[@]}"; do
    IFS=':' read -r name _ _ <<< "$entry"
    echo "  ${DOCKER_USER}/${name}:${TAG}"
done
