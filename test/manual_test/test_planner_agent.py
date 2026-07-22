"""Standalone driver to trigger DiagnosisAgent directly with a hardcoded query, for
debugging the agent's own investigation loop (e.g. tool selection, token cost from
HistoryCompactor) against a real cluster and real LLM calls, without needing the API
server, planning, or human approval.

Usage: python3 test_diagnosis_agent.py
"""

import asyncio
import logging

from graph.builder import init_planner_agent
from models import DiagnosisResult

logging.basicConfig(level=logging.INFO, format="%(message)s")


HARDCODED_STATE = {
    "repo_url": "https://github.com/BryMat24/Kubernaut-Gitops.git",
    "diagnosis_result": {
        "diagnosis_success": True,
        "requires_remediation": True,
        "root_cause": "Memory leak in backend application due to MEMORY_LEAK_MB_PER_SEC=50 environment variable causing OOMKilled events and pod crashes",
        "summary": "The application in the dev namespace is not working due to critical memory issues in the backend deployment. All three backend pods are experiencing repeated crashes due to Out of Memory (OOMKilled) conditions. The root cause is a memory leak in the backend application, intentionally caused by the environment variable MEMORY_LEAK_MB_PER_SEC set to 50, which leaks 50MB of memory per second. Two of the three backend pods are in CrashLoopBackOff state, with only one pod currently running and ready. The deployment status shows 'MinimumReplicasUnavailable', meaning the application doesn't have enough healthy replicas to serve traffic properly. The frontend and cache services are healthy, but the backend is a critical component that's failing.",
    },
}


async def main() -> None:
    planner_agent = await init_planner_agent()
    repo_url = HARDCODED_STATE["repo_url"]

    diagnosis_result = DiagnosisResult.model_validate(HARDCODED_STATE["diagnosis_result"])

    await planner_agent.ainvoke({
        "messages": [],
        "diagnosis_result": diagnosis_result,
        "repo_url": repo_url,
        "iteration_count": 0,
    })


if __name__ == "__main__":
    asyncio.run(main())