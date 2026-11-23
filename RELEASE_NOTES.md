# v1.1.1

一键部署 + 自动化质检版。

## 新增

### 一键安装器 `install.py` / `install.sh` / `install.cmd`
装依赖 → 装进 skill 目录 → 自检 → 打印下一步。
`--link` 依次尝试 符号链接 / Windows 目录联结 / 复制；自动检测 WorkBuddy / Claude Code /
Cursor / Copilot CLI / Codex CLI 的 skill 目录。

### 环境自检 `scripts/doctor.py`
一条命令确认本机能不能跑：Python 版本、依赖包、浏览器、ffmpeg/ffprobe、playwright 内核、
脚本语法、输出目录可写。`--json` 机器可读。

### 静态质检 `scripts/selftest.py`
不开浏览器不联网，秒级完成：语法编译、依赖清单与实际 import 双向差集、
README/SKILL.md 的 CLI 一致性、硬编码路径与密钥扫描、跨平台与编码（AST 精确判定）。
**P0+P1 归零才退出 0。**

### 文档
README 新增「30 秒部署」章节（含可直接转发给 AI Agent 的安装指令块）；
SKILL.md 新增「首次使用：先自检」与相对路径陷阱提醒。

## 验收
全新 clone → selftest P0+P1=0 → doctor 全绿 → 真实链接分析跑通（23 秒）。

## GitHub Actions
`docs/github-actions-ci.yml` 是工作流模板，复制到 `.github/workflows/ci.yml` 即可启用
（写入 `.github/workflows/` 需要令牌带 `workflow` scope；没有也不影响使用）。
