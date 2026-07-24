"""Standalone driver to trigger DiagnosisAgent directly with a hardcoded query, for
debugging the agent's own investigation loop (e.g. tool selection, token cost from
HistoryCompactor) against a real cluster and real LLM calls, without needing the API
server, planning, or human approval.

Usage: python3 test_diagnosis_agent.py
"""

import asyncio
import logging
from IPython.display import Image, display

from graph.builder import init_diagnosis_agent

logging.basicConfig(level=logging.INFO, format="%(message)s")

# repo_url is kept here for context/traceability back to the scenario being debugged
# (it's what RemediationAgent would act on next if this diagnosis called for a fix) --
# DiagnosisAgent itself only consumes "query", it never touches the GitOps repo.
HARDCODED_STATE = {
    "query": "the application in the dev namespace is not working, can you diagnose and fix it",
    "repo_url": "https://github.com/BryMat24/Kubernaut-Gitops.git",
}


async def main() -> None:
    query = HARDCODED_STATE["query"]

    await init_diagnosis_agent()
    diagnosis_agent = await init_diagnosis_agent()

    result = await diagnosis_agent.ainvoke({
        "messages": [],
        "query": query,
        "iteration_count": 0,
    })

    diagnosis_result = result.get("diagnosis_result")
    print("\n=== final state ===")
    print("diagnosis_success:", getattr(diagnosis_result, "diagnosis_success", None))
    print("requires_remediation:", getattr(diagnosis_result, "requires_remediation", None))
    print("root_cause:", getattr(diagnosis_result, "root_cause", None))
    print("summary:", getattr(diagnosis_result, "summary", None))


if __name__ == "__main__":
    asyncio.run(main())
