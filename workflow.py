import asyncio
import os

from agents import DiagnosisAgent
from mcp_clients import get_k8s_mcp_tools, get_promql_mcp_tools
from langchain_openrouter import ChatOpenRouter
from dotenv import load_dotenv

load_dotenv()

async def main() -> None:
    llm = ChatOpenRouter(
        model="qwen/qwen3-coder-next",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY")
    )

    k8s_tools = await get_k8s_mcp_tools()
    promql_tools = await get_promql_mcp_tools()

    diagnosis_agent = DiagnosisAgent(llm, k8s_tools + promql_tools)
    result = await diagnosis_agent.ainvoke({
        "messages": [],
        "query": "Why my application in dev namespace stopped working",
        "iteration_count": 0
    })

    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
