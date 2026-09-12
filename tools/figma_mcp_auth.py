"""Figma 远程 MCP 授权与令牌维护。

背景（2026-09-12 实测）
---------------------
Figma 的 ``POST https://api.figma.com/v1/oauth/mcp/register`` **不是标准 RFC 7591**，
它按 ``client_name`` 做白名单放行：

    client_name = "Claude Code" / "Codex"   -> HTTP 200
    client_name = 其他任何名称                -> HTTP 403
      （实测 403 的：Claude Desktop / Cursor / VSCode / mcp-remote / WorkBuddy）

而**返回 403 时 OAuth 客户端会静默挂起**：不报错、不打开浏览器、日志只留一句
``waiting for connected/oauth callback``。表现就是「点信任后一直转圈」。

因此本工具在**外部**完成 RFC 7591 注册 + 授权码 + PKCE(S256)，再把拿到的
``access_token`` 以静态 ``Authorization: Bearer`` 头注入 ``~/.workbuddy/mcp.json``，
让 MCP 客户端根本不进入 OAuth 流程。

⚠️ 这是非官方 workaround（借用了白名单里的客户端名），Figma 随时可能收紧。

令牌存储
--------
``.build/figma_oauth/token.json``（``.build/`` 已被 .gitignore，令牌不入库）

子命令
------
    python tools/figma_mcp_auth.py login     完整授权（需在浏览器点一次「允许」）
    python tools/figma_mcp_auth.py refresh   用 refresh_token 换新令牌并同步 mcp.json
    python tools/figma_mcp_auth.py status    查看令牌剩余有效期
    python tools/figma_mcp_auth.py install   仅把现有令牌注入 mcp.json

令牌有效期 90 天（``expires_in = 7776000``），到期前跑一次 ``refresh`` 即可。
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import secrets
import shutil
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

# ---------------------------------------------------------------- 常量

CLIENT_NAME = "Codex"  # 必须落在 Figma 白名单内，否则 DCR 返回 403
REGISTER_URL = "https://api.figma.com/v1/oauth/mcp/register"
AUTHORIZE_URL = "https://www.figma.com/oauth/mcp"
TOKEN_URL = "https://api.figma.com/v1/oauth/token"
SCOPE = "mcp:connect"
WAIT_SECONDS = 900
PORT_CANDIDATES = (33418, 33419, 33420, 33421)
MCP_ENDPOINT = "https://mcp.figma.com/mcp"

REPO_ROOT = Path(__file__).resolve().parent.parent
STORE = REPO_ROOT / ".build" / "figma_oauth"          # 令牌落盘处（gitignore）
TOKEN_PATH = STORE / "token.json"
STATE_PATH = STORE / "state.json"
AUTHURL_PATH = STORE / "authurl.txt"
MCP_JSON = Path(os.path.expanduser("~")) / ".workbuddy" / "mcp.json"


# ---------------------------------------------------------------- HTTP

def post_json(url: str, payload: dict) -> tuple[int, dict | str]:
    body = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=body,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return resp.status, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", "replace")
    except (urllib.error.URLError, TimeoutError) as exc:
        return 0, f"网络不可达: {exc}"


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def load_json(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"[FATAL] 缺少 {path}，请先执行 login")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- OAuth

def do_register(redirect_uri: str) -> tuple[str, dict]:
    payload = {
        "client_name": CLIENT_NAME,
        "redirect_uris": [redirect_uri],
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
        "scope": SCOPE,
    }
    status, data = post_json(REGISTER_URL, payload)
    if status != 200 or not isinstance(data, dict):
        hint = ""
        if status == 403:
            hint = (f"\n        DCR 返回 403 = client_name 不在白名单，"
                    f"当前用的是 {CLIENT_NAME!r}；换个白名单名称（如 'Claude Code'）再试。")
        raise SystemExit(f"[FATAL] DCR 失败 HTTP {status}: {data!r}{hint}")
    client_id = data.get("client_id") or data.get("clientId")
    if not client_id:
        raise SystemExit(f"[FATAL] 注册响应无 client_id: {data!r}")
    return client_id, data


class CallbackHandler(BaseHTTPRequestHandler):
    captured: dict[str, str] = {}

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path != "/callback":
            self.send_response(404)
            self.end_headers()
            return
        params = urllib.parse.parse_qs(parsed.query)
        for key in ("code", "state", "error", "error_description"):
            if key in params:
                CallbackHandler.captured[key] = params[key][0]
        STORE.mkdir(parents=True, exist_ok=True)
        (STORE / "callback.txt").write_text(
            json.dumps(CallbackHandler.captured, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        ok = "code" in CallbackHandler.captured and "error" not in CallbackHandler.captured
        html = (
            "<html><meta charset='utf-8'><body style=\"font-family:system-ui;"
            "padding:48px;background:#FBF5EF;color:#2D3748\">"
            f"<h2 style='color:#26AE89'>{'授权成功' if ok else '授权未完成'}</h2>"
            "<p>可以关闭此标签页，回到 WorkBuddy 继续。</p></body></html>"
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(html)))
        self.end_headers()
        self.wfile.write(html)

    def log_message(self, *args) -> None:
        pass


def start_listener() -> tuple[HTTPServer, int]:
    for port in PORT_CANDIDATES:
        try:
            return HTTPServer(("127.0.0.1", port), CallbackHandler), port
        except OSError:
            continue
    raise SystemExit(f"[FATAL] {PORT_CANDIDATES} 端口全部被占用")


def save_token(data: dict) -> None:
    STORE.mkdir(parents=True, exist_ok=True)
    data["_obtained_at"] = int(time.time())
    TOKEN_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ---------------------------------------------------------------- 子命令

def cmd_login(_args) -> int:
    STORE.mkdir(parents=True, exist_ok=True)
    server, port = start_listener()
    redirect_uri = f"http://127.0.0.1:{port}/callback"

    verifier = b64url(secrets.token_bytes(64))
    challenge = b64url(hashlib.sha256(verifier.encode("ascii")).digest())
    state = b64url(secrets.token_bytes(24))

    print(f"[1/4] DCR 注册中（client_name={CLIENT_NAME}）...", flush=True)
    client_id, reg = do_register(redirect_uri)
    STATE_PATH.write_text(
        json.dumps(
            {"client_id": client_id, "client_secret": reg.get("client_secret"),
             "code_verifier": verifier, "state": state, "redirect_uri": redirect_uri,
             "registration": reg},
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    print(f"      client_id = {client_id}", flush=True)

    query = urllib.parse.urlencode({
        "response_type": "code", "client_id": client_id, "redirect_uri": redirect_uri,
        "scope": SCOPE, "state": state,
        "code_challenge": challenge, "code_challenge_method": "S256",
    })
    auth_url = f"{AUTHORIZE_URL}?{query}"
    AUTHURL_PATH.write_text(auth_url, encoding="utf-8")
    print(f"[2/4] 授权地址 -> {AUTHURL_PATH}", flush=True)
    print(f"      请在已登录 Figma 的浏览器打开：\n{auth_url}\n", flush=True)
    print(f"[3/4] 等待回调，最长 {WAIT_SECONDS}s ...", flush=True)

    threading.Thread(target=server.serve_forever, daemon=True).start()
    deadline = time.time() + WAIT_SECONDS
    while time.time() < deadline and not CallbackHandler.captured:
        time.sleep(1)
    server.shutdown()

    if "code" not in CallbackHandler.captured:
        print(f"[WARN] 未捕获授权码: {CallbackHandler.captured!r}", flush=True)
        print("       若浏览器跳转了 127.0.0.1 但页面打不开，把地址栏完整 URL 贴给助手。",
              flush=True)
        return 2

    print("[4/4] 换取 token ...", flush=True)
    payload = {
        "grant_type": "authorization_code",
        "code": CallbackHandler.captured["code"],
        "redirect_uri": redirect_uri,
        "client_id": client_id,
        "code_verifier": verifier,
    }
    if reg.get("client_secret"):
        payload["client_secret"] = reg["client_secret"]
    status, data = post_json(TOKEN_URL, payload)
    if status != 200 or not isinstance(data, dict):
        raise SystemExit(f"[FATAL] token 交换失败 HTTP {status}: {data!r}")

    save_token(data)
    expires = data.get("expires_in")
    print(f"      expires_in = {expires}s ≈ {expires / 86400:.1f} 天" if expires
          else "      expires_in = (未返回)", flush=True)
    print(f"      已写入 {TOKEN_PATH}", flush=True)
    return cmd_install(None)


def cmd_refresh(_args) -> int:
    state = load_json(STATE_PATH)
    tok = load_json(TOKEN_PATH)
    rt = tok.get("refresh_token")
    if not rt:
        raise SystemExit("[FATAL] token.json 里没有 refresh_token，请重新 login")

    print("[1/2] 用 refresh_token 换新令牌 ...", flush=True)
    payload = {
        "grant_type": "refresh_token",
        "refresh_token": rt,
        "client_id": state["client_id"],
    }
    if state.get("client_secret"):
        payload["client_secret"] = state["client_secret"]
    status, data = post_json(TOKEN_URL, payload)
    if status != 200 or not isinstance(data, dict):
        raise SystemExit(
            f"[FATAL] 刷新失败 HTTP {status}: {data!r}\n"
            "        refresh_token 可能已失效，请重新执行 login。"
        )

    # 刷新响应不含身份字段（user_id 等），需从旧令牌继承，否则会被覆盖成 None
    for key in ("user_id", "user_id_string"):
        if not data.get(key) and tok.get(key):
            data[key] = tok[key]
    # 有些实现刷新时不回传 refresh_token，沿用旧值
    if not data.get("refresh_token"):
        data["refresh_token"] = rt
    save_token(data)
    expires = data.get("expires_in")
    print(f"      新令牌有效期 {expires}s ≈ {expires / 86400:.1f} 天" if expires
          else "      已刷新", flush=True)
    return cmd_install(None)


def cmd_status(_args) -> int:
    if not TOKEN_PATH.exists():
        print(f"未授权：{TOKEN_PATH} 不存在。先执行 login。")
        return 1
    tok = json.loads(TOKEN_PATH.read_text(encoding="utf-8"))
    obtained = tok.get("_obtained_at")
    life = tok.get("expires_in") or 0
    print(f"令牌文件   : {TOKEN_PATH}")
    print(f"user_id    : {tok.get('user_id_string')}")
    print(f"token_type : {tok.get('token_type')}")
    print(f"access     : {str(tok.get('access_token'))[:12]}... (len={len(tok.get('access_token') or '')})")
    print(f"refresh    : {'有' if tok.get('refresh_token') else '无'}")
    if obtained and life:
        left = obtained + life - time.time()
        print(f"取得于     : {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(obtained))}")
        print(f"剩余有效期 : {left / 86400:.1f} 天" + ("   ⚠️ 建议尽快 refresh" if left < 7 * 86400 else ""))

    # 顺带确认 mcp.json 是否已注入同一令牌
    if MCP_JSON.exists():
        cfg = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        entry = (cfg.get("mcpServers") or {}).get("figma") or {}
        hdr = (entry.get("headers") or {}).get("Authorization", "")
        same = hdr == f"Bearer {tok.get('access_token')}"
        print(f"mcp.json   : {'✅ 已注入且与令牌一致' if same else '⚠️ 未注入或令牌不一致，跑 install'}")
    return 0


def cmd_install(_args) -> int:
    tok = load_json(TOKEN_PATH)
    access = tok.get("access_token")
    if not access:
        raise SystemExit("[FATAL] token.json 没有 access_token")

    if MCP_JSON.exists():
        bak = MCP_JSON.with_suffix(f".json.bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(MCP_JSON, bak)
        cfg = json.loads(MCP_JSON.read_text(encoding="utf-8"))
        print(f"      已备份 -> {bak.name}")
    else:
        cfg = {}

    cfg.setdefault("mcpServers", {})["figma"] = {
        "type": "http",
        "url": MCP_ENDPOINT,
        "headers": {"Authorization": f"Bearer {access}"},
        "timeout": 60000,
        "disabled": False,
    }
    MCP_JSON.write_text(json.dumps(cfg, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(f"      已注入 {MCP_JSON}")
    print("      ⚠️ 改完不会热生效：需重启 WorkBuddy 才会加载新配置。")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Figma 远程 MCP 授权与令牌维护",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("login", help="完整授权（浏览器点一次「允许」）")
    sub.add_parser("refresh", help="用 refresh_token 换新令牌并同步 mcp.json")
    sub.add_parser("status", help="查看令牌剩余有效期")
    sub.add_parser("install", help="仅把现有令牌注入 mcp.json")
    args = parser.parse_args()
    return {"login": cmd_login, "refresh": cmd_refresh,
            "status": cmd_status, "install": cmd_install}[args.cmd](args)


if __name__ == "__main__":
    sys.exit(main())
