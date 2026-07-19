import asyncio
from agents import DiagnosisAgent
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools
from utils import create_llm_model

llm = create_llm_model("qwen/qwen3-coder-next")

async def test_diagnosis():
    k8s_tools = await get_k8s_mcp_tools()
    promql_tools = await get_promql_mcp_tools()
    diagnosis_agent = DiagnosisAgent(llm, k8s_tools + promql_tools)
    result = await diagnosis_agent.ainvoke({
        "messages": [],
        "query": "what the application in the dev namespace is not working?",
        "iteration_count": 0,
    })
    print(result)

if __name__ == "__main__":
    asyncio.run(test_diagnosis())