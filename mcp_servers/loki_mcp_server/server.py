import os

from fastmcp import FastMCP

from loki_tools import mcp as loki_tools_server

app = FastMCP("loki-mcp-server")
app.mount(loki_tools_server)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8082"))
    app.run(transport="http", host="0.0.0.0", port=port)
