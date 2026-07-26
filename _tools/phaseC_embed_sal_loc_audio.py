#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Embed real Ogg audio from VO packages into hollow LOC SAL_* SoundNodeWaves.

SAL proximity lines (e.g. Tartarus Tannis) do not fall back to VO when LOC has
Duration + empty Bulk — mouth moves, silence. Copy VO Ogg into the LOC export
(keeping subtitle props removed so .int Chinese still applies).
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from ue3_props import (
    ExportSerial,
    names_from_pkg,
    parse_soundnode_serial,
    recover_hub_subtitle_tags,
    write_props,
    write_soundnode_serial,
)
from ue3_upk import load_package, replace_export_serial, validate_package

DROP_NAMES = frozenset({"Subtitles", "LocalizedSubtitles"})
TRAILING_HDRS = 5
HDR = 16


def is_sal_leaf(leaf: str) -> bool:
    u = leaf.upper()
    return u.startswith("SAL_") or "_SAL_" in u


def should_embed_sal_leaf(leaf: str) -> bool:
    """Only proximity Live lines — not shop/radio Record lines (HUB bloat/crash)."""
    if not is_sal_leaf(leaf):
        return False
    u = leaf.upper()
    if "RECORD" in u:
        return False
    return "LIVE" in u


def export_leaf(pkg, e) -> str:
    base = pkg.name_str(e.name_index)
    return f"{base}_{e.name_number - 1}" if e.name_number > 0 else base


def extract_ogg_from_export(data: bytes, e, serial) -> bytes | None:
    t = serial.tail
    for i in range(0, min(len(t), 7 * HDR), HDR):
        _f, count, size, ofile = struct.unpack_from("<4i", t, i)
        payload = max(size, count)
        if payload <= 0 or ofile < 0 or ofile + payload > len(data):
            continue
        if data[ofile : ofile + 4] == b"OggS":
            return bytes(data[ofile : ofile + payload])
    rel = t.find(b"OggS")
    if rel >= 0 and rel + 4 < len(t):
        # best-effort: use size from preceding bulk header if present
        if rel >= HDR:
            _f, count, size, _o = struct.unpack_from("<4i", t, rel - HDR)
            payload = max(size, count)
            if payload > 0 and rel + payload <= len(t):
                return bytes(t[rel : rel + payload])
        # else take until end (minus trailing empty hdrs if obvious)
        return bytes(t[rel:])
    return None


def build_vo_ogg_index(cooked: Path) -> dict[str, bytes]:
    """leaf name -> ogg bytes from DLC SAL WAV + mainline VO_Missions_*_WAV."""
    index: dict[str, bytes] = {}
    candidates: list[Path] = []
    for dlc in ("DLC1", "DLC3", "DLC4"):
        root = cooked / dlc / "Packages"
        if root.is_dir():
            candidates.extend(root.rglob("*SAL*WAV*.upk"))
            candidates.extend(root.rglob("*SAL*Wav*.upk"))
            candidates.extend(root.rglob("*SAL*wav*.upk"))
    main_vo = cooked / "Packages" / "Audio" / "VO"
    if main_vo.is_dir():
        candidates.extend(main_vo.glob("VO_Missions_*_WAV.upk"))
        candidates.extend(main_vo.glob("VO_Missions_*_wav.upk"))

    seen: set[Path] = set()
    for upk in candidates:
        rp = upk.resolve()
        if rp in seen:
            continue
        name_l = upk.name.lower()
        if any(x in name_l for x in ("_deu", "_esn", "_fra", "_ita", "_jpn")):
            continue
        seen.add(rp)
        try:
            data = upk.read_bytes()
            pkg = load_package(data)
            names = names_from_pkg(pkg)
        except Exception:
            continue
        for e in pkg.exports:
            if e.serial_size < 200:
                continue
            leaf = export_leaf(pkg, e)
            if not should_embed_sal_leaf(leaf):
                continue
            try:
                serial = parse_soundnode_serial(
                    bytes(data[e.serial_offset : e.serial_offset + e.serial_size]),
                    names,
                )
            except Exception:
                continue
            ogg = extract_ogg_from_export(data, e, serial)
            if ogg and leaf not in index:
                index[leaf] = ogg
    return index


def loc_already_has_ogg(data: bytes, e, serial) -> bool:
    t = serial.tail
    for i in range(0, min(len(t), 7 * HDR), HDR):
        _f, count, size, ofile = struct.unpack_from("<4i", t, i)
        payload = max(size, count)
        if payload > 0 and 0 <= ofile and ofile + payload <= len(data):
            if data[ofile : ofile + 4] == b"OggS":
                return True
    return t.find(b"OggS") >= 0


def build_embedded_serial(
    serial: ExportSerial, names: list[str], ogg: bytes, serial_off: int
) -> bytes:
    recover_hub_subtitle_tags(serial.props, names)
    props = [p for p in serial.props if p.name not in DROP_NAMES]
    props_end = 4 + len(write_props(props))
    hdr1_off = serial_off + props_end + HDR
    ogg_off = serial_off + props_end + 2 * HDR
    sz = len(ogg)
    tail = bytearray()
    tail += struct.pack("<4i", 0, 0, 0, hdr1_off)
    tail += struct.pack("<4i", 0, sz, sz, ogg_off)
    tail += ogg
    base = ogg_off + sz
    for i in range(TRAILING_HDRS):
        tail += struct.pack("<4i", 0, 0, 0, base + (i + 1) * HDR)
    return write_soundnode_serial(
        ExportSerial(
            net_index=serial.net_index,
            props=props,
            props_end=0,
            tail=bytes(tail),
            serial_size=0,
        )
    )


def fix_inline_ogg_ofiles(pkg, export_index: int, names: list[str]) -> bool:
    """Point Bulk OffsetInFile at inline OggS after replace_export_serial shifts."""
    e = pkg.exports[export_index]
    blob = bytes(pkg.data[e.serial_offset : e.serial_offset + e.serial_size])
    try:
        serial = parse_soundnode_serial(blob, names)
    except Exception:
        return False
    rel = serial.tail.find(b"OggS")
    if rel < 0:
        return False
    ogg_abs = e.serial_offset + serial.props_end + rel
    # hdr immediately before OggS
    if rel < HDR:
        return False
    hdr1_rel = rel - HDR
    hdr0_rel = hdr1_rel - HDR if hdr1_rel >= HDR else None
    base = e.serial_offset + serial.props_end
    # size from existing header or until trailing empty chain
    _f, count, size, _old = struct.unpack_from("<4i", serial.tail, hdr1_rel)
    payload = max(size, count)
    if payload <= 0:
        payload = len(serial.tail) - rel - TRAILING_HDRS * HDR
        if payload < 4:
            return False
    struct.pack_into("<4i", pkg.data, base + hdr1_rel, 0, payload, payload, ogg_abs)
    if hdr0_rel is not None and hdr0_rel >= 0:
        hdr1_abs = base + hdr1_rel
        struct.pack_into("<4i", pkg.data, base + hdr0_rel, 0, 0, 0, hdr1_abs)
    return True


def _ofile_points_at_ogg(data: bytes, serial) -> bool:
    for i in range(0, min(len(serial.tail), 7 * HDR), HDR):
        _f, count, size, ofile = struct.unpack_from("<4i", serial.tail, i)
        payload = max(size, count)
        if (
            payload > 0
            and 0 <= ofile < len(data)
            and data[ofile : ofile + 4] == b"OggS"
        ):
            return True
    return False


def embed_sal_in_loc(upk: Path, ogg_index: dict[str, bytes]) -> dict:
    data = bytearray(upk.read_bytes())
    pkg = load_package(data)
    names = names_from_pkg(pkg)
    # (export_index, new_blob|None, leaf) — None blob => repair ofiles only
    jobs: list[tuple[int, bytes | None, str]] = []
    skipped = {"not_sal": 0, "no_vo": 0, "has_ogg": 0, "fail": 0}
    for e in pkg.exports:
        if e.serial_size < 200:
            continue
        leaf = export_leaf(pkg, e)
        if not should_embed_sal_leaf(leaf):
            skipped["not_sal"] += 1
            continue
        blob = bytes(pkg.data[e.serial_offset : e.serial_offset + e.serial_size])
        try:
            serial = parse_soundnode_serial(blob, names)
        except Exception:
            skipped["fail"] += 1
            continue
        has_inline = serial.tail.find(b"OggS") >= 0
        if has_inline:
            if _ofile_points_at_ogg(pkg.data, serial):
                skipped["has_ogg"] += 1
            else:
                jobs.append((e.index, None, leaf))
            continue
        ogg = ogg_index.get(leaf)
        if not ogg:
            skipped["no_vo"] += 1
            continue
        new_blob = build_embedded_serial(serial, names, ogg, e.serial_offset)
        if new_blob != blob:
            jobs.append((e.index, new_blob, leaf))

    jobs.sort(key=lambda j: pkg.exports[j[0]].serial_offset, reverse=True)
    for idx, new_blob, _leaf in jobs:
        if new_blob is not None:
            replace_export_serial(pkg, idx, new_blob)
        fix_inline_ogg_ofiles(pkg, idx, names)
    # Final geometry pass — grows shift later exports' ofiles.
    for idx, _new_blob, _leaf in jobs:
        fix_inline_ogg_ofiles(pkg, idx, names)
    errs = validate_package(pkg)
    if errs:
        raise RuntimeError(f"{upk.name}: {errs}")
    if jobs:
        upk.write_bytes(pkg.data)
    return {
        "patched": len(jobs),
        "skipped": skipped,
        "samples": [x[2] for x in jobs[:8]],
    }


def embed_all_loc(cooked: Path, loc_list: list[Path], log=print) -> int:
    """Embed SAL Live audio into LOC. Skips DLC3 HUB maps (Record bloat caused crashes)."""
    log("建立 SAL VO 音频索引（仅 Live 靠近台词）…")
    index = build_vo_ogg_index(cooked)
    log(f"  VO SAL Live 音频条数: {len(index)}")
    total = 0
    for upk in loc_list:
        name_u = upk.name.upper()
        # Floating Interchange / HUB: embedding fat SAL crashed map load.
        if "HUB" in name_u or "DLC3_HUB" in name_u:
            log(f"  跳过 HUB（防闪退）: {upk.relative_to(cooked)}")
            continue
        r = embed_sal_in_loc(upk, index)
        if r["patched"]:
            log(
                f"  {upk.relative_to(cooked)}: embedded={r['patched']} "
                f"samples={r['samples']}"
            )
            total += r["patched"]
    log(f"SAL 音频嵌入合计: {total}")
    return total


if __name__ == "__main__":
    cooked = Path(
        r"C:\downloadapps\sssteam\steamapps\common\BorderlandsGOTYEnhanced"
        r"\WillowGame\CookedPC"
    )
    from phaseC_pipeline import discover_loc_upks

    embed_all_loc(cooked, discover_loc_upks(cooked))
