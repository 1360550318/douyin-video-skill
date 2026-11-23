# -*- coding: utf-8 -*-
"""集中管理路径与外部依赖定位，让脚本在不同机器上开箱即用。

查找优先级：
  1. 环境变量（最高优先级）
  2. 系统 PATH
  3. 常见安装位置

可用环境变量：
  DOUYIN_OUT             产物输出根目录（默认 ~/douyin_output）
  DOUYIN_FFMPEG          ffmpeg 可执行文件路径
  DOUYIN_FFPROBE         ffprobe 可执行文件路径
  DOUYIN_BROWSER         浏览器可执行文件路径（默认自动找 Edge / Chrome）
  DOUYIN_BROWSER_CHANNEL Playwright 浏览器通道（msedge / chrome / chromium，留空用自带）
  DOUYIN_BROWSER_COOKIES yt-dlp 读取本机浏览器 cookie 的浏览器名（chrome / edge / firefox）
                         默认留空 = 不读取任何浏览器凭据
  DOUYIN_PROFILE         浏览器独立 profile 目录（默认 ~/.cache/douyin_video_skill）
"""
import os
import shutil
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
IS_WIN = os.name == "nt"


def _first_env(*names):
    for n in names:
        v = os.environ.get(n)
        if v:
            return v
    return None


def find_binary(name, env_names=(), extra_paths=()):
    """按 环境变量 -> PATH -> 常见安装位置 的顺序找可执行文件。"""
    v = _first_env(*env_names)
    if v and (os.path.isfile(v) or shutil.which(v)):
        return v
    p = shutil.which(name)
    if p:
        return p
    for d in extra_paths:
        cand = os.path.join(d, name + (".exe" if IS_WIN else ""))
        if os.path.isfile(cand):
            return cand
    return name  # 找不到就交回给系统报错，不静默失败


def find_browser():
    """优先 Edge，其次 Chrome。找不到返回 None，由调用方给出可读错误。"""
    v = os.environ.get("DOUYIN_BROWSER")
    if v and os.path.isfile(v):
        return v
    cands = []
    if IS_WIN:
        cands += [
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Google\Chrome\Application\chrome.exe",
            r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        ]
    else:
        cands += [
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/usr/bin/microsoft-edge",
            "/usr/bin/google-chrome",
            "/usr/bin/chromium",
            "/usr/bin/chromium-browser",
        ]
    for c in cands:
        if os.path.isfile(c):
            return c
    for n in ("msedge", "microsoft-edge", "chrome", "google-chrome", "chromium"):
        p = shutil.which(n)
        if p:
            return p
    return None


# ---- 解释器：始终用当前解释器，避免写死路径 ----
PY = sys.executable

# ---- ffmpeg / ffprobe ----
FFMPEG = find_binary("ffmpeg", ("DOUYIN_FFMPEG", "FFMPEG"))
FFPROBE = find_binary("ffprobe", ("DOUYIN_FFPROBE", "FFPROBE"))

# ---- 浏览器 ----
EDGE = find_browser()
_BCH = os.environ.get("DOUYIN_BROWSER_CHANNEL", "").strip()
if not _BCH:
    if EDGE and "edge" in EDGE.lower():
        _BCH = "msedge"
    elif EDGE and "chrome" in EDGE.lower():
        _BCH = "chrome"
    else:
        _BCH = ""
BROWSER_CHANNEL = _BCH

# ---- 是否允许 yt-dlp 读取本机浏览器 cookie（默认关闭）----
BROWSER_COOKIES = os.environ.get("DOUYIN_BROWSER_COOKIES", "").strip()

# ---- 产物目录 ----
OUT_ROOT = os.path.abspath(
    os.path.expanduser(os.environ.get("DOUYIN_OUT") or os.path.join("~", "douyin_output"))
)

# ---- 浏览器独立 profile（不碰用户日常浏览数据）----
PROFILE = os.path.join(
    os.path.abspath(os.path.expanduser(os.environ.get("DOUYIN_PROFILE")
                                       or os.path.join("~", ".cache", "douyin_video_skill"))),
    "edge_profile",
)

# ---- 同目录的分析器 ----
ANALYZER = os.path.join(HERE, "douyin_analyze.py")


def profile_dir(name):
    """每个脚本用独立的浏览器 profile 子目录。

    auto / channel / audio 同时跑时若共用一个 user-data-dir，第二个浏览器
    会因为 profile 锁冲突而启动失败。
    """
    p = os.path.join(PROFILE, name)
    os.makedirs(p, exist_ok=True)
    return p


# 本机 CDP 请求只打 127.0.0.1，必须绕开系统代理，
# 否则设置了 http_proxy 的机器上请求会被代理劫持而失败。
LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check_playwright():
    """douyin_crawler.py 专用依赖检查。"""
    miss = []
    try:
        import playwright  # noqa: F401
    except ImportError:
        miss.append("playwright 未安装（仅 douyin_crawler.py 需要）："
                    "pip install playwright && playwright install chromium")
    return miss


def ensure_out(*parts):
    """确保产物目录存在并返回完整路径。"""
    p = os.path.join(OUT_ROOT, *parts) if parts else OUT_ROOT
    os.makedirs(p, exist_ok=True)
    return p


def check_deps():
    """返回缺失依赖清单（供脚本启动时给出可读提示）。"""
    missing = []
    if not EDGE:
        missing.append("浏览器未找到（需 Edge 或 Chrome，可用 DOUYIN_BROWSER 指定）")
    if not shutil.which(FFMPEG) and not os.path.isfile(FFMPEG):
        missing.append("ffmpeg 未找到（安装后加入 PATH，或用 DOUYIN_FFMPEG 指定）")
    if not shutil.which(FFPROBE) and not os.path.isfile(FFPROBE):
        missing.append("ffprobe 未找到（安装后加入 PATH，或用 DOUYIN_FFPROBE 指定）")
    return missing
