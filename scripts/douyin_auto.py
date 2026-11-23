# -*- coding: utf-8 -*-
"""抖音分享链接 -> 自动取得视频 -> 输出内容分析（全自动，无需用户操作）。

原理：启动独立 headless Edge + CDP，让抖音页面自己的 JS 完成签名与取流，
从 <video>.currentSrc 拿播放地址，同时抓页面正文中平台自带的「章节要点」等文字。
再用 requests 下载 MP4，交给 douyin_analyze.py 做抽帧 OCR。

不读登录态文件、不调私有接口、不模拟点击、不需要 Docker / 模型 / API key。
用法：
  PY douyin_auto.py --url "https://v.douyin.com/xxxx/"
  PY douyin_auto.py --url "..." --interval 2 --frames 60
  PY douyin_auto.py --url "..." --text-only     # 只抓页面文字，不下载视频
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

import websocket

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (EDGE, PROFILE, PY, ANALYZER, OUT_ROOT,  # noqa: E402
                    check_deps, profile_dir, LOCAL_OPENER)

OUTBASE = OUT_ROOT
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36 Edg/138.0.0.0")

PROBE_EXPR = r"""
(() => {
  const vs = Array.from(document.querySelectorAll('video'));
  const v = vs.find(x => x.currentSrc || x.src) || vs[0];
  const res = performance.getEntriesByType('resource')
    .map(e => e.name)
    .filter(u => /douyinvod|video\/tos|\.mp4|\/media\/|aweme.*video/i.test(u));
  return JSON.stringify({
    src: v ? (v.currentSrc || v.src || '') : '',
    title: document.title || '',
    href: location.href,
    duration: v ? (isFinite(v.duration) ? v.duration : 0) : 0,
    resources: res.slice(-12),
    text: document.body ? document.body.innerText : ''
  });
})()
"""


class CDP:
    """最小 CDP 客户端：一个进程内启动 Edge、连接、发命令、收事件、收尾。"""

    def __init__(self, port=9224, args_extra=None, profile=None):
        self.port = port
        self.profile = profile
        self.proc = None
        self.ws = None
        self.seq = 0
        self.events = []
        self.args_extra = list(args_extra or [])

    def __enter__(self):
        if not EDGE:
            raise RuntimeError(
                "未找到浏览器（需要 Edge 或 Chrome）。"
                "请安装其中之一，或用环境变量 DOUYIN_BROWSER 指定可执行文件路径。")
        prof = self.profile or profile_dir("auto")
        os.makedirs(prof, exist_ok=True)
        args = [EDGE, "--headless=new", "--disable-gpu", "--no-first-run",
                "--no-default-browser-check", "--disable-background-networking",
                "--disable-extensions", "--remote-debugging-port=%d" % self.port,
                "--remote-allow-origins=*", "--user-data-dir=" + prof,
                "--user-agent=" + UA, "about:blank"] + self.args_extra
        self.proc = subprocess.Popen(args, stdout=subprocess.DEVNULL,
                                     stderr=subprocess.DEVNULL)
        for _ in range(40):
            try:
                self._http("/json/version")
                break
            except Exception:
                time.sleep(0.5)
        else:
            raise RuntimeError("CDP 未就绪（Edge 启动失败）")
        pages = [t for t in self._http("/json/list") if t.get("type") == "page"]
        self.ws = websocket.create_connection(pages[0]["webSocketDebuggerUrl"],
                                              timeout=30, suppress_origin=True)
        self.cmd("Page.enable")
        self.cmd("Runtime.enable")
        self.cmd("Network.enable", {"maxTotalBufferSize": 20_000_000,
                                    "maxResourceBufferSize": 10_000_000})
        return self

    def _http(self, path, timeout=3):
        # 用 LOCAL_OPENER 绕开系统代理（CDP 只打本机）
        with LOCAL_OPENER.open("http://127.0.0.1:%d%s" % (self.port, path),
                               timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8", "replace"))

    def _recv(self, timeout):
        self.ws.settimeout(timeout)
        try:
            m = json.loads(self.ws.recv())
        except Exception:
            return None
        if "method" in m:
            self.events.append(m)
        return m

    def cmd(self, method, params=None, timeout=20):
        self.seq += 1
        mid = self.seq
        self.ws.send(json.dumps({"id": mid, "method": method,
                                 "params": params or {}}))
        deadline = time.time() + timeout
        while time.time() < deadline:
            m = self._recv(min(1.0, max(0.1, deadline - time.time())))
            if m and m.get("id") == mid:
                return m
        return {"error": "timeout"}

    def pump(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            self._recv(min(1.0, max(0.1, end - time.time())))

    def eval(self, expr, timeout=15, await_promise=False):
        r = self.cmd("Runtime.evaluate",
                     {"expression": expr, "returnByValue": True,
                      "awaitPromise": await_promise}, timeout=timeout)
        return r.get("result", {}).get("result", {}).get("value")

    def __exit__(self, *exc):
        if self.ws:
            try:
                self.ws.close()
            except Exception:
                pass
        if self.proc:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=5)
            except Exception:
                self.proc.kill()


def real_stream(src):
    """真实 CDN 播放地址，而不是加载中的占位/ blob 地址。

    2026-11-17 修正（实测：Pachat 视频 7706896358234698923 抓取失败）：
    原兜底条件「http + 长度>150」会把抖音平台 JSON 接口
    （/aweme/v1/web/aweme/detail/，1657 字符）误判成视频流 →
    下载到 111KB 的 JSON 却存成 .mp4 → ffprobe 报 Invalid data found。
    现在必须命中媒体直链特征，并显式排除平台 JSON 接口。
    """
    if not src or src.startswith("blob:"):
        return False
    low = src.lower()
    if "/aweme/v1/" in low or "application/json" in low:
        return False
    if "douyinvod.com" in low or "video/tos" in low:
        return True
    if src.startswith("http") and len(src) > 150:
        return (".mp4" in low or "video_id=" in low or "vid=" in low
                or "playwm" in low or "media/" in low)
    return False


def pick_stream(d, resp_urls):
    """从 <video>、performance 资源表、Network 响应里挑出可直接下载的流。"""
    cands = []
    for u in ([d.get("src", "")] + d.get("resources", []) + resp_urls):
        if u and u not in cands and real_stream(u) and u.startswith("http"):
            cands.append(u)
    # 明确含视频 CDN 特征的地址优先
    cands.sort(key=lambda u: (0 if ("douyinvod" in u or "video/tos" in u) else 1,
                              -len(u)))
    return cands[0] if cands else ""


def ready(d, resp_urls):
    """页面数据加载完成：拿到可下载流 + 正文已渲染出实质内容。"""
    txt = d.get("text", "")
    loaded = len(txt) > 500 and "视频数据加载中" not in txt
    return loaded and bool(pick_stream(d, resp_urls))


def grab(url, wait=50, port=9224, profile=None):
    """返回 (页面信息 dict, cookies 列表)。

    抖音页面先渲染骨架再异步取视频数据；<video>.currentSrc 可能是 blob:
    （MediaSource 流），因此同时监听 Network 响应与 performance 资源表，
    三处汇总挑出真正可 HTTP 下载的 CDN 地址。
    """
    with CDP(port=port, profile=profile) as c:
        c.cmd("Page.navigate", {"url": url})
        best, best_score = {}, -1
        resp_urls = []
        evt_pos = 0
        deadline = time.time() + wait
        while time.time() < deadline:
            c.pump(2.0)
            for e in c.events[evt_pos:]:
                if e.get("method") == "Network.responseReceived":
                    u = e.get("params", {}).get("response", {}).get("url", "")
                    if real_stream(u) and u not in resp_urls:
                        resp_urls.append(u)
            evt_pos = len(c.events)
            raw = c.eval(PROBE_EXPR)
            if raw:
                try:
                    d = json.loads(raw)
                except Exception:
                    d = {}
                stream = pick_stream(d, resp_urls)
                score = (20000 if stream else 0) \
                        + len(d.get("text", "")) + len(d.get("src", ""))
                if score > best_score:
                    best, best_score = d, score
                if ready(d, resp_urls):
                    best = d
                    break
            c.pump(1.0)
        best["stream"] = pick_stream(best, best.get("resources", []) + resp_urls)
        ck = c.cmd("Network.getCookies", {}, timeout=12)
        cookies = ck.get("result", {}).get("cookies", [])
        return best, cookies


def clean_text(text):
    """去掉导航噪声，保留标题 / 章节要点 / 描述等实质内容。"""
    lines = [l.strip() for l in text.splitlines()]
    noise = {"充钻石", "客户端", "壁纸", "通知", "消息", "投稿", "登录", "首页",
             "推荐", "朋友", "我的", "直播", "放映厅", "短剧", "小游戏", "搜索",
             "更多", "打开声音", "倍速", "智能", "清屏", "连播", "下一章",
             "因浏览器限制，当前为静音", "00:00 / 00:00", "作者声明：内容由 AI 生成"}
    keep = []
    for l in lines:
        if not l or l in noise:
            continue
        if re.fullmatch(r"[\d:/\s]+", l):
            continue
        if len(l) < 4 and not re.search(r"[\u4e00-\u9fff]", l):
            continue
        keep.append(l)
    # 去重保序
    seen = set()
    out = []
    for l in keep:
        if l not in seen:
            seen.add(l)
            out.append(l)
    return out


def download(url, cookies, dest, timeout=180):
    import requests
    headers = {"User-Agent": UA, "Referer": "https://www.douyin.com/",
               "Accept": "*/*"}
    if cookies:
        headers["Cookie"] = "; ".join("%s=%s" % (x.get("name"), x.get("value"))
                                      for x in cookies if x.get("name"))
    t0 = time.time()
    total = 0
    with requests.get(url, headers=headers, stream=True,
                      timeout=(15, 60)) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        if "json" in ctype or "text/" in ctype:
            raise ValueError("取到的是 JSON/文本接口响应，不是媒体流（Content-Type=%s）" % ctype)
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                if chunk:
                    f.write(chunk)
                    total += len(chunk)
                if time.time() - t0 > timeout:
                    raise TimeoutError("下载超时（%.1f MB）" % (total / 2 ** 20))
    return total, time.time() - t0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="抖音分享链接 / 短链 / 长链")
    ap.add_argument("--interval", type=float, default=3.0, help="抽帧间隔秒")
    ap.add_argument("--frames", type=int, default=40, help="最多抽帧数")
    ap.add_argument("--wait", type=int, default=50, help="页面加载最长等待秒")
    ap.add_argument("--text-only", action="store_true", help="只抓页面文字，不下载")
    ap.add_argument("--port", type=int, default=9224,
                    help="CDP 调试端口（默认 9224；端口被占用时换一个）")
    a = ap.parse_args()

    miss = check_deps()
    if miss:
        print("ABORT: 缺少依赖：")
        for m in miss:
            print("  - " + m)
        print("  详见 README「安装」或 docs/常见问题.md")
        return 2

    rid = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = os.path.join(OUTBASE, "auto_" + rid)
    os.makedirs(work, exist_ok=True)
    print("输出目录:", work, flush=True)

    print("[1/4] 启动无头浏览器，加载页面…", flush=True)
    info, cookies = grab(a.url, a.wait, port=a.port)
    play = info.get("stream") or ""
    print("    最终地址:", info.get("href", "")[:120], flush=True)
    print("    标题:", info.get("title", "")[:80], flush=True)
    print("    播放地址:", ("%d 字符 %s..." % (len(play), play[:70])) if play else "未取得", flush=True)

    lines = clean_text(info.get("text", ""))
    meta = {"source_url": a.url, "final_url": info.get("href"),
            "page_title": info.get("title"), "html5_duration": info.get("duration"),
            "play_url": play, "grabbed_at": rid}
    with open(os.path.join(work, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    with open(os.path.join(work, "page_content.md"), "w", encoding="utf-8") as f:
        f.write("# 页面文字（平台自带，非 AI 编造）\n\n")
        f.write("> 来源：%s\n> 抓取时间：%s\n\n" % (a.url, rid))
        for l in lines:
            f.write("- %s\n" % l)
    print("[2/4] 页面文字 %d 行 -> page_content.md" % len(lines), flush=True)
    for l in lines[:12]:
        print("    %s" % l[:100], flush=True)

    if a.text_only or not play:
        if not play:
            print("ABORT: 未取得播放地址（视频可能私密/已下架/需要登录），仅保存页面文字")
        print("OK 输出:", work)
        return 0 if play else 2

    print("[3/4] 下载视频…", flush=True)
    mp4 = os.path.join(work, "source.mp4")
    try:
        size, sec = download(play, cookies, mp4)
        print("    下载完成 %.1f MB / %.1fs (%.0f KB/s)" %
              (size / 2 ** 20, sec, size / 1024 / max(sec, 0.1)), flush=True)
    except Exception as e:
        print("DOWNLOAD_FAIL:", type(e).__name__, str(e)[:120])
        print("已保留页面文字与播放地址；可用 ffmpeg 直接抽帧，或稍后重试")
        return 3

    print("[4/4] 抽帧 OCR 分析…", flush=True)
    try:
        r = subprocess.run([PY, ANALYZER, "--file", mp4,
                            "--interval", str(a.interval),
                            "--frames", str(a.frames)],
                           capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=600)
    except subprocess.TimeoutExpired:
        print("ANALYZE_FAIL: OCR 分析超时（>600s）。"
              "可调小 --frames 或调大 --interval 后重试")
        return 4
    print((r.stdout or "")[-1200:], flush=True)
    if r.returncode != 0:
        print("ANALYZE_FAIL rc=%d %s" % (r.returncode, (r.stderr or "")[-300:]))
        return 4

    # 把分析产物目录记进汇总
    m = re.search(r"输出目录:\s*(\S+)", r.stdout or "")
    ana = m.group(1) if m else ""
    with open(os.path.join(work, "RESULT.md"), "w", encoding="utf-8") as f:
        f.write("# 抖音链接分析结果\n\n")
        f.write("- 源链接：`%s`\n- 最终地址：`%s`\n- 页面标题：%s\n" %
                (a.url, info.get("href", ""), info.get("title", "")))
        f.write("- 视频：`%s`（%.1f MB）\n- 画面文字稿目录：`%s`\n" %
                (mp4, os.path.getsize(mp4) / 2 ** 20, ana))
        f.write("- 页面文字：`%s`\n\n## 页面自带文字（真实读取）\n\n" %
                os.path.join(work, "page_content.md"))
        for l in lines[:40]:
            f.write("- %s\n" % l)
    print("\nOK 全部完成:", work, flush=True)
    print("   画面文字稿:", ana, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
