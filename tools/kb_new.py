# -*- coding: utf-8 -*-
"""
脚手架：一条命令起一个新的知识库 skill。

做的事：
  1. 把 kbkit/（通用工具包）与 scripts/ tools/ 的 CLI 封装复制进目标目录
     —— 采用 **vendoring**，让每个知识库自包含，不跨仓库 import
  2. 渲染 knowledge.json / SKILL.md / README.md / .gitignore / LICENSE
  3. 可选 git init + 首次提交

只在本仓库里运行（不随新知识库复制）。

CLI：
    python tools/kb_new.py <目标目录> --name <技能名> --title <中文标题> \
        --source <文档站首页URL> \
        --blocks "/start/:01_start:快速开始:start,/api/:02_api:接口手册:api"

    # 只看会写什么，不落盘
    python tools/kb_new.py <目标目录> --name x --title y --source z --blocks ... --dry-run
"""
import argparse
import os
import re
import shutil
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MB_ROOT = os.path.dirname(HERE)                 # manual-baking 根
TEMPLATES = os.path.join(MB_ROOT, "templates")


def parse_blocks(spec):
    """
    "前缀:目录:中文名:别名,..." → [{prefix,dir,name,alias}]
    中文名可省略（默认用目录名），别名可省略（默认用目录名）。
    """
    out = []
    for item in filter(None, (s.strip() for s in spec.split(","))):
        parts = item.split(":")
        if len(parts) < 2:
            raise SystemExit(f"--blocks 每项至少 前缀:目录，收到 {item!r}")
        prefix, d = parts[0], parts[1]
        name = parts[2] if len(parts) > 2 and parts[2] else d
        alias = parts[3] if len(parts) > 3 and parts[3] else d
        out.append({"dir": d, "name": name, "alias": alias, "prefix": prefix})
    return out


def replace_blocks_array(text, blocks):
    """把 knowledge.json 模板里的 "blocks": [ ... ] 换成给定的块定义。"""
    i = text.find('"blocks"')
    if i < 0:
        return text
    j = text.index("[", i)
    depth, k = 0, j
    while k < len(text):
        if text[k] == "[":
            depth += 1
        elif text[k] == "]":
            depth -= 1
            if depth == 0:
                break
        k += 1
    body = ",\n".join(
        '    { "dir": "%s", "name": "%s", "alias": "%s", "prefix": "%s" }'
        % (b["dir"], b["name"], b["alias"], b["prefix"]) for b in blocks)
    return text[:j] + "[\n" + body + "\n  ]" + text[k + 1:]


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kb_new.py")
    ap.add_argument("target", help="目标目录（不存在则创建）")
    ap.add_argument("--name", required=True, help="技能名，如 fxiaoke-dev")
    ap.add_argument("--title", required=True, help="中文标题")
    ap.add_argument("--source", required=True, help="文档站首页 URL")
    ap.add_argument("--blocks", required=True, help="块定义，见 --help 顶部说明")
    ap.add_argument("--record-granularity", default="page", choices=["page", "heading"])
    ap.add_argument("--site-type", default="vuepress-ssg")
    ap.add_argument("--git", action="store_true", help="顺便 git init + 首次提交")
    ap.add_argument("--force", action="store_true", help="目标目录非空也继续")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    target = os.path.abspath(args.target)
    blocks = parse_blocks(args.blocks)
    today = time.strftime("%Y-%m-%d")

    if os.path.exists(target) and os.listdir(target) and not args.force:
        raise SystemExit(f"目标目录非空：{target}\n加 --force 继续（会覆盖同名文件）")

    plan = []
    # 1) 工具包与 CLI
    plan.append((os.path.join(MB_ROOT, "kbkit"), os.path.join(target, "kbkit")))
    for sub, keep in (("scripts", ("search.py", "build_index.py")),
                      ("tools", ("fetch_manual.py", "selftest.py", "siteprobe.py"))):
        for fn in keep:
            plan.append((os.path.join(MB_ROOT, sub, fn), os.path.join(target, sub, fn)))

    print(f"目标：{target}")
    print(f"技能名：{args.name}    标题：{args.title}")
    print(f"块：{', '.join(b['dir'] + '(' + b['alias'] + ')' for b in blocks)}")
    print()
    for src, dst in plan:
        print(f"  复制 {os.path.relpath(src, MB_ROOT)}  ->  {os.path.relpath(dst, target)}")
    for fn in ("knowledge.json", "SKILL.md", "README.md", ".gitignore", "LICENSE"):
        print(f"  生成 {fn}")

    if args.dry_run:
        print("\n[DRY-RUN] 未落盘。")
        return 0

    os.makedirs(target, exist_ok=True)
    for src, dst in plan:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if os.path.isdir(src):
            if os.path.exists(dst):
                shutil.rmtree(dst)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        else:
            shutil.copy2(src, dst)

    # 2) knowledge.json
    kj = open(os.path.join(TEMPLATES, "knowledge.json"), encoding="utf-8").read()
    kj = kj.replace('"my-knowledge-base"', f'"{args.name}"')
    kj = kj.replace('"某某文档知识库"', f'"{args.title}"')
    kj = kj.replace('"https://docs.example.com/"', f'"{args.source}"')
    kj = kj.replace('"siteType": "vuepress-ssg"', f'"siteType": "{args.site_type}"')
    kj = kj.replace('"recordGranularity": "page"', f'"recordGranularity": "{args.record_granularity}"')
    kj = replace_blocks_array(kj, blocks)
    write(os.path.join(target, "knowledge.json"), kj)

    # 3) SKILL.md / README.md
    block_rows = "\n".join(
        f"| {b['name']} 相关 | `--scope {b['alias']}` → `references/{b['dir']}/` |"
        for b in blocks)
    scope_help = " / ".join(b["alias"] for b in blocks)
    blocks_arg = ",".join(f"{b['prefix']}:{b['dir']}:{b['name']}:{b['alias']}" for b in blocks)
    ctx = dict(name=args.name, title=args.title, source=args.source, date=today,
               block_rows=block_rows, scope_help=scope_help, first_alias=blocks[0]["alias"],
               blocks_arg=blocks_arg, total="（抓取后由 build_index.py 生成）")
    for tmpl, out in (("SKILL.md.tmpl", "SKILL.md"), ("README.md.tmpl", "README.md")):
        text = open(os.path.join(TEMPLATES, tmpl), encoding="utf-8").read()
        write(os.path.join(target, out), text.format(**ctx))

    # 4) .gitignore / LICENSE
    write(os.path.join(target, ".gitignore"),
          open(os.path.join(TEMPLATES, "gitignore.tmpl"), encoding="utf-8").read())
    shutil.copy2(os.path.join(MB_ROOT, "LICENSE"), os.path.join(target, "LICENSE"))
    write(os.path.join(target, ".gitattributes"), "* text=auto eol=lf\n")
    for d in ("references", "_source", "examples"):
        os.makedirs(os.path.join(target, d), exist_ok=True)

    print(f"\n已生成 {target}")
    print("\n下一步：")
    print(f"  1) 判站点类型   python tools/siteprobe.py detect {args.source}")
    print(f"  2) 提路由清单   python tools/siteprobe.py routes {args.source} "
          f"--out _source/_toc.json --blocks \"{blocks_arg}\"")
    print(f"  3) 抓取         python tools/fetch_manual.py")
    print(f"  4) 建索引       python scripts/build_index.py")
    print(f"  5) 自检         python tools/selftest.py --online 30")
    print(f"  6) 按需改 knowledge.json 里的 html.* 选择器（先只改 contentMarker）")

    if args.git:
        subprocess.run(["git", "init", "-q"], cwd=target, check=True)
        subprocess.run(["git", "symbolic-ref", "HEAD", "refs/heads/main"], cwd=target, check=True)
        subprocess.run(["git", "add", "-A"], cwd=target, check=True)
        subprocess.run(["git", "-c", "core.autocrlf=false", "commit", "-q",
                        "-m", f"init: {args.name} —— {args.title}\n\n"
                              f"由 manual-baking 脚手架生成（kbkit 通用工具包 + 站点配置）。"],
                       cwd=target, check=True)
        print("\n已 git init 并提交（分支 main）")
    return 0


def write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


if __name__ == "__main__":
    sys.exit(main())
