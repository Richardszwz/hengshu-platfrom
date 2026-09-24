"""主机 GPU 指标采集（架构自动探测）。

优先级：
1. pynvml（NVIDIA 管理库）：x86 服务器 + aarch64 带驱动机型（DGX Spark 等）
2. tegrastats（Jetson Orin/Nano）：jtop 依赖 systemd，容器内不可靠，直接读 CLI
3. 全 0 降级：心跳优先，指标尽力而为
"""
from __future__ import annotations

import platform

from .tegra import TegraSampler

_nvm = None           # pynvml 句柄（None=未探测，False=不可用）
tegra = TegraSampler()


def _try_pynvml():
    global _nvm
    if _nvm is not None:
        return _nvm
    try:
        import pynvml  # nvidia-ml-py
        pynvml.nvmlInit()
        _nvm = pynvml
    except Exception:
        _nvm = False
    return _nvm


async def start() -> None:
    """Agent 启动时调用：若 pynvml 不可用则起 tegrastats 后台。"""
    if not _try_pynvml():
        await tegra.start()


def sample() -> dict:
    """返回 {gpu_util, vram, temp, power}；任何来源失败都返回全 0。"""
    nvm = _try_pynvml()
    if nvm:
        try:
            return _sample_nvm(nvm)
        except Exception:
            pass
    t = tegra.sample()
    if t["gpu_util"] or t["vram"]:
        return t
    return {"gpu_util": 0.0, "vram": 0.0, "temp": 0.0, "power": 0.0}


def _sample_nvm(nvm) -> dict:
    from pynvml import (nvmlDeviceGetHandleByIndex,
                        nvmlDeviceGetMemoryInfo,
                        nvmlDeviceGetPowerUsage,
                        nvmlDeviceGetTemperature,
                        nvmlDeviceGetTemperatureThreshold,
                        nvmlDeviceGetUtilizationRates)
    util_sum = vram_sum = temp_max = power_sum = 0.0
    count = 0
    try:
        from pynvml import nvmlDeviceGetCount
        count = nvmlDeviceGetCount()
    except Exception:
        count = 1
    mem_total_max = 0.0
    for i in range(count):
        h = nvmlDeviceGetHandleByIndex(i)
        u = nvmlDeviceGetUtilizationRates(h)
        m = nvmlDeviceGetMemoryInfo(h)
        util_sum += u.gpu
        mem_total_max = max(mem_total_max, float(m.total))
        vram_sum += float(m.used)
        try:
            t = nvmlDeviceGetTemperature(h, 0)  # NVML_TEMPERATURE_GPU
            temp_max = max(temp_max, float(t))
        except Exception:
            pass
        try:
            p = nvmlDeviceGetPowerUsage(h) / 1000.0  # mW→W
            power_sum += p
        except Exception:
            pass
    n = max(count, 1)
    return {
        "gpu_util": round(util_sum / n, 1),
        "vram": round(vram_sum / mem_total_max * 100, 1) if mem_total_max else 0.0,
        "temp": temp_max,
        "power": round(power_sum, 1),
    }
