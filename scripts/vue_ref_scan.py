#!/usr/bin/env python3
"""扫描 .vue 文件：模板/脚本里用到的标识符是否都在 script setup 中有定义。
专抓回滚/重构误删定义类 bug（launchNodes/env 事件的同类）。
启发式：提取 script 中的 const/let/function/import/props 声明 + 模板表达式根标识符，差集即嫌疑。"""
import re, sys, glob, json

# JS/Vue 内建与模板编译器注入的标识符
GLOBALS = set("""true false null undefined NaN Infinity Math JSON Object Array String Number Boolean Date
RegExp Error Promise Symbol Map Set WeakMap WeakRef console window document localStorage sessionStorage
fetch alert confirm prompt setTimeout setInterval clearTimeout clearInterval parseInt parseFloat isNaN
encodeURIComponent decodeURIComponent location history navigator FormData URLSearchParams URL Blob File
Event KeyboardEvent MouseEvent XMLHttpRequest ResizeObserver IntersectionObserver getComputedStyle
requestAnimationFrame cancelAnimationFrame structuredClone queueMicrotask Intl BigInt Proxy Reflect
""".split())

def scan(path):
    src = open(path, encoding='utf-8').read()
    m = re.search(r'<script setup[^>]*>(.*?)</script>', src, re.S)
    t = re.search(r'<template>(.*?)</template>', src, re.S)
    if not m or not t:
        return []
    script, template = m.group(1), t.group(1)

    declared = set()
    # import 绑定
    for im in re.finditer(r'import\s+(?:([A-Za-z_$][\w$]*)|(?:\{([^}]*)\})|(?:\*\s+as\s+([A-Za-z_$][\w$]*)))', script):
        if im.group(1): declared.add(im.group(1))
        if im.group(3): declared.add(im.group(3))
        if im.group(2):
            for part in im.group(2).split(','):
                part = part.strip()
                if not part: continue
                # "x as y" 或 "x"
                name = re.split(r'\s+as\s+', part)[-1].strip()
                if name: declared.add(name)
    # const/let/var/function/class 声明（含 async function；解构单独处理）
    for d in re.finditer(r'(?:^|\n)\s*(?:async\s+)?(?:const|let|var|function|class)\s+([A-Za-z_$][\w$]*)', script):
        declared.add(d.group(1))
    # 解构 const { a, b } = ...
    for d in re.finditer(r'(?:const|let|var)\s*\{([^}]*)\}', script):
        for part in d.group(1).split(','):
            part = part.strip().split(':')[-1].split('=')[0].strip()
            if re.match(r'^[A-Za-z_$][\w$]*$', part): declared.add(part)
    # 函数参数/箭头函数局部变量（粗糙）
    for d in re.finditer(r'(?:function\s*\(|\()\s*([A-Za-z_$][\w$]*(?:\s*:\s*[^,)=]+)?(?:\s*,\s*[A-Za-z_$][\w$]*(?:\s*:\s*[^,)=]+)?)*)\s*\)\s*=>', script):
        for p in d.group(1).split(','):
            p = p.strip().split(':')[0].strip()
            if p: declared.add(p)

    # 模板表达式：{{ ... }}、:attr="..."、@evt="..."、v-if/for/show/model
    exprs = re.findall(r'\{\{(.*?)\}\}', template, re.S)
    exprs += re.findall(r'(?:^|\s)[:@#][\w.-]+(?:\[[^\]]*\])?="([^"]*)"', template)
    exprs += re.findall(r'\sv-(?:if|else-if|show|model(?:\.\w+)*|for|slot:[\w-]+)="([^"]*)"', template)

    # 模板局部变量（v-for 声明）
    locals_ = set()
    for vf in re.findall(r'\sv-for="([^"]*)"', template):
        m2 = re.match(r'\s*(?:\(([^)]*)\)|([A-Za-z_$][\w$]*))\s+in\s+', vf)
        if m2:
            names = (m2.group(1) or m2.group(2) or '').split(',')
            for nm in names:
                nm = nm.strip()
                if re.match(r'^[A-Za-z_$][\w$]*$', nm): locals_.add(nm)

    suspects = {}
    for ex in exprs:
        # 取表达式中所有标识符（排除属性访问 .x 和字符串）
        ex2 = re.sub(r'(["\'])(?:\\.|(?!\1).)*\1', ' ', ex)  # 去字符串
        ex2 = re.sub(r'`(?:\\.|[^`])*`', ' ', ex2)  # 去模板串（可能漏 ${} 内变量，够用）
        # 去对象字面量键（{ key: ... } 的 key 不是变量引用）
        ex2 = re.sub(r'([{,]\s*)([A-Za-z_$][\w$]*)\s*:', r'\1', ex2)
        # 去箭头函数参数 (x) => 或 x =>
        ex2 = re.sub(r'\(\s*[A-Za-z_$][\w$]*(?:\s*,\s*[A-Za-z_$][\w$]*)*\s*\)\s*=>', ' ', ex2)
        ex2 = re.sub(r'(?<![.\w$])[A-Za-z_$][\w$]*\s*=>', ' ', ex2)
        ex2 = re.sub(r'\$event(?:\.\w+)*', ' ', ex2)  # Vue 注入
        ex2 = re.sub(r'as\s+[A-Za-z_$][\w$]*', ' ', ex2)  # TS as 断言
        for tok in re.finditer(r'(?<![.\w$])([A-Za-z_$][\w$]*)', ex2):
            name = tok.group(1)
            if name in GLOBALS or name in declared or name in locals_:
                continue
            if name in ('in', 'of', 'as', 'true', 'false', 'null', 'undefined', 'typeof', 'new', 'delete', 'void', 'instanceof', 'HTMLSelectElement', 'HTMLInputElement'):
                continue
            suspects.setdefault(name, ex.strip()[:100])
    return [(k, v) for k, v in suspects.items()]

all_bad = {}
for f in glob.glob(r'D:\hengshu\console\frontend\src\**\*.vue', recursive=True):
    bad = scan(f)
    if bad:
        all_bad[f.split('src\\')[-1]] = bad

print(json.dumps(all_bad, ensure_ascii=False, indent=1) if all_bad else 'ALL CLEAN')
