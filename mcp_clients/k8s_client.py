from langchain_mcp_adapters.client import MultiServerMCPClient
import os

K8S_MCP_SERVER_URL = os.getenv("K8S_MCP_SERVER_URL", "http://localhost:8080/mcp")

def get_mcp_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        "k8s": {
            "url": K8S_MCP_SERVER_URL,
            "transport": "streamable_http",
        }
    })

async def get_mcp_tools() -> list: 
    client = get_mcp_client()
    return await client.get_tools()