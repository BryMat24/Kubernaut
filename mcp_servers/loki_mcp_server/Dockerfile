FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY loki_tools.py server.py ./

# LOKI_URL must point at the in-cluster Loki service (e.g.
# http://loki.dev-monitoring.svc.cluster.local:3100) -- no log shipping or
# storage happens in this image, it only queries an existing Loki instance.
ENV LOKI_URL=http://localhost:3100

EXPOSE 8000
ENV PORT=8000

CMD ["python", "server.py"]
