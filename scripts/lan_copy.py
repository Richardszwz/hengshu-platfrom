"""内网免密大文件拷贝：tar 流 从 SRC_HOST 管道到 DST_HOST（经本机中转，双端互不信任也能拷）。

用法: env SRC_HOST= SRC_USER= SRC_PASS= SRC_DIR= SRC_NAME= DST_HOST= DST_USER= DST_PASS= DST_DIR=
      python lan_copy.py
"""
import asyncio
import os
import sys
import time

import asyncssh

SRC_HOST = os.environ["SRC_HOST"]
SRC_USER = os.environ["SRC_USER"]
SRC_PASS = os.environ["SRC_PASS"]
SRC_DIR = os.environ["SRC_DIR"]      # tar 的 -C 目录
SRC_NAME = os.environ["SRC_NAME"]    # 要拷的目录名
DST_HOST = os.environ["DST_HOST"]
DST_USER = os.environ["DST_USER"]
DST_PASS = os.environ["DST_PASS"]
DST_DIR = os.environ["DST_DIR"]      # 解包目标目录


async def main() -> int:
    async with asyncssh.connect(SRC_HOST, username=SRC_USER, password=SRC_PASS,
                                known_hosts=None) as src, \
               asyncssh.connect(DST_HOST, username=DST_USER, password=DST_PASS,
                                known_hosts=None) as dst:
        sp = await src.create_process(f"tar cf - -C {SRC_DIR} {SRC_NAME}", encoding=None)
        dp = await dst.create_process(f"mkdir -p {DST_DIR} && tar xf - -C {DST_DIR}",
                                      encoding=None)
        t0 = time.time()
        total = 0
        last_log = t0
        while True:
            chunk = await sp.stdout.read(1 << 20)
            if not chunk:
                break
            dp.stdin.write(chunk)
            await dp.stdin.drain()
            total += len(chunk)
            now = time.time()
            if now - last_log >= 20:
                print(f"[copy] {total / 2**30:.1f} GiB  {total / 2**20 / (now - t0):.0f} MB/s",
                      flush=True)
                last_log = now
        dp.stdin.write_eof()
        src_rc = await sp.wait(check=False)
        dst_rc = await dp.wait(check=False)
        dt = time.time() - t0
        print(f"[copy] DONE {total / 2**30:.2f} GiB in {dt:.0f}s "
              f"({total / 2**20 / dt:.0f} MB/s) src_rc={src_rc.exit_status} dst_rc={dst_rc.exit_status}")
        return 0 if src_rc.exit_status == 0 and dst_rc.exit_status == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
