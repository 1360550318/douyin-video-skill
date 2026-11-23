# v1.0.1

审计发现项修复版。可移植性、性能与健壮性三方面改进。

## 修复

| 级别 | 问题 | 修复 |
|---|---|---|
| **P0** | 找不到浏览器时 `subprocess.Popen([None, ...])` 直接崩 | 启动前做依赖预检（`check_deps()` / `check_playwright()`），缺什么列什么并给安装方法 |
| **P1 性能** | 批量模式每条视频起一个子进程跑 OCR，RapidOCR 模型重复加载（每次 2–3 秒） | 抽出 `analyze_video()` / `write_outputs()`，批量改为**进程内**调用，模型只加载一次；顺带省掉 N 次进程启动，产物也直接落在每个视频目录里 |
| **P2** | 设置了 `http_proxy` 的机器上，CDP 请求 `127.0.0.1` 会被代理劫持 | 新增 `LOCAL_OPENER`（`ProxyHandler({})`）绕开代理 |
| **P2** | OCR 子进程超时会抛未捕获异常 | 捕获 `TimeoutExpired` 并给出可读提示 |
| **P2** | `auto` 与 `channel` 共用浏览器 profile，同时跑会因 profile 锁冲突起不来 | 各自独立子目录 `auto` / `channel` / `audio` |
| **P3** | CDP 端口硬编码 9224，被占用时无法规避 | 新增 `--port` 参数 |
| **P3** | 未指定作者时批量汇总标题为空 | 从已校验的作品元信息自动回填昵称 |

## 速度实测（v1.0.1）

```
--text-only（只要平台章节要点）      ~15 s
完整分析（147 秒视频 / 12 帧 OCR）   ~44 s
视频下载 5.9 MB                      0.6 s（10.7 MB/s）
批量模式（douyin_channel）           每条约 5 s，OCR 引擎只加载一次
```

主要耗时在浏览器启动与等页面签名取流，不是下载也不是 OCR。

## 回归验证

- 5 个脚本语法编译 + `--help` 全通过
- 单条视频端到端跑通（真实链接，产出 transcript.md / .srt / report.json）
- 批量链路跑通（作者逐条校验、每条 OCR 出段、INDEX.md 汇总正确）
- `check_deps()` / `check_playwright()` 返回空
- 新增 API（`get_ocr` / `analyze_video` / `write_outputs` / `profile_dir` / `LOCAL_OPENER`）冒烟通过
- CLI 与 README / SKILL.md 逐条核对一致

## 文档同步

README、SKILL.md、`docs/技术原理.md`（新增「工程细节：可移植性与性能」一节）、
`docs/常见问题.md`（新增浏览器缺失 / 端口冲突 / 依赖预检 / 批量慢 四个问答）
