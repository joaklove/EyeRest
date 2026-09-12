"""直连 Figma remote MCP 端点调用工具（绕过 WorkBuddy 客户端）。

用途：当 WorkBuddy 侧 MCP 工具不可用（未注册、被限流、配额耗尽）时，
用已授权的静态 Bearer 令牌直接调 `https://mcp.figma.com/mcp`。

Figma 的 remote MCP 是 **无状态** 的 streamable HTTP 服务：不返回
`mcp-session-id`，每次调用都可以独立发起（`initialize` 不是必需前置，
但保留它便于确认服务端版本）。

用法：
    # 列出全部工具名
    python tools/figma_mcp_call.py list

    # 调用某个工具（arguments 为 JSON）
    python tools/figma_mcp_call.py call whoami
    python tools/figma_mcp_call.py call get_metadata '{"fileKey":"...","nodeId":"5:28"}'

    # 直接执行一段 use_figma 脚本（从文件读，避免命令行转义地狱）
    python tools/figma_mcp_call.py use_figma <fileKey> <script.js> "描述"

说明：令牌读取自 `.build/figma_oauth/token.json`（由 `figma_mcp_auth.py login` 写入）。
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOKEN_PATH = ROOT / ".build" / "figma_oauth" / "token.json"
ENDPOINT = "https://mcp.figma.com/mcp"

HEADERS_BASE = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def load_token() -> str:
    if not TOKEN_PATH.exists():
        raise SystemExit(f"令牌文件不存在：{TOKEN_PATH}\n先运行 python tools/figma_mcp_auth.py login")
    data = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    token = data.get("access_token")
    if not token:
        raise SystemExit("令牌文件里没有 access_token")
    return token


def rpc(method: str, params: dict | None, token: str, req_id: int = 1) -> dict:
    """发一次 JSON-RPC 调用，返回解析后的完整响应（兼容 SSE 与纯 JSON）。"""
    payload = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        payload["params"] = params

    headers = dict(HEADERS_BASE)
    headers["Authorization"] = f"Bearer {token}"

    req = urllib.request.Request(
        ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            body = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:  # 4xx/5xx 也要把体读出来看原因
        detail = exc.read().decode("utf-8", errors="replace")
        raise SystemExit(f"HTTP {exc.code}\n{detail[:2000]}") from None

    # SSE 响应：逐行找 `data: {...}`
    if body.lstrip().startswith("event:") or "\ndata:" in body or body.startswith("data:"):
        for line in body.splitlines():
            line = line.strip()
            if line.startswith("data:"):
                chunk = line[len("data:"):].strip()
                if chunk and chunk != "[DONE]":
                    try:
                        obj = json.loads(chunk)
                    except json.JSONDecodeError:
                        continue
                    if "result" in obj or "error" in obj:
                        return obj
        raise SystemExit(f"SSE 里没有可解析的结果：\n{body[:2000]}")

    return json.loads(body)


def call_tool(name: str, arguments: dict, token: str) -> dict:
    obj = rpc("tools/call", {"name": name, "arguments": arguments}, token)
    if "error" in obj:
        raise SystemExit(f"工具报错：{json.dumps(obj['error'], ensure_ascii=False)}")
    return obj["result"]


def main() -> int:
    parser = argparse.ArgumentParser(description="直连 Figma remote MCP")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("list", help="列出全部工具名")
    sub.add_parser("whoami", help="查询当前授权身份")

    p_raw = sub.add_parser("raw", help="直接发任意 JSON-RPC 方法（如 resources/read）")
    p_raw.add_argument("method")
    p_raw.add_argument("params", nargs="?", default="{}")

    p_call = sub.add_parser("call", help="调用任意工具")
    p_call.add_argument("tool")
    p_call.add_argument("args", nargs="?", default="{}")

    p_use = sub.add_parser("use_figma", help="执行 use_figma 脚本")
    p_use.add_argument("file_key")
    p_use.add_argument("script", help="脚本文件路径")
    p_use.add_argument("description")
    p_use.add_argument("--skills", default="resource:figma-use")

    ns = parser.parse_args()
    token = load_token()

    if ns.cmd == "list":
        obj = rpc("tools/list", {}, token)
        names = sorted(t["name"] for t in obj["result"]["tools"])
        print(f"共 {len(names)} 个工具：")
        for n in names:
            print(" ", n)
        return 0

    if ns.cmd == "whoami":
        result = call_tool("whoami", {}, token)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if ns.cmd == "raw":
        obj = rpc(ns.method, json.loads(ns.params), token)
        print(json.dumps(obj, ensure_ascii=False, indent=2))
        return 0

    if ns.cmd == "call":
        result = call_tool(ns.tool, json.loads(ns.args), token)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    if ns.cmd == "use_figma":
        code = Path(ns.script).read_text(encoding="utf-8")
        result = call_tool(
            "use_figma",
            {
                "fileKey": ns.file_key,
                "code": code,
                "description": ns.description,
                "skillNames": ns.skills,
            },
            token,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    return 1


if __name__ == "__main__":
    sys.exit(main())
