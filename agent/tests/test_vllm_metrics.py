"""vllm 指标差分：counter→速率/窗口平均 TTFT。"""
import asyncio

from app.collectors import vllm as V

METRICS_1 = '''vllm:num_requests_running{engine="0",model_name="m"} 1.0
vllm:num_requests_waiting{engine="0",model_name="m"} 0.0
vllm:generation_tokens_total{engine="0",model_name="m"} 1000.0
vllm:time_to_first_token_seconds_sum{engine="0",model_name="m"} 1.0
vllm:time_to_first_token_seconds_count{engine="0",model_name="m"} 10.0
'''
METRICS_2 = '''vllm:num_requests_running{engine="0",model_name="m"} 2.0
vllm:num_requests_waiting{engine="0",model_name="m"} 3.0
vllm:generation_tokens_total{engine="0",model_name="m"} 1300.0
vllm:time_to_first_token_seconds_sum{engine="0",model_name="m"} 1.5
vllm:time_to_first_token_seconds_count{engine="0",model_name="m"} 15.0
'''


class _Resp:
    def __init__(self, text):
        self.text = text
        self.status_code = 200


class _Client:
    def __init__(self, text):
        self._text = text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def get(self, url):
        return _Resp(self._text)


def test_counter_delta_to_rate(monkeypatch):
    # 不打 time：asyncio 内部也调 monotonic。TTFT 与 dt 无关可精确断言，TPS 断 >0。
    monkeypatch.setattr(V.httpx, "AsyncClient", lambda timeout: _Client(METRICS_1))
    c = V.VllmCollector()
    s1 = asyncio.run(c.collect("http://x:8000"))
    assert s1.tokens_per_s == 0.0 and s1.ttft == 0.0  # 首样本无前值

    monkeypatch.setattr(V.httpx, "AsyncClient", lambda timeout: _Client(METRICS_2))
    s2 = asyncio.run(c.collect("http://x:8000"))
    assert s2.tokens_per_s > 0                # 300 tok 增量 / 实测 dt
    assert s2.ttft == 100.0                   # (1.5-1.0)s / (15-10) = 0.1s = 100ms
    assert s2.sessions == 2 and s2.queue == 3.0
    V._PREV.clear()
