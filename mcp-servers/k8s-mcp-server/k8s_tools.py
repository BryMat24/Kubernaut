import json
import subprocess
from enum import Enum
from typing import Annotated

from fastmcp import FastMCP

mcp = FastMCP("k8s-tools")


class ResourceKind(str, Enum):
    POD = "pod"
    DEPLOYMENT = "deployment"
    SERVICE = "service"
    CONFIGMAP = "configmap"
    SECRET = "secret"
    INGRESS = "ingress"
    JOB = "job"
    REPLICASET = "replicaset"
    STATEFULSET = "statefulset"
    DAEMONSET = "daemonset"


# Kubernetes uses PascalCase for the `kind` field on objects and events
# (e.g. involvedObject.kind), unlike the lowercase names kubectl accepts
# as CLI resource type arguments.
_KUBERNETES_KIND_NAMES = {
    ResourceKind.POD: "Pod",
    ResourceKind.DEPLOYMENT: "Deployment",
    ResourceKind.SERVICE: "Service",
    ResourceKind.CONFIGMAP: "ConfigMap",
    ResourceKind.SECRET: "Secret",
    ResourceKind.INGRESS: "Ingress",
    ResourceKind.JOB: "Job",
    ResourceKind.REPLICASET: "ReplicaSet",
    ResourceKind.STATEFULSET: "StatefulSet",
    ResourceKind.DAEMONSET: "DaemonSet",
}


# DISCOVERY
@mcp.tool
def list_namespaces() -> list[dict]:
    """List all Kubernetes namespaces in the cluster."""
    result = subprocess.run(
        ["kubectl", "get", "namespaces", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)

    namespaces = []
    for item in data["items"]:
        namespaces.append({
            "name": item["metadata"]["name"],
            "status": item["status"]["phase"],
            "creation_timestamp": item["metadata"]["creationTimestamp"],
        })

    return namespaces


@mcp.tool
def get_resource(
    kind: Annotated[ResourceKind, "Kubernetes resource type."],
    name: Annotated[str, "Name of the resource."],
    namespace: Annotated[str, "Namespace containing the resource."] = "default",
) -> dict:
    """Retrieve the complete manifest (metadata, spec, status) of a specific Kubernetes resource."""
    result = subprocess.run(
        ["kubectl", "get", kind.value, name, "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )

    return json.loads(result.stdout)


@mcp.tool
def list_resources(
    kind: Annotated[ResourceKind, "Kubernetes resource type."],
    namespace: Annotated[str, "Namespace to search. Pass an empty string to search all namespaces."] = "default",
) -> list[dict]:
    """List Kubernetes resources of a given kind, for discovery before inspecting individual resources."""
    result = subprocess.run(
        ["kubectl", "get", kind.value, "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )

    return json.loads(result.stdout)


@mcp.tool
def describe_resource(
    kind: Annotated[ResourceKind, "Kubernetes resource type."],
    name: Annotated[str, "Resource name."],
    namespace: Annotated[str, "Namespace containing the resource."] = "default",
) -> dict:
    """Retrieve a detailed runtime description (conditions, restart counts, scheduling, recent events) — use when investigating why a resource is unhealthy."""
    result = subprocess.run(
        ["kubectl", "describe", kind.value, name, "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )

    return json.loads(result.stdout)


# EVENTS
@mcp.tool
def get_events(
    namespace: Annotated[str, "Namespace to retrieve events from. Pass an empty string to search all namespaces."] = "default",
    kind: Annotated[ResourceKind | None, "Resource type to filter events by."] = None,
    name: Annotated[str | None, "Resource name to filter by. Must be provided together with kind."] = None,
) -> list[dict]:
    """Retrieve recent Kubernetes events, explaining actions taken by the control plane (scheduling, image pulls, volume mounts, rollouts, probe failures)."""
    cmd = ["kubectl", "get", "events", "--sort-by=.lastTimestamp", "-o", "json"]

    if namespace:
        cmd.extend(["-n", namespace])
    else:
        cmd.append("-A")

    if kind and name:
        kube_kind = _KUBERNETES_KIND_NAMES[kind]
        cmd.extend(["--field-selector", f"involvedObject.kind={kube_kind},involvedObject.name={name}"])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)

    events = []
    for item in data["items"]:
        events.append({
            "timestamp": item.get("lastTimestamp"),
            "type": item.get("type"),
            "reason": item.get("reason"),
            "involved_object": item.get("involvedObject", {}),
            "message": item.get("message"),
        })

    return events


# LOGS
@mcp.tool
def get_pod_logs(
    pod: Annotated[str, "Pod name."],
    namespace: Annotated[str, "Namespace containing the Pod."] = "default",
    container: Annotated[str | None, "Container name, for multi-container Pods."] = None,
    tail: Annotated[int, "Number of recent log lines to retrieve."] = 100,
) -> str:
    """Retrieve logs from a running container."""
    cmd = ["kubectl", "logs", pod, "-n", namespace, f"--tail={tail}"]

    if container:
        cmd.extend(["-c", container])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    return result.stdout


@mcp.tool
def get_previous_logs(
    pod: Annotated[str, "Pod name."],
    namespace: Annotated[str, "Namespace containing the Pod."] = "default",
    container: Annotated[str | None, "Container name, for multi-container Pods."] = None,
    tail: Annotated[int, "Number of recent log lines to retrieve."] = 100,
) -> str:
    """Retrieve logs from the previous instance of a restarted container — use for diagnosing CrashLoopBackOff."""
    cmd = ["kubectl", "logs", pod, "-n", namespace, "--previous", f"--tail={tail}"]

    if container:
        cmd.extend(["-c", container])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    return result.stdout


# TODO
# RELATIONSHIPS

# METRICS
@mcp.tool
def top_pods(
    namespace: Annotated[str, "Namespace to inspect. Pass an empty string for all namespaces."] = "default",
) -> list[dict]:
    """Retrieve current CPU and memory usage for Pods — use when investigating high CPU, excessive memory, or OOMKilled containers."""
    cmd = ["kubectl", "top", "pods"]

    if namespace:
        cmd.extend(["-n", namespace])
    else:
        cmd.append("-A")

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.strip().splitlines()

    pods = []
    for line in lines[1:]:
        fields = line.split()
        if namespace:
            pod, cpu, memory = fields
            pods.append({
                "namespace": namespace,
                "pod": pod,
                "cpu": cpu,
                "memory": memory,
            })
        else:
            ns, pod, cpu, memory = fields
            pods.append({
                "namespace": ns,
                "pod": pod,
                "cpu": cpu,
                "memory": memory,
            })

    return pods


@mcp.tool
def top_nodes() -> list[dict]:
    """Retrieve current CPU and memory usage for Kubernetes nodes — use when investigating cluster-wide resource pressure or scheduling failures."""
    result = subprocess.run(
        ["kubectl", "top", "nodes"],
        capture_output=True,
        text=True,
        check=True,
    )

    lines = result.stdout.strip().splitlines()

    nodes = []
    for line in lines[1:]:
        node, cpu, cpu_percent, memory, memory_percent = line.split()
        nodes.append({
            "node": node,
            "cpu": cpu,
            "cpu_percent": cpu_percent,
            "memory": memory,
            "memory_percent": memory_percent,
        })

    return nodes


# rollout status
@mcp.tool
def rollout_status(
    deployment: Annotated[str, "Name of the Deployment."],
    namespace: Annotated[str, "Namespace containing the Deployment."] = "default",
) -> dict:
    """Check whether a Deployment rollout has completed or is still progressing."""
    result = subprocess.run(
        ["kubectl", "rollout", "status", f"deployment/{deployment}", "-n", namespace, "--watch=false"],
        capture_output=True,
        text=True,
    )

    completed = result.returncode == 0
    message = result.stdout.strip() or result.stderr.strip()

    return {
        "status": "complete" if completed else "in_progress",
        "message": message,
        "completed": completed,
    }


# RELATIONSHIPS
@mcp.tool
def check_service_connectivity(
    service: Annotated[str, "Name of the Service."],
    namespace: Annotated[str, "Namespace containing the Service."] = "default",
) -> dict:
    """Check whether a Kubernetes Service has ready endpoints (backing pods) — use for diagnosing "service unreachable" or "connection refused" reports."""
    result = subprocess.run(
        ["kubectl", "get", "endpoints", service, "-n", namespace, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    data = json.loads(result.stdout)
    subsets = data.get("subsets", [])
    ready_addresses = sum(len(s.get("addresses", [])) for s in subsets)
    return {
        "service": service,
        "has_ready_endpoints": ready_addresses > 0,
        "ready_endpoint_count": ready_addresses,
        "subsets": subsets,
    }
