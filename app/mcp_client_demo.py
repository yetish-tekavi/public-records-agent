"""MCP client demo: start the local MCP server, discover its tools, and call each one.

Usage:
    python -m app.mcp_client_demo              # keyword-only retrieval (fast start)
    python -m app.mcp_client_demo --semantic   # hybrid retrieval (loads the embedding model)
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys

from app.agents.executors import McpToolExecutor
from app.console import for_console, prepare_stdout
from app.tools.service import ToolError


UTF8 = False


def show(title: str, payload: object) -> None:
    text = json.dumps(payload, indent=2, ensure_ascii=False)
    lines = text.splitlines()
    if len(lines) > 30:
        text = "\n".join(lines[:30] + [f"  ... ({len(lines) - 30} more lines)"])
    print(for_console(f"\n--- {title}\n{text}", UTF8))


async def demo(semantic: bool) -> None:
    async with McpToolExecutor(env={"PRA_SEMANTIC": "1" if semantic else "0"}) as mcp:
        tools = await mcp.list_tools()
        print(f"Connected to MCP server; discovered {len(tools)} tool(s):")
        for t in tools:
            params = ", ".join(t.input_schema.get("properties", {}))
            print(f"  - {t.name}({params}): {(t.description or '').splitlines()[0]}")

        policy = await mcp.call("search_policy", {"query": "Is a driver license number confidential?", "k": 2})
        show("call search_policy", {
            "status": policy["status"],
            "passages": [{"chunk_id": p["chunk"]["chunk_id"], "section": p["chunk"]["section"], "page": p["chunk"]["page_start"]} for p in policy["passages"]],
        })

        pii = await mcp.call("detect_pii", {"record_id": "TEST-001"})
        show("call detect_pii", [{k: f[k] for k in ("finding_id", "category", "text", "confidence")} for f in pii["findings"]])

        chunk_ids = [p["chunk"]["chunk_id"] for p in policy["passages"]]
        proposal = await mcp.call("propose_redactions", {"record_id": "TEST-001", "findings": pii["findings"], "policy_chunk_ids": chunk_ids})
        show("call propose_redactions", [{k: r[k] for k in ("finding_id", "action", "rationale")} for r in proposal["recommendations"]])

        try:
            await mcp.call("propose_redactions", {"record_id": "TEST-001", "findings": [], "policy_chunk_ids": ["made_up:9999"]})
        except ToolError as exc:
            show("call propose_redactions with an invented chunk id (expected error)", exc.to_dict())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Discover and call the MCP tools")
    parser.add_argument("--semantic", action="store_true", help="enable semantic retrieval on the server")
    args = parser.parse_args(argv)
    global UTF8
    UTF8 = prepare_stdout()
    asyncio.run(demo(args.semantic))
    return 0


if __name__ == "__main__":
    sys.exit(main())
