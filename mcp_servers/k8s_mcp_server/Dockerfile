FROM python:3.12-slim

# kubectl is required at runtime — every tool in k8s_tools.py shells out to it.
ARG KUBECTL_VERSION=v1.31.0
RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ca-certificates \
    && curl -fsSL -o /usr/local/bin/kubectl \
        "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
    && chmod +x /usr/local/bin/kubectl \
    && apt-get purge -y --auto-remove curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY k8s_tools.py server.py ./

# When deployed in-cluster, kubectl auto-detects the mounted ServiceAccount
# token/CA cert — no kubeconfig file needed here, but the pod's
# ServiceAccount must be bound (via RBAC) to the permissions each tool needs
# (get/list/watch on the resource kinds in ResourceKind, plus pods/log and
# metrics.k8s.io for top_pods/top_nodes). That RBAC wiring is not part of
# this image and must be applied separately at deploy time.

EXPOSE 8000
ENV PORT=8000

CMD ["python", "server.py"]
