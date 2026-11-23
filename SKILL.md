---
name: douyin-video
description: 抖音链接一键内容分析。当用户发来抖音分享链接、短链（v.douyin.com）、或要求「分析这个视频讲了什么」「把视频转成文字稿」「提取视频里的字」「分析这个博主的全部作品」时使用。主链路全自动、零用户操作：douyin_auto.py 用无头 Edge + CDP 加载页面，取真实 CDN 播放流并抓平台自带章节要点，下载 MP4 后抽帧 OCR，输出带时间轴文字稿 + SRT + report.json + 页面文字。也能用 douyin_crawler.py 从任意一条作品链接反查作者并采集其全部作品。零模型、零 API、零 Docker、不读登录态、不编造内容。也适用于本地视频文件分析。
---

# 抖音视频 → 内容分析

## 一句话用法（主链路，全自动）

用户给链接，直接跑（**不需要用户装插件、不需要关浏览器、不需要登录**）：

```bash
python scripts/douyin_auto.py --url "https://v.douyin.com/xxxx/" --interval 5 --frames 40
```

脚本自己完成 4 步：启动独立 headless Edge + CDP → 等页面异步加载完（拿真实 CDN 流 + 页面正文）
→ 下载 MP4 → 调 `douyin_analyze.py` 抽帧 OCR。产物目录在 stdout 里，另生成 `RESULT.md` 汇总。

**实测**：链接 → 147.33 秒 / 576×1024 / 5.89MB 视频，下载 0.8 秒，抽 29 帧识别 29 段画面文字，
同时抓到平台自带 7 章「章节要点」、作者名、发布时间。

## 采集一个博主的全部作品

```bash
python scripts/douyin_crawler.py --url "https://www.douyin.com/video/<任意一条作品id>"
```

脚本会从这条作品反查作者 `sec_uid`，再翻页遍历主页作品接口 + 视频页 BFS，逐条校验作者归属，
增量落盘 JSONL，并输出 `coverage.json` 对照平台 `aweme_count` **如实报告覆盖率，不虚报条数**。

## 核心原理与边界（先读）

- 抖音 2026-08 起关闭纯 HTTP 通道，`yt-dlp` 裸跑 `403 / Fresh cookies needed`；匿名 HTTP 抓页面只给空壳。
- `/aweme/v1/web/aweme/detail/` 是**签名保护接口**，纯 Python 签名 3/8 成功、其余 403，必须浏览器环境兜底
  → **不要手搓纯 Python 逆向**。
- 正确做法：让**抖音页面自己的 JS** 完成签名取流，我们只从 `<video>` / `performance` 资源表 /
  `Network.responseReceived` 里读它已经拿到的 CDN 地址。不调私有接口、不读登录态文件、不模拟点击。
- 坑 1：页面先渲染骨架，`<video>.currentSrc` 早期是 71 字符占位流或 `blob:`（MSE）。
  **必须等到真实 CDN 地址（`douyinvod` / `video/tos`）+ 正文里「视频数据加载中」消失**，
  脚本已内置 `real_stream()` / `ready()` 判定与 50 秒上限。
- 坑 2：`blob:` 地址不能直接 HTTP 下载，脚本会从网络响应里挑真实 URL（`pick_stream()`）。
- 坑 3：抖音 CDN 把**画面流和声音流分成两个独立地址**，`douyin_auto.py` 下载到的 MP4
  **只有画面没有音轨**。要音频必须单独走 `douyin_audio.py`。
- 坑 4：批量复用一个 CDP 会话时，挑流**只能认当前页**（`<video>.src` → 本页 `performance` 资源 →
  本轮导航的响应）。混进历史响应会导致所有视频下载成同一个文件。挑流不稳就回退到「逐条调用
  `douyin_auto.py`」，实测 10/10 可靠。
- 本技能只做**真实提取与统计**（页面文字 + 画面 OCR + 时间轴 + 高频词），不生成 AI 语义总结、
  不编造视频内容；识别不到就写 0 条。

## 工具位置

| 用途 | 路径 |
|---|---|
| **全自动主脚本** | `scripts/douyin_auto.py` |
| 抽帧 OCR 分析器 | `scripts/douyin_analyze.py`（支持 `--file` / `--latest` / `--url`） |
| 博主全部作品采集 | `scripts/douyin_crawler.py` |
| 博主最近 N 期 | `scripts/douyin_channel.py --sec-uid <sec_uid> --limit 10` |
| 只要音频 | `scripts/douyin_audio.py` |
| 路径与依赖解析 | `scripts/config.py` |

产物根目录默认 `~/douyin_output/`（可用环境变量 `DOUYIN_OUT` 改）。
依赖：`websocket-client`、`requests`、`rapidocr_onnxruntime`、`numpy`、`Pillow`、
`playwright`（仅 crawler）、`yt-dlp`（仅 analyze 的 `--url` 兜底）；ffmpeg/ffprobe 需在 PATH。

## 四条链路，按可靠性排序

### A. `douyin_auto.py --url`（主链路，全自动）

可选参数：`--interval`（抽帧间隔秒，默认 3）、`--frames`（默认 40）、`--wait`（页面等待上限，默认 50）、
`--text-only`（只抓页面文字不下载视频）。

成功时输出：`page_content.md`（平台自带章节要点/描述/作者/发布时间）、`meta.json`、`source.mp4`、
`RESULT.md`，以及 `transcript.md` / `transcript.srt` / `report.json`。

### B. 已有本地视频（最稳、最快）

```bash
python scripts/douyin_analyze.py --file "video.mp4" --interval 3 --frames 60
python scripts/douyin_analyze.py --latest --interval 3 --frames 60
```

### C. 采集博主全部作品

```bash
python scripts/douyin_crawler.py --url "<创作者主页 或 他任意一条作品链接>" --max-videos 400
```

产物：`videos.jsonl` / `coverage.json` / `crawl_meta.json`。
**能力边界**：服务端对未登录会话只暴露最近 38 条，平台 `aweme_count` 还含非视频与风控折叠项
→ **「扒全 N 条」不可达**，脚本会如实输出覆盖率，绝不虚报。补齐历史靠每日增量账本长期累积。

### D. 只要音频（mp3）

```bash
python scripts/douyin_audio.py --url "https://v.douyin.com/xxxx/" [--out 路径.mp3] [--bitrate 192]
```

复用 CDP 在**页面上下文** fetch 作品详情（页面自己的 JS 补签名）→
解析 `aweme_detail.music.play_url.url_list` 取音乐直链 → 下载后用 ffprobe 验「确有音频流」→ ffmpeg 转 mp3。

## 输出后怎么回答用户

- 有产物：先给视频档案（标题/作者/发布时间/时长），再用**平台自带章节要点**说明讲了什么，
  然后摘几段带时间轴的画面文字，最后列产物路径。
- 明确区分三类信息：用户贴的分享文案 / 外部核实资料 / **视频本体真实内容**。前两类不能包装成第三类。
- 数字口径：视频画面里的涨星数是视频采集日的，若另抓的榜单是滚动 7 天，时间点不同不要说谁错。
- 没拿到视频：如实说，不推测。若只拿到页面文字（`--text-only` 或下载失败），
  说明「视频本体未取得，以下为平台页面文字」。
- OCR 结果可能有错字，引用时注明是「画面 OCR 文字」。
- 用户要口播逐字稿 / 语义总结 → 需 ASR/LLM，**先征得同意再接**。

## 合规

仅用于个人学习/研究用户自己已经能正常访问的公开内容；不批量采集、不绕过付费/私密内容、
不二次分发、不存储他人凭据（`DOUYIN_BROWSER_COOKIES` 默认关闭）。
