#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 C：把加强版地图 LOC 里的 SoundNodeWave 去掉 Subtitles/LocalizedSubtitles，
变成接近原版的「空壳」，迫使运行时回退到 VO + Localization\\INT\\*.int。

  # 仅验收句（推荐第一步）
  python _tools/phaseC_hollow_loc.py --apply --only DLC3_NAR_Echo_Scooter_3

  # 整个 HUB Dynamic LOC 里所有带字幕的 Wave
  python _tools/phaseC_hollow_loc.py --apply --upk ".../dlc3_HUB_Dynamic_LOC_INT.upk"

  # 配合：把 DLC3 VO 还原成英文原包，才能证明中文来自 .int 而非 VO 内嵌
  python _tools/phaseC_hollow_loc.py --restore-vo-english
"""
from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ue3_props import (
    ExportSerial,
    names_from_pkg,
    parse_soundnode_serial,
    recover_hub_subtitle_tags,
    write_soundnode_serial,
)
from ue3_upk import load_package, replace_export_serial, validate_package

ROOT = Path(__file__).resolve().parents[1]
BACKUP = ROOT / "_knoxx_echo_backup" / "phaseC_hollow"
DEFAULT_HUB = Path(
    r"C:\downloadapps\sssteam\steamapps\common\BorderlandsGOTYEnhanced"
    r"\WillowGame\CookedPC\DLC3\Maps\dlc3_HUB_Dynamic_LOC_INT.upk"
)
DEFAULT_VO = Path(
    r"C:\downloadapps\sssteam\steamapps\common\BorderlandsGOTYEnhanced"
    r"\WillowGame\CookedPC\DLC3\Packages\Audio\VO\DLC3_VO_Narrative_WAV.upk"
)
VO_ENGLISH_BAK = ROOT / "_knoxx_echo_backup" / "wav_batch_backup" / "DLC3_VO_Narrative_WAV.upk.bak"
DROP_NAMES = frozenset({"Subtitles", "LocalizedSubtitles"})


def export_leaf(pkg, e) -> str:
    base = pkg.name_str(e.name_index)
    return f"{base}_{e.name_number - 1}" if e.name_number > 0 else base


def hollow_serial(blob: bytes, names: list[str]) -> tuple[bytes | None, dict]:
    info: dict = {}
    try:
        serial = parse_soundnode_serial(blob, names)
    except Exception as ex:
        info["error"] = f"parse:{ex}"
        return None, info
    # HUB / PackageDefinition often mis-tag subtitle arrays as SourceFilePath
    info["recovered"] = recover_hub_subtitle_tags(serial.props, names)
    before = [p.name for p in serial.props if not p.is_none]
    keep = [p for p in serial.props if p.name not in DROP_NAMES]
    dropped = [n for n in before if n in DROP_NAMES]
    if not dropped:
        info["error"] = "no_subtitle_props"
        return None, info
    # Wipe leftover fat Bulk headers (size>0 pointing at VO/OOB audio). Keeping
    # those stubs after dropping Subtitles can cut Echo audio mid-line while the
    # portrait/Duration keeps running.
    tail = serial.tail
    if len(tail) >= 16:
        import struct as _struct

        clean = bytearray(tail)
        max_bytes = min(len(clean), 7 * 16)
        for i in range(0, max_bytes, 16):
            flags, count, size, ofile = _struct.unpack_from("<4i", clean, i)
            if size > 0 or count > 0:
                # preserve ofile; zero payload claim (replace_export_serial will
                # still adjust OffsetInFile for package geometry)
                _struct.pack_into("<4i", clean, i, 0, 0, 0, ofile)
        tail = bytes(clean)
    hollow = ExportSerial(
        net_index=serial.net_index,
        props=keep,
        props_end=0,
        tail=tail,
        serial_size=0,
    )
    out = write_soundnode_serial(hollow)
    info.update(
        {
            "dropped": dropped,
            "old_size": len(blob),
            "new_size": len(out),
            "delta": len(out) - len(blob),
            "props_after": [p.name for p in keep if not p.is_none],
        }
    )
    return out, info


def hollow_package(
    upk_path: Path,
    *,
    apply: bool,
    backup_dir: Path,
    only: set[str] | None,
    min_serial: int = 200,
    max_tail: int | None = None,
    leaf_pred=None,
) -> dict:
    """
    max_tail: if set, skip exports whose leftover after props is larger
      (LOC 空壳字幕尾约 80–112；带内嵌 PCM 的战斗语音尾部很大——全量空壳曾导致无声).
    leaf_pred: optional callable(leaf)->bool; False => skip.
    """
    data = bytearray(upk_path.read_bytes())
    pkg = load_package(data)
    names = names_from_pkg(pkg)

    jobs: list[tuple[int, bytes, str, dict]] = []
    skipped = {"no_subs": 0, "filtered": 0, "fail": 0, "tail_too_big": 0, "leaf_pred": 0}
    for e in pkg.exports:
        if e.serial_size < min_serial:
            continue
        leaf = export_leaf(pkg, e)
        if only is not None and leaf not in only and leaf.lower() not in {x.lower() for x in only}:
            skipped["filtered"] += 1
            continue
        if leaf_pred is not None and not leaf_pred(leaf):
            skipped["leaf_pred"] += 1
            continue
        blob = bytes(pkg.data[e.serial_offset : e.serial_offset + e.serial_size])
        try:
            probe = parse_soundnode_serial(blob, names)
        except Exception:
            skipped["fail"] += 1
            continue
        if max_tail is not None and len(probe.tail) > max_tail:
            skipped["tail_too_big"] += 1
            continue
        new_blob, info = hollow_serial(blob, names)
        if new_blob is None:
            if info.get("error") == "no_subtitle_props":
                skipped["no_subs"] += 1
            else:
                skipped["fail"] += 1
            continue
        if new_blob == blob:
            continue
        jobs.append((e.index, new_blob, leaf, info))

    jobs.sort(key=lambda j: pkg.exports[j[0]].serial_offset, reverse=True)
    total_delta = 0
    for idx, new_blob, leaf, info in jobs:
        total_delta += replace_export_serial(pkg, idx, new_blob)

    errs = validate_package(pkg)
    result = {
        "file": str(upk_path),
        "patched": len(jobs),
        "total_delta": total_delta,
        "skipped": skipped,
        "validate": errs or ["OK"],
        "samples": [
            {"leaf": leaf, "old": info["old_size"], "new": info["new_size"]}
            for _, _, leaf, info in jobs[:12]
        ],
        "backup": None,
        "written": False,
    }

    if apply:
        if errs:
            raise RuntimeError(f"validate failed: {errs}")
        backup_dir.mkdir(parents=True, exist_ok=True)
        bak = backup_dir / (upk_path.name + ".bak")
        if not bak.exists():
            shutil.copy2(upk_path, bak)
            result["backup"] = str(bak)
        else:
            result["backup"] = f"exists:{bak}"
        upk_path.write_bytes(pkg.data)
        result["written"] = True
        result["file_size"] = upk_path.stat().st_size
    return result


def restore_vo_english() -> None:
    if not VO_ENGLISH_BAK.is_file():
        raise SystemExit(f"缺少英文 VO 备份: {VO_ENGLISH_BAK}")
    BACKUP.mkdir(parents=True, exist_ok=True)
    cur_bak = BACKUP / "DLC3_VO_Narrative_WAV.upk.before_english_restore"
    if DEFAULT_VO.is_file() and not cur_bak.exists():
        shutil.copy2(DEFAULT_VO, cur_bak)
    shutil.copy2(VO_ENGLISH_BAK, DEFAULT_VO)
    print(f"已还原英文 VO: {DEFAULT_VO}")
    print(f"  来源: {VO_ENGLISH_BAK}")
    print(f"  还原前备份: {cur_bak}")


def main() -> int:
    ap = argparse.ArgumentParser(description="阶段 C：LOC SoundNodeWave 空壳化")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--upk", type=Path, default=DEFAULT_HUB)
    ap.add_argument("--only", action="append", default=[], help="只处理这些 leaf 名，可重复")
    ap.add_argument("--backup-dir", type=Path, default=BACKUP)
    ap.add_argument(
        "--restore-vo-english",
        action="store_true",
        help="把 DLC3_VO_Narrative_WAV 还原为 wav_batch 英文包（验证 .int）",
    )
    ap.add_argument(
        "--all-narrative",
        action="store_true",
        help="处理包内所有名称含 NAR_Echo / NAR_Live / NAR_ 的对象（仍限本 --upk）",
    )
    args = ap.parse_args()
    apply = bool(args.apply) and not args.dry_run

    if args.restore_vo_english:
        restore_vo_english()
        if not args.apply and not args.only and not args.all_narrative:
            # 允许只还原 VO
            if not any([args.apply, args.dry_run]):
                return 0

    only: set[str] | None = set(args.only) if args.only else None
    if args.all_narrative:
        # 先扫一遍收集
        data = args.upk.read_bytes()
        pkg = load_package(data)
        only = set()
        for e in pkg.exports:
            leaf = export_leaf(pkg, e)
            u = leaf.upper()
            if "NAR_" in u or "ECHO" in u:
                only.add(leaf)
        print(f"all-narrative candidates: {len(only)}")

    if only is None and not args.all_narrative and not args.only:
        # 默认验收句
        only = {"DLC3_NAR_Echo_Scooter_3"}
        print("默认只处理验收句 DLC3_NAR_Echo_Scooter_3（加 --all-narrative 或 --only 扩展）")

    print(f"upk={args.upk}")
    print(f"apply={apply} only={sorted(only) if only and len(only) < 20 else only}")
    r = hollow_package(
        args.upk, apply=apply, backup_dir=args.backup_dir, only=only
    )
    print(
        f"patched={r['patched']} delta={r['total_delta']} "
        f"skip={r['skipped']} validate={r['validate']}"
    )
    for s in r["samples"]:
        print(f"  {s['leaf']}: {s['old']} -> {s['new']}")
    if r.get("backup"):
        print(f"backup={r['backup']}")
    if r.get("written"):
        print(f"written size={r['file_size']}")
    return 0 if r["validate"] == ["OK"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
