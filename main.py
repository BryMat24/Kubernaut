"""Standalone driver to trigger RemediationAgent directly with a hardcoded state,
bypassing diagnosis/planning/human-approval -- for debugging the agent's own
reasoning/tool loop (e.g. why completed_steps isn't marked) against a real repo
clone and real LLM calls, without needing the API server or a prior chat.

Usage: python3 main.py
"""

import asyncio
import logging

from models import RemediationPlan

from graph.builder import init_remediation_agent

logging.basicConfig(level=logging.INFO, format="%(message)s")

# Captured verbatim from a real orchestrator interrupt payload -- only "plan" and
# "repo_url" are consumed below; "query"/"diagnosis_result"/"approved" are kept here
# for context/traceability back to the scenario being debugged.
HARDCODED_STATE = {
    "approved": True,
    "diagnosis_result": {
        "diagnosis_success": True,
        "requires_remediation": True,
        "root_cause": "Memory leak in backend application due to MEMORY_LEAK_MB_PER_SEC=50 environment variable causing OOMKilled events and pod crashes",
        "summary": "The application in the dev namespace is not working due to critical memory issues in the backend deployment. All three backend pods are experiencing repeated crashes due to Out of Memory (OOMKilled) conditions. The root cause is a memory leak in the backend application, intentionally caused by the environment variable MEMORY_LEAK_MB_PER_SEC set to 50, which leaks 50MB of memory per second. Two of the three backend pods are in CrashLoopBackOff state, with only one pod currently running and ready. The deployment status shows 'MinimumReplicasUnavailable', meaning the application doesn't have enough healthy replicas to serve traffic properly. The frontend and cache services are healthy, but the backend is a critical component that's failing.",
    },
    "plan": {
        "planning_success": True,
        "steps": [
            {
                "description": "Remove the `MEMORY_LEAK_MB_PER_SEC` environment variable from the backend container's `env` list. This variable is the root cause of the memory leak and pod crashes.",
                "file_path": "app/backend/base/deployment.yaml",
                "new_content": "                  env:\n                      - name: DOWNSTREAM_URL\n                        valueFrom:\n                            configMapKeyRef:\n                                name: backend-config\n                                key: DOWNSTREAM_URL",
                "step_number": 1,
            }
        ],
        "summary": "Remove the memory leak environment variable from the backend deployment to stop the OOMKilled crashes and restore the deployment to the desired 3 replicas.",
    },
    "query": "the application in the dev namespace is not working, can you diagnose and fix it",
    "repo_url": "https://github.com/BryMat24/Kubernaut-Gitops.git",
}


async def main() -> None:
    plan = RemediationPlan.model_validate(HARDCODED_STATE["plan"])
    repo_url = HARDCODED_STATE["repo_url"]

    remediation_agent = await init_remediation_agent()

    result = await remediation_agent.ainvoke({
        "messages": [],
        "plan": plan,
        "iteration_count": 0,
        "eval_passed": False,
        "eval_reasoning": "",
        "pr_url": "",
        "repo_url": repo_url,
        "bare_path": "",
        "repo_path": "",
        "branch": "",
    })

    print("\n=== final state ===")
    print("completed_steps:", result.get("completed_steps"))
    print("eval_passed:", result.get("eval_passed"))
    print("eval_reasoning:", result.get("eval_reasoning"))
    print("pr_url:", result.get("pr_url"))


if __name__ == "__main__":
    asyncio.run(main())
