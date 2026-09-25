"""ServiceCtl docker 后端 argv 构造测试（mock _run 捕获命令行）。"""
import pytest

from app import service_ctl
from app.service_ctl import StartRequest, ServiceCtl, start_docker, stop_docker, logs_docker


@pytest.fixture
def captured(monkeypatch):
    calls: list[list[str]] = []

    async def fake_run(cmd, timeout=30.0):
        calls.append(cmd)
        return 0, "ok-output"

    monkeypatch.setattr(service_ctl, "_docker_available", lambda: True)
    monkeypatch.setattr(service_ctl, "_run", fake_run)
    return calls


def test_docker_run_argv(captured, monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_BACKEND", "docker")
    req = StartRequest(
        name="qwen3-30b", start_cmd="vllm serve /models/qwen3 --port 8000",
        workdir="/models", env={"HF_HOME": "/hf"}, port=8000,
    )
    asyncio_run(start_docker(req))
    # 前面有 is_running / image inspect 探测，取 docker run 那次调用
    argv = next(c for c in captured if c[:2] == ["docker", "run"])
    assert argv[:5] == ["docker", "run", "-d", "--name", "hs-svc-qwen3-30b"]
    assert "--restart" in argv and "unless-stopped" in argv
    assert "--gpus" in argv and "all" in argv
    assert "-p" in argv and "8000:8000" in argv
    assert "-w" in argv and "/models" in argv
    assert "-e" in argv and "HF_HOME=/hf" in argv
    assert argv[-4:] == ["vllm/vllm-openai:latest", "sh", "-c",
                         "vllm serve /models/qwen3 --port 8000"]


def test_docker_pulls_missing_image(monkeypatch):
    """镜像本地不存在（inspect 失败）→ 先 docker pull，再 run。"""
    monkeypatch.setenv("AGENT_SERVICE_BACKEND", "docker")
    calls: list[list[str]] = []

    async def fake_run(cmd, timeout=30.0):
        calls.append(cmd)
        rc = 1 if cmd[:2] == ["docker", "image"] else 0
        return rc, ""

    monkeypatch.setattr(service_ctl, "_docker_available", lambda: True)
    monkeypatch.setattr(service_ctl, "_run", fake_run)
    req = StartRequest(name="m", start_cmd="serve --port 8000", port=8000,
                       image="registry.local/custom:v1")
    asyncio_run(start_docker(req))
    pull = next(c for c in calls if c[:2] == ["docker", "pull"])
    assert pull[2] == "registry.local/custom:v1"
    assert next(c for c in calls if c[:2] == ["docker", "run"])


def test_docker_start_idempotent(monkeypatch):
    """已运行实例再次 start → 直接返回，不再 docker run。"""
    monkeypatch.setenv("AGENT_SERVICE_BACKEND", "docker")

    async def fake_run(cmd, timeout=30.0):
        return 0, "true"

    monkeypatch.setattr(service_ctl, "_docker_available", lambda: True)
    monkeypatch.setattr(service_ctl, "_run", fake_run)
    req = StartRequest(name="m", start_cmd="x", port=8000)
    assert "already running" in asyncio_run(start_docker(req))


def test_docker_stop_argv(captured):
    asyncio_run(stop_docker("qwen3-30b"))
    assert captured[0] == ["docker", "rm", "-f", "hs-svc-qwen3-30b"]


def test_docker_logs_argv(captured):
    asyncio_run(logs_docker("qwen3-30b", 50))
    assert captured[0] == ["docker", "logs", "--tail", "50", "hs-svc-qwen3-30b"]


def test_backend_forced_process(monkeypatch):
    monkeypatch.setenv("AGENT_SERVICE_BACKEND", "process")
    assert service_ctl._backend() == "process"
    monkeypatch.setenv("AGENT_SERVICE_BACKEND", "docker")
    assert service_ctl._backend() == "docker"


def test_ctl_dispatches_by_backend(monkeypatch, captured):
    monkeypatch.setenv("AGENT_SERVICE_BACKEND", "docker")
    ctl = ServiceCtl()
    req = StartRequest(name="m1", start_cmd="sleep 1", port=9000)
    asyncio_run(ctl.start(req))
    assert captured[0][0] == "docker"


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)
