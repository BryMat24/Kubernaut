import asyncio
from agents import DiagnosisAgent, PlannerAgent, RemediationAgent
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools
from models import RemediationPlan, RemediationStep
from utils import create_llm_model
from tools.file_tools import (
    list_files_in_directory,
    read_file_content,
    grep,
    find,
    edit_file,
    write_file
)

llm = create_llm_model("qwen/qwen3-coder-next")
judge = create_llm_model("openai/gpt-5.3-codex")

async def test_diagnosis():
    k8s_tools = await get_k8s_mcp_tools()
    promql_tools = await get_promql_mcp_tools()
    diagnosis_agent = DiagnosisAgent(llm, k8s_tools + promql_tools)
    result = await diagnosis_agent.ainvoke({
        "messages": [],
        "query": "what the application in the dev namespace is not working?",
        "iteration_count": 0,
    })
    return result

async def test_planner(diagnosis_result):
    tools = [
        find,
        grep,
        list_files_in_directory,
        read_file_content
    ]
    planner_agent = PlannerAgent(llm, tools)
    result = await planner_agent.ainvoke({
        "diagnosis_result": diagnosis_result,
        "iteration_count": 0,
        "repo_url": "https://github.com/BryMat24/Kubernaut-Gitops.git"
    })
    return result

async def test_remediation(plan):
    file_tools = [
        list_files_in_directory,
        read_file_content,
        grep,
        find,
        edit_file,
        write_file,
    ]
    remediation_agent = RemediationAgent(llm, file_tools, llm)
    result = await remediation_agent.ainvoke({
        "plan": plan,
        "iteration_count": 0,
        "repo_url": "https://github.com/BryMat24/Kubernaut-Gitops.git"
    })
    return result

async def test():
    diagnosis_result = await test_diagnosis()
    with open("diagnosis_result.txt", "w") as f:
        f.write(diagnosis_result["diagnosis_result"].model_dump_json(indent=2))

    plan_result = await test_planner(diagnosis_result["diagnosis_result"])
    with open("plan_result.txt", "w") as f:
        f.write(plan_result["plan"].model_dump_json(indent=2))

    remediation_result = await test_remediation(plan_result["plan"])
    with open("output_remediation.txt", "w") as f:
        f.write(remediation_result["pr_url"])

if __name__ == "__main__":
    asyncio.run(test())