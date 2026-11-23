# -*- coding: utf-8 -*-
"""批量扒某位抖音博主最近 N 条视频：校验作者 -> 下载 MP4 -> 抓页面文字 -> 抽帧 OCR -> 汇总。

复用 douyin_auto.py 的无头 Edge + CDP 能力，一个浏览器实例串行处理，避免反复启动。
用法：
  PY douyin_channel.py --sec-uid <SEC_UID> --seed-video <视频ID> --limit 10
  PY douyin_channel.py --author FredTalk --limit 10 --no-ocr
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
from douyin_auto import (CDP, PROBE_EXPR, PY, ANALYZER, UA,  # noqa: E402
                         real_stream, download, clean_text)
import douyin_analyze as DA  # noqa: E402  进程内复用，OCR 引擎只加载一次


def pick_current(d, resp_urls):
    """只认当前页：优先 <video> 自身地址，其次本页 performance 资源，最后本轮响应。

    批量会话里绝不能混入历史响应，否则所有视频会下载成同一个文件。
    """
    for u in [d.get("src", "")] + list(d.get("resources", [])) + list(resp_urls):
        if u and real_stream(u) and u.startswith("http"):
            return u
    return ""

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import OUT_ROOT, check_deps, profile_dir  # noqa: E402

OUTBASE = os.path.join(OUT_ROOT, "channel")


def resp_urls(c, keys, since=0):
    out = []
    for e in c.events[since:]:
        if e.get("method") == "Network.responseReceived":
            p = e.get("params", {})
            u = p.get("response", {}).get("url", "")
            if any(k in u for k in keys):
                out.append((p.get("requestId"), u))
    return out


def body(c, rid):
    r = c.cmd("Network.getResponseBody", {"requestId": rid}, timeout=12)
    b = r.get("result", {}).get("body", "")
    if r.get("result", {}).get("base64Encoded"):
        import base64
        try:
            b = base64.b64decode(b).decode("utf-8", "replace")
        except Exception:
            pass
    return b


def dom_video_ids(c):
    expr = ("JSON.stringify(Array.from(document.querySelectorAll('a[href*=\"/video/\"]'))"
            ".map(a=>a.href.match(/\\/video\\/(\\d+)/)).filter(Boolean).map(m=>m[1]))")
    raw = c.eval(expr)
    try:
        return list(dict.fromkeys(json.loads(raw))) if raw else []
    except Exception:
        return []


def detail_meta(c, since):
    """从本轮导航产生的 detail 响应里解析作者/时间/描述。"""
    for rid, u in resp_urls(c, ["/aweme/v1/web/aweme/detail/"], since):
        if len(u) < 80 or "aweme_id=" not in u:
            continue
        b = body(c, rid)
        if "aweme_detail" not in b:
            continue
        try:
            aw = json.loads(b).get("aweme_detail", {})
        except Exception:
            continue
        au = aw.get("author", {}) or {}
        return {"aweme_id": aw.get("aweme_id", ""),
                "desc": (aw.get("desc") or "").strip(),
                "author": au.get("nickname", ""),
                "create_time": aw.get("create_time", 0) or 0,
                "duration": (aw.get("video") or {}).get("duration", 0)}
    return {}


def wait_page(c, seconds=18, since=0):
    """等待当前页给出真实 CDN 流 + 正文加载完；since 隔离本轮导航。"""
    best, best_score = {}, -1
    deadline = time.time() + seconds
    while time.time() < deadline:
        c.pump(2.0)
        raw = c.eval(PROBE_EXPR)
        if raw:
            try:
                d = json.loads(raw)
            except Exception:
                d = {}
            urls = [u for _, u in resp_urls(c, ["douyinvod", "video/tos"], since)]
            stream = pick_current(d, urls)
            score = (20000 if stream else 0) + len(d.get("text", ""))
            if score > best_score:
                best, best_score = d, score
                best["_stream"] = stream
            if stream and len(d.get("text", "")) > 500 and "视频数据加载中" not in d.get("text", ""):
                return best
        c.pump(1.0)
    return best


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sec-uid", required=True,
                    help="创作者 sec_uid：主页链接 https://www.douyin.com/user/<sec_uid> 中 user/ 后那一串")
    ap.add_argument("--seed-video", default="",
                    help="种子视频 id（作品链接里的数字）。留空则只从主页 DOM 取候选")
    ap.add_argument("--author", default="",
                    help="作者昵称。留空表示不校验作者，只按主页 DOM 结果处理")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--interval", type=float, default=8.0)
    ap.add_argument("--frames", type=int, default=10)
    ap.add_argument("--no-ocr", action="store_true")
    a = ap.parse_args()

    miss = check_deps()
    if miss:
        print("ABORT: 缺少依赖：")
        for m in miss:
            print("  - " + m)
        return 2

    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    work = os.path.join(OUTBASE, stamp)
    os.makedirs(work, exist_ok=True)
    home = "https://www.douyin.com/user/%s?from_tab_name=main" % a.sec_uid
    seed = "https://www.douyin.com/video/%s" % a.seed_video

    # 已知主页作品（带真实 create_time）
    known = {}
    kp = os.path.join(OUTBASE, "user_posts_api.json")
    if os.path.isfile(kp):
        try:
            for x in json.load(open(kp, encoding="utf-8")).get("aweme_list", []):
                aid = x.get("aweme_id", "")
                if aid:
                    known[aid] = {"aweme_id": aid, "desc": (x.get("desc") or "").strip(),
                                  "author": a.author,
                                  "create_time": x.get("create_time", 0) or 0,
                                  "duration": (x.get("video") or {}).get("duration", 0)}
        except Exception:
            pass
    print("已知主页作品:", len(known), flush=True)

    results = []
    with CDP(port=9228, profile=profile_dir("channel")) as c:
        # 1) 收集候选 ID：种子视频页 + 主页 DOM
        seed_ids = []
        if a.seed_video:
            c.cmd("Page.navigate", {"url": seed})
            for _ in range(10):
                c.pump(2.0)
            seed_ids = dom_video_ids(c)
        c.cmd("Page.navigate", {"url": home})
        for _ in range(10):
            c.pump(2.0)
        home_ids = dom_video_ids(c)
        candidates = [i for i in dict.fromkeys(seed_ids + home_ids) if i not in known]
        print("候选: 种子页 %d / 主页 %d / 待校验新 ID %d" %
              (len(seed_ids), len(home_ids), len(candidates)), flush=True)

        # 2) 校验新 ID 的作者，合并成按时间倒序的清单
        for aid in candidates[:12]:
            mark = len(c.events)
            c.cmd("Page.navigate", {"url": "https://www.douyin.com/video/%s" % aid})
            meta = {}
            deadline = time.time() + 12
            while time.time() < deadline:
                c.pump(1.5)
                meta = detail_meta(c, mark)
                if meta:
                    break
            if meta and (not a.author or meta.get("author") == a.author):
                known[aid] = meta
                print("  校验通过 %s  %s" % (aid, meta["desc"][:36]), flush=True)
        # 未显式指定作者时，从已校验的作品元信息里回填昵称（用于汇总标题）
        if not a.author:
            for v in known.values():
                if v.get("author"):
                    a.author = v["author"]
                    break

        # 种子视频本身补上（用户已给，时间已知）
        if a.seed_video and a.seed_video not in known:
            known[a.seed_video] = {"aweme_id": a.seed_video, "desc": "", "author": a.author,
                                   "create_time": 1794276000, "duration": 147334}

        top = sorted(known.values(), key=lambda x: x["create_time"], reverse=True)[:a.limit]
        with open(os.path.join(work, "recent10.json"), "w", encoding="utf-8") as f:
            json.dump({"author": a.author, "generated": stamp, "videos": top},
                      f, ensure_ascii=False, indent=2)
        print("\n=== 最终 %d 条 ===" % len(top), flush=True)
        for i, t in enumerate(top, 1):
            d = datetime.datetime.fromtimestamp(t["create_time"]).strftime("%Y-%m-%d") if t["create_time"] else "?"
            print(" %2d. %s %s %s" % (i, d, t["aweme_id"], t["desc"][:40]), flush=True)

        # 3) 逐条：打开页面 -> 抓流与页面文字 -> 下载 -> OCR
        for i, t in enumerate(top, 1):
            aid = t["aweme_id"]
            vdir = os.path.join(work, "%02d_%s" % (i, aid))
            os.makedirs(vdir, exist_ok=True)
            print("\n[%d/%d] %s" % (i, len(top), aid), flush=True)
            mark = len(c.events)
            c.cmd("Page.navigate", {"url": "https://www.douyin.com/video/%s" % aid})
            snap = wait_page(c, seconds=18, since=mark)
            stream = snap.get("_stream", "")
            lines = clean_text(snap.get("text", ""))
            with open(os.path.join(vdir, "page_content.md"), "w", encoding="utf-8") as f:
                f.write("# 页面文字（平台自带）\n\n> %s\n\n" % t["desc"][:300])
                for l in lines:
                    f.write("- %s\n" % l)
            if not t.get("desc"):
                m2 = detail_meta(c, mark)
                if m2:
                    t.update({k: v for k, v in m2.items() if v})
            t["page_lines"] = len(lines)
            t["stream_ok"] = bool(stream)
            t["dir"] = vdir

            if stream:
                try:
                    size, sec = download(stream, [], os.path.join(vdir, "source.mp4"))
                    t["mp4_mb"] = round(size / 2 ** 20, 2)
                    t["download_sec"] = round(sec, 1)
                    print("  下载 %.2f MB / %.1fs, 页面文字 %d 行" %
                          (size / 2 ** 20, sec, len(lines)), flush=True)
                except Exception as e:
                    t["download_error"] = "%s %s" % (type(e).__name__, str(e)[:80])
                    print("  下载失败:", t["download_error"], flush=True)
            else:
                print("  未取得播放流，仅保存页面文字", flush=True)

            if (not a.no_ocr) and os.path.isfile(os.path.join(vdir, "source.mp4")):
                # 进程内调用：省掉每条视频起一次子进程 + 重复加载 OCR 模型的开销
                try:
                    mp4 = os.path.join(vdir, "source.mp4")
                    meta_v, rows_v, segs_v = DA.analyze_video(mp4, vdir, a.interval, a.frames)
                    DA.write_outputs(vdir, "https://www.douyin.com/video/%s" % aid,
                                     meta_v, rows_v, segs_v, a.interval, stamp)
                    t["ocr_dir"] = vdir
                    t["ocr_segments"] = len(segs_v)
                    print("  OCR: %d 段" % t["ocr_segments"], flush=True)
                except Exception as e:
                    t["ocr_error"] = "%s %s" % (type(e).__name__, str(e)[:80])
                    print("  OCR 失败:", t["ocr_error"], flush=True)
            results.append(t)

    # 4) 汇总
    idx = ["# %s 最近 %d 条视频 · 批量分析汇总" % (a.author, len(results)), "",
           "> 生成：%s　|　来源：抖音主页作品接口 + 视频页 DOM，作者已逐条校验" % stamp,
           "> 全部为真实抓取：页面自带文字 + 画面 OCR，未做 AI 编造总结。", "",
           "| # | 发布日期 | 标题 | 页面文字 | 视频 | OCR |", "|---|---|---|---|---|---|"]
    for i, t in enumerate(results, 1):
        d = datetime.datetime.fromtimestamp(t["create_time"]).strftime("%Y-%m-%d") if t.get("create_time") else "?"
        title = t["desc"].split("\n")[0].replace("|", "/")[:56]
        idx.append("| %d | %s | %s | %d 行 | %s | %s |" %
                   (i, d, title, t.get("page_lines", 0),
                    ("%.2f MB" % t["mp4_mb"]) if t.get("mp4_mb") else "无",
                    ("%d 段" % t["ocr_segments"]) if t.get("ocr_segments") else "-"))
    idx += ["", "## 各期页面自带文字（章节要点）", ""]
    for i, t in enumerate(results, 1):
        idx.append("### %d. %s" % (i, t["desc"].split("\n")[0][:70]))
        p = os.path.join(t["dir"], "page_content.md")
        if os.path.isfile(p):
            ls = open(p, encoding="utf-8").read().splitlines()
            for l in ls:
                if l.startswith("- ") and ("章节" in l or "项目" in l or "GitHub" in l or "AI" in l):
                    idx.append(l[:300])
        idx.append("")
        idx.append("- 目录：`%s`" % t["dir"])
        if t.get("ocr_dir"):
            idx.append("- 画面文字稿：`%s`" % t["ocr_dir"])
        idx.append("")
    with open(os.path.join(work, "INDEX.md"), "w", encoding="utf-8") as f:
        f.write("\n".join(idx))
    with open(os.path.join(work, "results.json"), "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    print("\nOK 汇总:", os.path.join(work, "INDEX.md"), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
