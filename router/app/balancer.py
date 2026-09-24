"""负载评分调度器（方案书 §3.4 定稿口径）。

score = 0.35·(1−GPU利用率) + 0.25·(1−显存占用) + 0.25·(1−队列饱和度) + 0.15·(1−TTFT 相对水位)
      ∈ [0,1]，越大越闲。
粘性：已绑定实例的有效分 = score ÷ sticky_factor(0.7)（≈1.43× 空闲度加成），
除非明显过载，否则续打到原实例——保住 KV cache 前缀（方案书"粘性 ×0.7"决策，
实现取倒数域：分越高越闲，故除性放大）。
会话在新模型或原实例死亡时重新选择；指标归一化分位使用固定水位分母。
"""
from __future__ import annotations

from .config import settings
from .registry import Instance

W_GPU, W_VRAM, W_QUEUE, W_TTFT = 0.35, 0.25, 0.25, 0.15

QUEUE_NORM = 8.0     # 队列长度归一化分母（饱和 = 8 个排队请求）
TTFT_NORM = 2000.0   # TTFT 归一化分母（ms，2s 视为满载水位）


def score(inst: Instance) -> float:
    m = inst.metrics
    gpu = _clamp01(m.get("m_gpu_util", 0.0) / 100.0)
    vram = _clamp01(m.get("m_vram", 0.0) / 100.0)
    queue = _clamp01(m.get("m_queue", 0.0) / QUEUE_NORM)
    ttft = _clamp01(m.get("m_ttft", 0.0) / TTFT_NORM)
    return (W_GPU * (1 - gpu) + W_VRAM * (1 - vram)
            + W_QUEUE * (1 - queue) + W_TTFT * (1 - ttft))


def pick(candidates: list[Instance], bound_inst_id: str | None = None) -> Instance:
    """从候选中选一个实例。绑定实例健康时按粘性系数加成保留。"""
    if not candidates:
        raise LookupError("no live instance for model")
    if len(candidates) == 1:
        return candidates[0]
    best, best_s = candidates[0], -1.0
    for inst in candidates:
        s = score(inst)
        if bound_inst_id and inst.inst_id == bound_inst_id:
            s /= settings.sticky_factor      # 空闲度域反向放大：÷0.7 ≈ 1.43× 偏好
        if s > best_s:
            best, best_s = inst, s
    return best


def _clamp01(v: float) -> float:
    return 0.0 if v < 0 else (1.0 if v > 1 else v)
