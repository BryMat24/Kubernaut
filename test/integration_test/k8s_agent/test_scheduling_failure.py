"""
Category 1 (Scheduling failures) integration tests for KubernetesAgent, per PLAN.json.

These run against a real, live Kubernetes cluster (kubectl's current context) and make
real LLM calls -- they are not part of the default `pytest test/` run. Run explicitly with:

    pytest test/integration_test/k8s_agent/test_scheduling_failure.py -m integration

pending-nodeselector-mismatch is intentionally not covered here: PLAN.json marks it
priority "skip" (documented gap only, no test built for the 2-3 week timeline).
"""
import asyncio
import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

from agents import KubernetesAgent
from evaluator import ScenarioEvaluator
from langchain_openrouter import ChatOpenRouter
from mcp_clients.k8s_client import get_mcp_tools

load_dotenv()

pytestmark = pytest.mark.integration

CASES_DIR = Path(__file__).parent / "cases" / "scheduling_failures"
NODE_NAME = "kind-control-plane"
MCP_SERVER_DIR = Path(__file__).parents[3] / "mcp_servers" / "k8s_mcp_server"


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


def _kubectl(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["kubectl", *args], capture_output=True, text=True, check=check)


def _load_expected_answer(scenario_id: str) -> dict:
    path = CASES_DIR / scenario_id / "expected_answer.json"
    return json.loads(path.read_text())


def _apply_manifest(scenario_id: str, namespace: str) -> None:
    manifest = CASES_DIR / scenario_id / "manifest.yaml"
    _kubectl("apply", "-f", str(manifest), "-n", namespace)


@pytest.fixture(scope="session")
def mcp_server():
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=MCP_SERVER_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    try:
        deadline = time.time() + 20
        reachable = False
        while time.time() < deadline:
            if proc.poll() is not None:
                output = proc.stdout.read() if proc.stdout else ""
                raise RuntimeError(f"MCP server exited early:\n{output}")
            try:
                asyncio.run(get_mcp_tools())
                reachable = True
                break
            except Exception:
                time.sleep(0.5)
        if not reachable:
            raise RuntimeError("MCP server did not become reachable within 20s")
        yield
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()


@pytest.fixture(scope="session")
def kubernetes_agent(mcp_server):
    async def _build() -> KubernetesAgent:
        llm = ChatOpenRouter(
            model="qwen/qwen3-coder-next",
            temperature=0.1,
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        tools = await get_mcp_tools()
        return KubernetesAgent(llm, tools)

    return asyncio.run(_build())


@pytest.fixture(scope="session")
def judge() -> ScenarioEvaluator:
    # A distinct, more reliable-at-structured-output model from the agent's own model,
    # matching the same judge/reasoning-model split used by RemediationAgent's DiffEvaluator.
    llm = ChatOpenRouter(
        model="openai/gpt-5.3-codex",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    return ScenarioEvaluator(llm)


@pytest.fixture
def pending_insufficient_resources_scenario():
    namespace = "test-pending-insufficient-resources"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("pending-insufficient-resources", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.fixture
def pending_taint_no_toleration_scenario():
    namespace = "test-pending-taint-no-toleration"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("pending-taint-no-toleration", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)
        _kubectl(
            "taint", "node", NODE_NAME,
            "scenario.kubernaut.io/dedicated:NoSchedule-",
            check=False,
        )


def _pressure_conditions_patch(pressure: bool) -> str:
    return json.dumps({
        "status": {
            "conditions": [
                {
                    "type": "DiskPressure",
                    "status": "True" if pressure else "False",
                    "reason": "ScenarioInjected" if pressure else "KubeletHasNoDiskPressure",
                    "message": "Injected by integration test" if pressure else "kubelet has no disk pressure",
                },
                {
                    "type": "MemoryPressure",
                    "status": "True" if pressure else "False",
                    "reason": "ScenarioInjected" if pressure else "KubeletHasSufficientMemory",
                    "message": "Injected by integration test" if pressure else "kubelet has sufficient memory available",
                },
            ]
        }
    })


@pytest.fixture
def node_disk_memory_pressure_scenario():
    # The real kubelet reports its own (healthy) node status roughly every 10s, which
    # would silently overwrite a one-shot status patch before the agent gets around to
    # querying it (confirmed: patched condition reverted within ~8s in manual testing).
    # A background thread keeps re-asserting the injected condition every 3s -- well
    # inside kubelet's reconciliation window -- for the duration of the test.
    #
    # The condition must be injected BEFORE the pod is applied, not after: Kubernetes'
    # node lifecycle controller auto-applies NoSchedule taints (node.kubernetes.io/
    # disk-pressure, node.kubernetes.io/memory-pressure) the moment the condition flips
    # True (confirmed via manual test), which is what actually keeps a newly-created pod
    # Pending on this single-node cluster. Applying the manifest first let the pod
    # schedule onto the still-healthy node before pressure ever kicked in, so it ended up
    # Running -- there was no Pending pod for the agent to find, which is why a query
    # claiming "pods are stuck Pending" sent it wandering through discovery indefinitely.
    namespace = "test-node-disk-memory-pressure"
    _kubectl("create", "namespace", namespace)

    stop_event = threading.Event()

    def _keep_patching() -> None:
        while not stop_event.is_set():
            _kubectl(
                "patch", "node", NODE_NAME,
                "--subresource=status", "--type=merge",
                "-p", _pressure_conditions_patch(pressure=True),
                check=False,
            )
            stop_event.wait(3)

    _kubectl(
        "patch", "node", NODE_NAME,
        "--subresource=status", "--type=merge",
        "-p", _pressure_conditions_patch(pressure=True),
    )
    patcher = threading.Thread(target=_keep_patching, daemon=True)
    patcher.start()

    _apply_manifest("node-disk-memory-pressure", namespace)

    try:
        yield namespace
    finally:
        stop_event.set()
        patcher.join(timeout=5)
        _kubectl(
            "patch", "node", NODE_NAME,
            "--subresource=status", "--type=merge",
            "-p", _pressure_conditions_patch(pressure=False),
            check=False,
        )
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_pending_insufficient_resources(kubernetes_agent, judge, pending_insufficient_resources_scenario):
    namespace = pending_insufficient_resources_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The pod sample-app in namespace {namespace} is not working. Diagnose the root cause.",
        "iteration_count": 0,
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("pending-insufficient-resources")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )


@pytest.mark.anyio
async def test_pending_taint_no_toleration(kubernetes_agent, judge, pending_taint_no_toleration_scenario):
    namespace = pending_taint_no_toleration_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The pod sample-app in namespace {namespace} is not working. Please diagnose the issue",
        "iteration_count": 0,
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("pending-taint-no-toleration")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )


@pytest.mark.anyio
async def test_node_disk_memory_pressure(kubernetes_agent, judge, node_disk_memory_pressure_scenario):
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"Pods are stuck in Pending state and not getting scheduled. Investigate and diagnose.",
        "iteration_count": 0,
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("node-disk-memory-pressure")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
