"""生成控制台 admin 密码的 argon2 哈希，写入 .env。

用法：python scripts/gen_admin_hash.py <密码>
"""
import sys

from argon2 import PasswordHasher

if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("用法: python scripts/gen_admin_hash.py <密码>")
    print(PasswordHasher().hash(sys.argv[1]))
