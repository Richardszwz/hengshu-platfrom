"""STD 信号求值器：keyword / context / structure。

每个求值器输入统一为 ReqCtx，输出 (passed: bool, note: str)。
规则的 when 块里出现的条件全部 AND；条件缺省=不检。
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

_CJK_RE = re.compile(r"[一-鿿぀-ヿ가-힯]")
_CODE_FENCE_RE = re.compile(r"```|~~~|^\s{4}\S", re.M)


@dataclass
class ReqCtx:
    prompt: str = ""            # 最后一条 user 消息
    all_text: str = ""          # 全部消息拼接（keyword_scope=all 用）
    ctx_tokens: int = 0         # 上下文 token 估算（调用方给）
    hints: dict = field(default_factory=dict)


def eval_keyword(when: dict, ctx: ReqCtx) -> tuple[bool, str]:
    kws = [k for k in (when.get("keywords") or []) if k]
    if not kws:
        return True, ""
    scope = when.get("keyword_scope", "last_user")
    hay = (ctx.all_text if scope == "all" else ctx.prompt).lower()
    for k in kws:
        if k.lower() in hay:
            return True, f"kw:{k}"
    return False, "keyword_miss"


def eval_context(when: dict, ctx: ReqCtx) -> tuple[bool, str]:
    lo = when.get("ctx_min")
    hi = when.get("ctx_max")
    if lo is None and hi is None:
        return True, ""
    if lo is not None and ctx.ctx_tokens < int(lo):
        return False, f"ctx<{lo}"
    if hi is not None and ctx.ctx_tokens > int(hi):
        return False, f"ctx>{hi}"
    return True, f"ctx:{ctx.ctx_tokens}"


def eval_structure(when: dict, ctx: ReqCtx) -> tuple[bool, str]:
    want_code = when.get("has_code_block")
    if want_code is not None:
        has = bool(_CODE_FENCE_RE.search(ctx.prompt))
        if has != bool(want_code):
            return False, f"code_block={has}"
    want_lang = when.get("lang")
    if want_lang:
        text = ctx.prompt
        cjk = len(_CJK_RE.findall(text))
        ratio = cjk / max(1, len(text))
        lang = "zh" if ratio > 0.2 else "en"
        if lang != want_lang:
            return False, f"lang={lang}"
    return True, ""


# 信号 → 所需授权能力位；条件用到而能力未授权 → 规则整体跳过（授权诚实）
SIGNAL_CAPS = {
    "keywords": "keyword", "keyword_scope": "keyword",
    "ctx_min": "context", "ctx_max": "context",
    "has_code_block": "structure", "lang": "structure",
    "semantic": "semantic",
    "judge": "judge",
}

EVALUATORS = {
    "keyword": eval_keyword,
    "context": eval_context,
    "structure": eval_structure,
}
