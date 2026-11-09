"""Local MCP server exposing the three compliance tools over stdio (official MCP Python SDK 2.x).

Run directly (an MCP client normally starts it for you):
    python -m app.mcp_server

Environment:
    PRA_SEMANTIC=0                 use keyword-only policy retrieval (no embedding model)
    PRA_DATA_DIR=path              synthetic dataset directory (default: data/)
    PRA_POLICY_DOC_IDS=a,b         restrict the policy corpus to these manifest doc_ids

Logging goes to stderr; stdout carries the MCP protocol and must stay clean.
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError as McpToolError
from mcp.types import ToolAnnotations
from pydantic import Field

from app.data_loader import DEFAULT_DATA_DIR
from app.tools.service import ToolError, ToolService

logger = logging.getLogger("app.mcp_server")

READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)

service = ToolService(
    data_dir=Path(os.environ.get("PRA_DATA_DIR", str(DEFAULT_DATA_DIR))),
    enable_semantic=os.environ.get("PRA_SEMANTIC", "1") != "0",
    policy_doc_ids=[d for d in os.environ.get("PRA_POLICY_DOC_IDS", "").split(",") if d] or None,
)
LOG_LEVEL = os.environ.get("PRA_LOG_LEVEL", "WARNING").upper()

mcp = MCPServer(
    name="public-records-tools",
    log_level=LOG_LEVEL,
    instructions=(
        "Tools for reviewing synthetic New Jersey public-records requests. search_policy returns verbatim "
        "passages from official OPRA/GRC/AG documents or INSUFFICIENT; detect_pii returns candidate "
        "sensitive spans; propose_redactions returns recommendations that always require human approval. "
        "Tool outputs are evidence, not legal conclusions."
    ),
)


def _call(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    try:
        return service.call(name, {k: v for k, v in arguments.items() if v is not None})
    except ToolError as exc:
        # Structured error text so clients can recover the error code.
        raise McpToolError(json.dumps(exc.to_dict())) from exc


@mcp.tool(annotations=READ_ONLY)
def search_policy(
    query: Annotated[str, Field(description="Policy question or keywords, e.g. 'Is a driver license number confidential?'")],
    k: Annotated[int, Field(description="Maximum passages to return (1-10)")] = 3,
    method: Annotated[str, Field(description="hybrid, keyword, or semantic")] = "hybrid",
) -> dict[str, Any]:
    """Retrieve verbatim passages from official NJ public-records policy documents, with citations.

    Returns status SUFFICIENT with passages (title, section, page, source URL, excerpt) or
    INSUFFICIENT with no passages, meaning no policy basis was found and human review is required.
    """
    return _call("search_policy", {"query": query, "k": k, "method": method})


@mcp.tool(annotations=READ_ONLY)
def detect_pii(
    text: Annotated[str | None, Field(description="Text to scan (max 20,000 chars). Omit if record_id is given.")] = None,
    record_id: Annotated[str | None, Field(description="Synthetic record id such as TEST-001. Omit if text is given.")] = None,
) -> dict[str, Any]:
    """Identify candidate sensitive information with Microsoft Presidio.

    Provide exactly one of text or record_id. Returns findings with category, exact text span,
    character offsets, and detector confidence. Findings are candidates, not decisions.
    """
    return _call("detect_pii", {"text": text, "record_id": record_id})


@mcp.tool(annotations=READ_ONLY)
def propose_redactions(
    record_id: Annotated[str, Field(description="Synthetic record id such as TEST-001")],
    findings: Annotated[list[dict[str, Any]], Field(description="Finding objects exactly as returned by detect_pii")],
    policy_chunk_ids: Annotated[list[str], Field(description="chunk_id values of passages returned by search_policy")],
) -> dict[str, Any]:
    """Produce structured redaction recommendations for a record.

    REDACT only where a supplied policy passage explicitly names the category; otherwise
    HUMAN_REVIEW_REQUIRED with open questions. Unknown chunk ids are rejected, so citations can
    only come from retrieved passages. Every recommendation requires human approval.
    """
    return _call("propose_redactions", {"record_id": record_id, "findings": findings, "policy_chunk_ids": policy_chunk_ids})


@mcp.tool(annotations=READ_ONLY)
def validate_citations(
    citations: Annotated[list[dict[str, Any]], Field(description="Objects with chunk_id and optional excerpt to verify verbatim")],
) -> dict[str, Any]:
    """Verify that cited policy chunks exist in the indexed official corpus and that excerpts are verbatim.

    Used by the Compliance & QA Agent to reject fabricated or altered citations.
    """
    return _call("validate_citations", {"citations": citations})


def main() -> None:
    logging.basicConfig(level=LOG_LEVEL, stream=sys.stderr, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("presidio-analyzer").setLevel(logging.ERROR)  # very chatty at INFO
    logger.info("Starting MCP server (semantic=%s)", service.enable_semantic)
    mcp.run("stdio")


if __name__ == "__main__":
    main()
