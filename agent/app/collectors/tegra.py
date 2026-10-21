"""Jetson tegrastats 解析与采样（P1 实装，接口 P0 已预留）。

tegrastats 输出样例（Orin）：
  RAM 6543/62841MB (lfb 1218x4MB) SWAP 0/31420MB ... GR3D_FREQ 37%
  AO@35C GPU@35.5C ... VDD_IN 5444mW/5444mW ...

字段口径：
  gpu_util = GR3D_FREQ 百分比
  vram     = RAM used/total（统一折算百分比）
  temp     = GPU@ 摄氏
  power    = VDD_IN 瞬时 mW → W
"""
from __future__ import annotations

import asyncio
import re

_GR3D = re.compile(r"GR3D_FREQ\s+(\d+)%")
_RAM = re.compile(r"RAM\s+(\d+)/(\d+)MB")
_TEMP = re.compile(r"GPU@([\d.]+)C")
_PWR = re.compile(r"VDD_IN\s+(\d+)mW")


def parse_tegrastats(line: str) -> dict:
    """解析单行 tegrastats 输出；缺失字段给 0。"""
    out = {"gpu_util": 0.0, "vram": 0.0, "temp": 0.0, "power": 0.0}
    m = _GR3D.search(line)
    if m:
        out["gpu_util"] = float(m.group(1))
    m = _RAM.search(line)
    if m:
        used, total = float(m.group(1)), float(m.group(2))
        out["vram"] = round(used / total * 100, 1) if total else 0.0
    m = _TEMP.search(line)
    if m:
        out["temp"] = float(m.group(1))
    m = _PWR.search(line)
    if m:
        out["power"] = round(float(m.group(1)) / 1000, 1)
    return out


class TegraSampler:
    """后台跑 `tegrastats --interval 1`，保留最新一行解析结果。

    采集器独立进程生命周期由 Agent 管理；读不到（非 root/无 tegrastats）
    不致命，sample() 返回全 0。
    """

    def __init__(self, interval: int = 1):
        self.interval = interval
        self._latest: dict = {"gpu_util": 0.0, "vram": 0.0, "temp": 0.0, "power": 0.0}
        self._proc: asyncio.subprocess.Process | None = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if self._task is not None:
            return
        self._task = asyncio.create_task(self._run())

    async def _run(self) -> None:
        try:
            self._proc = await asyncio.create_subprocess_exec(
                "tegrastats", "--interval", str(self.interval),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL)
            if self._proc.stdout is None:
                raise RuntimeError("tegrastats stdout unavailable")
            while True:
                raw = await self._proc.stdout.readline()
                if not raw:
                    break
                line = raw.decode(errors="ignore")
                if "GR3D_FREQ" in line or "RAM" in line:
                    self._latest = parse_tegrastats(line)
        except Exception:
            pass  # tegrastats 不可用 → 保持全 0

    def sample(self) -> dict:
        return dict(self._latest)

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._proc:
            self._proc.terminate()
