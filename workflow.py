import asyncio
import os

from agents import DiagnosisAgent
from langchain_mcp_adapters.client import MultiServerMCPClient
from langchain_openrouter import ChatOpenRouter
from dotenv import load_dotenv

load_dotenv()

K8S_MCP_SERVER_URL = os.getenv("K8S_MCP_SERVER_URL", "http://localhost:8000/mcp")


async def main() -> None:
    llm = ChatOpenRouter(
        model="qwen/qwen3-coder-next",
        temperature=0.1,
        api_key=os.getenv("OPENROUTER_API_KEY")
    )

    client = MultiServerMCPClient({
        "k8s": {
            "url": K8S_MCP_SERVER_URL,
            "transport": "streamable_http",
        }
    })
    tools = await client.get_tools()

    diagnosis_agent = DiagnosisAgent(llm, tools)
    result = await diagnosis_agent.ainvoke({
        "messages": [],
        "query": "What is the rollout status of the deployment in the default namespace",
        "iteration_count": 0
    })

    print(result["messages"][-1].content)


if __name__ == "__main__":
    asyncio.run(main())
