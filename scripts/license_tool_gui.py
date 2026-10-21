#!/usr/bin/env python3
"""衡枢 License 签发工具 GUI（Windows 表单版）。

复用 libs/hslicense 签名核心；密钥默认 exe 旁 license_keys\\。
打包：pyinstaller --onefile --windowed --name hs-license-tool-gui
"""
from __future__ import annotations

import json
import sys
import time
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from libs.hslicense import ALL_CAPS, EDITION_CAPS, cluster_fingerprint, gen_keypair, sign_payload

if getattr(sys, "frozen", False):
    KEY_DIR = Path(sys.executable).resolve().parent / "license_keys"
else:
    KEY_DIR = Path(__file__).resolve().parent / "dev_keys"

BG = "#0b1420"
CARD = "#101c2c"
FG = "#d8e6f3"
DIM = "#7d93ac"
CYAN = "#37b6ff"
GREEN = "#3dd68c"
YELLOW = "#ffc857"
RED = "#ff6b6b"
CAP_LABELS = {
    "keyword": "关键字", "context": "上下文长度", "structure": "结构特征",
    "semantic": "语义例句", "replay": "决策回放", "alpha": "α激进度",
    "judge": "裁判", "affinity": "前缀亲和", "auto": "自学习统计",
    "tenant_isolation": "租户隔离",
}


class App(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("衡枢 License 签发工具")
        self.geometry("640x780")
        self.configure(bg=BG)
        self.key_dir = tk.StringVar(value=str(KEY_DIR))
        self._style()
        self._build()
        self._refresh_key_state()

    def _style(self) -> None:
        st = ttk.Style(self)
        st.theme_use("clam")
        st.configure("TFrame", background=BG)
        st.configure("Card.TFrame", background=CARD)
        st.configure("TLabel", background=BG, foreground=FG, font=("Microsoft YaHei UI", 10))
        st.configure("Card.TLabel", background=CARD, foreground=FG)
        st.configure("Dim.TLabel", background=CARD, foreground=DIM, font=("Microsoft YaHei UI", 9))
        st.configure("H.TLabel", background=CARD, foreground=CYAN,
                     font=("Microsoft YaHei UI", 11, "bold"))
        st.configure("TButton", font=("Microsoft YaHei UI", 10), padding=6)
        st.configure("Accent.TButton", background=CYAN, foreground="#04121c")
        st.map("Accent.TButton", background=[("active", "#5ec8ff")])
        st.configure("TRadiobutton", background=CARD, foreground=FG)
        st.configure("TCheckbutton", background=CARD, foreground=FG)
        st.map("TRadiobutton", background=[("active", CARD)])
        st.map("TCheckbutton", background=[("active", CARD)])

    def _entry(self, parent, var, width=40, show=None):
        e = tk.Entry(parent, textvariable=var, width=width, show=show,
                     bg="#0a1a2a", fg=FG, insertbackground=FG, relief="flat",
                     font=("Consolas", 10))
        e.pack(side="left", padx=4, ipady=3)
        return e

    def _build(self) -> None:
        # ── 密钥区 ──
        kf = ttk.Frame(self, style="Card.TFrame")
        kf.pack(fill="x", padx=12, pady=(12, 6))
        ttk.Label(kf, text="① 密钥", style="H.TLabel").pack(anchor="w", padx=10, pady=(8, 2))
        row = ttk.Frame(kf, style="Card.TFrame")
        row.pack(fill="x", padx=10)
        ttk.Label(row, text="密钥目录", style="Card.TLabel").pack(side="left")
        self._entry(row, self.key_dir, width=44)
        ttk.Button(row, text="浏览…", command=self._pick_dir).pack(side="left", padx=4)
        row2 = ttk.Frame(kf, style="Card.TFrame")
        row2.pack(fill="x", padx=10, pady=6)
        ttk.Button(row2, text="生成密钥对（首次）", command=self._gen_key).pack(side="left")
        ttk.Button(row2, text="查看公钥", command=self._show_pubkey).pack(side="left", padx=6)
        self.key_state = ttk.Label(kf, text="", style="Dim.TLabel")
        self.key_state.pack(anchor="w", padx=10, pady=(0, 8))

        # ── 客户与基本 License ──
        bf = ttk.Frame(self, style="Card.TFrame")
        bf.pack(fill="x", padx=12, pady=6)
        ttk.Label(bf, text="② 客户与基本 License", style="H.TLabel").pack(anchor="w", padx=10, pady=(8, 2))
        self.customer = tk.StringVar()
        self.fingerprint = tk.StringVar()
        self.base_mode = tk.StringVar(value="subscription")
        self.expires = tk.StringVar()
        self.max_nodes = tk.IntVar(value=8)
        self.version_cap = tk.StringVar(value="1.x")
        r = ttk.Frame(bf, style="Card.TFrame")
        r.pack(fill="x", padx=10, pady=2)
        ttk.Label(r, text="客户名称", style="Card.TLabel", width=12).pack(side="left")
        self._entry(r, self.customer, 44)
        r = ttk.Frame(bf, style="Card.TFrame")
        r.pack(fill="x", padx=10, pady=2)
        ttk.Label(r, text="集群指纹", style="Card.TLabel", width=12).pack(side="left")
        self._entry(r, self.fingerprint, 44)
        ttk.Button(r, text="本机指纹", command=lambda: self.fingerprint.set(cluster_fingerprint())).pack(side="left")
        r = ttk.Frame(bf, style="Card.TFrame")
        r.pack(fill="x", padx=10, pady=2)
        ttk.Label(r, text="授权模式", style="Card.TLabel", width=12).pack(side="left")
        ttk.Radiobutton(r, text="订阅年费", value="subscription", variable=self.base_mode).pack(side="left")
        ttk.Radiobutton(r, text="买断（永久当前大版本）", value="perpetual", variable=self.base_mode).pack(side="left", padx=8)
        r = ttk.Frame(bf, style="Card.TFrame")
        r.pack(fill="x", padx=10, pady=2)
        ttk.Label(r, text="到期日", style="Card.TLabel", width=12).pack(side="left")
        self._entry(r, self.expires, 12)
        ttk.Label(r, text="YYYY-MM-DD（订阅制必填；买断留空）", style="Dim.TLabel").pack(side="left")
        r = ttk.Frame(bf, style="Card.TFrame")
        r.pack(fill="x", padx=10, pady=2)
        ttk.Label(r, text="节点上限", style="Card.TLabel", width=12).pack(side="left")
        self._entry(r, self.max_nodes, 8)
        ttk.Label(r, text="0 = 不限", style="Dim.TLabel").pack(side="left")
        r = ttk.Frame(bf, style="Card.TFrame")
        r.pack(fill="x", padx=10, pady=(2, 8))
        ttk.Label(r, text="版本范围", style="Card.TLabel", width=12).pack(side="left")
        self._entry(r, self.version_cap, 8)
        ttk.Label(r, text="买断制生效（如 1.x）", style="Dim.TLabel").pack(side="left")

        # ── 引擎授权 ──
        ef = ttk.Frame(self, style="Card.TFrame")
        ef.pack(fill="x", padx=12, pady=6)
        ttk.Label(ef, text="③ 路由引擎授权", style="H.TLabel").pack(anchor="w", padx=10, pady=(8, 2))
        self.edition = tk.StringVar(value="STD")
        self.engine_expires = tk.StringVar()
        r = ttk.Frame(ef, style="Card.TFrame")
        r.pack(fill="x", padx=10, pady=2)
        ttk.Label(r, text="版本", style="Card.TLabel", width=12).pack(side="left")
        for ed in ("STD", "PRO", "ENT"):
            ttk.Radiobutton(r, text=ed, value=ed, variable=self.edition,
                            command=self._apply_edition).pack(side="left", padx=8)
        r = ttk.Frame(ef, style="Card.TFrame")
        r.pack(fill="x", padx=10, pady=2)
        ttk.Label(r, text="引擎到期", style="Card.TLabel", width=12).pack(side="left")
        self._entry(r, self.engine_expires, 12)
        ttk.Label(r, text="留空 = 同基本 License", style="Dim.TLabel").pack(side="left")
        ttk.Label(ef, text="能力位（默认=版本全集，可裁剪）：", style="Dim.TLabel").pack(anchor="w", padx=10)
        self.cap_vars: dict[str, tk.BooleanVar] = {}
        grid = ttk.Frame(ef, style="Card.TFrame")
        grid.pack(fill="x", padx=10, pady=(2, 8))
        for i, cap in enumerate(ALL_CAPS):
            v = tk.BooleanVar(value=False)
            self.cap_vars[cap] = v
            ttk.Checkbutton(grid, text=f"{CAP_LABELS[cap]} ({cap})",
                            variable=v).grid(row=i // 3, column=i % 3, sticky="w", padx=6, pady=1)

        # ── 签发 ──
        sf = ttk.Frame(self, style="Card.TFrame")
        sf.pack(fill="x", padx=12, pady=6)
        ttk.Button(sf, text="④ 签发并保存 license.json", style="Accent.TButton",
                   command=self._sign).pack(padx=10, pady=10, fill="x")
        self.status = tk.Label(self, text="就绪", bg=BG, fg=DIM, anchor="w",
                               font=("Microsoft YaHei UI", 9))
        self.status.pack(fill="x", padx=12, pady=(0, 10))
        self._apply_edition()

    # ── 行为 ──
    def _kd(self) -> Path:
        return Path(self.key_dir.get()).resolve()

    def _say(self, text: str, color: str = DIM) -> None:
        self.status.configure(text=text, fg=color)

    def _refresh_key_state(self) -> None:
        sk = self._kd() / "license_private.pem"
        pk = self._kd() / "license_public.pem"
        if sk.exists() and pk.exists():
            self.key_state.configure(text=f"✓ 密钥对已就绪（{self._kd()}）", foreground=GREEN)
        else:
            self.key_state.configure(text="✗ 未找到密钥对——先「生成密钥对」或浏览到已有目录",
                                     foreground=RED)

    def _pick_dir(self) -> None:
        d = filedialog.askdirectory(initialdir=self.key_dir.get())
        if d:
            self.key_dir.set(d)
            self._refresh_key_state()

    def _gen_key(self) -> None:
        kd = self._kd()
        if (kd / "license_private.pem").exists():
            if not messagebox.askyesno("覆盖确认", "该目录已有密钥对，覆盖后旧 license 全部无法验签。确认重新生成？"):
                return
        kd.mkdir(parents=True, exist_ok=True)
        sk, pk = gen_keypair()
        (kd / "license_private.pem").write_bytes(sk)
        (kd / "license_public.pem").write_bytes(pk)
        self._refresh_key_state()
        self._say(f"密钥对已生成：{kd}（私钥务必保密备份）", GREEN)

    def _show_pubkey(self) -> None:
        pk = self._kd() / "license_public.pem"
        if not pk.exists():
            messagebox.showerror("无公钥", "先生成密钥对")
            return
        win = tk.Toplevel(self)
        win.title("公钥（交给研发嵌入平台 / HS_LICENSE_PUBKEY）")
        win.configure(bg=BG)
        t = tk.Text(win, bg="#0a1a2a", fg=FG, relief="flat", font=("Consolas", 9),
                    width=80, height=6)
        t.pack(padx=10, pady=10)
        t.insert("1.0", pk.read_text().strip())
        t.configure(state="disabled")

    def _apply_edition(self) -> None:
        on = set(EDITION_CAPS[self.edition.get()])
        for cap, v in self.cap_vars.items():
            v.set(cap in on)

    @staticmethod
    def _ts(s: str) -> int:
        return int(time.mktime(time.strptime(s.strip(), "%Y-%m-%d"))) if s.strip() else 0

    def _sign(self) -> None:
        try:
            if not self.customer.get().strip():
                raise ValueError("客户名称不能为空")
            if not self.fingerprint.get().strip().startswith("sha256:"):
                raise ValueError("集群指纹须以 sha256: 开头（客户在 系统设置→授权 页复制）")
            base_exp = self._ts(self.expires.get())
            if self.base_mode.get() == "subscription" and not base_exp:
                raise ValueError("订阅制必须填到期日")
            caps = [c for c, v in self.cap_vars.items() if v.get()]
            if not caps:
                raise ValueError("至少勾选一个能力位")
            sk = self._kd() / "license_private.pem"
            if not sk.exists():
                raise ValueError("私钥不存在——先生成密钥对")
            payload = {
                "customer": self.customer.get().strip(),
                "fingerprint": self.fingerprint.get().strip(),
                "base": {"mode": self.base_mode.get(),
                         "max_nodes": int(self.max_nodes.get() or 0),
                         "expires": base_exp,
                         "version_cap": self.version_cap.get().strip() or "1.x"},
                "engine": {"edition": self.edition.get(), "caps": caps,
                           "expires": self._ts(self.engine_expires.get()) or base_exp},
            }
            doc = sign_payload(payload, sk.read_bytes())
        except Exception as e:
            self._say(f"✗ {e}", RED)
            return
        path = filedialog.asksaveasfilename(
            defaultextension=".json",
            initialfile=f"license-{payload['customer']}.json",
            filetypes=[("衡枢 License", "*.json")])
        if not path:
            return
        Path(path).write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
        self._say(f"✓ 已签发 → {path}", GREEN)


if __name__ == "__main__":
    App().mainloop()
