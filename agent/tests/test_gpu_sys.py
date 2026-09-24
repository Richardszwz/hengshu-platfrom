"""GPU 采集降级链测试（无 pynvml / 无 tegrastats → 全 0；tegrastats 解析接入）。"""
import asyncio

from app.collectors import gpu_sys


def setup_function():
    gpu_sys._nvm = None  # 重置探测缓存


def test_try_pynvml_caches_result():
    r1 = gpu_sys._try_pynvml()
    r2 = gpu_sys._try_pynvml()
    assert r1 is r2  # 二次调用走缓存


def test_sample_zero_when_no_source(monkeypatch):
    # 强制 pynvml 不可用 + tegrastats 不存在
    monkeypatch.setattr(gpu_sys, "_nvm", False)
    monkeypatch.setattr(gpu_sys.tegra, "_latest",
                        {"gpu_util": 0, "vram": 0.0, "temp": 0.0, "power": 0.0})
    m = gpu_sys.sample()
    assert m == {"gpu_util": 0.0, "vram": 0.0, "temp": 0.0, "power": 0.0}


def test_sample_uses_tegra_when_no_nvml(monkeypatch):
    monkeypatch.setattr(gpu_sys, "_nvm", False)
    monkeypatch.setattr(gpu_sys.tegra, "_latest",
                        {"gpu_util": 42, "vram": 10.4, "temp": 35.5, "power": 5.4})
    m = gpu_sys.sample()
    assert m["gpu_util"] == 42 and m["vram"] == 10.4


def test_start_without_sources_no_crash(monkeypatch):
    monkeypatch.setattr(gpu_sys, "_nvm", False)
    monkeypatch.setattr(gpu_sys.tegra, "start", _noop)
    asyncio.run(gpu_sys.start())


async def _noop():
    return None
