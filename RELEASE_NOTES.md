# v1.0.0

首个版本：把一条抖音分享链接变成一份可复核的内容分析报告，并支持采集一个博主的全部作品。

## 能力

| 能力 | 命令 |
|---|---|
| 单条视频全分析 | `python scripts/douyin_auto.py --url "<分享链接>"` |
| 博主全部作品采集 | `python scripts/douyin_crawler.py --url "<主页或任意一条作品链接>"` |
| 博主最近 N 期 | `python scripts/douyin_channel.py --sec-uid "<sec_uid>" --limit 10` |
| 只要音频 | `python scripts/douyin_audio.py --url "<分享链接>"` |
| 本地视频分析 | `python scripts/douyin_analyze.py --file "video.mp4"` |

## 特点

- **零模型、零 API、零 Docker**：OCR 用本地 RapidOCR（ONNX），不联网、不调大模型
- **不读登录态**：主链路用独立的 headless 浏览器 profile；yt-dlp 读浏览器 cookie 是**显式开启**的，默认关闭
- **不编造**：只做真实提取与统计，识别不到就写 0 条
- **不虚报条数**：采集覆盖率如实对照平台 `aweme_count` 写在 `coverage.json` 里

## 实测

```
链接 → 147.33 秒 / 576×1024 / h264 / 5.89 MB
下载 0.6s（10.7 MB/s）
抽 15 帧 → 识别 15 段画面文字
同时抓到平台自带 7 章「章节要点」+ 作者 + 发布时间
```

创作者采集：自动反查 `sec_uid`、BFS 遍历、逐条归属校验、增量落盘、覆盖率如实报告。

## 安装

```bash
pip install -r requirements.txt
playwright install chromium   # 仅 douyin_crawler.py 需要
```

需要 Edge 或 Chrome，以及 PATH 里的 ffmpeg / ffprobe。可用 `DOUYIN_OUT`、`DOUYIN_BROWSER`、
`DOUYIN_FFMPEG` 等环境变量覆盖默认值。

## 边界

- 服务端对未登录会话只暴露最近 38 条作品，「一次扒全 N 条」在当前平台条件下**不可达**，
  补齐历史需靠每日增量账本长期累积
- OCR 文字可能有错字，引用前请人工复核
- 仅用于个人学习与研究你已能正常访问的公开内容，版权属于原作者，请勿商用或再分发
