"""调度器单测：评分口径 + 粘性 + 健康过滤。"""
import pytest

from app.balancer import pick, score
from app.registry import Instance


def mk(name: str, gpu: float, vram: float, queue: float, ttft: float) -> Instance:
    return Instance(node_id=name.split(":")[0], port=int(name.split(":")[1]),
                    model="m", framework="vllm", url=f"http://{name}",
                    metrics={"m_gpu_util": gpu, "m_vram": vram,
                             "m_queue": queue, "m_ttft": ttft})


class TestScore:
    def test_idle_beats_busy(self):
        idle = mk("a:1", 10, 10, 0, 100)
        busy = mk("b:1", 95, 95, 8, 1800)
        assert score(idle) > score(busy)

    def test_full_bounds(self):
        full = mk("a:1", 100, 100, 99, 99999)
        assert score(full) == pytest.approx(0.0, abs=1e-6)
        empty = mk("a:1", 0, 0, 0, 0)
        assert score(empty) == pytest.approx(1.0, abs=1e-6)


class TestPick:
    def test_picks_freest(self):
        cands = [mk("a:1", 90, 90, 8, 1500), mk("b:1", 20, 20, 1, 200)]
        assert pick(cands).node_id == "b"

    def test_sticky_keeps_moderate_bound(self):
        bound = mk("a:1", 30, 30, 2, 400)
        other = mk("b:1", 20, 20, 1, 200)
        # 绑定实例负载略高（30% vs 20% GPU）÷0.7 加成后应保留；若真过载则让位
        assert pick([other, bound], bound_inst_id="a:1").node_id == "a"

    def test_sticky_drops_overloaded_bound(self):
        bound = mk("a:1", 100, 100, 10, 2000)
        other = mk("b:1", 5, 5, 0, 100)
        assert pick([other, bound], bound_inst_id="a:1").node_id == "b"

    def test_single_candidate(self):
        only = mk("a:1", 50, 50, 1, 300)
        assert pick([only]).inst_id == "a:1"

    def test_no_candidate_raises(self):
        with pytest.raises(LookupError):
            pick([])
