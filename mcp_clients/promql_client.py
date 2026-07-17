from langchain_mcp_adapters.client import MultiServerMCPClient
import os

PROMETHEUS_MCP_SERVER_URL = os.getenv("PROMETHEUS_MCP_SERVER_URL", "http://localhost:8081/mcp")

def get_mcp_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        "promql": {
            "url": PROMETHEUS_MCP_SERVER_URL,
            "transport": "streamable_http",
        }
    })

async def get_mcp_tools() -> list: 
    client = get_mcp_client()
    return await client.get_tools()