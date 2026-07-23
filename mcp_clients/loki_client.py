from langchain_mcp_adapters.client import MultiServerMCPClient
import os

LOKI_MCP_SERVER_URL = os.getenv("LOKI_MCP_SERVER_URL", "http://localhost:8082/mcp")

def get_mcp_client() -> MultiServerMCPClient:
    return MultiServerMCPClient({
        "loki": {
            "url": LOKI_MCP_SERVER_URL,
            "transport": "streamable_http",
        }
    })

async def get_mcp_tools() -> list:
    client = get_mcp_client()
    return await client.get_tools()
