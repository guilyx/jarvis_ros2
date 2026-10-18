# Copyright 2026 Erwin L.
# SPDX-License-Identifier: Apache-2.0
"""A Model Context Protocol server over stdio (spec 2025-06-18; 2025-03-26 and 2024-11-05 clients too).

MCP is JSON-RPC 2.0, one message per line: `initialize`, then `tools/list` and `tools/call`. This
module is the protocol; what the tools do is in tools.py. No dependency beyond the standard
library, so the server installs anywhere an assistant runs (a laptop, CI) without ROS.
"""

import json
import logging
import sys
import traceback

from jarvis_mcp import __version__

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")  # newest first
log = logging.getLogger("jarvis_mcp")

PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR = (
    -32700,
    -32600,
    -32601,
    -32602,
    -32603,
)


class ToolError(Exception):
    """A tool could not do what it was asked: reported to the model as a tool result (isError),
    which it can read and act on, not as a protocol error."""


class Server:
    """Dispatches JSON-RPC messages to a tool set (tools.Toolbox or anything with `specs()` and
    `call(name, arguments)`)."""

    def __init__(self, tools, instructions=""):
        self.tools = tools
        self.instructions = instructions
        self.initialized = False

    # -- one message ---------------------------------------------------------------------------------

    def handle(self, message):
        """The reply to one decoded message (a dict), or None for a notification or a response."""
        if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
            return _error(None, INVALID_REQUEST, "not a JSON-RPC 2.0 message")
        if "method" not in message:
            return None  # a response to something we never ask: ignore it
        method, params, rid = message["method"], message.get("params") or {}, message.get("id")
        notification = "id" not in message
        try:
            result = self._dispatch(method, params)
        except _RpcError as e:
            return None if notification else _error(rid, e.code, e.message)
        except Exception as e:  # noqa: BLE001 - the client hears it, the server goes on
            log.error("%s failed: %s", method, traceback.format_exc())
            return None if notification else _error(rid, INTERNAL_ERROR, f"{type(e).__name__}: {e}")
        if notification:
            return None
        return {"jsonrpc": "2.0", "id": rid, "result": result}

    def _dispatch(self, method, params):
        if method == "initialize":
            asked = params.get("protocolVersion", "")
            self.initialized = True
            result = {
                "protocolVersion": asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0],
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {
                    "name": "jarvis",
                    "title": "Jarvis (ROS 2 faults and recovery)",
                    "version": __version__,
                },
            }
            if self.instructions:
                result["instructions"] = self.instructions
            return result
        if method == "ping":
            return {}
        if method.startswith("notifications/"):
            return None  # initialized, cancelled, ...: nothing to do
        if method == "tools/list":
            return {"tools": self.tools.specs()}
        if method == "tools/call":
            name = params.get("name")
            arguments = params.get("arguments") or {}
            if not isinstance(name, str) or not isinstance(arguments, dict):
                raise _RpcError(INVALID_PARAMS, "tools/call needs a name and an arguments object")
            if name not in {t["name"] for t in self.tools.specs()}:
                raise _RpcError(INVALID_PARAMS, f"unknown tool '{name}'")
            try:
                return _result(self.tools.call(name, arguments))
            except ToolError as e:
                return {"content": [{"type": "text", "text": str(e)}], "isError": True}
        raise _RpcError(METHOD_NOT_FOUND, f"method '{method}' not found")

    # -- stdio ---------------------------------------------------------------------------------------

    def serve(self, stdin=None, stdout=None):
        """Reads messages line by line until stdin closes. Logs go to stderr: stdout is the protocol's."""
        stdin, stdout = stdin or sys.stdin, stdout or sys.stdout
        for line in stdin:
            line = line.strip()
            if not line:
                continue
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                reply = _error(None, PARSE_ERROR, "invalid JSON")
            else:
                if isinstance(message, list):  # a batch (2025-03-26)
                    replies = [r for r in (self.handle(m) for m in message) if r is not None]
                    reply = replies or None
                else:
                    reply = self.handle(message)
            if reply is not None:
                stdout.write(json.dumps(reply, separators=(",", ":")) + "\n")
                stdout.flush()


class _RpcError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code, self.message = code, message


def _error(rid, code, message):
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _result(value):
    """A tool's return value as a tools/call result: the JSON as text, and as structured content."""
    text = value if isinstance(value, str) else json.dumps(value, indent=1, ensure_ascii=False, default=str)
    out = {"content": [{"type": "text", "text": text}], "isError": False}
    if isinstance(value, dict):
        out["structuredContent"] = value
    return out
