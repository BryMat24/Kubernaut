"""
Category 8 (Secret / ConfigMap edge cases) integration tests for KubernetesAgent, per
PLAN.json.

These run against a real, live Kubernetes cluster (kubectl's current context) and make
real LLM calls -- they are not part of the default `pytest test/` run. Run explicitly with:

    pytest test/integration_test/k8s_agent/test_secret_configmap_failure.py -m integration
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

from agents import KubernetesAgent
from evaluator import ScenarioEvaluator
from langchain_openrouter import ChatOpenRouter
from mcp_clients.k8s_client import get_mcp_tools

load_dotenv()

pytestmark = pytest.mark.integration

CASES_DIR = Path(__file__).parent / "cases" / "secret_configmap_failures"
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
    llm = ChatOpenRouter(
        model="openai/gpt-5.3-codex",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    return ScenarioEvaluator(llm)


@pytest.fixture
def secret_wrong_key_reference_scenario():
    namespace = "test-secret-wrong-key-reference"
    _kubectl("create", "namespace", namespace)
    _apply_manifest("secret-wrong-key-reference", namespace)
    try:
        yield namespace
    finally:
        _kubectl("delete", "namespace", namespace, "--wait=true", check=False)


@pytest.mark.anyio
async def test_secret_wrong_key_reference(kubernetes_agent, judge, secret_wrong_key_reference_scenario):
    namespace = secret_wrong_key_reference_scenario
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": f"The workload sample-app in namespace {namespace} is not working as expected. Diagnose the root cause.",
        "iteration_count": 0,
    })
    diagnosis = result["messages"][-1].content

    expected = _load_expected_answer("secret-wrong-key-reference")
    verdict = judge.evaluate(expected, diagnosis)
    assert verdict.correct, (
        f"{verdict.reasoning}\nmissing_evidence={verdict.missing_evidence}\n\ndiagnosis was:\n{diagnosis}"
    )
