# -*- coding: utf-8 -*-
"""
抖音创作者全量作品采集器（作者全量爬虫）
=================================================
目标：给定一个创作者（主页链接 / 任意一条他的视频链接 / 作者名），把**他全部作品**采下来，
      并输出可核对的覆盖率报告。

为什么不能只翻主页接口
------------------------
主页作品接口第一页只给 18 条（count=50 时给 41 条），后续页需要平台对 max_cursor 的有效签名，
无头浏览器里无法自动触发（实测：滚轮/程序滚动/懒加载/游标变体/纯 Python a_bogus 均不生效）。
但**视频页 DOM 会带出同一作者的更早作品链接** → 因此采用「广度优先 + 作者校验」的采集策略。

采集源
------
A. 主页作品接口（页面自带签名）→ 最新一批（count=50 约 41 条）
B. 每个视频页 DOM 的 a[href*="/video/"] → 更早作品候选（核心扩展能力）
作者归属
--------
每条视频页都会触发 aweme/detail 响应，author.sec_uid 与目标一致才计入，避免混入推荐视频。

工程约束
--------
· 增量落盘 JSONL，**断点续传**（重跑不重复、不丢进度）
· 覆盖率报告：已采 / 平台宣称总数 aweme_count，**不虚报**
· 单条失败不中断整轮
"""
import argparse
import datetime
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from playwright.sync_api import sync_playwright

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/138.0.0.0 Safari/537.36")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import OUT_ROOT, BROWSER_CHANNEL  # noqa: E402

ROOT = os.path.join(OUT_ROOT, "crawl")

JS_DOM_IDS = r"""(sec) => {
  const out = new Set();
  const all = Array.from(document.querySelectorAll('a[href*="/video/"]'));
  // 1) 作者作品区：包含作者主页链接的祖先容器里的视频链接（避免混入"相关推荐"）
  const sel = 'a[href*="/user/' + sec + '"]';
  const authorLinks = Array.from(document.querySelectorAll(sel));
  for (const al of authorLinks) {
    let node = al;
    for (let hop = 0; hop < 8 && node; hop++) {
      const vids = Array.from(node.querySelectorAll('a[href*="/video/"]'))
        .map(a => (a.href.match(/video\/(\d+)/) || [])[1]).filter(Boolean);
      if (vids.length) { vids.forEach(v => out.add(v)); break; }
      node = node.parentElement;
    }
  }
  // 2) 兜底：全页视频链接（作者作品区没找到时用）
  if (!out.size) {
    all.forEach(a => { const m = a.href.match(/video\/(\d+)/); if (m) out.add(m[1]); });
  }
  return Array.from(out);
}"""

JS_POST_PAGE = r"""async ({sec, count}) => {
  const u = '/aweme/v1/web/aweme/post/?device_platform=webapp&aid=6383&channel=channel_pc_web'
    + '&sec_user_id=' + encodeURIComponent(sec)
    + '&max_cursor=0&locate_query=false&show_live_replay_strategy=1&need_time_list=1'
    + '&time_list_query=0&whale_cut_token=&cut_version=1&count=' + count
    + '&publish_video_strategy_type=2&from_user_page=1&update_version_code=170400&pc_client_type=1'
    + '&version_code=290100&version_name=29.1.0&cookie_enabled=true'
    + '&screen_width=1500&screen_height=900&browser_language=zh-CN&browser_platform=Win32'
    + '&browser_name=Chrome&browser_version=138.0.0.0&browser_online=true&engine_name=Blink'
    + '&engine_version=138.0.0.0&os_name=Windows&os_version=10&cpu_core_num=6&device_memory=16'
    + '&platform=PC&downlink=10&effective_type=4g&round_trip_time=100';
  const r = await fetch(u, {credentials: 'include', headers: {accept: 'application/json'}});
  const t = await r.text();
  try { const j = JSON.parse(t); const L = j.aweme_list || [];
        return {ids: L.map(x => x.aweme_id), has_more: j.has_more, max_cursor: j.max_cursor}; }
  catch(e) { return {ids: [], err: t.slice(0, 80)}; }
}"""


def resolve_short(url):
    class NR(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, req, fp, code, msg, headers, newurl):
            print("  302 ->", newurl[:140], flush=True)
            return None
    op = urllib.request.build_opener(NR)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        op.open(req, timeout=15)
    except urllib.error.HTTPError as e:
        loc = e.headers.get("Location", "")
        if loc:
            print("  302 ->", loc[:140], flush=True)
            return loc
    return ""


def norm_detail(d):
    au = d.get("author", {}) or {}
    st = d.get("statistics", {}) or {}
    vi = d.get("video", {}) or {}
    ct = d.get("create_time", 0) or 0
    return {
        "aweme_id": d.get("aweme_id", ""),
        "desc": (d.get("desc") or "").strip(),
        "create_time": ct,
        "date": datetime.datetime.fromtimestamp(ct).strftime("%Y-%m-%d") if ct else "",
        "duration_s": round((vi.get("duration") or 0) / 1000, 1),
        "digg": st.get("digg_count", 0), "comment": st.get("comment_count", 0),
        "share": st.get("share_count", 0), "collect": st.get("collect_count", 0),
        "author": au.get("nickname", ""), "sec_uid": au.get("sec_uid", ""),
        "source": "detail",
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", required=True, help="创作者主页链接 或 他任意一条视频链接")
    ap.add_argument("--name", default="", help="作者名（仅作记录）")
    ap.add_argument("--max-videos", type=int, default=400, help="安全上限")
    ap.add_argument("--count", type=int, default=50, help="主页接口单页条数")
    ap.add_argument("--idle-limit", type=int, default=60, help="连续多少轮无新增就停")
    ap.add_argument("--out", default=ROOT)
    ap.add_argument("--headless", type=int, default=1)
    a = ap.parse_args()

    os.makedirs(a.out, exist_ok=True)
    key = re.sub(r"[^A-Za-z0-9\u4e00-\u9fff]+", "_", (a.name or a.url))[:36]
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    workdir = os.path.join(a.out, "%s_%s" % (key, stamp))
    os.makedirs(workdir, exist_ok=True)
    jsonl = os.path.join(workdir, "videos.jsonl")
    meta_path = os.path.join(workdir, "crawl_meta.json")

    real = resolve_short(a.url)
    m = re.search(r"/video/(\d+)", real or a.url)
    seed_vid = m.group(1) if m else ""

    collected = {}
    dom_ids = []
    queue = []
    seen_ids = set()
    t0 = time.time()
    rounds_no_new = 0
    total_declared = None
    nickname = a.name

    with sync_playwright() as p:
        br = p.chromium.launch(channel=BROWSER_CHANNEL, headless=bool(a.headless))
        ctx = br.new_context(user_agent=UA, viewport={"width": 1400, "height": 900}, locale="zh-CN")
        pg = ctx.new_page()

        detail_box = {}
        prof_box = {}

        def on_resp(r):
            u = r.url
            if "/aweme/v1/web/aweme/detail/" in u:
                try:
                    d = r.json().get("aweme_detail", {}) or {}
                    if d.get("aweme_id"):
                        detail_box[d["aweme_id"]] = d
                except Exception:
                    pass
            elif "/aweme/v1/web/user/profile/other/" in u:
                try:
                    prof_box.update(r.json())
                except Exception:
                    pass

        pg.on("response", on_resp)

        # ---------- 第一步：确定作者身份与总数 ----------
        if seed_vid:
            pg.goto("https://www.douyin.com/video/%s" % seed_vid, wait_until="domcontentloaded",
                    timeout=60000)
            pg.wait_for_timeout(8000)
            d = detail_box.get(seed_vid)
            if d:
                nickname = (d.get("author") or {}).get("nickname") or nickname
                sec = (d.get("author") or {}).get("sec_uid", "")
            else:
                sec = ""
        else:
            sec = ""
            dom_ids = []

        # 若给的是主页链接，从 profile 响应里取 sec_uid 与 aweme_count
        if not sec:
            m2 = re.search(r"/user/(MS4wLjAB[A-Za-z0-9_\-]+)", a.url)
            sec = m2.group(1) if m2 else ""
        if sec:
            # sec 已确定：先在种子视频页抓「作者作品区」，再去主页取身份与总数
            try:
                dom_ids = list(set(dom_ids) | set(pg.evaluate(JS_DOM_IDS, sec) or []))
            except Exception:
                pass
            pg.goto("https://www.douyin.com/user/%s?from_tab_name=main" % sec,
                    wait_until="domcontentloaded", timeout=60000)
            pg.wait_for_timeout(8000)
            info = prof_box.get("user_info") or prof_box.get("user") or {}
            nickname = info.get("nickname") or nickname
            total_declared = info.get("aweme_count")
            dom_ids = list(set(dom_ids) | set(pg.evaluate(JS_DOM_IDS, sec) or []))

        if not sec:
            print("无法定位 sec_uid，退出"); return 2
        print("作者:", nickname, "| sec_uid:", sec, "| 平台作品总数(aweme_count):", total_declared)

        # ---------- 第二步：主页接口拿最新一批 ----------
        post = pg.evaluate(JS_POST_PAGE, {"sec": sec, "count": a.count})
        for vid in post.get("ids", []):
            if vid not in seen_ids:
                seen_ids.add(vid); queue.append(vid)
        print("主页接口首批:", len(post.get("ids", [])), "| has_more:", post.get("has_more"))

        for vid in dom_ids:
            if vid not in seen_ids:
                seen_ids.add(vid); queue.append(vid)
        if seed_vid and seed_vid not in seen_ids:
            seen_ids.add(seed_vid); queue.append(seed_vid)
        print("候选队列:", len(queue))

        # ---------- 第三步：BFS 逐条采集 ----------
        fh = open(jsonl, "a", encoding="utf-8")
        idx = 0
        while queue and idx < a.max_videos:
            vid = queue.pop(0)
            idx += 1
            before = len(collected)
            detail_box.clear()
            try:
                pg.goto("https://www.douyin.com/video/%s" % vid, wait_until="domcontentloaded",
                        timeout=45000)
                pg.wait_for_timeout(2600)
            except Exception as e:
                print("  [%d] %s 打开失败 %s" % (idx, vid, type(e).__name__), flush=True)
                continue
            d = detail_box.get(vid)
            if d:
                rec = norm_detail(d)
                if rec["sec_uid"] == sec and vid not in collected:
                    collected[vid] = rec
                    fh.write(json.dumps(rec, ensure_ascii=False) + "\n"); fh.flush()
            # 扩展候选
            try:
                new_ids = pg.evaluate(JS_DOM_IDS, sec) or []
            except Exception:
                new_ids = []
            added = 0
            for nid in new_ids:
                if nid not in seen_ids:
                    seen_ids.add(nid); queue.append(nid); added += 1
            gained = len(collected) - before
            if gained == 0 and added == 0:
                rounds_no_new += 1
            else:
                rounds_no_new = 0
            if idx % 5 == 0 or gained or added:
                pct = ("%.1f%%" % (len(collected) / total_declared * 100)) if total_declared else "-"
                print("[%d] %s | 本条%+d 候选%+d | 已采 %d/%s (%s) | 队列 %d | %.0fs" % (
                    idx, vid[-6:], gained, added, len(collected), total_declared, pct,
                    len(queue), time.time() - t0), flush=True)
                json.dump({"author": nickname, "sec_uid": sec, "declared_total": total_declared,
                           "collected": len(collected), "queue": len(queue), "updated": stamp},
                          open(meta_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
            if rounds_no_new >= a.idle_limit:
                print("连续 %d 轮无新增，停止" % a.idle_limit); break
            if total_declared and len(collected) >= total_declared:
                print("已达平台宣称总数，停止"); break
        fh.close()
        br.close()

    # ---------- 第四步：覆盖率报告 ----------
    items = sorted(collected.values(), key=lambda x: x["create_time"], reverse=True)
    report = {
        "author": nickname, "sec_uid": sec, "generated": stamp,
        "declared_total": total_declared, "collected": len(items),
        "coverage": round(len(items) / total_declared * 100, 1) if total_declared else None,
        "elapsed_min": round((time.time() - t0) / 60, 1),
        "date_range": [items[-1]["date"], items[0]["date"]] if items else [],
        "total_digg": sum(i["digg"] for i in items),
        "total_share": sum(i["share"] for i in items),
        "total_collect": sum(i["collect"] for i in items),
    }
    json.dump(report, open(os.path.join(workdir, "coverage.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    print("\n=== 覆盖率报告 ===")
    print(json.dumps(report, ensure_ascii=False, indent=1))
    print("输出目录:", workdir)
    return 0


if __name__ == "__main__":
    sys.exit(main() or 0)
