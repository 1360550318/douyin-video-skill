#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""一键安装：装依赖 -> 装进 skill 目录 -> 自检。

给「拿到 GitHub 链接想让朋友的 AI Agent 直接能用」的场景。

用法：
    python install.py                # 装依赖 + 自检
    python install.py --link         # 再把仓库装进检测到的 skills 目录（软链/联结/复制）
    python install.py --check        # 只自检，不装任何东西
    python install.py --no-playwright  # 跳过 playwright（只有采集博主全部作品才需要）

原理：仓库根目录本身就是合法的 skill 目录（SKILL.md 在根），所以
    git clone <repo> <skills_dir>/douyin-video
即可被 WorkBuddy / Claude Code / Cursor 等识别。
"""
import argparse
import os
import shutil
import subprocess
import sys

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = os.path.dirname(os.path.abspath(__file__))
NAME = "douyin-video"

# 各家 AI Agent 的 skill 目录，按优先级排列
SKILL_DIRS = [
    (".workbuddy/skills", "WorkBuddy"),
    (".claude/skills", "Claude Code"),
    (".config/github-copilot/skills", "GitHub Copilot CLI"),
    (".cursor/skills", "Cursor"),
    (".codex/skills", "Codex CLI"),
]


def run(cmd, **kw):
    print("  $ " + " ".join(cmd[:6]) + (" ..." if len(cmd) > 6 else ""))
    return subprocess.run(cmd, **kw)


def step(title):
    print("\n=== %s ===" % title)


def install_deps(with_playwright=True):
    step("1/3 安装 Python 依赖")
    req = os.path.join(ROOT, "requirements.txt")
    r = run([sys.executable, "-m", "pip", "install", "-r", req])
    if r.returncode != 0:
        print("  [WARN] pip 安装有非零返回，请手动检查上面的输出")
    if with_playwright:
        print("  -> 安装 playwright 内核（仅 douyin_crawler.py 需要，可跳过）")
        run([sys.executable, "-m", "playwright", "install", "chromium"])


def detect_skill_dirs():
    home = os.path.expanduser("~")
    found = []
    for rel, who in SKILL_DIRS:
        p = os.path.join(home, rel)
        if os.path.isdir(p):
            found.append((p, who))
    return found


def link_into_skill_dir():
    step("2/3 安装到 skill 目录")
    cands = detect_skill_dirs()
    if not cands:
        print("  没检测到已知的 skill 目录。手动装法：")
        print("    git clone <本仓库> <你的 skill 目录>/%s" % NAME)
        return False
    base, who = cands[0]
    target = os.path.join(base, NAME)
    print("  目标：%s （%s）" % (target, who))

    if os.path.exists(target):
        if os.path.islink(target) or os.path.realpath(target) == os.path.realpath(ROOT):
            print("  已指向本仓库，跳过。")
            return True
        backup = target + ".bak"
        i = 1
        while os.path.exists(backup):
            backup = "%s.bak%d" % (target, i)
            i += 1
        print("  目标已存在，重命名为 %s" % os.path.basename(backup))
        os.rename(target, backup)

    # 依次尝试：符号链接 -> Windows 目录联结 -> 复制
    try:
        os.symlink(ROOT, target, target_is_directory=True)
        print("  已创建符号链接。")
        return True
    except (OSError, NotImplementedError):
        pass
    if os.name == "nt":
        r = subprocess.run(["cmd", "/c", "mklink", "/J", target, ROOT],
                           capture_output=True, text=True)
        if r.returncode == 0:
            print("  已创建目录联结（junction）。")
            return True
    try:
        shutil.copytree(ROOT, target,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
        print("  已复制到目标目录（注意：之后需要手动同步更新）。")
        return True
    except Exception as ex:
        print("  安装失败：%s" % ex)
        return False


def doctor():
    step("3/3 环境自检")
    r = subprocess.run([sys.executable, os.path.join(ROOT, "scripts", "doctor.py")])
    return r.returncode == 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--link", action="store_true", help="装进检测到的 skill 目录")
    ap.add_argument("--check", action="store_true", help="只自检")
    ap.add_argument("--no-playwright", action="store_true", help="跳过 playwright 内核")
    args = ap.parse_args()

    print("douyin-video-skill 安装器")
    print("仓库：%s" % ROOT)

    if not args.check:
        install_deps(with_playwright=not args.no_playwright)
        if args.link:
            link_into_skill_dir()
    ok = doctor()

    print("\n下一步：")
    print("  1) 让 AI Agent 读 SKILL.md，它就知道怎么用了")
    print("  2) 直接给它一条抖音分享链接，例如：")
    print('     python scripts/douyin_auto.py --url "https://v.douyin.com/xxxx/"')
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
