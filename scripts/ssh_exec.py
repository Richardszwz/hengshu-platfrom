"""一次性 SSH 命令执行（asyncssh，非交互）。诊断/运维用。

用法：SSH_HOST=... SSH_USER=dgx SSH_PASS=... python scripts/ssh_exec.py "cmd"
"""
import asyncio
import os
import sys

import asyncssh


async def main() -> None:
    host = os.environ["SSH_HOST"]
    user = os.environ.get("SSH_USER", "dgx")
    pwd = os.environ["SSH_PASS"]
    cmd = sys.argv[1] if len(sys.argv) > 1 else "uname -a"
    async with asyncssh.connect(host=host, username=user, password=pwd,
                                known_hosts=None, connect_timeout=10) as conn:
        r = await conn.run(cmd)
        sys.stdout.write(r.stdout or "")
        if r.stderr:
            sys.stderr.write(r.stderr)


if __name__ == "__main__":
    asyncio.run(main())
