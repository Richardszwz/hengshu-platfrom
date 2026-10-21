"""指标采集插件抽象层。

推理节点异构：
- x86（L40S/RTX6000/H100 PCIe）→ nvidia-smi / pynvml
- ARM64（Jetson Orin/Nano）    → jtop(tegrastats) 或 /sys 接口降级
采集失败不允许拖垮心跳——单插件异常返回空指标。
"""
from __future__ import annotations

import platform
from dataclasses import dataclass, field
from typing import Protocol

# 运行期架构标记：'x86_64' | 'aarch64'
ARCH = platform.machine()


@dataclass
class Sample:
    """单实例一次采样的归一化指标（balancer 直接消费，口径统一在此层）。"""
    gpu_util: float = 0.0      # %
    vram: float = 0.0          # %
    queue: float = 0.0         # 排队请求数
    ttft: float = 0.0          # ms（近窗口平均）
    tokens_per_s: float = 0.0
    sessions: int = 0

    def as_metrics(self) -> dict[str, float]:
        return {
            "m_gpu_util": round(self.gpu_util, 1),
            "m_vram": round(self.vram, 1),
            "m_queue": round(self.queue, 1),
            "m_ttft": round(self.ttft, 1),
            "m_tps": round(self.tokens_per_s, 1),
        }


class Collector(Protocol):
    """每推理框架一个插件。probe 失败抛异常，main 层捕获并跳过该实例。"""

    framework: str

    async def probe(self, base_url: str) -> bool: ...
    async def collect(self, base_url: str) -> Sample: ...


@dataclass
class CollectorRegistry:
    plugins: dict[str, Collector] = field(default_factory=dict)

    def register(self, c: Collector) -> None:
        self.plugins[c.framework] = c

    def get(self, framework: str) -> Collector | None:
        return self.plugins.get(framework)
