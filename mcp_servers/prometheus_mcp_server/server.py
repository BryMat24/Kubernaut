import os

from fastmcp import FastMCP

from promql_tools import mcp as promql_tools_server

app = FastMCP("prometheus-mcp-server")
app.mount(promql_tools_server)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8081"))
    app.run(transport="http", host="0.0.0.0", port=port)
