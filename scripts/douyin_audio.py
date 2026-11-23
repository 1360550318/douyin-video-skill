# -*- coding: utf-8 -*-
"""抖音视频 → 音频（mp3）提取器。

背景（2026-11-17 实测）：抖音 CDN 把**画面流和声音流分成两个独立地址**，
douyin_auto.py 的 pick_stream 只挑到一条 → 下载下来的 MP4 **没有音轨**（ffprobe 只有 h264 video）。
本脚本改为在页面上下文里取回作品详情 JSON，直接拿**音乐直链**（music.play_url）下载音频，
再用 ffmpeg 转成 mp3。

做法：复用 douyin_auto 的 CDP（让页面自己的 JS 完成签名），在页面里 fetch 详情接口，
解析出候选音频地址并下载。合规：仅抓取公开内容供个人学习/收藏，不批量、不二次分发。

用法：
  PY douyin_audio.py --url "https://v.douyin.com/xxxx/" [--out "输出路径.mp3"] [--bitrate 192]
"""
import argparse
import datetime
import json
import os
import re
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import douyin_auto as DA  # noqa: E402

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import FFMPEG, FFPROBE, OUT_ROOT, check_deps  # noqa: E402

OUTDIR = os.path.join(OUT_ROOT, "audio")

# 在页面上下文里 fetch 详情接口：页面自己的 JS 会补上签名
FETCH_EXPR = r"""
(async () => {
  const m = location.pathname.match(/\/video\/(\d+)/);
  const id = m ? m[1] : '';
  if (!id) return JSON.stringify({error: 'no aweme id', href: location.href});
  const q = 'device_platform=webapp&aid=6383&channel=channel_pc_web&pc_client_type=1' +
            '&version_code=190500&version_name=19.5.0&aweme_id=' + id + '&update_version_code=170400';
  try {
    const r = await fetch('/aweme/v1/web/aweme/detail/?' + q, {credentials: 'include'});
    const t = await r.text();
    return JSON.stringify({ok: true, id: id, body: t.slice(0, 400000)});
  } catch (e) {
    return JSON.stringify({ok: false, id: id, error: String(e)});
  }
})()
"""


def note(msg):
    print(msg, flush=True)


def probe_audio(url):
    """返回 (标题, [候选音频url], 详情json)"""
    with DA.CDP() as c:
        c.cmd("Page.navigate", {"url": url})
        deadline = time.time() + 45
        raw = ""
        while time.time() < deadline:
            c.pump(2.0)
            raw = c.eval(FETCH_EXPR, await_promise=True, timeout=30)
            if raw:
                try:
                    d = json.loads(raw)
                except Exception:
                    d = {}
                if d.get("ok") and d.get("body"):
                    break
                if d.get("error"):
                    note("   fetch 报错：%s" % d["error"])
                    break
        if not raw:
            return None, [], {}
        d = json.loads(raw)
        if not d.get("ok"):
            note("取详情失败：%s" % d.get("error"))
            return None, [], {}
        try:
            body = json.loads(d["body"])
        except Exception:
            note("详情不是 JSON（前 200 字）：%s" % d["body"][:200])
            return None, [], {}
        det = body.get("aweme_detail") or {}
        if not det:
            note("   详情顶层键：%s" % list(body)[:12])
            note("   aweme_detail 为空，body 前 400 字：%s" % d["body"][:400])
        title = det.get("desc") or ""
        urls = []
        music = det.get("music") or {}
        for u in ((music.get("play_url") or {}).get("url_list") or []):
            urls.append(("music.play_url", u))
        for i, br in enumerate(det.get("video", {}).get("bit_rate") or []):
            for u in ((br.get("play_addr") or {}).get("url_list") or []):
                urls.append(("video.bit_rate[%d]" % i, u))
        for u in ((det.get("video") or {}).get("play_addr", {}) or {}).get("url_list") or []:
            urls.append(("video.play_addr", u))
        return title, urls, det


def download(url, dest, referer="https://www.douyin.com/"):
    import requests
    headers = {"User-Agent": DA.UA, "Referer": referer, "Accept": "*/*"}
    with requests.get(url, headers=headers, stream=True, timeout=(15, 120)) as r:
        r.raise_for_status()
        ctype = (r.headers.get("Content-Type") or "").lower()
        with open(dest, "wb") as f:
            for chunk in r.iter_content(1 << 18):
                if chunk:
                    f.write(chunk)
    return os.path.getsize(dest), ctype


def to_mp3(src, dest, bitrate=192):
    cmd = [FFMPEG, "-y", "-v", "error", "-i", src, "-vn",
           "-c:a", "libmp3lame", "-b:a", "%dk" % bitrate, "-ar", "44100", dest]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return r.returncode == 0, (r.stderr or "")[:400]


def clean_name(s, maxlen=40):
    s = re.sub('[\\/:*?"<>|\r\n\t]', '', s or "")     # 注意：非 raw 字符串，\r\n\t 才是真控制符
    s = re.sub(r"\s+", " ", s).strip()
    return (s[:maxlen] or "audio")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True)
    ap.add_argument("--out", help="输出 mp3 路径")
    ap.add_argument("--bitrate", type=int, default=192)
    a = ap.parse_args()

    os.makedirs(OUTDIR, exist_ok=True)
    note("① 打开页面并取作品详情（用页面自己的签名）…")
    title, urls, det = probe_audio(a.url)
    if not urls:
        note("没拿到任何候选流。")
        return 2
    note("   标题：%s" % (title[:60] or "(无)"))
    note("   候选流 %d 条：" % len(urls))
    for kind, u in urls[:8]:
        note("     [%s] %s" % (kind, u[:110]))

    tmp = os.path.join(OUTDIR, "_raw_%s" % datetime.datetime.now().strftime("%H%M%S"))
    picked = None
    for kind, u in urls:
        low = u.lower()
        prefer = ("music" in kind) or ("audio" in low) or (".mp3" in low) or ("playwm" in low)
        try:
            size, ctype = download(u, tmp)
        except Exception as e:
            note("   下载失败[%s]：%s" % (kind, str(e)[:80]))
            continue
        # 用 ffprobe 判断这个文件到底有没有音频流
        r = subprocess.run([FFPROBE, "-v", "error", "-select_streams", "a",
                            "-show_entries", "stream=codec_name", "-of", "csv=p=0", tmp],
                           capture_output=True, text=True, encoding="utf-8", errors="replace")
        has_audio = bool((r.stdout or "").strip())
        note("   [%s] %.1f KB  ctype=%s  音频流=%s" % (kind, size / 1024, ctype[:30],
                                                     "有" if has_audio else "无"))
        if has_audio:
            picked = (kind, tmp, size)
            break
        if prefer and picked is None:
            picked = (kind, tmp, size)
    if not picked:
        note("所有候选都不含音频流。")
        return 3

    kind, src, size = picked
    out = a.out or os.path.join(OUTDIR, "%s.mp3" % clean_name(title))
    note("② 用 ffmpeg 转 mp3（%d kbps）…" % a.bitrate)
    ok, err = to_mp3(src, out, a.bitrate)
    if not ok:
        note("转码失败：%s" % err)
        return 4
    if os.path.exists(tmp):
        os.remove(tmp)
    r = subprocess.run([FFPROBE, "-v", "error", "-show_entries",
                        "format=duration,bit_rate,format_name", "-of", "default=nw=1", out],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    note("③ 完成：%s" % out)
    note(r.stdout or "")
    return 0


if __name__ == "__main__":
    sys.exit(main())
