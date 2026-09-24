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


def test_docker_run_argv(captured):
    req = StartRequest(
        name="qwen3-30b", start_cmd="vllm serve /models/qwen3 --port 8000",
        workdir="/models", env={"HF_HOME": "/hf"}, port=8000,
    )
    asyncio_run(start_docker(req))
    argv = captured[0]
    assert argv[:5] == ["docker", "run", "-d", "--name", "hs-svc-qwen3-30b"]
    assert "--restart" in argv and "unless-stopped" in argv
    assert "--gpus" in argv and "all" in argv
    assert "-p" in argv and "8000:8000" in argv
    assert "-w" in argv and "/models" in argv
    assert "-e" in argv and "HF_HOME=/hf" in argv
    assert argv[-4:] == ["vllm/vllm-openai:latest", "sh", "-c",
                         "vllm serve /models/qwen3 --port 8000"]


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
