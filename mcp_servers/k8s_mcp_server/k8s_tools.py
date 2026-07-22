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
    NODE = "node"
    PERSISTENTVOLUMECLAIM = "pvc"
    PERSISTENTVOLUME = "pv"
    STORAGECLASS = "storageclass"
    RESOURCEQUOTA = "resourcequota"
    LIMITRANGE = "limitrange"
    HORIZONTALPODAUTOSCALER = "hpa"
    NETWORKPOLICY = "networkpolicy"
    CLUSTERROLE = "clusterrole"
    CLUSTERROLEBINDING = "clusterrolebinding"


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
    ResourceKind.NODE: "Node",
    ResourceKind.PERSISTENTVOLUMECLAIM: "PersistentVolumeClaim",
    ResourceKind.PERSISTENTVOLUME: "PersistentVolume",
    ResourceKind.STORAGECLASS: "StorageClass",
    ResourceKind.RESOURCEQUOTA: "ResourceQuota",
    ResourceKind.LIMITRANGE: "LimitRange",
    ResourceKind.HORIZONTALPODAUTOSCALER: "HorizontalPodAutoscaler",
    ResourceKind.NETWORKPOLICY: "NetworkPolicy",
    ResourceKind.CLUSTERROLE: "ClusterRole",
    ResourceKind.CLUSTERROLEBINDING: "ClusterRoleBinding",
}

_CLUSTER_SCOPED_KINDS = {
    ResourceKind.NODE,
    ResourceKind.PERSISTENTVOLUME,
    ResourceKind.STORAGECLASS,
    ResourceKind.CLUSTERROLE,
    ResourceKind.CLUSTERROLEBINDING,
}


def _project_resource_summary(manifest: dict) -> dict:
    """
    Reduce a manifest to the minimal fields useful for identifying and orienting around a
    resource: identity, labels, creation time, and its ownership chain (e.g. a Pod's
    ownerReferences pointing at its ReplicaSet). Deep spec/status detail (image, replicas,
    env vars, conditions) is intentionally not included here -- describe_resource is the
    tool for that.
    """
    metadata = manifest.get("metadata", {})
    return {
        "name": metadata.get("name"),
        "namespace": metadata.get("namespace"),
        "labels": metadata.get("labels", {}),
        "creationTimestamp": metadata.get("creationTimestamp"),
        "ownerReferences": metadata.get("ownerReferences", []),
    }


# DISCOVERY
@mcp.tool
def list_namespaces() -> list[dict]:
    """
    List all Kubernetes namespaces in the cluster.

    Example: kubectl get namespaces -o json

    Use when: you don't yet know which namespace an app lives in, or need to confirm a
    namespace exists before running namespaced commands.
    """
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
    """
    Retrieve the complete manifest (metadata, spec, status) of a specific Kubernetes resource.

    Example: kubectl get deployment my-app -n default -o json

    Use when: you already know the resource's kind, name, and namespace and need its exact
    current configuration (image, replicas, env vars, selectors, labels, detailed spec of the resource such as resource limits, volumes). For human-readable
    runtime diagnostics (conditions, restart counts, recent events) use describe_resource
    instead — this tool returns the structured manifest, not runtime state explanations.

    Note: namespace is ignored for cluster-scoped kinds (Node, PersistentVolume,
    StorageClass, ClusterRole, ClusterRoleBinding).
    """
    cmd = ["kubectl", "get", kind.value, name]
    if kind not in _CLUSTER_SCOPED_KINDS:
        cmd.extend(["-n", namespace])
    cmd.extend(["-o", "json"])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    return result.stdout


@mcp.tool
def list_resources(
    kind: Annotated[ResourceKind, "Kubernetes resource type."],
    namespace: Annotated[str, "Namespace to search. Pass an empty string to search all namespaces."] = "default",
    label_selector: Annotated[str | None, "Label selector to filter results, e.g. app=my-service."] = None,
) -> list[dict]:
    """
    List Kubernetes resources of a given kind, for discovery before inspecting individual
    resources. Returns a minimal identity projection per resource — name, namespace, labels,
    creationTimestamp, ownerReferences — not the full manifest. For spec/status detail (image,
    replicas, conditions) on a specific resource, use describe_resource once you've found it
    here.

    Example: kubectl get pod -n default -o json
    Example (filtered by label): kubectl get pod -n default -l app=my-service -o json

    Use when: you know the kind but not the exact resource name yet — e.g. finding which pods
    exist in a namespace, or tracing an ownership chain (ownerReferences) from a Pod to its
    ReplicaSet/Deployment — before drilling into one with get_resource (exact identity) or
    describe_resource (runtime detail).

    Note: namespace is ignored for cluster-scoped kinds (Node, PersistentVolume,
    StorageClass, ClusterRole, ClusterRoleBinding).
    """
    cmd = ["kubectl", "get", kind.value]
    if kind not in _CLUSTER_SCOPED_KINDS:
        cmd.extend(["-n", namespace])
    if label_selector:
        cmd.extend(["-l", label_selector])
    cmd.extend(["-o", "json"])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    return [_project_resource_summary(item) for item in data.get("items", [])]


@mcp.tool
def describe_resource(
    kind: Annotated[ResourceKind, "Kubernetes resource type."],
    name: Annotated[str, "Resource name."],
    namespace: Annotated[str, "Namespace containing the resource."] = "default",
) -> str:
    """
    Retrieve a detailed, human-readable runtime description of a resource (conditions,
    container states, restart counts, scheduling, recent events).

    Example: kubectl describe pod my-pod -n default

    Use when: investigating why a resource is unhealthy — this surfaces runtime information
    Kubernetes generates (probe failures, scheduling decisions, recent events) that the plain
    manifest from get_resource does not include. Unlike the other tools here, this returns
    formatted text, not JSON.

    Note: namespace is ignored for cluster-scoped kinds (Node, PersistentVolume,
    StorageClass, ClusterRole, ClusterRoleBinding).
    """
    cmd = ["kubectl", "describe", kind.value, name]
    if kind not in _CLUSTER_SCOPED_KINDS:
        cmd.extend(["-n", namespace])

    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        check=True,
    )

    return result.stdout


# EVENTS
@mcp.tool
def get_events(
    namespace: Annotated[str, "Namespace to retrieve events from. Pass an empty string to search all namespaces."] = "default",
    kind: Annotated[ResourceKind | None, "Resource type to filter events by."] = None,
    name: Annotated[str | None, "Resource name to filter by. Must be provided together with kind."] = None,
) -> list[dict]:
    """
    Retrieve recent Kubernetes events, explaining actions taken by the control plane
    (scheduling, image pulls, volume mounts, rollouts, probe failures).

    Example: kubectl get events --sort-by=.lastTimestamp -o json -n default
    Example (filtered to one resource): kubectl get events --sort-by=.lastTimestamp -o json
    -n default --field-selector involvedObject.kind=Pod,involvedObject.name=my-pod

    Use when: you need to know why something happened (FailedScheduling, ImagePullBackOff,
    FailedMount) rather than what the current state is — get_resource/describe_resource show
    state, this shows the history of control-plane actions and failures.
    """
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
    """
    Retrieve logs from a running container.

    Example: kubectl logs my-pod -n default --tail=100

    Use when: the container is currently running (or was last terminated normally) and you
    need to see recent application output or errors. If the container has restarted and you
    suspect a crash, use get_previous_logs instead — this only shows the current instance.
    """
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
    """
    Retrieve logs from the previous instance of a restarted container.

    Example: kubectl logs my-pod -n default --previous --tail=100

    Use when: diagnosing CrashLoopBackOff or any restart — the current container's logs
    (get_pod_logs) would only show the new instance since restart, missing the actual crash
    reason, which only exists in the previous instance's logs.
    """
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

# METRICS
@mcp.tool
def top_pods(
    namespace: Annotated[str, "Namespace to inspect. Pass an empty string for all namespaces."] = "default",
) -> list[dict]:
    """
    Retrieve current CPU and memory usage for Pods.

    Example: kubectl top pods -n default

    Use when: investigating high CPU, excessive memory usage, application slowness, or
    OOMKilled containers — confirms whether resource exhaustion is actually occurring before
    looking elsewhere.
    """
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
    """
    Retrieve current CPU and memory usage for Kubernetes nodes.

    Example: kubectl top nodes

    Use when: investigating cluster-wide resource pressure or scheduling failures
    (FailedScheduling due to insufficient CPU/memory) that might stem from node capacity
    rather than any single pod's configuration.
    """
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


# NODE HEALTH
@mcp.tool
def get_node_conditions(
    name: Annotated[str, "Name of the Node."],
) -> dict:
    """
    Retrieve a Node's conditions, taints, and capacity vs. allocatable resources.

    Example: kubectl get node my-node -o json

    Use when: diagnosing why a pod is stuck Pending or a workload isn't scheduling —
    checks the specific fields that block scheduling (NoSchedule/NoExecute taints,
    DiskPressure/MemoryPressure/PIDPressure conditions, insufficient allocatable resources)
    without wading through describe_resource's dense mixed text output (pod list, events,
    conditions, capacity all together). Node is cluster-scoped, so there's no namespace
    parameter.
    """
    result = subprocess.run(
        ["kubectl", "get", "node", name, "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )

    data = json.loads(result.stdout)
    status = data.get("status", {})

    return {
        "name": name,
        "conditions": [
            {
                "type": c.get("type"),
                "status": c.get("status"),
                "reason": c.get("reason"),
                "message": c.get("message"),
                "last_transition_time": c.get("lastTransitionTime"),
            }
            for c in status.get("conditions", [])
        ],
        "taints": data.get("spec", {}).get("taints", []),
        "capacity": status.get("capacity", {}),
        "allocatable": status.get("allocatable", {}),
    }


# rollout status
@mcp.tool
def rollout_status(
    deployment: Annotated[str, "Name of the Deployment."],
    namespace: Annotated[str, "Namespace containing the Deployment."] = "default",
) -> dict:
    """
    Check whether a Deployment rollout has completed or is still progressing.

    Example: kubectl rollout status deployment/my-app -n default --watch=false

    Use when: confirming whether a recent Deployment change (image update, replica change) has
    finished rolling out successfully, or is stuck/still in progress.
    """
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
    """
    Check whether a Kubernetes Service has ready endpoints (backing pods).

    Example: kubectl get endpoints my-service -n default -o json

    Use when: diagnosing "service unreachable" or "connection refused" reports — confirms
    whether the Service actually has ready backing pods. A common root cause when this comes
    back empty is a label-selector mismatch between the Service and its target Pods.
    """
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
