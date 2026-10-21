#!/usr/bin/env python3
"""衡枢 License 签发工具（我方持有，不进交付包）。

用法：
  python scripts/gen_license.py --gen-key          # 生成密钥对（首次）
  python scripts/gen_license.py --pubkey           # 打印公钥（嵌回 libs/hslicense）
  python scripts/gen_license.py --sign \
      --customer "XX公司" --fingerprint sha256:... \
      --base-mode subscription --expires 2027-12-31 --max-nodes 8 \
      --edition PRO [--caps keyword,context,semantic] [--engine-expires 2027-12-31] \
      [--version-cap 1.x] [-o license.json]
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from libs.hslicense import ALL_CAPS, EDITION_CAPS, cluster_fingerprint, gen_keypair, sign_payload

# 密钥目录：源码模式=脚本旁 dev_keys/；PyInstaller exe 模式=exe 旁 license_keys/
if getattr(sys, "frozen", False):
    KEY_DIR = Path(sys.executable).resolve().parent / "license_keys"
else:
    KEY_DIR = Path(__file__).resolve().parent / "dev_keys"
SK = KEY_DIR / "license_private.pem"
PK = KEY_DIR / "license_public.pem"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--gen-key", action="store_true")
    ap.add_argument("--pubkey", action="store_true")
    ap.add_argument("--sign", action="store_true")
    ap.add_argument("--customer", default="")
    ap.add_argument("--fingerprint", default="")
    ap.add_argument("--base-mode", choices=["subscription", "perpetual"],
                    default="subscription")
    ap.add_argument("--expires", default="", help="基本 license 到期 YYYY-MM-DD（订阅制）")
    ap.add_argument("--engine-expires", default="", help="引擎包到期（缺省同基本）")
    ap.add_argument("--max-nodes", type=int, default=8)
    ap.add_argument("--version-cap", default="1.x")
    ap.add_argument("--edition", choices=["STD", "PRO", "ENT"], default="STD")
    ap.add_argument("--caps", default="", help="逗号分隔；缺省=edition 全集")
    ap.add_argument("-o", "--out", default="")
    ap.add_argument("--key-dir", default="",
                    help="密钥目录（缺省=exe 旁 license_keys/ 或脚本旁 dev_keys/）")
    args = ap.parse_args()

    key_dir = Path(args.key_dir).resolve() if args.key_dir else KEY_DIR
    sk, pk = key_dir / "license_private.pem", key_dir / "license_public.pem"

    if args.gen_key:
        key_dir.mkdir(parents=True, exist_ok=True)
        sk_b, pk_b = gen_keypair()
        sk.write_bytes(sk_b)
        pk.write_bytes(pk_b)
        print(f"已生成：\n  私钥 {sk}（保密！）\n  公钥 {pk}")
        return

    if args.pubkey:
        print(pk.read_text())
        return

    if args.sign:
        if not sk.exists():
            sys.exit(f"先 --gen-key 生成密钥对（{key_dir}）")
        def ts(d: str) -> int:
            return int(time.mktime(time.strptime(d, "%Y-%m-%d"))) if d else 0
        caps = ([c.strip() for c in args.caps.split(",") if c.strip()]
                if args.caps else EDITION_CAPS[args.edition])
        bad = [c for c in caps if c not in ALL_CAPS]
        if bad:
            sys.exit(f"非法 caps: {bad}")
        payload = {
            "customer": args.customer,
            "fingerprint": args.fingerprint or cluster_fingerprint(),
            "base": {"mode": args.base_mode, "max_nodes": args.max_nodes,
                     "expires": ts(args.expires), "version_cap": args.version_cap},
            "engine": {"edition": args.edition, "caps": caps,
                       "expires": ts(args.engine_expires) or ts(args.expires)},
        }
        doc = sign_payload(payload, sk.read_bytes())
        text = json.dumps(doc, ensure_ascii=False, indent=2)
        if args.out:
            Path(args.out).write_text(text, encoding="utf-8")
            print(f"已签发 → {args.out}")
        else:
            print(text)
        return

    ap.print_help()


if __name__ == "__main__":
    main()
