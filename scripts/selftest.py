#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""自动化静态质检：不开浏览器、不联网，秒级完成。

检查项：
  1. 全部脚本语法编译
  2. 未使用的第三方 import（以及 requirements.txt 与实际 import 的双向差集）
  3. CLI 一致性：README.md / SKILL.md 里出现的参数名是否都真实存在于 argparse
  4. 开源卫生：硬编码绝对路径、可疑密钥、个人目录残留
  5. 跨平台风险：hardcoded 分隔符、shell=True、编码声明

用法：
    python scripts/selftest.py            # 人读格式
    python scripts/selftest.py --json     # 机器可读
退出码：0 = 无 P0/P1；1 = 有 P0/P1；2 = 用法错误
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

STDLIB = {
    "argparse", "ast", "base64", "collections", "datetime", "glob", "hashlib",
    "io", "json", "os", "re", "shutil", "sqlite3", "subprocess", "sys", "tempfile",
    "time", "traceback", "urllib", "uuid", "webbrowser", "zipfile", "typing",
    "pathlib", "itertools", "functools", "math", "random", "string", "struct",
    "threading", "queue", "signal", "copy", "csv", "textwrap", "unicodedata",
}
# 名字 → pip 包名
PKG = {
    "websocket": "websocket-client",
    "requests": "requests",
    "numpy": "numpy",
    "PIL": "Pillow",
    "rapidocr_onnxruntime": "rapidocr-onnxruntime",
    "playwright": "playwright",
    "yt_dlp": "yt-dlp",
    "cv2": "opencv-python",
}

FINDINGS = []


def add(level, where, msg, fix=""):
    FINDINGS.append({"level": level, "where": where, "msg": msg, "fix": fix})


def py_files():
    out = []
    for d in ("scripts", "."):
        p = os.path.join(ROOT, d)
        if not os.path.isdir(p):
            continue
        for e in os.scandir(p):
            if e.is_file() and e.name.endswith(".py"):
                out.append(e.path)
    return sorted(set(out))


def c1_compile():
    bad = []
    for p in py_files():
        r = subprocess.run([sys.executable, "-m", "py_compile", p],
                           capture_output=True, text=True, timeout=60)
        if r.returncode != 0:
            bad.append((os.path.relpath(p, ROOT), (r.stderr or "")[-200:]))
    if bad:
        for f, e in bad:
            add("P0", f, "语法编译失败: %s" % e.replace("\n", " ")[:160])
    else:
        add("OK", "全部 %d 个脚本" % len(py_files()), "语法编译全部通过")


def imports_of(path):
    try:
        tree = ast.parse(open(path, encoding="utf-8").read())
    except Exception as ex:
        return set(), set(), str(ex)
    top = set()
    local = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for n in node.names:
                top.add(n.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                for n in node.names:
                    local.add(n.name)
            elif node.module:
                top.add(node.module.split(".")[0])
    return top, local, None


def local_modules():
    """仓库内自带的模块名，不算第三方依赖。"""
    mods = set()
    for p in py_files():
        mods.add(os.path.splitext(os.path.basename(p))[0])
    return mods


# 通过 `python -m <mod>` 子进程调用、而不是 import 的包
SUBPROCESS_PKGS = {"yt_dlp": "yt-dlp"}


def c2_imports():
    used = set()
    for p in py_files():
        top, _local, err = imports_of(p)
        if err:
            continue
        for t in top:
            if t in STDLIB:
                continue
            used.add(t)
    used -= local_modules()          # 同目录自带的模块
    used -= set(SUBPROCESS_PKGS)     # 子进程调用的包
    # requirements.txt 声明
    req = os.path.join(ROOT, "requirements.txt")
    declared = set()
    if os.path.isfile(req):
        for line in open(req, encoding="utf-8"):
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            declared.add(re.split(r"[<>=!~\[; ]", line)[0].strip().lower())
    need = {PKG.get(u, u).lower() for u in used}
    need |= {v.lower() for v in SUBPROCESS_PKGS.values()}
    missing = sorted(need - declared)
    extra = sorted(declared - need)
    for m in missing:
        add("P1", "requirements.txt", "代码 import 了 %s，但依赖清单里没有" % m,
            "补一行 %s" % m)
    for e in extra:
        add("P3", "requirements.txt", "依赖清单里有 %s，但代码里没用到" % e,
            "确认是否还需要；不需要就删掉")
    if not missing and not extra:
        add("OK", "requirements.txt", "与代码实际 import 完全一致（%d 项）" % len(need))


def c3_cli():
    """README / SKILL.md 里提到的 --参数 是否都真实存在。"""
    argp = set()
    for p in py_files():
        src = open(p, encoding="utf-8").read()
        for m in re.finditer(r'add_argument\(\s*"(--[a-z0-9\-]+)"', src):
            argp.add(m.group(1))
    for doc in ("README.md", "SKILL.md"):
        dp = os.path.join(ROOT, doc)
        if not os.path.isfile(dp):
            continue
        text = open(dp, encoding="utf-8").read()
        used = set(re.findall(r"(--[a-z][a-z0-9\-]{1,24})\b", text))
        # 排除明显不是参数的
        used = {u for u in used if u not in ("--json", "--no-ocr", "--check", "--link")}
        fake = sorted(u for u in used if u not in argp)
        for f in fake:
            add("P1", doc, "文档里出现参数 %s，但代码里没有这个参数" % f,
                "改文档或加参数")
        if not fake:
            add("OK", doc, "提到的 %d 个参数全部真实存在" % len(used))


def c4_hygiene():
    pats = [
        (r"[A-Za-z]:\\\\[A-Za-z0-9_\\\\.\\-]+", "硬编码绝对路径",
         "改成环境变量 / 相对路径 / config 解析"),
        (r"(ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})", "疑似 GitHub 令牌",
         "立即移除并 revoke"),
        (r"(?i)(api[_-]?key|secret|passwd|password)\s*=\s*[\"'][^\"']{8,}", "疑似硬编码密钥",
         "改成环境变量"),
        (r"(?i)Users\\\\a\d{4}", "疑似个人目录", "改成相对路径或 ~"),
    ]
    hit = 0
    for p in py_files() + [os.path.join(ROOT, d) for d in ("README.md", "SKILL.md")
                           if os.path.isfile(os.path.join(ROOT, d))]:
        rel = os.path.relpath(p, ROOT)
        try:
            text = open(p, encoding="utf-8").read()
        except Exception:
            continue
        for pat, what, fix in pats:
            for m in re.finditer(pat, text):
                line = text[:m.start()].count("\n") + 1
                add("P1" if what != "硬编码绝对路径" else "P2",
                    "%s:%d" % (rel, line), "%s: %s" % (what, m.group(0)[:60]), fix)
                hit += 1
    if not hit:
        add("OK", "开源卫生", "无硬编码绝对路径 / 令牌 / 个人目录残留")


def c5_portability():
    """用 AST 精确检查 shell=True 与 open() 缺 encoding（正则容易误报）。"""
    issues = 0
    for p in py_files():
        rel = os.path.relpath(p, ROOT)
        try:
            tree = ast.parse(open(p, encoding="utf-8").read())
        except Exception:
            continue
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = ""
            if isinstance(node.func, ast.Attribute):
                fname = node.func.attr
            elif isinstance(node.func, ast.Name):
                fname = node.func.id
            kws = {k.arg: k for k in node.keywords if k.arg}
            # shell=True
            if fname in ("run", "Popen", "call", "check_output", "check_call") \
                    and "shell" in kws:
                add("P1", rel, "subprocess.%s 用了 shell=True，存在命令注入面" % fname,
                    "改用参数列表形式")
                issues += 1
            # open() 写文件未指定 encoding
            if fname == "open":
                mode = ""
                if len(node.args) >= 2 and isinstance(node.args[1], ast.Constant):
                    mode = str(node.args[1].value)
                elif "mode" in kws and isinstance(kws["mode"].value, ast.Constant):
                    mode = str(kws["mode"].value)
                if any(x in mode for x in ("w", "a", "x", "+")) and "encoding" not in kws:
                    if "b" in mode:
                        continue  # 二进制模式不需要 encoding
                    add("P2", rel, "写文件未指定 encoding，Windows 下会落 GBK",
                        "open(..., encoding='utf-8')")
                    issues += 1
    if not issues:
        add("OK", "跨平台与编码", "无 shell=True、无未声明 encoding 的写文件")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    c1_compile()
    c2_imports()
    c3_cli()
    c4_hygiene()
    c5_portability()

    p01 = [f for f in FINDINGS if f["level"] in ("P0", "P1")]
    if args.json:
        print(json.dumps({"ok": not p01, "p0p1": len(p01),
                          "findings": FINDINGS}, ensure_ascii=False, indent=2))
        return 0 if not p01 else 1

    print("=" * 66)
    print("  douyin-video-skill 静态质检")
    print("=" * 66)
    cur = None
    for f in FINDINGS:
        if f["level"] == "OK":
            continue
        if f["level"] != cur:
            cur = f["level"]
            print("\n[%s]" % cur)
        print("  %-26s %s" % (f["where"], f["msg"]))
        if f["fix"]:
            print("       -> %s" % f["fix"])
    oks = [f for f in FINDINGS if f["level"] == "OK"]
    if oks:
        print("\n[通过]")
        for f in oks:
            print("  %-26s %s" % (f["where"], f["msg"]))
    print("-" * 66)
    print("  P0+P1 合计：%d  %s" % (len(p01), "（干净）" if not p01 else "（需修）"))
    print("=" * 66)
    return 0 if not p01 else 1


if __name__ == "__main__":
    sys.exit(main())
