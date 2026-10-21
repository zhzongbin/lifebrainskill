#!/usr/bin/env python3
"""打包可分发的 lifebrain-skill.zip（不依赖 zip 命令，任何装了 python3 的机器都能跑）。

用法：
    python3 make_zip.py                # 生成 dist/lifebrain-skill.zip
    python3 make_zip.py -o /tmp/x.zip

包里是 `lifebrain/` 这一层（SKILL.md 在顶层），别人解压后直接就是可安装的 skill。
"""
from __future__ import annotations

import argparse
import pathlib
import zipfile

ROOT = pathlib.Path(__file__).resolve().parent
SKILL_DIR = ROOT / "lifebrain"
EXCLUDE_PARTS = {"__pycache__", ".git", ".pytest_cache", ".DS_Store"}
EXCLUDE_SUFFIX = {".pyc", ".pyo"}


def main() -> int:
    ap = argparse.ArgumentParser(description="打包 lifebrain skill 为 ZIP")
    ap.add_argument("-o", "--output", default=str(ROOT / "dist" / "lifebrain-skill.zip"))
    args = ap.parse_args()

    out = pathlib.Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)

    files = [
        p for p in sorted(SKILL_DIR.rglob("*"))
        if p.is_file()
        and not (set(p.parts) & EXCLUDE_PARTS)
        and p.suffix not in EXCLUDE_SUFFIX
    ]
    if not files:
        print(f"ERROR: {SKILL_DIR} 下没有可打包的文件", )
        return 1

    with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in files:
            zf.write(p, p.relative_to(ROOT))

    size = out.stat().st_size
    print(f"→ {out} ({len(files)} 个文件, {size} 字节)")
    for p in files:
        print(f"  {p.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
