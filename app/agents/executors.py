"""Tool executors: the same three tools, called in-process or through the MCP server."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from contextlib import AsyncExitStack
from typing import Any, Protocol

from app.data_loader import PROJECT_ROOT
from app.tools.service import ToolError, ToolService


class ToolExecutor(Protocol):
    transport: str

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Return the tool's JSON result, or raise ToolError."""


class LocalToolExecutor:
    """Calls ToolService directly. Blocking work runs in a thread so the event loop stays free."""

    transport = "in-process"

    def __init__(self, service: ToolService | None = None) -> None:
        self.service = service or ToolService()

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.to_thread(self.service.call, tool, arguments)


class McpToolExecutor:
    """Calls the tools on a local MCP server started as a subprocess over stdio.

    Usage:
        async with McpToolExecutor() as executor:
            await executor.call("search_policy", {"query": "..."})
    """

    transport = "mcp-stdio"

    def __init__(self, server_args: list[str] | None = None, env: dict[str, str] | None = None) -> None:
        self.server_args = server_args or ["-m", "app.mcp_server"]
        self.env = env
        self._stack: AsyncExitStack | None = None
        self.client = None

    async def __aenter__(self) -> McpToolExecutor:
        from mcp import Client, StdioServerParameters

        params = StdioServerParameters(
            command=sys.executable,
            args=self.server_args,
            env={**os.environ, **(self.env or {})},
            cwd=str(PROJECT_ROOT),
        )
        self._stack = AsyncExitStack()
        self.client = await self._stack.enter_async_context(Client(params))
        return self

    async def __aexit__(self, *exc_info) -> None:
        if self._stack is not None:
            await self._stack.aclose()

    async def list_tools(self) -> list[Any]:
        return (await self.client.list_tools()).tools

    async def call(self, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
        result = await self.client.call_tool(tool, arguments)
        text = "".join(getattr(block, "text", "") for block in result.content)
        if result.is_error:
            # MCPServer prefixes the message ("Error executing tool x: {...}"); recover our JSON payload.
            try:
                err = json.loads(text[text.index("{") :])
            except (ValueError, json.JSONDecodeError):
                raise ToolError("TOOL_FAILURE", text or "MCP tool call failed") from None
            raise ToolError(err.get("error", "TOOL_FAILURE"), err.get("message", text))
        if result.structured_content:
            return dict(result.structured_content)
        return json.loads(text)
