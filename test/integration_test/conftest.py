import asyncio
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
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools, get_loki_mcp_tools

load_dotenv()

REPO_ROOT = Path(__file__).parents[2]
K8S_MCP_SERVER_DIR = REPO_ROOT / "mcp_servers" / "k8s_mcp_server"
PROMETHEUS_MCP_SERVER_DIR = REPO_ROOT / "mcp_servers" / "prometheus_mcp_server"
LOKI_MCP_SERVER_DIR = REPO_ROOT / "mcp_servers" / "loki_mcp_server"

SCOPE_TOOL_NAMES = {
    "list_namespaces",
    "list_resources",
    "get_events",
    "top_pods",
    "top_nodes",
    "rollout_status",
    "application_health",
    "error_rate",
    "cpu_saturation",
    "error_logs",
}


@pytest.fixture(scope="session")
def anyio_backend():
    return "asyncio"


def _start_mcp_server(server_dir: Path, get_tools) -> subprocess.Popen:
    proc = subprocess.Popen(
        [sys.executable, "server.py"],
        cwd=server_dir,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"MCP server in {server_dir} exited early:\n{output}")
        try:
            asyncio.run(get_tools())
            return proc
        except Exception:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"MCP server in {server_dir} did not become reachable within 20s")


def _start_port_forward(namespace: str, target: str, local_port: int, remote_port: int) -> subprocess.Popen:
    proc = subprocess.Popen(
        ["kubectl", "port-forward", "-n", namespace, target, f"{local_port}:{remote_port}"],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    deadline = time.time() + 20
    while time.time() < deadline:
        if proc.poll() is not None:
            output = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"port-forward for {target} exited early:\n{output}")
        try:
            with __import__("socket").create_connection(("localhost", local_port), timeout=1):
                return proc
        except OSError:
            time.sleep(0.5)
    proc.terminate()
    raise RuntimeError(f"port-forward for {target} did not become reachable within 20s")


@pytest.fixture(scope="session")
def mcp_servers():
    # Prometheus/Loki MCP tools hit http://localhost:9090 / :3100 directly (they run outside
    # the cluster), so the real in-cluster services must be port-forwarded first -- without
    # this, error_rate/cpu_saturation/error_logs/recent_logs would silently return empty
    # results for every scenario that needs them (resource_governance, network).
    procs = [
        _start_port_forward("dev-monitoring", "svc/prometheus-kube-prometheus-prometheus", 9090, 9090),
        _start_port_forward("dev-monitoring", "svc/loki", 3100, 3100),
        _start_mcp_server(K8S_MCP_SERVER_DIR, get_k8s_mcp_tools),
        _start_mcp_server(PROMETHEUS_MCP_SERVER_DIR, get_promql_mcp_tools),
        _start_mcp_server(LOKI_MCP_SERVER_DIR, get_loki_mcp_tools),
    ]
    try:
        yield
    finally:
        for proc in procs:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()


@pytest.fixture(scope="session")
def kubernetes_agent(mcp_servers):
    async def _build() -> DiagnosisAgent:
        llm = ChatOpenRouter(
            model="qwen/qwen3-coder-next",
            temperature=0.1,
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        utility_llm = ChatOpenRouter(
            model="qwen/qwen3-coder-next",
            temperature=0.1,
            api_key=os.getenv("OPENROUTER_API_KEY"),
        )
        k8s_tools = await get_k8s_mcp_tools()
        promql_tools = await get_promql_mcp_tools()
        loki_tools = await get_loki_mcp_tools()
        all_tools = k8s_tools + promql_tools + loki_tools
        scope_tools = [tool for tool in all_tools if tool.name in SCOPE_TOOL_NAMES]
        return DiagnosisAgent(
            llm=llm,
            investigate_tools=all_tools,
            scope_tools=scope_tools,
            utility_llm=utility_llm,
        )

    return asyncio.run(_build())


@pytest.fixture(scope="session")
def judge() -> ScenarioEvaluator:
    llm = ChatOpenRouter(
        model="openai/gpt-5.3-codex",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY"),
    )
    return ScenarioEvaluator(llm)
