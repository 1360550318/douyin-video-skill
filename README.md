# douyin-video-skill

**一条抖音分享链接 → 视频内容分析 + 博主作品全量采集。零模型、零 API、零 Docker、不读登录态。**

> English summary — Turn a Douyin share link into a full content report (timeline OCR text,
> SRT subtitles, platform-provided chapter highlights) and crawl a creator's entire catalogue.
> No LLM, no API key, no Docker, no account login required.
> 仅供学习与研究你已能正常访问的公开内容，请遵守 [使用边界](#七使用边界与合规声明)。

---

## 一、它能干什么

| 能力 | 命令 | 说明 |
|---|---|---|
| **单条视频全分析** | `python scripts/douyin_auto.py --url "<分享链接>"` | 自动开无头浏览器取真实 CDN 流 → 下载 MP4 → 抽帧 OCR → 产出带时间轴的文字稿 + SRT + 平台自带章节要点 |
| **博主全部作品采集** | `python scripts/douyin_crawler.py --url "<主页或任意一条作品链接>"` | 从任意一条作品链接反查作者 → 翻页遍历 → 归属校验 → 增量落盘 JSONL + 覆盖率对照 |
| **博主最近 N 期** | `python scripts/douyin_channel.py --sec-uid "<sec_uid>" --limit 10` | 主页作品接口 + DOM 合并候选，逐条校验作者，按发布时间倒序取前 N |
| **只要音频** | `python scripts/douyin_audio.py --url "<分享链接>"` | 单独抓音频直链转 mp3（画面流与声音流是两条独立地址，见下文坑 2） |
| **本地视频分析** | `python scripts/douyin_analyze.py --file "video.mp4"` | 已有文件直接抽帧 OCR |

### 真实产物长什么样

一次实测（2026-11-10，链接 `v.douyin.com/HUYCq2k7vFc/`）：

```
147.33 秒 / 576×1024 / 5.89 MB  →  下载耗时 0.8 秒
抽 29 帧  →  识别出 29 段画面文字
同时抓到平台自带的 7 章「章节要点」+ 作者 FredTalk + 发布时间
```

每个视频目录产出：

| 文件 | 内容 |
|---|---|
| `page_content.md` | 平台自带章节要点、描述、作者、发布时间 |
| `transcript.md` | 带时间轴的画面文字稿（去重合并成时间段） |
| `transcript.srt` | 同上，SRT 字幕格式，可直接烧进视频 |
| `report.json` | 结构化报告：时长/分辨率/编码/帧率/词频/逐段文字 |
| `source.mp4` | 下载到的原片 |
| `meta.json` | 页面元信息快照 |

---

## 二、为什么不用 yt-dlp 直接下

抖音 2026-08 起关闭了纯 HTTP 通道：

- `yt-dlp` 裸跑 → `403 / Fresh cookies needed`
- 匿名 HTTP 抓页面 → 只返回空壳
- `/aweme/v1/web/aweme/detail/` 是**签名保护接口**，纯 Python 签名 3/8 成功、其余 403

社区项目 `Evil0ctal/Douyin_TikTok_Download_API` 的 `signing/protection.py` 里写得很清楚：
必须浏览器环境兜底。

**所以本项目的做法是**：让**抖音页面自己的 JS** 完成签名与取流，我们只从
`<video>.currentSrc` / `performance` 资源表 / `Network.responseReceived` 里读它**已经拿到的** CDN 地址。

- 不调私有接口
- 不读你本机浏览器的登录态文件（`douyin_analyze.py` 的 cookie 兜底是**显式开启**的，见下）
- 不模拟点击、不做逆向

### 踩过的坑（都已内建处理）

| 坑 | 现象 | 处理 |
|---|---|---|
| 1 | 页面先渲染骨架，`<video>.currentSrc` 早期是 71 字符占位流或 `blob:`（MSE） | 等到真实 CDN 地址（`douyinvod` / `video/tos`）+ 正文里「视频数据加载中」消失，内置 50 秒上限 |
| 2 | 抖音 CDN 把**画面流和声音流分成两个独立地址** | 下载到的 MP4 只有 h264 画面、**没有音轨**，ffprobe 只列出一条 video 流。要音频必须走 `douyin_audio.py` 单独链路 |
| 3 | 批量复用一个 CDP 会话时挑流会串 | 挑流**只认当前页**：`<video>.src` → 本页 `performance` 资源 → 本轮导航响应。混进历史响应会导致所有视频下载成同一个文件 |
| 4 | 视频页 DOM 里的 `/video/` 链接**大多是推荐区别人的作品** | 归属校验只用 `detail.author.sec_uid == 目标`，实测 0/12 误命中 |

---

## 三、安装

### 依赖

- **Python 3.9+**
- **Edge 或 Chrome**（用来执行页面自己的 JS 取流）
- **ffmpeg / ffprobe**（加入 PATH，或用环境变量指定）
- Python 包：

```bash
pip install -r requirements.txt
playwright install chromium      # 只有 douyin_crawler.py 需要
```

### 全局配置（可选，全有默认值）

| 环境变量 | 默认 | 说明 |
|---|---|---|
| `DOUYIN_OUT` | `~/douyin_output` | 产物根目录 |
| `DOUYIN_FFMPEG` / `DOUYIN_FFPROBE` | 自动查找 | ffmpeg / ffprobe 路径 |
| `DOUYIN_BROWSER` | 自动找 Edge → Chrome | 浏览器可执行文件 |
| `DOUYIN_BROWSER_CHANNEL` | 自动推断 `msedge`/`chrome` | Playwright 通道，留空用自带内核 |
| `DOUYIN_BROWSER_COOKIES` | **空（不读）** | 设为 `chrome`/`edge` 才让 yt-dlp 读本机浏览器 cookie |
| `DOUYIN_PROFILE` | `~/.cache/douyin_video_skill` | 独立浏览器 profile，不污染你的日常浏览数据 |

---

## 四、使用

### 1. 分析一条视频

```bash
python scripts/douyin_auto.py --url "https://v.douyin.com/xxxxxxx/" --interval 3 --frames 40
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--url` | 必填 | 分享短链或完整链接 |
| `--interval` | 3 | 抽帧间隔（秒） |
| `--frames` | 40 | 最多抽多少帧 |
| `--wait` | 50 | 页面等待上限（秒） |
| `--text-only` | 关 | 只抓页面文字，不下载视频（最省时间） |

产物在 `~/douyin_output/auto_<时间戳>/`，同时生成 `RESULT.md` 汇总。

### 2. 采集一个博主的全部作品

```bash
python scripts/douyin_crawler.py --url "https://www.douyin.com/user/<sec_uid>"
# 或者给任意一条他的作品链接，脚本自动反查作者
python scripts/douyin_crawler.py --url "https://www.douyin.com/video/xxxx"
```

产物在 `~/douyin_output/crawl/<作者>_<时间戳>/`：

| 文件 | 内容 |
|---|---|
| `videos.jsonl` | 增量落盘的逐条视频记录（断点续传） |
| `coverage.json` | 覆盖率对照（平台 `aweme_count` vs 实收条数） |
| `crawl_meta.json` | 作者、sec_uid、平台声明总数、耗时 |

### 3. 只要音频

```bash
python scripts/douyin_audio.py --url "https://v.douyin.com/xxxx/" --bitrate 192
```

### 4. 分析本地视频

```bash
python scripts/douyin_analyze.py --file "video.mp4" --interval 3 --frames 60
python scripts/douyin_analyze.py --latest --dir ~/Downloads
```

---

## 五、能力边界（请务必读完再用）

### ⚠️ 「扒全 N 条作品」在当前平台条件下**不可达**

服务端对未登录/风控会话只暴露**最近 38 条**（`count` 从 1 调到 500 全部收敛到 38）。
平台页面上的 `aweme_count`（如 133）还**包含非视频作品与风控折叠项**。

所以本项目做到的是：

- **尽力遍历并如实报告覆盖率**，绝不虚报条数（`coverage.json` 里能直接对照）
- 想补齐历史，只能靠**每日增量账本**长期累积（每天跑一次，去重入账）

### 其他边界

- 只做**真实提取与统计**（页面文字 + 画面 OCR + 时间轴 + 词频），**不生成 AI 语义总结**、不编造内容
- 识别不到就写 0 条，并如实说明「视频本体未取得」
- 抽取到的画面文字是 OCR 结果，**可能有错字**，需要人工复核
- 页面章节要点是**平台自己生成的**，与视频实际口播可能不一致

---

## 六、目录结构

```
douyin-video-skill/
├── SKILL.md                    # WorkBuddy / Claude Skill 定义
├── README.md
├── LICENSE                     # MIT
├── requirements.txt
├── scripts/
│   ├── config.py               # 路径与外部依赖集中解析
│   ├── douyin_auto.py          # 主链路：链接 → 视频 → 全分析
│   ├── douyin_analyze.py       # 抽帧 OCR 分析器（也可分析本地文件）
│   ├── douyin_channel.py       # 博主最近 N 期
│   ├── douyin_audio.py         # 只要音频（独立 CDN 地址）
│   └── douyin_crawler.py       # 博主全部作品采集
└── docs/
    ├── 技术原理.md
    └── 常见问题.md
```

## 七、使用边界与合规声明

本项目仅用于**个人学习与研究你已能正常访问的公开内容**。

请遵守：

- 不批量采集、不绕过付费或私密内容、不二次分发
- 不存储他人凭据；`DOUYIN_BROWSER_COOKIES` 默认关闭，需要时由使用者自行决定
- 抓取到的内容版权属于原作者，**请勿直接商用或再分发**
- 遵守目标平台的服务条款与所在地法律

## 八、致谢

- [LizardByte/Sunshine](https://github.com/LizardByte/Sunshine)（本文思路与其 CDP 客户端风格相近的最小实现思路）
- [Evil0ctal/Douyin_TikTok_Download_API](https://github.com/Evil0ctal/Douyin_TikTok_Download_API)（签名保护机制的公开记录）
- [RapidAI/RapidOCR](https://github.com/RapidAI/RapidOCR)（本地 OCR 引擎）

## License

MIT
