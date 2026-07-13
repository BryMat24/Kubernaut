import os

from fastmcp import FastMCP

from k8s_tools import mcp as k8s_tools_server

app = FastMCP("k8s-mcp-server")
app.mount(k8s_tools_server)

if __name__ == "__main__":
    port = int(os.getenv("PORT", "8080"))
    app.run(transport="http", host="0.0.0.0", port=port)
