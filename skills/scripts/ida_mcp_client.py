#!/usr/bin/env python3
"""Generic JSON-RPC client for the IDA Pro MCP HTTP server.

The server runs inside IDA (via the ida_pro_mcp plugin) and listens on
http://127.0.0.1:13337/mcp by default.

Usage:
    python ida_mcp_client.py tools/list
    python ida_mcp_client.py decompile '{"addr":"0x<address>"}'
    python ida_mcp_client.py decompile @q.json
    python ida_mcp_client.py disasm '{"addr":"0x<address>","max_instructions":30}'
    python ida_mcp_client.py xrefs_to @q.json
    python ida_mcp_client.py py_eval @code.json      {"code": "..."}

Params may be a JSON string or "@file.json" (file containing JSON).
**Prefer @file.** A shell (PowerShell especially) rewrites quotes and braces on
the way to a native program, so an inline JSON argument silently becomes
malformed; the file form cannot be mangled.

Override the endpoint with the IDA_MCP_URL environment variable.

Tool results are unwrapped before printing: `decompile` yields pseudocode,
`disasm` yields instructions, `py_eval` yields stdout/stderr, instead of the
raw JSON envelope. This matters because the server returns those payloads as a
JSON *string*, so without unwrapping you read escaped `\\n` code.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request

URL = os.environ.get("IDA_MCP_URL", "http://127.0.0.1:13337/mcp")
_seq = [0]


def call(method: str, params=None, notify: bool = False):
    _seq[0] += 1
    req = {"jsonrpc": "2.0", "method": method}
    if params is not None:
        req["params"] = params
    if not notify:
        req["id"] = _seq[0]
    data = json.dumps(req).encode("utf-8")
    request = urllib.request.Request(
        URL, data=data,
        headers={"Content-Type": "application/json",
                 "Accept": "application/json, text/event-stream"})
    with urllib.request.urlopen(request, timeout=600) as resp:
        raw = resp.read().decode("utf-8", errors="replace")
    if not raw.strip():
        return None
    out = None
    for line in raw.splitlines():
        line = line.strip()
        if line.startswith("data:"):
            line = line[5:].strip()
        if not line or line.startswith("event:") or line.startswith(":"):
            continue
        try:
            out = json.loads(line)
        except json.JSONDecodeError:
            continue
    return out


def main() -> int:
    try:
        call("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "codex-helper", "version": "1.0"},
        })
        call("notifications/initialized", None, notify=True)
    except Exception as exc:  # noqa: BLE001
        print(f"init warning: {exc}", file=sys.stderr)

    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    method = sys.argv[1]
    params = None
    if len(sys.argv) > 2:
        arg = sys.argv[2]
        if arg.startswith("@"):
            with open(arg[1:], "r", encoding="utf-8-sig") as f:
                params = json.load(f)
        else:
            params = json.loads(arg)

    standard = {"initialize", "ping", "tools/list", "tools/call",
                "resources/list", "resources/read", "resources/templates/list",
                "prompts/list", "notifications/initialized"}
    if method in standard:
        res = call(method, params)
    else:
        res = call("tools/call", {"name": method, "arguments": params or {}})
    if res is None:
        print("<no response> (is IDA open with the MCP plugin started?)")
        return 1
    if "error" in res:
        print(json.dumps(res["error"], ensure_ascii=False, indent=2))
        return 1
    result = res.get("result")
    if isinstance(result, dict) and "content" in result:
        text = "\n".join(
            item.get("text", "") for item in result["content"]
            if isinstance(item, dict) and item.get("type") == "text")
        # Many tools return a JSON blob as their text payload. Unwrap it so the
        # output is the useful part, not escaped JSON:
        #   decompile -> {"addr": ..., "code": "<pseudocode>"}
        #   disasm    -> {"addr": ..., "instructions": "<asm>"}
        #   py_eval   -> {"result": ..., "stdout": ..., "stderr": ...}
        # `code` is checked after `stdout` because py_eval echoes the submitted
        # code back in a `code` field.
        try:
            parsed = json.loads(text)
        except (json.JSONDecodeError, TypeError):
            parsed = None
        if isinstance(parsed, dict):
            if "stdout" in parsed or "stderr" in parsed:
                text = (parsed.get("stdout") or "") + (parsed.get("stderr") or "")
                if not text.strip():
                    text = json.dumps(
                        {k: v for k, v in parsed.items() if k != "code"},
                        ensure_ascii=False, indent=2)
            else:
                for key in ("code", "instructions", "text", "result"):
                    if isinstance(parsed.get(key), str):
                        text = parsed[key]
                        break
                else:
                    text = json.dumps(parsed, ensure_ascii=False, indent=2)
        print(text)
        if result.get("isError"):
            return 1
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
