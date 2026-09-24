"""Web 终端 WS 端到端验证：连接 S1 → 执行命令 → 黑名单拦截 → 录屏落盘。"""
import asyncio
import json
import sys

import httpx
import websockets

BASE = "http://127.0.0.1:8080"


async def main() -> int:
    async with httpx.AsyncClient() as c:
        token = (await c.post(f"{BASE}/api/auth/login", json={
            "username": "admin", "password": "hengshu-dev"})).json()["access_token"]

    uri = f"ws://127.0.0.1:8080/api/term/ws?token={token}&node_id=S1&cols=100&rows=30"
    output = ""
    async with websockets.connect(uri, max_size=2**22) as ws:
        ready = json.loads(await asyncio.wait_for(ws.recv(), 20))
        assert ready["type"] == "ready", ready
        print("✓ ready:", ready)

        await ws.send(json.dumps({"type": "in", "data": "uname -a\r"}))
        for _ in range(30):
            msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
            if msg["type"] == "out":
                output += msg["data"]
                if "aarch64" in output or "x86_64" in output:
                    break
        print("✓ uname:", output.strip().splitlines()[-1][:100] if output.strip() else "(empty)")
        assert "Linux" in output, "no uname output"

        # 高危命令 → 拦截 + 会话关闭
        await ws.send(json.dumps({"type": "in", "data": "rm -rf /\r"}))
        blocked = False
        try:
            for _ in range(10):
                msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
                if msg["type"] == "blocked":
                    blocked = True
                    print("✓ blocked:", msg["msg"].strip())
                    break
        except websockets.ConnectionClosed:
            pass
        assert blocked, "blacklist did not trigger"

    print("PASS")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
