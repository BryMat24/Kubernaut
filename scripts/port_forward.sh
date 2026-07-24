#!/usr/bin/env bash

set -euo pipefail

KUBERNAUT_NS="kubernaut"
MONITORING_NS="dev-monitoring"
APP_NS="dev"

GRAFANA_SERVICE="prometheus-grafana"
FRONTEND_SERVICE="frontend-service"
KUBERNAUT_UI="kubernaut-ui"
LOKI_SERVICE="loki"

echo "=== Grafana Credentials ==="
echo "Username: admin"

PASSWORD=$(kubectl get secret -n "${MONITORING_NS}" "${GRAFANA_SERVICE}" \
    -o jsonpath="{.data.admin-password}" | base64 --decode)

echo "Password: ${PASSWORD}"
echo

cleanup() {
    echo
    echo "Stopping port-forwards..."
    kill ${GRAFANA_PID:-} ${FRONTEND_PID:-} ${LOKI_PID:-} 2>/dev/null || true
}

trap cleanup EXIT INT TERM

echo "Starting Grafana port-forward..."
kubectl port-forward -n "${MONITORING_NS}" svc/"${GRAFANA_SERVICE}" 3000:80 \
    >/tmp/grafana-portforward.log 2>&1 &
GRAFANA_PID=$!

echo "Starting Kubernaut UI port-forward..."
kubectl port-forward -n "${KUBERNAUT_NS}" svc/"${KUBERNAUT_UI}" 8001:3000 \
    >/tmp/ui-portforward.log 2>&1 &
GRAFANA_PID=$!

echo "Starting Frontend port-forward..."
kubectl port-forward -n "${APP_NS}" svc/"${FRONTEND_SERVICE}" 8000:80 \
    >/tmp/frontend-portforward.log 2>&1 &
FRONTEND_PID=$!

echo "Starting Loki port-forward..."
kubectl port-forward -n "${MONITORING_NS}" svc/"${LOKI_SERVICE}" 3100:3100 \
    >/tmp/loki-portforward.log 2>&1 &
LOKI_PID=$!

sleep 2

echo
echo "======================================"
echo "Grafana : http://localhost:3000"
echo "Frontend: http://localhost:8000"
echo "Loki    : http://localhost:3100"
echo "Kubernaut : http://localhost:8001"
echo "======================================"
echo
echo "Press Ctrl+C to stop."

wait