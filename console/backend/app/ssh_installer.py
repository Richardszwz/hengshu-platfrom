"""SSH 推送安装 Agent（方案书 P1 纳管链路）。

流程：asyncssh 连接（密码或私钥）→ 主机指纹 TOFU 记录（hs:fp:{node}，
再次安装发现指纹变更即中止并告警）→ 探测 arch/docker/nvidia →
docker run 拉起 Agent 容器（挂 docker.sock 获得服务管控能力）→
心跳自动上线（控制台不阻塞等待，前端轮询节点列表）。

气隙变体：INSTALL_TAR_DIR 下存在 agent-{arch}.tar 时，先 scp+docker load。
"""
from __future__ import annotations

import asyncio
import json
import os
import time

import asyncssh
import redis.asyncio as aioredis

FP_PREFIX = "hs:fp:"
INSTALL_TIMEOUT = 120.0


async def _event(r: aioredis.Redis, level: str, msg: str) -> None:
    line = json.dumps({"ts": round(time.time(), 3), "level": level, "msg": msg},
                      ensure_ascii=False)
    async with r.pipeline() as p:
        p.rpush("hs:events", line)
        p.ltrim("hs:events", -200, -1)
        await p.execute()


async def _run(client: asyncssh.SSHClientConnection, cmd: str, timeout: float = 30.0) -> str:
    result = await asyncio.wait_for(client.run(cmd), timeout=timeout)
    return (result.stdout or result.stderr or "").strip()


async def _fingerprint_tofu(r: aioredis.Redis, node_id: str, host: str,
                            conn: asyncssh.SSHClientConnection) -> str:
    key = conn.get_server_host_key()
    fp = key.get_fingerprint().replace(":", "")
    stored = await r.get(f"{FP_PREFIX}{node_id}")
    if stored and stored != fp:
        await _event(r, "ERROR",
                     f"install ▸ {host} 主机指纹变更! 可能中间人攻击,已中止。"
                     f"({stored[:12]}→{fp[:12]})")
        raise RuntimeError("host key fingerprint mismatch — possible MITM")
    if not stored:
        await r.set(f"{FP_PREFIX}{node_id}", fp)
        await _event(r, "INFO", f"install ▸ {host} 首次信任,指纹已记录 {fp[:16]}…")
    return fp


async def install(r: aioredis.Redis, *, node_id: str, host: str, ssh_port: int,
                  username: str, password: str | None = None,
                  private_key: str | None = None,
                  agent_image: str, redis_advertise: str,
                  tar_dir: str | None = None) -> dict:
    steps: list[str] = []

    async def log(level: str, msg: str) -> None:
        steps.append(msg)
        await _event(r, level, msg)

    await log("INFO", f"install ▸ 连接 {username}@{host}:{ssh_port} …")

    try:
        conn_kwargs: dict = {
            "host": host, "port": ssh_port, "username": username,
            "known_hosts": None,  # 指纹由 TOFU 逻辑接管
            "connect_timeout": 15,
        }
        if private_key:
            try:
                conn_kwargs["client_keys"] = [asyncssh.import_private_key(private_key)]
            except (asyncssh.KeyImportError, ValueError) as e:
                raise RuntimeError(f"私钥格式无法解析（需完整 PEM 文本）: {e}") from e
        elif password:
            conn_kwargs["password"] = password

        async with asyncssh.connect(**conn_kwargs) as conn:
            await _fingerprint_tofu(r, node_id, host, conn)
            await log("OK", f"install ▸ SSH 连接成功 {username}@{host}")

            arch = (await _run(conn, "uname -m")) or "unknown"
            await log("INFO", f"install ▸ 架构探测: {arch}")

            docker_v = await _run(conn, "docker info --format '{{.ServerVersion}}' 2>/dev/null")
            if not docker_v:
                raise RuntimeError("目标机 docker 不可用(非 root 组?未安装?) — 气隙/裸机路径 P1.2")
            await log("OK", f"install ▸ Docker {docker_v}")

            gpu = await _run(conn, "nvidia-smi -L 2>/dev/null | head -1")
            if gpu:
                await log("INFO", f"install ▸ GPU: {gpu}")
            else:
                await log("INFO", "install ▸ 未检测到 nvidia-smi(Jetson? 走 tegrastats 路径)")

            # 气隙：本地 tar → 远端 docker load
            if tar_dir:
                tar_path = f"{tar_dir}/agent-{arch}.tar"
                if await asyncio.to_thread(os.path.exists, tar_path):
                    await log("INFO", f"install ▸ 气隙模式装载 {tar_path}")
                    async with conn.start_sftp_client() as sftp:
                        await sftp.put(tar_path, "/tmp/hengshu-agent.tar")
                    await _run(conn, "docker load -i /tmp/hengshu-agent.tar", timeout=180)
                    await _run(conn, "rm -f /tmp/hengshu-agent.tar")

            # 清理旧实例（幂等重装）
            await _run(conn,
                       "docker rm -f hengshu-agent 2>/dev/null; true")
            cmd = (
                "docker run -d --name hengshu-agent --restart unless-stopped "
                f"-e REDIS_URL='{redis_advertise}' -e NODE_ID='{node_id}' "
                "-e AGENT_PORT=9100 -p 9100:9100 "
                "-v /var/run/docker.sock:/var/run/docker.sock "
                "-v hengshu-agent-data:/data "
                f"--gpus all {agent_image}"
            )
            rc_out = await _run(conn, cmd, timeout=INSTALL_TIMEOUT)
            # 校验容器真实运行（docker run 失败也会输出"Unable to find image..."等文本）
            state = await _run(conn,
                               "docker inspect -f '{{.State.Running}}' hengshu-agent 2>/dev/null")
            if state.strip() != "true":
                raise RuntimeError(
                    f"Agent 容器未运行（镜像缺失/启动失败）: {(rc_out or state)[:300]}")
            await log("OK", f"install ▸ Agent 容器运行中 {rc_out[:12]}")

        await log("OK", f"install ▸ {node_id} 纳管完成,等待首次心跳上线")
        return {"node_id": node_id, "arch": arch, "docker": docker_v, "steps": steps}
    except (asyncssh.Error, asyncio.TimeoutError, RuntimeError, OSError) as e:
        msg = str(e) or f"{type(e).__name__}（连接超时/被重置/协议失败，详见控制台日志）"
        await log("ERROR", f"install ▸ {node_id} 失败: {msg}")
        return {"node_id": node_id, "error": msg, "steps": steps}
