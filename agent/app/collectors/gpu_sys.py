"""主机 GPU 指标采集（架构感知）。

x86_64  → pynvml（NVIDIA 管理库，覆盖 L4/L40S/RTX6000/H100 PCIe）
aarch64 → Jetson：优先 jtop 的 tegrastats 数据；无 jtop 时降级 /sys 读不到即返回 0
          （P1 接 Jetson 实机后补 tegrastats 解析，接口已留好）
任一来源失败都静默归零，不影响心跳与框架级指标。
"""
from __future__ import annotations

import logging

from .base import ARCH

log = logging.getLogger("gpu-sys")


class GpuSysSampler:
    def __init__(self) -> None:
        self._backend = "none"
        self._nvml = None
        self._init()

    def _init(self) -> None:
        if ARCH == "x86_64":
            try:
                import pynvml  # type: ignore

                pynvml.nvmlInit()
                self._nvml = pynvml
                self._backend = "nvml"
                return
            except Exception as e:  # noqa: BLE001
                log.warning("pynvml init failed, gpu sys metrics disabled: %s", e)
        elif ARCH == "aarch64":
            # P1：接 jtop 服务（pip install jtop + systemctl）或解析 tegrastats
            log.info("aarch64 detected (Jetson?): gpu sys metrics via jtop — P1")
        else:
            log.info("unknown arch %s: gpu sys metrics disabled", ARCH)

    def sample(self) -> dict[str, float]:
        if self._backend == "nvml" and self._nvml:
            try:
                return self._nvml_sample()
            except Exception as e:  # noqa: BLE001
                log.warning("nvml sample failed: %s", e)
        return {"gpu_util": 0.0, "vram": 0.0}

    def _nvml_sample(self) -> dict[str, float]:
        nvml, n = self._nvml, nvml.nvmlDeviceGetCount()
        utils, vrams = [], []
        for i in range(n):
            h = nvml.nvmlDeviceGetHandleByIndex(i)
            utils.append(nvml.nvmlDeviceGetUtilizationRates(h).gpu)
            mem = nvml.nvmlDeviceGetMemoryInfo(h)
            vrams.append(100.0 * mem.used / mem.total if mem.total else 0.0)
        return {
            "gpu_util": sum(utils) / len(utils) if utils else 0.0,
            "vram": sum(vrams) / len(vrams) if vrams else 0.0,
        }
