import asyncio
import os

from agents import KubernetesAgent
from mcp_clients.k8s_client import get_mcp_tools
from langchain_openrouter import ChatOpenRouter
from dotenv import load_dotenv

load_dotenv()

async def main() -> None:
    llm = ChatOpenRouter(
        model="qwen/qwen3-coder-next",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY")
    )

    tools = await get_mcp_tools()

    kubernetes_agent = KubernetesAgent(llm, tools)
    result = await kubernetes_agent.ainvoke({
        "messages": [],
        "query": "What is the rollout status of the deployment in the default namespace",
        "iteration_count": 0
    })

    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
