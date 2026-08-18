#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 echo_cn_fix 源码打成可直接丢进 sdk_mods 的 .sdkmod。"""
from __future__ import annotations

from io import BytesIO
from pathlib import Path
from zipfile import ZIP_DEFLATED, ZipFile

ROOT = Path(__file__).resolve().parent
MOD_NAME = "echo_cn_fix"
SRC = ROOT / MOD_NAME
OUT = ROOT / f"{MOD_NAME}.sdkmod"
KEEP = {".py", ".toml", ".txt", ".md"}


def main() -> int:
    if not (SRC / "__init__.py").is_file():
        raise SystemExit(f"缺少 {SRC / '__init__.py'}")
    files = [
        p
        for p in SRC.rglob("*")
        if p.is_file()
        and p.suffix.lower() in KEEP
        and "__pycache__" not in p.parts
    ]
    buffer = BytesIO()
    with ZipFile(buffer, "w", ZIP_DEFLATED, compresslevel=9) as zf:
        for path in files:
            zf.write(path, Path(MOD_NAME) / path.relative_to(SRC))
    OUT.write_bytes(buffer.getvalue())
    print(f"wrote {OUT} ({OUT.stat().st_size} bytes, {len(files)} files)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
