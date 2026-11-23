#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""环境自检：一条命令确认这台机器能不能跑 douyin-video-skill。

用法：
    python scripts/doctor.py            # 自检
    python scripts/doctor.py --json     # 机器可读输出（给 CI / AI Agent 用）

退出码：0 = 必需项齐全；1 = 有必需项缺失；2 = 用法错误
"""
import argparse
import json
import os
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

OK = "OK"
WARN = "WARN"
FAIL = "FAIL"

RESULTS = []


def add(level, name, detail, fix=""):
    RESULTS.append({"level": level, "name": name, "detail": detail, "fix": fix})
    return level


def check_python():
    v = sys.version_info
    if v >= (3, 9):
        return add(OK, "Python 版本", "%d.%d.%d" % v[:3])
    return add(FAIL, "Python 版本", "%d.%d.%d" % v[:3], "需要 Python 3.9 及以上")


def check_packages():
    required = [
        ("websocket", "websocket-client", "CDP 客户端（douyin_auto / channel / audio）"),
        ("requests", "requests", "下载 MP4 / 音频直链"),
        ("rapidocr_onnxruntime", "rapidocr-onnxruntime", "本地 OCR 引擎"),
        ("numpy", "numpy", "OCR 图像数组"),
        ("PIL", "Pillow", "读帧图"),
    ]
    optional = [
        ("playwright", "playwright", "仅 douyin_crawler.py 需要"),
        ("yt_dlp", "yt-dlp", "仅 douyin_analyze.py --url 兜底需要"),
    ]
    for mod, pkg, why in required:
        try:
            __import__(mod)
            add(OK, "包 %s" % pkg, why)
        except ImportError:
            add(FAIL, "包 %s" % pkg, why, "pip install %s" % pkg)
    for mod, pkg, why in optional:
        try:
            __import__(mod)
            add(OK, "包 %s（可选）" % pkg, why)
        except ImportError:
            add(WARN, "包 %s（可选）" % pkg, why, "pip install %s" % pkg)


def check_browser():
    try:
        import config
    except Exception as ex:
        return add(FAIL, "浏览器", "config.py 导入失败: %s" % ex)
    if config.EDGE and os.path.isfile(config.EDGE):
        return add(OK, "浏览器", config.EDGE)
    add(FAIL, "浏览器", "未找到 Edge / Chrome",
        "安装其中一个，或设环境变量 DOUYIN_BROWSER 指向可执行文件")


def check_ffmpeg():
    try:
        import config
    except Exception:
        return add(FAIL, "ffmpeg", "config.py 导入失败")
    for name, exe in (("ffmpeg", config.FFMPEG), ("ffprobe", config.FFPROBE)):
        found = shutil.which(exe) or (exe if os.path.isfile(exe) else None)
        if found:
            add(OK, name, found)
        else:
            add(FAIL, name, "未找到", "装 ffmpeg 并加入 PATH，或设 DOUYIN_%s" % name.upper())


def check_playwright_browser():
    try:
        import playwright  # noqa: F401
    except ImportError:
        return  # 已在 check_packages 里报过
    # 用系统浏览器通道（msedge / chrome）时不需要下载 playwright 自带内核
    try:
        import config
        ch = getattr(config, "BROWSER_CHANNEL", "") or ""
    except Exception:
        ch = ""
    if ch in ("msedge", "chrome"):
        return add(OK, "playwright 内核", "无需下载（用系统 %s 通道）" % ch)
    from playwright.sync_api import sync_playwright
    try:
        with sync_playwright() as p:
            path = p.chromium.executable_path
        if path and os.path.exists(path):
            add(OK, "playwright 内核", path)
        else:
            add(WARN, "playwright 内核", "未下载",
                "执行 playwright install chromium，或设 DOUYIN_BROWSER_CHANNEL=msedge")
    except Exception as ex:
        add(WARN, "playwright 内核", "检测失败: %s" % str(ex)[:80],
            "执行 playwright install chromium，或设 DOUYIN_BROWSER_CHANNEL=msedge")


def check_scripts():
    names = ["douyin_auto.py", "douyin_analyze.py", "douyin_channel.py",
             "douyin_audio.py", "douyin_crawler.py", "config.py"]
    missing = [n for n in names if not os.path.isfile(os.path.join(HERE, n))]
    if missing:
        return add(FAIL, "脚本完整性", "缺少: %s" % ", ".join(missing),
                   "重新 clone 仓库")
    bad = []
    for n in names:
        p = os.path.join(HERE, n)
        try:
            subprocess.run([sys.executable, "-m", "py_compile", p],
                           capture_output=True, timeout=60)
        except Exception:
            bad.append(n)
    if bad:
        return add(FAIL, "脚本语法", "编译失败: %s" % ", ".join(bad))
    add(OK, "脚本完整性", "%d 个脚本，语法全部通过" % len(names))


def check_outdir():
    try:
        import config
        d = config.OUT_ROOT
        os.makedirs(d, exist_ok=True)
        probe = os.path.join(d, ".write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("x")
        os.remove(probe)
        add(OK, "输出目录", d)
    except Exception as ex:
        add(FAIL, "输出目录", "不可写: %s" % str(ex)[:80],
            "设环境变量 DOUYIN_OUT 指向可写目录")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true", help="输出 JSON")
    args = ap.parse_args()

    check_python()
    check_packages()
    check_browser()
    check_ffmpeg()
    check_playwright_browser()
    check_scripts()
    check_outdir()

    n_fail = sum(1 for r in RESULTS if r["level"] == FAIL)
    n_warn = sum(1 for r in RESULTS if r["level"] == WARN)

    if args.json:
        print(json.dumps({"ok": n_fail == 0, "fail": n_fail, "warn": n_warn,
                          "results": RESULTS}, ensure_ascii=False, indent=2))
        return 0 if n_fail == 0 else 1

    print("=" * 62)
    print("  douyin-video-skill 环境自检")
    print("=" * 62)
    icon = {OK: "[ OK ]", WARN: "[WARN]", FAIL: "[FAIL]"}
    for r in RESULTS:
        print("%s %-22s %s" % (icon[r["level"]], r["name"], r["detail"]))
        if r["level"] != OK and r["fix"]:
            print("       -> %s" % r["fix"])
    print("-" * 62)
    if n_fail:
        print("  结论：%d 项必需依赖缺失，%d 项可选缺失。装完上面标 [FAIL] 的即可。" % (n_fail, n_warn))
    elif n_warn:
        print("  结论：可以用了（%d 项可选依赖缺失，不影响主链路）。" % n_warn)
    else:
        print("  结论：环境完全就绪。")
    print("=" * 62)
    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
