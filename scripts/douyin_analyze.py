# -*- coding: utf-8 -*-
"""抖音/短视频内容分析（本机零模型、零 API、零 Docker 链路）。

输入一个视频文件或抖音链接，输出带时间轴的画面文字稿（MD + SRT）与结构化报告。
只做「真实提取 + 统计」，不生成 AI 总结、不编造内容；没识别到文字就如实写 0 条。

用法：
  python douyin_analyze.py --file "path/to/video.mp4"
  python douyin_analyze.py --file xxx.mp4 --interval 2 --frames 60
  python douyin_analyze.py --url "https://v.douyin.com/xxxx/"   # 需浏览器已访问过 douyin.com

抖音限制（2026-08 起）：纯 HTTP 接口已关，yt-dlp 需要活跃浏览器会话
（__ac 签名 + s_v_web_id）。链接下载失败时脚本会明确报错并提示改用本地文件，
不会假装成功、也不会伪造分析结果。
"""
import argparse
import datetime
import glob
import json
import os
import re
import subprocess
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import PY, FFMPEG, FFPROBE, OUT_ROOT, BROWSER_COOKIES  # noqa: E402

OUTBASE = OUT_ROOT


def run(cmd, timeout=300):
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", timeout=timeout)
    return r.returncode, (r.stdout or "").strip(), (r.stderr or "").strip()


def probe(path):
    rc, out, err = run([FFPROBE, "-v", "error", "-show_entries",
                        "format=duration,size:stream=codec_name,width,height,r_frame_rate",
                        "-of", "json", path], timeout=60)
    if rc != 0:
        return {"error": err[-200:]}
    d = json.loads(out)
    st = next((s for s in d.get("streams", []) if s.get("width")), d.get("streams", [{}])[0])
    return {"duration": round(float(d.get("format", {}).get("duration", 0)), 2),
            "size_mb": round(int(d.get("format", {}).get("size", 0)) / 2**20, 2),
            "codec": st.get("codec_name"), "width": st.get("width"),
            "height": st.get("height"), "fps": st.get("r_frame_rate")}


def download(url, work):
    """抖音链接 -> 本地文件。需要 Chrome 活跃会话，失败即如实返回。"""
    tpl = os.path.join(work, "source.%(ext)s")
    cmd = [PY, "-m", "yt_dlp"]
    if BROWSER_COOKIES:
        cmd += ["--cookies-from-browser", BROWSER_COOKIES]
    cmd += ["-f", "best[height<=720]/best", "--no-playlist", "-o", tpl, url]
    rc, out, err = run(cmd, timeout=240)
    files = [f for f in glob.glob(os.path.join(work, "source.*")) if not f.endswith((".part", ".ytdl"))]
    if files:
        return files[0], None
    hint = ("抖音下载失败。常见原因：缺 __ac 签名（未在浏览器打开过 douyin.com）、"
            "视频需登录/已删除、触发风控。可选做法：设置环境变量 DOUYIN_BROWSER_COOKIES=chrome "
            "让 yt-dlp 读取本机浏览器 cookie，或先下载视频到本地再用 --file 分析。")
    return None, hint + "\nyt-dlp: " + (err or out)[-300:]


def extract_frames(video, work, interval, max_frames):
    # 用 JPG：PNG 单帧 3-4MB，长视频会攒出几百 MB；JPG 每帧约 200KB。
    tpl = os.path.join(work, "frame_%04d.jpg")
    rc, out, err = run([FFMPEG, "-y", "-i", video, "-vf", "fps=1/%s" % interval,
                        "-frames:v", str(max_frames), "-q:v", "5", tpl], timeout=300)
    frames = sorted(glob.glob(os.path.join(work, "frame_*.jpg")))
    return frames, (err[-200:] if rc != 0 else "")


def ocr_frames(frames, interval):
    from rapidocr_onnxruntime import RapidOCR
    import numpy as np
    from PIL import Image
    ocr = RapidOCR()
    rows = []
    for i, fp in enumerate(frames):
        t = round(i * interval, 2)
        try:
            img = np.asarray(Image.open(fp).convert("RGB"))
            res, _ = ocr(img)
            texts = []
            if res:
                for item in res:
                    txt = (item[1] or "").strip()
                    score = float(item[2]) if len(item) > 2 else 0.0
                    if len(txt) >= 2 and score >= 0.5:
                        texts.append(txt)
            rows.append((t, texts))
        except Exception as e:
            rows.append((t, ["[OCR错误:%s]" % type(e).__name__]))
        # 不删除临时帧：本机 safe-delete 钩子会拦截逐文件删除并超时（10s/次）。
        # 抽帧图保留在工作目录，便于人工复核 OCR 结果。
    return rows


def dedupe(rows, interval):
    """相邻帧相同文字合并成一段时间段。"""
    segs = []
    for t, texts in rows:
        line = " ".join(dict.fromkeys(texts))
        if not line or line.startswith("[OCR错误"):
            continue
        if segs and segs[-1]["text"] == line:
            segs[-1]["end"] = round(t + interval, 2)
        else:
            segs.append({"start": t, "end": round(t + interval, 2), "text": line})
    return segs


def ts(sec, comma=False):
    h = int(sec // 3600); m = int(sec % 3600 // 60); s = sec % 60
    sep = "," if comma else ":"
    return "%02d:%02d:%02d%s%03d" % (h, m, int(s), sep, int((s - int(s)) * 1000))


def keywords(segs, top=12):
    txt = "".join(re.findall(r"[\u4e00-\u9fff]+", " ".join(s["text"] for s in segs)))
    grams = Counter(txt[i:i + 2] for i in range(len(txt) - 1))
    return [{"word": w, "count": c} for w, c in grams.most_common(top)]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--file", help="本地视频文件")
    ap.add_argument("--url", help="抖音链接（需浏览器活跃会话；推荐改用插件下载后 --latest）")
    ap.add_argument("--latest", action="store_true",
                    help="自动取下载目录里最新的一段视频（配合浏览器插件使用）")
    ap.add_argument("--dir", default=os.path.join(os.path.expanduser("~"), "Downloads"),
                    help="--latest 的扫描目录")
    ap.add_argument("--interval", type=float, default=2.0, help="抽帧间隔秒")
    ap.add_argument("--frames", type=int, default=60, help="最多抽多少帧")
    a = ap.parse_args()

    if a.latest and not a.file:
        import glob
        exts = ("*.mp4", "*.mov", "*.mkv", "*.webm", "*.flv")
        cand = []
        for e in exts:
            cand += glob.glob(os.path.join(a.dir, "**", e), recursive=True)
        cand = [p for p in cand if os.path.isfile(p)]
        if not cand:
            print("ABORT: %s 下没有找到视频，请先用浏览器插件下载" % a.dir)
            return 2
        a.file = max(cand, key=os.path.getmtime)
        print("自动选取最新视频:", a.file, flush=True)
    if not a.file and not a.url:
        ap.print_help(); return 1

    rid = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = os.path.join(OUTBASE, rid)
    os.makedirs(work, exist_ok=True)
    print("输出目录:", work, flush=True)

    video = a.file
    source = a.file or a.url
    if a.url:
        print("尝试从链接下载（需要 Chrome 已访问过 douyin.com）...", flush=True)
        video, err = download(a.url, work)
        if not video:
            print("ABORT:", err)
            return 2
    if not video or not os.path.isfile(video):
        print("ABORT: 视频文件不存在:", video)
        return 2

    meta = probe(video)
    print("元信息:", meta, flush=True)
    frames, ferr = extract_frames(video, work, a.interval, a.frames)
    print("抽帧 %d 张 %s" % (len(frames), ferr), flush=True)
    if not frames:
        print("ABORT: 抽帧失败，未产出任何分析")
        return 2

    rows = ocr_frames(frames, a.interval)
    segs = dedupe(rows, a.interval)
    print("识别出 %d 个文字段" % len(segs), flush=True)
    for s in segs[:6]:
        print("  [%s] %s" % (ts(s["start"]), s["text"][:70]), flush=True)

    md = ["# 视频画面文字稿（OCR 提取）", "",
          "| 项 | 值 |", "|---|---|",
          "| 来源 | `%s` |" % source,
          "| 时长 | %s 秒 |" % meta.get("duration"),
          "| 分辨率 | %s x %s |" % (meta.get("width"), meta.get("height")),
          "| 抽帧 | 每 %s 秒，共 %d 帧 |" % (a.interval, len(rows)),
          "| 文字段 | %d |" % len(segs), "",
          "## 带时间轴文字稿", ""]
    for s in segs:
        md.append("- **%s → %s**　%s" % (ts(s["start"]), ts(s["end"]), s["text"]))
    if not segs:
        md.append("- （未识别到画面文字；该视频可能无硬字幕，或需语音转写）")
    md += ["", "## 高频词（2-gram 统计，非语义总结）", ""]
    md += ["- %s ×%d" % (k["word"], k["count"]) for k in keywords(segs)] or ["- （无）"]
    md += ["", "> 本脚本只做真实 OCR 提取与统计，**不生成 AI 总结、不编造内容**。",
           "> 若要「内容讲了什么」的语义总结，需另接 LLM；若无硬字幕，需语音转写（ASR）。", ""]
    with open(os.path.join(work, "transcript.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(md))

    srt = []
    for i, s in enumerate(segs, 1):
        srt += [str(i), "%s --> %s" % (ts(s["start"], True), ts(s["end"], True)), s["text"], ""]
    with open(os.path.join(work, "transcript.srt"), "w", encoding="utf-8-sig") as f:
        f.write("\n".join(srt))

    report = {"generated": rid, "source": source, "meta": meta,
              "interval": a.interval, "frames_sampled": len(rows),
              "segments": segs, "unique_texts": len(set(s["text"] for s in segs)),
              "keywords": keywords(segs),
              "engine": "ffmpeg frames + rapidocr_onnxruntime (no model weights, no API, no Docker)",
              "note": "只做真实提取与统计，不生成语义总结；无硬字幕时结果为 0 条属正常"}
    with open(os.path.join(work, "report.json"), "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
    print("\nOK 输出:", work)
    print("  transcript.md / transcript.srt / report.json")
    return 0


if __name__ == "__main__":
    sys.exit(main())
