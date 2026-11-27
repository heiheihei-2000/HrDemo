import json
import sys
from contextlib import AsyncExitStack

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import get_default_environment, stdio_client

from .config import MCP_SERVER_DIR

SERVERS = {
    "boss": MCP_SERVER_DIR / "boss_mock_server.py",
    "ledger": MCP_SERVER_DIR / "ledger_server.py",
}


class ToolError(RuntimeError):
    pass


class MCPHub:
    def __init__(self) -> None:
        self._stack: AsyncExitStack | None = None
        self.sessions: dict[str, ClientSession] = {}

    async def connect(self) -> list[str]:
        await self.close()
        self._stack = AsyncExitStack()
        env = {**get_default_environment(), "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        try:
            for name, script in SERVERS.items():
                params = StdioServerParameters(command=sys.executable, args=[str(script)], env=env)
                read, write = await self._stack.enter_async_context(stdio_client(params))
                session = await self._stack.enter_async_context(ClientSession(read, write))
                await session.initialize()
                self.sessions[name] = session
        except BaseException:
            await self.close()
            raise
        return list(self.sessions)

    async def close(self) -> None:
        if self._stack is not None:
            await self._stack.aclose()
        self._stack = None
        self.sessions = {}

    async def call(self, server: str, tool: str, args: dict | None = None) -> dict:
        if server not in self.sessions:
            raise ToolError(f"MCP 服务 {server} 未连接")
        res = await self.sessions[server].call_tool(tool, args or {})
        text = "\n".join(c.text for c in res.content if getattr(c, "type", "") == "text")
        if res.isError:
            raise ToolError(f"{server}.{tool} 调用失败：{text}")
        if res.structuredContent is not None:
            return res.structuredContent
        return json.loads(text) if text else {}
