"""
Category 4 (Autoscaling) integration tests for DiagnosisAgent, per PLAN.json.

These run against a real, live Kubernetes cluster (kubectl's current context) and make
real LLM calls -- they are not part of the default `pytest test/` run. Run explicitly with:

    pytest test/integration_test/k8s_agent/test_autoscaling.py -m integration
"""
import asyncio
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest
from dotenv import load_dotenv

from agents import DiagnosisAgent
from agents import ScenarioEvaluator
from langchain_openrouter import ChatOpenRouter
from mcp_clients.k8s_client import get_mcp_tools

load_dotenv()

pytestmark = pytest.mark.integration

CASES_DIR = Path(__file__).parent / "cases" / "autoscaling"
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


def _wait_for_hpa_current_replicas(
    namespace: str, hpa_name: str, target: int, timeout: float = 120.0
) -> None:
    """Poll until the HPA's status.currentReplicas reaches `target`, or timeout elapses.

    metrics-server needs time to scrape CPU usage for a brand-new pod, and the HPA
    controller itself only syncs on its own ~15s interval. Without this wait, the
    HPA can still be reporting no current metrics by the time the agent starts
    investigating, so the scenario's intended ScalingLimited/TooManyReplicas
    condition hasn't materialized yet.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        result = _kubectl(
            "get", "hpa", hpa_name, "-n", namespace,
            "-o", "jsonpath={.status.currentReplicas}",
            check=False,
        )
        if result.stdout.strip() == str(target):
            return
        time.sleep(3)


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
    async def _build() -> DiagnosisAgent:
        llm = ChatOpenRouter(
            model="qwen/qwen3-coder-next",
            temperature=0.1,
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        tools = await get_mcp_tools()
        return DiagnosisAgent(llm, tools)

    return asyncio.run(_build())


@pytest.fixture(scope="session")
def judge() -> ScenarioEvaluator:
    llm = ChatOpenRouter(
        model="openai/gpt-5.3-codex",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    return ScenarioEvaluator(llm)


@pytest.fixture
def hpa_metrics_unavailable_scenario():
    namespace = "test-hpa-metrics-unavailable"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("hpa-metrics-unavailable", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.fixture
def hpa_capped_at_max_replicas_scenario():
    namespace = "test-hpa-capped-at-max-replicas"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("hpa-capped-at-max-replicas", namespace)
    _wait_for_hpa_current_replicas(namespace, "sample-app-hpa", target=2)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_hpa_metrics_unavailable(kubernetes_agent, judge, hpa_metrics_unavailable_scenario):
    namespace = hpa_metrics_unavailable_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The workload sample-app in namespace {namespace} is not working as expected. Diagnose the root cause.",
        "iteration_count": 0,
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("hpa-metrics-unavailable")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )


@pytest.mark.anyio
async def test_hpa_capped_at_max_replicas(kubernetes_agent, judge, hpa_capped_at_max_replicas_scenario):
    namespace = hpa_capped_at_max_replicas_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The workload sample-app in namespace {namespace} is not working as expected. Diagnose the root cause.",
        "iteration_count": 0,
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("hpa-capped-at-max-replicas")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
