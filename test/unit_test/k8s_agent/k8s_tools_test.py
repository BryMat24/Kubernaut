import json
import subprocess
from unittest.mock import MagicMock, patch

import pytest

from mcp_servers.k8s_mcp_server import k8s_tools
from mcp_servers.k8s_mcp_server.k8s_tools import ResourceKind


def make_completed_process(stdout: str = "", stderr: str = "", returncode: int = 0):
    proc = MagicMock()
    proc.stdout = stdout
    proc.stderr = stderr
    proc.returncode = returncode
    return proc


@pytest.fixture
def mock_run():
    with patch.object(k8s_tools.subprocess, "run") as mock:
        yield mock


# ------------------------------------------------------------------
# list_namespaces
# ------------------------------------------------------------------

def test_list_namespaces_success(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "items": [
            {
                "metadata": {"name": "default", "creationTimestamp": "2026-01-01T00:00:00Z"},
                "status": {"phase": "Active"},
            },
            {
                "metadata": {"name": "kube-system", "creationTimestamp": "2026-01-02T00:00:00Z"},
                "status": {"phase": "Active"},
            },
        ]
    }))

    result = k8s_tools.list_namespaces()

    mock_run.assert_called_once_with(
        ["kubectl", "get", "namespaces", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == [
        {"name": "default", "status": "Active", "creation_timestamp": "2026-01-01T00:00:00Z"},
        {"name": "kube-system", "status": "Active", "creation_timestamp": "2026-01-02T00:00:00Z"},
    ]


def test_list_namespaces_empty(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))
    assert k8s_tools.list_namespaces() == []


def test_list_namespaces_kubectl_failure_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.list_namespaces()


# ------------------------------------------------------------------
# _project_resource_summary
# ------------------------------------------------------------------

def test_project_resource_summary_extracts_minimal_fields():
    manifest = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "my-pod",
            "namespace": "dev",
            "labels": {"app": "backend"},
            "creationTimestamp": "2026-01-01T00:00:00Z",
            "ownerReferences": [{"kind": "ReplicaSet", "name": "backend-abc123"}],
            "managedFields": [{"manager": "kubectl"}],
            "annotations": {"kubectl.kubernetes.io/last-applied-configuration": "{...}"},
        },
        "spec": {"containers": [{"image": "backend:v2"}]},
        "status": {"phase": "Running"},
    }

    result = k8s_tools._project_resource_summary(manifest)

    assert result == {
        "name": "my-pod",
        "namespace": "dev",
        "labels": {"app": "backend"},
        "creationTimestamp": "2026-01-01T00:00:00Z",
        "ownerReferences": [{"kind": "ReplicaSet", "name": "backend-abc123"}],
    }


def test_project_resource_summary_defaults_missing_fields():
    manifest = {"metadata": {"name": "my-node"}}

    result = k8s_tools._project_resource_summary(manifest)

    assert result == {
        "name": "my-node",
        "namespace": None,
        "labels": {},
        "creationTimestamp": None,
        "ownerReferences": [],
    }


def test_project_resource_summary_handles_missing_metadata():
    result = k8s_tools._project_resource_summary({"kind": "Pod"})

    assert result == {
        "name": None,
        "namespace": None,
        "labels": {},
        "creationTimestamp": None,
        "ownerReferences": [],
    }


# ------------------------------------------------------------------
# get_resource
# ------------------------------------------------------------------

def test_get_resource_success(mock_run):
    manifest = {"apiVersion": "v1", "kind": "Pod", "metadata": {"name": "my-pod"}}
    mock_run.return_value = make_completed_process(stdout=json.dumps(manifest))

    result = k8s_tools.get_resource(ResourceKind.POD, "my-pod", "default")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "pod", "my-pod", "-n", "default", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == {
        "name": "my-pod",
        "namespace": None,
        "labels": {},
        "creationTimestamp": None,
        "ownerReferences": [],
    }


def test_get_resource_uses_default_namespace(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({}))
    k8s_tools.get_resource(ResourceKind.DEPLOYMENT, "my-deploy")
    mock_run.assert_called_once_with(
        ["kubectl", "get", "deployment", "my-deploy", "-n", "default", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_resource_not_found_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.get_resource(ResourceKind.POD, "missing-pod")


def test_get_resource_cluster_scoped_kind_omits_namespace_flag(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"kind": "Node"}))

    k8s_tools.get_resource(ResourceKind.NODE, "my-node", "default")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "node", "my-node", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_resource_clusterrole_omits_namespace_flag(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"kind": "ClusterRole"}))

    k8s_tools.get_resource(ResourceKind.CLUSTERROLE, "my-role")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "clusterrole", "my-role", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_resource_clusterrolebinding_omits_namespace_flag(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"kind": "ClusterRoleBinding"}))

    k8s_tools.get_resource(ResourceKind.CLUSTERROLEBINDING, "my-binding")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "clusterrolebinding", "my-binding", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )


# ------------------------------------------------------------------
# list_resources
# ------------------------------------------------------------------

def test_list_resources_success(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "kind": "List",
        "items": [
            {"metadata": {"name": "api"}},
            {"metadata": {"name": "worker"}},
        ],
    }))

    result = k8s_tools.list_resources(ResourceKind.DEPLOYMENT, "default")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "deployment", "-n", "default", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == [
        {"name": "api", "namespace": None, "labels": {}, "creationTimestamp": None, "ownerReferences": []},
        {"name": "worker", "namespace": None, "labels": {}, "creationTimestamp": None, "ownerReferences": []},
    ]


def test_list_resources_returns_list_not_envelope(mock_run):
    # regression test: list_resources must unwrap kubectl's {"kind": "List", "items": [...]}
    # envelope rather than returning it whole, since the return type is list[dict].
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "apiVersion": "v1", "kind": "List", "metadata": {"resourceVersion": ""}, "items": [],
    }))
    result = k8s_tools.list_resources(ResourceKind.POD)
    assert result == []
    assert isinstance(result, list)


def test_list_resources_missing_items_key_defaults_to_empty_list(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"kind": "List"}))
    assert k8s_tools.list_resources(ResourceKind.POD) == []


def test_list_resources_empty_namespace_does_not_add_all_namespaces_flag(mock_run):
    # Characterizes current behavior: unlike get_events/top_pods, list_resources does not
    # append kubectl's -A flag for an empty namespace string, despite its docstring saying
    # "pass an empty string to search all namespaces" -- it just passes -n "" through.
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))
    k8s_tools.list_resources(ResourceKind.POD, namespace="")
    mock_run.assert_called_once_with(
        ["kubectl", "get", "pod", "-n", "", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_list_resources_cluster_scoped_kind_omits_namespace_flag(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))

    k8s_tools.list_resources(ResourceKind.STORAGECLASS, "default")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "storageclass", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_list_resources_with_label_selector(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "items": [{"metadata": {"name": "api", "labels": {"app": "my-service"}}}],
    }))

    result = k8s_tools.list_resources(ResourceKind.POD, "default", label_selector="app=my-service")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "pod", "-n", "default", "-l", "app=my-service", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == [
        {
            "name": "api",
            "namespace": None,
            "labels": {"app": "my-service"},
            "creationTimestamp": None,
            "ownerReferences": [],
        },
    ]


def test_list_resources_without_label_selector_omits_l_flag(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))

    k8s_tools.list_resources(ResourceKind.POD, "default")

    cmd = mock_run.call_args.args[0]
    assert "-l" not in cmd


def test_list_resources_empty_label_selector_omits_l_flag(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))

    k8s_tools.list_resources(ResourceKind.POD, "default", label_selector="")

    cmd = mock_run.call_args.args[0]
    assert "-l" not in cmd


def test_list_resources_label_selector_with_cluster_scoped_kind(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))

    k8s_tools.list_resources(ResourceKind.STORAGECLASS, "default", label_selector="tier=fast")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "storageclass", "-l", "tier=fast", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )


# ------------------------------------------------------------------
# describe_resource
# ------------------------------------------------------------------

def test_describe_resource_success(mock_run):
    description_text = "Name:  my-pod\nStatus: Running\nConditions:\n  Ready  True\n"
    mock_run.return_value = make_completed_process(stdout=description_text)

    result = k8s_tools.describe_resource(ResourceKind.POD, "my-pod", "default")

    # kubectl describe does not support -o/--output, unlike kubectl get -- no -o json here.
    mock_run.assert_called_once_with(
        ["kubectl", "describe", "pod", "my-pod", "-n", "default"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == description_text


def test_describe_resource_failure_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.describe_resource(ResourceKind.POD, "my-pod")


def test_describe_resource_cluster_scoped_kind_omits_namespace_flag(mock_run):
    mock_run.return_value = make_completed_process(stdout="Name: my-node\n")

    k8s_tools.describe_resource(ResourceKind.PERSISTENTVOLUME, "my-pv", "default")

    mock_run.assert_called_once_with(
        ["kubectl", "describe", "pv", "my-pv"],
        capture_output=True,
        text=True,
        check=True,
    )


# ------------------------------------------------------------------
# get_events
# ------------------------------------------------------------------

def test_get_events_default_namespace(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))
    k8s_tools.get_events()
    mock_run.assert_called_once_with(
        ["kubectl", "get", "events", "--sort-by=.lastTimestamp", "-o", "json", "-n", "default"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_events_empty_namespace_adds_all_namespaces_flag(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))
    k8s_tools.get_events(namespace="")
    mock_run.assert_called_once_with(
        ["kubectl", "get", "events", "--sort-by=.lastTimestamp", "-o", "json", "-A"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_events_with_kind_and_name_adds_field_selector(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))
    k8s_tools.get_events(namespace="default", kind=ResourceKind.DEPLOYMENT, name="api")
    mock_run.assert_called_once_with(
        [
            "kubectl", "get", "events", "--sort-by=.lastTimestamp", "-o", "json",
            "-n", "default",
            "--field-selector", "involvedObject.kind=Deployment,involvedObject.name=api",
        ],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_events_kind_without_name_omits_field_selector(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"items": []}))
    k8s_tools.get_events(namespace="default", kind=ResourceKind.POD, name=None)
    mock_run.assert_called_once_with(
        ["kubectl", "get", "events", "--sort-by=.lastTimestamp", "-o", "json", "-n", "default"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_events_parses_items(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "items": [
            {
                "lastTimestamp": "2026-01-01T00:00:00Z",
                "type": "Warning",
                "reason": "FailedScheduling",
                "involvedObject": {"kind": "Pod", "name": "my-pod"},
                "message": "0/3 nodes are available",
            }
        ]
    }))

    result = k8s_tools.get_events()

    assert result == [
        {
            "timestamp": "2026-01-01T00:00:00Z",
            "type": "Warning",
            "reason": "FailedScheduling",
            "involved_object": {"kind": "Pod", "name": "my-pod"},
            "message": "0/3 nodes are available",
        }
    ]


# ------------------------------------------------------------------
# get_pod_logs
# ------------------------------------------------------------------

def test_get_pod_logs_success(mock_run):
    mock_run.return_value = make_completed_process(stdout="line1\nline2\n")

    result = k8s_tools.get_pod_logs("my-pod", "default")

    mock_run.assert_called_once_with(
        ["kubectl", "logs", "my-pod", "-n", "default", "--tail=100"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == "line1\nline2\n"


def test_get_pod_logs_with_container_and_tail(mock_run):
    mock_run.return_value = make_completed_process(stdout="")
    k8s_tools.get_pod_logs("my-pod", "default", container="sidecar", tail=50)
    mock_run.assert_called_once_with(
        ["kubectl", "logs", "my-pod", "-n", "default", "--tail=50", "-c", "sidecar"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_pod_logs_failure_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.get_pod_logs("missing-pod")


# ------------------------------------------------------------------
# get_previous_logs
# ------------------------------------------------------------------

def test_get_previous_logs_success(mock_run):
    mock_run.return_value = make_completed_process(stdout="crash trace\n")

    result = k8s_tools.get_previous_logs("my-pod", "default")

    mock_run.assert_called_once_with(
        ["kubectl", "logs", "my-pod", "-n", "default", "--previous", "--tail=100"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == "crash trace\n"


def test_get_previous_logs_with_container(mock_run):
    mock_run.return_value = make_completed_process(stdout="")
    k8s_tools.get_previous_logs("my-pod", container="app")
    mock_run.assert_called_once_with(
        ["kubectl", "logs", "my-pod", "-n", "default", "--previous", "--tail=100", "-c", "app"],
        capture_output=True,
        text=True,
        check=True,
    )


def test_get_previous_logs_failure_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.get_previous_logs("my-pod")


# ------------------------------------------------------------------
# top_pods
# ------------------------------------------------------------------

def test_top_pods_with_namespace(mock_run):
    mock_run.return_value = make_completed_process(
        stdout="NAME       CPU(cores)   MEMORY(bytes)\nmy-pod     10m          20Mi\n"
    )

    result = k8s_tools.top_pods("default")

    mock_run.assert_called_once_with(
        ["kubectl", "top", "pods", "-n", "default"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == [{"namespace": "default", "pod": "my-pod", "cpu": "10m", "memory": "20Mi"}]


def test_top_pods_all_namespaces(mock_run):
    mock_run.return_value = make_completed_process(
        stdout="NAMESPACE   NAME       CPU(cores)   MEMORY(bytes)\ndefault     my-pod     10m          20Mi\n"
    )

    result = k8s_tools.top_pods(namespace="")

    mock_run.assert_called_once_with(
        ["kubectl", "top", "pods", "-A"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == [{"namespace": "default", "pod": "my-pod", "cpu": "10m", "memory": "20Mi"}]


def test_top_pods_empty_result(mock_run):
    mock_run.return_value = make_completed_process(stdout="NAME   CPU(cores)   MEMORY(bytes)\n")
    assert k8s_tools.top_pods("default") == []


def test_top_pods_failure_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.top_pods()


# ------------------------------------------------------------------
# top_nodes
# ------------------------------------------------------------------

def test_top_nodes_success(mock_run):
    mock_run.return_value = make_completed_process(
        stdout="NAME     CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%\nnode-1   500m         25%    2000Mi           50%\n"
    )

    result = k8s_tools.top_nodes()

    mock_run.assert_called_once_with(
        ["kubectl", "top", "nodes"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == [
        {"node": "node-1", "cpu": "500m", "cpu_percent": "25%", "memory": "2000Mi", "memory_percent": "50%"}
    ]


def test_top_nodes_empty_result(mock_run):
    mock_run.return_value = make_completed_process(stdout="NAME   CPU(cores)   CPU%   MEMORY(bytes)   MEMORY%\n")
    assert k8s_tools.top_nodes() == []


def test_top_nodes_failure_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.top_nodes()


# ------------------------------------------------------------------
# get_node_conditions
# ------------------------------------------------------------------

def test_get_node_conditions_success(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "spec": {
            "taints": [
                {"key": "node.kubernetes.io/unschedulable", "effect": "NoSchedule"},
            ],
        },
        "status": {
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True",
                    "reason": "KubeletReady",
                    "message": "kubelet is posting ready status",
                    "lastTransitionTime": "2026-01-01T00:00:00Z",
                },
                {
                    "type": "DiskPressure",
                    "status": "False",
                    "reason": "KubeletHasNoDiskPressure",
                    "message": "kubelet has no disk pressure",
                    "lastTransitionTime": "2026-01-01T00:00:00Z",
                },
            ],
            "capacity": {"cpu": "4", "memory": "16336792Ki", "pods": "110"},
            "allocatable": {"cpu": "3800m", "memory": "15000000Ki", "pods": "110"},
        },
    }))

    result = k8s_tools.get_node_conditions("my-node")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "node", "my-node", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == {
        "name": "my-node",
        "conditions": [
            {
                "type": "Ready",
                "status": "True",
                "reason": "KubeletReady",
                "message": "kubelet is posting ready status",
                "last_transition_time": "2026-01-01T00:00:00Z",
            },
            {
                "type": "DiskPressure",
                "status": "False",
                "reason": "KubeletHasNoDiskPressure",
                "message": "kubelet has no disk pressure",
                "last_transition_time": "2026-01-01T00:00:00Z",
            },
        ],
        "taints": [{"key": "node.kubernetes.io/unschedulable", "effect": "NoSchedule"}],
        "capacity": {"cpu": "4", "memory": "16336792Ki", "pods": "110"},
        "allocatable": {"cpu": "3800m", "memory": "15000000Ki", "pods": "110"},
    }


def test_get_node_conditions_no_taints(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "spec": {},
        "status": {"conditions": [], "capacity": {}, "allocatable": {}},
    }))

    result = k8s_tools.get_node_conditions("my-node")

    assert result["taints"] == []
    assert result["conditions"] == []


def test_get_node_conditions_failure_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.get_node_conditions("missing-node")


# ------------------------------------------------------------------
# rollout_status
# ------------------------------------------------------------------

def test_rollout_status_completed(mock_run):
    mock_run.return_value = make_completed_process(
        stdout="deployment \"api\" successfully rolled out\n", returncode=0
    )

    result = k8s_tools.rollout_status("api", "default")

    mock_run.assert_called_once_with(
        ["kubectl", "rollout", "status", "deployment/api", "-n", "default", "--watch=false"],
        capture_output=True,
        text=True,
    )
    assert result == {
        "status": "complete",
        "message": 'deployment "api" successfully rolled out',
        "completed": True,
    }


def test_rollout_status_in_progress(mock_run):
    mock_run.return_value = make_completed_process(
        stdout="", stderr="Waiting for rollout to finish\n", returncode=1
    )

    result = k8s_tools.rollout_status("api")

    assert result == {
        "status": "in_progress",
        "message": "Waiting for rollout to finish",
        "completed": False,
    }


def test_rollout_status_does_not_raise_on_nonzero_exit(mock_run):
    # rollout_status deliberately omits check=True so it can branch on returncode itself.
    mock_run.return_value = make_completed_process(stdout="", stderr="not ready", returncode=1)
    result = k8s_tools.rollout_status("api")
    assert result["completed"] is False


# ------------------------------------------------------------------
# check_service_connectivity
# ------------------------------------------------------------------

def test_check_service_connectivity_with_ready_endpoints(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({
        "subsets": [{"addresses": [{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}]}]
    }))

    result = k8s_tools.check_service_connectivity("my-service", "default")

    mock_run.assert_called_once_with(
        ["kubectl", "get", "endpoints", "my-service", "-n", "default", "-o", "json"],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result == {
        "service": "my-service",
        "has_ready_endpoints": True,
        "ready_endpoint_count": 2,
        "subsets": [{"addresses": [{"ip": "10.0.0.1"}, {"ip": "10.0.0.2"}]}],
    }


def test_check_service_connectivity_no_ready_endpoints(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({"subsets": []}))

    result = k8s_tools.check_service_connectivity("my-service")

    assert result == {
        "service": "my-service",
        "has_ready_endpoints": False,
        "ready_endpoint_count": 0,
        "subsets": [],
    }


def test_check_service_connectivity_missing_subsets_key(mock_run):
    mock_run.return_value = make_completed_process(stdout=json.dumps({}))
    result = k8s_tools.check_service_connectivity("my-service")
    assert result["has_ready_endpoints"] is False
    assert result["ready_endpoint_count"] == 0


def test_check_service_connectivity_failure_propagates(mock_run):
    mock_run.side_effect = subprocess.CalledProcessError(1, ["kubectl"])
    with pytest.raises(subprocess.CalledProcessError):
        k8s_tools.check_service_connectivity("missing-service")
