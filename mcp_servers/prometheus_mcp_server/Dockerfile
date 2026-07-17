FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY promql_tools.py server.py ./

# PROMETHEUS_URL must point at the in-cluster Prometheus service (e.g.
# http://prometheus-server.monitoring.svc.cluster.local:9090) — no scraping
# or storage happens in this image, it only queries an existing Prometheus.
ENV PROMETHEUS_URL=http://localhost:9090

EXPOSE 8000
ENV PORT=8000

CMD ["python", "server.py"]
