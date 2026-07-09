from langchain_core.tools import tool
import json
import subprocess
from enum import Enum

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
@tool
def list_namespaces():
    """
    List all Kubernetes namespaces in the cluster.

    This tool invokes:
        kubectl get namespaces

    Use this tool to discover available namespaces before inspecting
    namespaced resources. This is useful when the namespace is unknown or
    when investigating issues across multiple namespaces.

    Typical use cases:
      - Discover the namespace containing an application.
      - Enumerate all namespaces in the cluster.
      - Verify whether a namespace exists.

    Args:
        None

    Returns:
        list[dict]:
            One entry per namespace containing fields such as:
                - name
                - status
                - age
    """
    try:
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
    except Exception as e:
        return f"Error in getting namespace: {e}"


@tool
def get_resource(kind: ResourceKind, name: str, namespace: str = "default"):
    """
    Retrieve the complete Kubernetes resource definition.

    This tool invokes:
        kubectl get <kind> <name> -n <namespace> -o yaml

    Use this tool when you need to inspect the complete configuration or
    current state of a Kubernetes resource. It returns the full manifest,
    including metadata, spec, and status.

    Typical use cases:
      - Verify Deployment images, replicas, or selectors.
      - Inspect Pod specifications, environment variables, or volumes.
      - Examine Services, ConfigMaps, Secrets, or Ingress definitions.
      - Understand how a resource is configured.

    Args:
        kind (ResourceKind = "pod", "deployment", "service", "configmap", "secret", "ingress", "job", "replicaset", "statefulset", "daemonset"):
            Kubernetes resource type
        name (str):
            Name of the resource.
        namespace (str):
            Namespace containing the resource.

    Returns:
        dict:
            Complete Kubernetes resource manifest containing metadata,
            spec, and status.
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", kind.value, name, "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
        )

        return json.loads(result.stdout)
    except Exception as e:
        return f"Error in getting resource: {e}"


@tool
def list_resources(kind: ResourceKind, namespace: str = "default"):
    """
    List Kubernetes resources of a given type.

    This tool invokes one of:
        kubectl get <kind> -A
        kubectl get <kind> -n <namespace>

    Use this tool for resource discovery before inspecting individual
    resources. It provides a high-level overview of existing resources and
    their current status.

    Typical use cases:
      - Find Pods, Deployments, or Services.
      - Discover resource names.
      - Identify unhealthy workloads.

    Args:
        kind (ResourceKind = "pod", "deployment", "service", "configmap", "secret", "ingress", "job", "replicaset", "statefulset", "daemonset"):
            Kubernetes resource type.
        namespace (str | None):
            Namespace to search. If omitted, all namespaces are searched.

    Returns:
        list[dict]:
            Summary of each resource, including fields such as:
                - name
                - namespace
                - status
                - ready
                - age
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", kind.value, "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
        )

        return json.loads(result.stdout)
    except Exception as e:
        return f"Error in getting resource: {e}"


@tool
def describe_resource(kind: ResourceKind, name: str, namespace: str = "default"):
    """
    Retrieve a detailed runtime description of a Kubernetes resource.

    This tool invokes:
        kubectl describe <kind> <name> -n <namespace>

    Use this tool when investigating why a resource is unhealthy. Unlike
    get_resource(), this includes runtime information generated by Kubernetes,
    such as conditions, restart counts, probe failures, scheduling details,
    and recent events.

    Typical use cases:
      - Investigate CrashLoopBackOff.
      - Inspect Pod conditions.
      - Diagnose rollout failures.
      - Examine scheduling information.

    Args:
        kind (ResourceKind = "pod", "deployment", "service", "configmap", "secret", "ingress", "job", "replicaset", "statefulset", "daemonset"):
            Kubernetes resource type.
        name (str):
            Resource name.
        namespace (str):
            Namespace containing the resource.

    Returns:
        dict:
            Structured runtime information including:
                - conditions
                - container states
                - restart counts
                - node assignment
                - recent events
    """
    try:
        result = subprocess.run(
            ["kubectl", "describe", kind.value, name, "-n", namespace, "-o", "json"],
            capture_output=True,
            text=True,
            check=True,
        )

        return json.loads(result.stdout)
    except Exception as e:
        return f"Error in getting resource: {e}"

# EVENTS
@tool
def get_events(namespace: str = "default", kind: ResourceKind | None = None, name: str | None = None):
    """
    Retrieve recent Kubernetes events.

    This tool invokes one of:
        kubectl get events --sort-by=.lastTimestamp
        kubectl get events -n <namespace> --sort-by=.lastTimestamp
        kubectl get events -n <namespace> \
            --field-selector involvedObject.kind=<kind>,involvedObject.name=<name> \
            --sort-by=.lastTimestamp

    Use this tool to determine why Kubernetes succeeded or failed to perform
    an operation. Events provide explanations for actions taken by the
    Kubernetes control plane and are the primary source of information for
    scheduling, image pull, volume mount, rollout, and probe failures.

    Typical use cases:
      - Diagnose Pending Pods.
      - Investigate FailedScheduling.
      - Identify ImagePullBackOff causes.
      - Examine volume mount or probe failures.
      - Retrieve events for a specific Pod, Deployment, Job, or other resource.

    Args:
        namespace (str | None):
            Namespace whose events should be retrieved. If omitted, events
            from all namespaces are returned.
        kind (ResourceKind | None):
            Optional Kubernetes resource type to filter events by
            (e.g. ResourceKind.POD, ResourceKind.DEPLOYMENT, ResourceKind.JOB).
        name (str | None):
            Optional resource name. Must be provided together with kind.

    Returns:
        list[dict]:
            One entry per event containing:
                - timestamp
                - type
                - reason
                - involved_object
                - message
    """
    try:
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
    except Exception as e:
        return f"Error in getting events: {e}"


# LOGS
@tool
def get_pod_logs(pod: str, namespace: str = "default", container: str | None = None, tail: int = 100):
    """
    Retrieve logs from a running container.

    This tool invokes one of:
        kubectl logs <pod> -n <namespace> --tail=<tail>
        kubectl logs <pod> -n <namespace> -c <container> --tail=<tail>

    Use this tool to inspect application behavior after the container has
    successfully started. This is the primary tool for diagnosing application
    errors and runtime failures.

    Typical use cases:
      - Investigate application exceptions.
      - Inspect startup logs.
      - Diagnose request failures.
      - Examine runtime behavior.

    Args:
        pod (str):
            Pod name.
        namespace (str):
            Namespace containing the Pod.
        container (str | None):
            Optional container name for multi-container Pods.
        tail (int):
            Number of recent log lines to retrieve.

    Returns:
        str:
            Container log output.
    """
    try:
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
    except Exception as e:
        return f"Error in getting pod logs: {e}"


@tool
def get_previous_logs(pod: str, namespace: str = "default", container: str | None = None, tail: int = 100):
    """
    Retrieve logs from the previous instance of a restarted container.

    This tool invokes one of:
        kubectl logs <pod> -n <namespace> --previous --tail=<tail>
        kubectl logs <pod> -n <namespace> -c <container> --previous --tail=<tail>

    Use this tool when a container has restarted, particularly during
    CrashLoopBackOff. Previous logs often contain the error that caused the
    previous container instance to terminate.

    Typical use cases:
      - Diagnose CrashLoopBackOff.
      - Investigate failed startup.
      - Recover logs after container restart.

    Args:
        pod (str):
            Pod name.
        namespace (str):
            Namespace containing the Pod.
        container (str | None):
            Optional container name.
        tail (int):
            Number of recent log lines to retrieve.

    Returns:
        str:
            Previous container log output.
    """
    try:
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
    except Exception as e:
        return f"Error in getting previous pod logs: {e}"

# TODO
# RELATIONSHIPS

# METRICS
@tool
def top_pods(namespace: str = "default"):
    """
    Retrieve current CPU and memory usage for Pods.

    This tool invokes one of:
        kubectl top pods -A
        kubectl top pods -n <namespace>

    Use this tool when investigating resource-related issues such as high CPU,
    excessive memory usage, application slowness, or OOMKilled containers.
    It should generally be used after identifying symptoms that suggest
    resource exhaustion.

    Typical use cases:
      - Investigate OOMKilled Pods.
      - Compare resource usage across replicas.
      - Diagnose performance issues.
      - Identify CPU-intensive workloads.

    Args:
        namespace (str | None):
            Namespace to inspect. If omitted, metrics are retrieved across all
            namespaces.

    Returns:
        list[dict]:
            One entry per Pod containing:
                - namespace
                - pod
                - cpu
                - memory
    """
    try:
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
    except Exception as e:
        return f"Error in getting pod metrics: {e}"


@tool
def top_nodes():
    """
    Retrieve current CPU and memory usage for Kubernetes nodes.

    This tool invokes:
        kubectl top nodes

    Use this tool when investigating cluster-wide resource pressure,
    scheduling failures, or overall cluster capacity. It helps determine
    whether node resource exhaustion is affecting workloads.

    Typical use cases:
      - Diagnose FailedScheduling due to insufficient CPU or memory.
      - Investigate MemoryPressure or DiskPressure.
      - Identify overloaded nodes.
      - Assess cluster capacity.

    Args:
        None

    Returns:
        list[dict]:
            One entry per node containing:
                - node
                - cpu
                - cpu_percent
                - memory
                - memory_percent
    """
    try:
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
    except Exception as e:
        return f"Error in getting node metrics: {e}"


# rollout status
@tool
def rollout_status(deployment: str, namespace: str = "default"):
    """
    Retrieve the rollout status of a Deployment.

    This tool invokes:
        kubectl rollout status deployment/<deployment> -n <namespace>

    Use this tool to determine whether a Deployment rollout has completed
    successfully or is still progressing. It is especially useful after
    applying changes to a Deployment or when investigating rollout failures.

    Typical use cases:
      - Verify a Deployment has rolled out successfully.
      - Detect stalled or failed rollouts.
      - Monitor progress after updating an image or manifest.
      - Confirm whether new Pods have become available.

    Args:
        deployment (str):
            Name of the Deployment.
        namespace (str):
            Namespace containing the Deployment.

    Returns:
        dict:
            Rollout status information including fields such as:
                - status (str)
                - message (str)
                - completed (bool)
    """
    try:
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
    except Exception as e:
        return f"Error in getting rollout status: {e}"

# RELATIONSHIPS
@tool
def check_service_connectivity(service: str, namespace: str = "default"):
    """
    Check whether a Kubernetes Service is reachable and resolving correctly.

    Verifies the Service exists, has ready Endpoints (backing pods), and
    optionally that the port is reachable from within the cluster.

    Typical use cases:
      - Diagnose "service unreachable" or "connection refused" reports.
      - Confirm a Service has no ready endpoints (common cause: label
        selector mismatch, or all backing pods unhealthy).
    """
    try:
        result = subprocess.run(
            ["kubectl", "get", "endpoints", service, "-n", namespace, "-o", "json"],
            capture_output=True, text=True, check=True,
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
    except Exception as e:
        return f"Error checking service connectivity: {e}"