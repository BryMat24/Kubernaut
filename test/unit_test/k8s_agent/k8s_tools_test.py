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
# _strip_manifest_noise
# ------------------------------------------------------------------

def test_strip_manifest_noise_removes_last_applied_configuration_annotation():
    manifest = {
        "metadata": {
            "name": "backend-deployment",
            "annotations": {
                "deployment.kubernetes.io/revision": "11",
                "kubectl.kubernetes.io/last-applied-configuration": '{"apiVersion":"apps/v1"}',
            },
        },
    }
    result = k8s_tools._strip_manifest_noise(manifest)
    assert "kubectl.kubernetes.io/last-applied-configuration" not in result["metadata"]["annotations"]
    assert result["metadata"]["annotations"]["deployment.kubernetes.io/revision"] == "11"


def test_strip_manifest_noise_removes_request_bookkeeping_fields():
    manifest = {
        "metadata": {
            "name": "backend-deployment",
            "resourceVersion": "133392",
            "uid": "21dbb296-2146-415f-b21c-34e546c82e81",
            "generation": 12,
        },
    }
    result = k8s_tools._strip_manifest_noise(manifest)
    assert "resourceVersion" not in result["metadata"]
    assert "uid" not in result["metadata"]
    assert "generation" not in result["metadata"]
    assert result["metadata"]["name"] == "backend-deployment"


def test_strip_manifest_noise_removes_managed_fields():
    manifest = {"metadata": {"name": "my-pod", "managedFields": [{"manager": "kubectl"}]}}
    result = k8s_tools._strip_manifest_noise(manifest)
    assert "managedFields" not in result["metadata"]


def test_strip_manifest_noise_keeps_spec_and_status_intact():
    manifest = {
        "metadata": {"name": "backend-deployment"},
        "spec": {"replicas": 3, "template": {"spec": {"containers": [{"image": "backend:v2"}]}}},
        "status": {"readyReplicas": 1, "conditions": [{"type": "Available", "status": "False"}]},
    }
    result = k8s_tools._strip_manifest_noise(manifest)
    assert result["spec"] == manifest["spec"]
    assert result["status"] == manifest["status"]


def test_strip_manifest_noise_handles_missing_metadata():
    assert k8s_tools._strip_manifest_noise({"kind": "Pod"}) == {"kind": "Pod"}


def test_strip_manifest_noise_handles_missing_annotations():
    manifest = {"metadata": {"name": "my-pod"}}
    assert k8s_tools._strip_manifest_noise(manifest) == {"metadata": {"name": "my-pod"}}


# ------------------------------------------------------------------
# _summarize_pod
# ------------------------------------------------------------------

def test_summarize_container_state_waiting():
    assert k8s_tools._summarize_container_state({"waiting": {"reason": "CrashLoopBackOff"}}) == {
        "status": "waiting",
        "reason": "CrashLoopBackOff",
    }


def test_summarize_container_state_terminated():
    state = {"terminated": {"reason": "OOMKilled", "exitCode": 137}}
    assert k8s_tools._summarize_container_state(state) == {
        "status": "terminated",
        "reason": "OOMKilled",
        "exitCode": 137,
    }


def test_summarize_container_state_running():
    assert k8s_tools._summarize_container_state({"running": {"startedAt": "2026-01-01T00:00:00Z"}}) == {
        "status": "running",
    }


def test_summarize_container_state_unknown_when_empty():
    assert k8s_tools._summarize_container_state({}) == {"status": "unknown"}


def test_summarize_pod_extracts_phase_and_container_health():
    manifest = {
        "metadata": {"name": "backend-6qzrs", "namespace": "dev", "labels": {"app": "backend"}},
        "spec": {"containers": [{"name": "backend", "image": "backend:v2"}]},
        "status": {
            "phase": "Running",
            "containerStatuses": [
                {
                    "name": "backend",
                    "ready": False,
                    "restartCount": 269,
                    "state": {"waiting": {"reason": "CrashLoopBackOff"}},
                }
            ],
        },
    }
    result = k8s_tools._summarize_pod(manifest)
    assert result == {
        "name": "backend-6qzrs",
        "namespace": "dev",
        "labels": {"app": "backend"},
        "ownerReferences": [],
        "phase": "Running",
        "containerStatuses": [
            {"name": "backend", "ready": False, "restartCount": 269, "state": {"status": "waiting", "reason": "CrashLoopBackOff"}}
        ],
    }
    # spec (image, env, resources) is intentionally not part of the pod summary
    assert "spec" not in result


def test_summarize_pod_defaults_missing_fields():
    assert k8s_tools._summarize_pod({"metadata": {"name": "my-pod"}}) == {
        "name": "my-pod",
        "namespace": None,
        "labels": {},
        "ownerReferences": [],
        "phase": None,
        "containerStatuses": [],
    }


# ------------------------------------------------------------------
# workload-controller summarizers
# ------------------------------------------------------------------

def test_summarize_deployment_extracts_replica_health_and_conditions():
    manifest = {
        "metadata": {"name": "backend-deployment", "namespace": "dev", "labels": {"app": "backend"}},
        "spec": {"replicas": 3},
        "status": {
            "replicas": 3,
            "updatedReplicas": 3,
            "readyReplicas": 1,
            "availableReplicas": 1,
            "unavailableReplicas": 2,
            "conditions": [
                {"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"},
            ],
        },
    }
    result = k8s_tools._summarize_deployment(manifest)
    assert result == {
        "name": "backend-deployment",
        "namespace": "dev",
        "labels": {"app": "backend"},
        "ownerReferences": [],
        "desiredReplicas": 3,
        "replicas": 3,
        "updatedReplicas": 3,
        "readyReplicas": 1,
        "availableReplicas": 1,
        "unavailableReplicas": 2,
        "conditions": [{"type": "Available", "status": "False", "reason": "MinimumReplicasUnavailable"}],
    }
    assert "spec" not in result


def test_summarize_deployment_defaults_missing_fields():
    result = k8s_tools._summarize_deployment({"metadata": {"name": "d"}})
    assert result["desiredReplicas"] is None
    assert result["conditions"] == []


def test_summarize_replicaset_extracts_replica_health():
    manifest = {
        "metadata": {"name": "backend-57b96fdb87", "namespace": "dev", "labels": {}},
        "spec": {"replicas": 3},
        "status": {"replicas": 3, "readyReplicas": 1, "availableReplicas": 1},
    }
    result = k8s_tools._summarize_replicaset(manifest)
    assert result == {
        "name": "backend-57b96fdb87",
        "namespace": "dev",
        "labels": {},
        "ownerReferences": [],
        "desiredReplicas": 3,
        "replicas": 3,
        "readyReplicas": 1,
        "availableReplicas": 1,
    }


def test_summarize_statefulset_extracts_replica_health_and_service_name():
    manifest = {
        "metadata": {"name": "cache", "namespace": "dev", "labels": {}},
        "spec": {"replicas": 3, "serviceName": "cache-headless"},
        "status": {"replicas": 3, "readyReplicas": 3, "currentReplicas": 3, "updatedReplicas": 3},
    }
    result = k8s_tools._summarize_statefulset(manifest)
    assert result == {
        "name": "cache",
        "namespace": "dev",
        "labels": {},
        "ownerReferences": [],
        "serviceName": "cache-headless",
        "desiredReplicas": 3,
        "replicas": 3,
        "readyReplicas": 3,
        "currentReplicas": 3,
        "updatedReplicas": 3,
    }


def test_summarize_daemonset_extracts_scheduling_health():
    manifest = {
        "metadata": {"name": "log-agent", "namespace": "kube-system", "labels": {}},
        "status": {
            "desiredNumberScheduled": 3,
            "currentNumberScheduled": 3,
            "numberReady": 2,
            "numberAvailable": 2,
            "numberUnavailable": 1,
        },
    }
    result = k8s_tools._summarize_daemonset(manifest)
    assert result == {
        "name": "log-agent",
        "namespace": "kube-system",
        "labels": {},
        "ownerReferences": [],
        "desiredNumberScheduled": 3,
        "currentNumberScheduled": 3,
        "numberReady": 2,
        "numberAvailable": 2,
        "numberUnavailable": 1,
    }


# ------------------------------------------------------------------
# networking summarizers
# ------------------------------------------------------------------

def test_summarize_service_extracts_type_selector_ports_and_load_balancer():
    manifest = {
        "metadata": {"name": "backend-service", "namespace": "dev", "labels": {}},
        "spec": {
            "type": "ClusterIP",
            "clusterIP": "10.96.0.5",
            "selector": {"app": "backend"},
            "ports": [{"port": 80, "targetPort": 8000, "protocol": "TCP"}],
        },
        "status": {"loadBalancer": {}},
    }
    result = k8s_tools._summarize_service(manifest)
    assert result == {
        "name": "backend-service",
        "namespace": "dev",
        "labels": {},
        "type": "ClusterIP",
        "clusterIP": "10.96.0.5",
        "selector": {"app": "backend"},
        "ports": [{"port": 80, "targetPort": 8000, "protocol": "TCP"}],
        "loadBalancer": {},
    }


def test_summarize_service_defaults_missing_fields():
    result = k8s_tools._summarize_service({"metadata": {"name": "s"}})
    assert result["selector"] == {}
    assert result["ports"] == []
    assert result["loadBalancer"] == {}


def test_summarize_networkpolicy_extracts_selector_types_and_rules():
    manifest = {
        "metadata": {"name": "deny-all-except-frontend", "namespace": "dev", "labels": {}},
        "spec": {
            "podSelector": {"matchLabels": {"app": "backend"}},
            "policyTypes": ["Ingress"],
            "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": "frontend"}}}]}],
        },
    }
    result = k8s_tools._summarize_networkpolicy(manifest)
    assert result == {
        "name": "deny-all-except-frontend",
        "namespace": "dev",
        "labels": {},
        "podSelector": {"matchLabels": {"app": "backend"}},
        "policyTypes": ["Ingress"],
        "ingress": [{"from": [{"podSelector": {"matchLabels": {"app": "frontend"}}}]}],
        "egress": [],
    }


# ------------------------------------------------------------------
# storage summarizers
# ------------------------------------------------------------------

def test_summarize_pvc_extracts_phase_and_storage_request():
    manifest = {
        "metadata": {"name": "backend-data", "namespace": "dev", "labels": {}},
        "spec": {
            "storageClassName": "standard",
            "accessModes": ["ReadWriteOnce"],
            "resources": {"requests": {"storage": "10Gi"}},
        },
        "status": {"phase": "Pending", "capacity": {}},
    }
    result = k8s_tools._summarize_pvc(manifest)
    assert result == {
        "name": "backend-data",
        "namespace": "dev",
        "labels": {},
        "phase": "Pending",
        "storageClassName": "standard",
        "accessModes": ["ReadWriteOnce"],
        "requestedStorage": "10Gi",
        "capacity": {},
    }


def test_summarize_pvc_defaults_missing_fields():
    result = k8s_tools._summarize_pvc({"metadata": {"name": "p"}})
    assert result["phase"] is None
    assert result["requestedStorage"] is None
    assert result["accessModes"] == []


def test_summarize_pv_extracts_phase_capacity_and_claim_ref():
    manifest = {
        "metadata": {"name": "pv-0001", "labels": {}},
        "spec": {
            "capacity": {"storage": "10Gi"},
            "storageClassName": "standard",
            "persistentVolumeReclaimPolicy": "Delete",
            "claimRef": {"namespace": "dev", "name": "backend-data"},
        },
        "status": {"phase": "Bound"},
    }
    result = k8s_tools._summarize_pv(manifest)
    assert result == {
        "name": "pv-0001",
        "labels": {},
        "phase": "Bound",
        "capacity": {"storage": "10Gi"},
        "storageClassName": "standard",
        "reclaimPolicy": "Delete",
        "claimRef": {"namespace": "dev", "name": "backend-data"},
    }


def test_summarize_pv_defaults_missing_claim_ref():
    result = k8s_tools._summarize_pv({"metadata": {"name": "pv-0002"}})
    assert result["claimRef"] == {"namespace": None, "name": None}


# ------------------------------------------------------------------
# config/limits summarizers
# ------------------------------------------------------------------

def test_summarize_configmap_extracts_data_with_value_truncation():
    manifest = {
        "metadata": {"name": "backend-config", "namespace": "dev", "labels": {}},
        "data": {"DOWNSTREAM_URL": "http://cache-service:6379", "BIG": "x" * 300},
    }
    result = k8s_tools._summarize_configmap(manifest)
    assert result["dataKeys"] == ["DOWNSTREAM_URL", "BIG"]
    assert result["data"]["DOWNSTREAM_URL"] == "http://cache-service:6379"
    assert result["data"]["BIG"] == "x" * 200 + "...[truncated]"


def test_summarize_configmap_defaults_missing_data():
    result = k8s_tools._summarize_configmap({"metadata": {"name": "c"}})
    assert result["dataKeys"] == []
    assert result["data"] == {}


def test_summarize_secret_never_includes_values():
    manifest = {
        "metadata": {"name": "backend-tls", "namespace": "dev", "labels": {}},
        "type": "kubernetes.io/tls",
        "data": {"tls.crt": "base64stuff==", "tls.key": "base64secret=="},
    }
    result = k8s_tools._summarize_secret(manifest)
    assert result == {
        "name": "backend-tls",
        "namespace": "dev",
        "labels": {},
        "type": "kubernetes.io/tls",
        "dataKeys": ["tls.crt", "tls.key"],
    }
    assert "data" not in result
    assert "base64secret==" not in str(result)


def test_summarize_resourcequota_extracts_used_vs_hard():
    manifest = {
        "metadata": {"name": "dev-quota", "namespace": "dev", "labels": {}},
        "status": {
            "hard": {"pods": "10", "requests.cpu": "4"},
            "used": {"pods": "10", "requests.cpu": "2"},
        },
    }
    result = k8s_tools._summarize_resourcequota(manifest)
    assert result == {
        "name": "dev-quota",
        "namespace": "dev",
        "labels": {},
        "hard": {"pods": "10", "requests.cpu": "4"},
        "used": {"pods": "10", "requests.cpu": "2"},
    }


# ------------------------------------------------------------------
# controller-status summarizers
# ------------------------------------------------------------------

def test_summarize_hpa_extracts_replica_counts_and_conditions():
    manifest = {
        "metadata": {"name": "backend-hpa", "namespace": "dev", "labels": {}},
        "spec": {
            "scaleTargetRef": {"kind": "Deployment", "name": "backend-deployment"},
            "minReplicas": 1,
            "maxReplicas": 5,
        },
        "status": {
            "currentReplicas": 3,
            "desiredReplicas": 3,
            "conditions": [{"type": "ScalingActive", "status": "False", "reason": "FailedGetResourceMetric"}],
        },
    }
    result = k8s_tools._summarize_hpa(manifest)
    assert result == {
        "name": "backend-hpa",
        "namespace": "dev",
        "labels": {},
        "scaleTargetRef": {"kind": "Deployment", "name": "backend-deployment"},
        "minReplicas": 1,
        "maxReplicas": 5,
        "currentReplicas": 3,
        "desiredReplicas": 3,
        "conditions": [{"type": "ScalingActive", "status": "False", "reason": "FailedGetResourceMetric"}],
    }


def test_summarize_job_extracts_completion_counts_and_conditions():
    manifest = {
        "metadata": {"name": "backup-job", "namespace": "dev", "labels": {}},
        "spec": {"completions": 1, "backoffLimit": 3},
        "status": {
            "active": 0,
            "succeeded": 0,
            "failed": 3,
            "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
        },
    }
    result = k8s_tools._summarize_job(manifest)
    assert result == {
        "name": "backup-job",
        "namespace": "dev",
        "labels": {},
        "ownerReferences": [],
        "completions": 1,
        "backoffLimit": 3,
        "active": 0,
        "succeeded": 0,
        "failed": 3,
        "conditions": [{"type": "Failed", "status": "True", "reason": "BackoffLimitExceeded"}],
    }


def test_summarize_job_defaults_missing_fields():
    result = k8s_tools._summarize_job({"metadata": {"name": "j"}})
    assert result["active"] is None
    assert result["conditions"] == []


# ------------------------------------------------------------------
# _summarize_node
# ------------------------------------------------------------------

def test_summarize_node_extracts_ready_condition_and_schedulability():
    manifest = {
        "metadata": {"name": "minikube", "labels": {"kubernetes.io/hostname": "minikube"}},
        "spec": {"unschedulable": False},
        "status": {
            "conditions": [
                {"type": "MemoryPressure", "status": "False"},
                {"type": "Ready", "status": "True"},
            ]
        },
    }
    result = k8s_tools._summarize_node(manifest)
    assert result == {
        "name": "minikube",
        "labels": {"kubernetes.io/hostname": "minikube"},
        "ready": "True",
        "unschedulable": False,
    }


def test_summarize_node_defaults_when_no_ready_condition_present():
    result = k8s_tools._summarize_node({"metadata": {"name": "n"}})
    assert result["ready"] is None
    assert result["unschedulable"] is False


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
    assert result == manifest


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


def test_get_resource_strips_last_applied_configuration_and_bookkeeping(mock_run):
    manifest = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "backend-deployment",
            "namespace": "dev",
            "resourceVersion": "133392",
            "uid": "21dbb296-2146-415f-b21c-34e546c82e81",
            "generation": 12,
            "annotations": {
                "deployment.kubernetes.io/revision": "11",
                "kubectl.kubernetes.io/last-applied-configuration": '{"apiVersion":"apps/v1"}',
            },
        },
        "spec": {"replicas": 3},
        "status": {"readyReplicas": 1},
    }
    mock_run.return_value = make_completed_process(stdout=json.dumps(manifest))

    result = k8s_tools.get_resource(ResourceKind.DEPLOYMENT, "backend-deployment", "dev")

    assert "resourceVersion" not in result["metadata"]
    assert "uid" not in result["metadata"]
    assert "generation" not in result["metadata"]
    assert "kubectl.kubernetes.io/last-applied-configuration" not in result["metadata"]["annotations"]
    assert result["metadata"]["annotations"]["deployment.kubernetes.io/revision"] == "11"
    assert result["spec"] == {"replicas": 3}
    assert result["status"] == {"readyReplicas": 1}


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
