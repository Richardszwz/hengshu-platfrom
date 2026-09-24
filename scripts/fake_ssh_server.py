"""一次性诊断用假 SSH 服务器（asyncssh）：接受任意密码，exec 返回罐头输出。

用途：本地复现安装器完整 happy path（uname/docker/nvidia-smi/docker run），
验证 console→SSH 全链路不 500。诊断完即弃，不进 git。
"""
import asyncio

import asyncssh

CANNED = {
    "uname -m": "x86_64",
    "docker info --format '{{.ServerVersion}}' 2>/dev/null": "24.0.7",
    "nvidia-smi -L 2>/dev/null | head -1": "GPU 0: NVIDIA L40S (UUID: GPU-fake-0001)",
    "docker rm -f hengshu-agent 2>/dev/null; true": "",
}


class _Session(asyncssh.SSHServerSession):
    def __init__(self):
        self._cmd = ""
        self._chan = None

    def connection_made(self, chan):
        self._chan = chan

    def exec_requested(self, command):
        self._cmd = command
        return True

    def shell_requested(self):
        return True

    def session_started(self):
        if self._cmd:
            out = CANNED.get(self._cmd)
            if out is None and self._cmd.startswith("docker run"):
                out = "f" * 64  # 假容器 ID
            self._chan.write(out or "")
            self._chan.write_eof()
            self._chan.exit(0 if out is not None else 127)
        else:
            self._chan.write("fake-shell$ ")
            self._chan.write_eof()
            self._chan.exit(0)


class _Server(asyncssh.SSHServer):
    def password_auth_supported(self):
        return True

    def validate_password(self, username, password):
        print(f"[fake-ssh] auth {username}/{'*' * len(password)}")
        return True

    def session_requested(self):
        return _Session()


async def main():
    await asyncssh.create_server(
        _Server, "127.0.0.1", 2299,
        server_host_keys=[asyncssh.generate_private_key("ssh-rsa")],
    )
    print("[fake-ssh] listening on 127.0.0.1:2299 (any password)")
    await asyncio.Event().wait()


if __name__ == "__main__":
    asyncio.run(main())
