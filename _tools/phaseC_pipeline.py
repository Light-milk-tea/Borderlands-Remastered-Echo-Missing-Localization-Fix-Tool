#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
阶段 C 一键流水线（可配置游戏路径）。

步骤：
  1) 备份将改动的 UPK / .int
  2) LOC 空壳化（Echo/Live/Com，短尾，排除战斗语音）
  3) 清洗 LOC Bulk 脏尾
  4) VO 叙事对象去掉 LocalizedSubtitles（缺 Subtitles 时先从槽 0 提升）
  5) 可选：加强天邈 .int 键名

  python _tools/phaseC_pipeline.py --game "C:\\...\\BorderlandsGOTYEnhanced"
"""
from __future__ import annotations

import argparse
import re
import shutil
import struct
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

sys.path.insert(0, str(Path(__file__).resolve().parent))
from phaseC_hollow_loc import hollow_package
from ue3_props import (
    ExportSerial,
    Prop,
    names_from_pkg,
    parse_soundnode_serial,
    recover_hub_subtitle_tags,
    write_soundnode_serial,
)
from ue3_upk import load_package, replace_export_serial, validate_package


def _app_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[1]


ROOT = _app_root()
LogFn = Callable[[str], None]

DLC_IDS = ("DLC1", "DLC3", "DLC4")
FOREIGN = ("_deu.upk", "_esn.upk", "_fra.upk", "_ita.upk", "_jpn.upk")
MAX_TAIL = 128
N_HDR = 7
HDR = 16

VO_INT_PAIRS = [
    ("DLC1/Packages/Audio/Voice/DLC1_VO_Narrative_WAV.upk", "dlc1_vo_narrative_wav.int"),
    ("DLC1/Packages/Audio/Voice/DLC1_VO_Logs_WAV.upk", "dlc1_vo_logs_wav.int"),
    ("DLC1/Packages/Audio/Voice/DLC1_VO_Missions_Ned_WAV.upk", "dlc1_vo_missions_ned_wav.int"),
    ("DLC1/Packages/Audio/Voice/DLC1_VO_Missions_Baha_WAV.upk", "dlc1_vo_missions_baha_wav.int"),
    ("DLC3/Packages/Audio/VO/DLC3_VO_Narrative_WAV.upk", "dlc3_vo_narrative_wav.int"),
    ("DLC3/Packages/Audio/VO/DLC3_VO_Logs_WAV.upk", "dlc3_vo_logs_wav.int"),
    ("DLC3/Packages/Audio/VO/DLC3_VO_Athena_WAV.upk", "dlc3_vo_athena_wav.int"),
    ("DLC3/Packages/Audio/VO/DLC3_VO_Moxxi_WAV.upk", "dlc3_vo_moxxi_wav.int"),
    ("DLC4/Packages/Audio/VO/DLC4_VO_Narrative_wav.upk", "dlc4_vo_narrative_wav.int"),
]

_SECTION_RE = re.compile(
    r"^\[(?P<header>[^\]]+)\]\s*\r?\n(?P<body>.*?)(?=\r?\n\[|\Z)",
    re.S | re.M,
)


@dataclass
class GamePaths:
    game_root: Path
    willow: Path
    cooked: Path
    int_dir: Path

    @staticmethod
    def resolve(user_path: str | Path) -> "GamePaths":
        p = Path(user_path).expanduser().resolve()
        candidates = [
            p,
            p / "BorderlandsGOTYEnhanced",
            p / "WillowGame",
            p.parent if p.name.lower() == "cookedpc" else None,
        ]
        willow = None
        for c in candidates:
            if c is None:
                continue
            if (c / "CookedPC").is_dir() and (c / "Localization").is_dir():
                willow = c
                break
            if (c / "WillowGame" / "CookedPC").is_dir():
                willow = c / "WillowGame"
                break
        if willow is None:
            raise FileNotFoundError(
                "未找到 WillowGame（需含 CookedPC 与 Localization）。\n"
                "请选择：BorderlandsGOTYEnhanced 根目录，或其中的 WillowGame 目录。"
            )
        game_root = willow.parent if willow.name == "WillowGame" else willow
        return GamePaths(
            game_root=game_root,
            willow=willow,
            cooked=willow / "CookedPC",
            int_dir=willow / "Localization" / "INT",
        )


@dataclass
class PipelineResult:
    ok: bool = False
    message: str = ""
    backup_dir: Path | None = None
    loc_hollowed: int = 0
    loc_sanitized: int = 0
    vo_stripped: int = 0
    int_boosted: int = 0
    warnings: list[str] = field(default_factory=list)


def _log(log: LogFn | None, msg: str) -> None:
    if log:
        log(msg)
    else:
        print(msg)


def export_leaf(pkg, e) -> str:
    base = pkg.name_str(e.name_index)
    return f"{base}_{e.name_number - 1}" if e.name_number > 0 else base


def is_narrative_leaf(leaf: str) -> bool:
    u = leaf.upper()
    if any(x in u for x in ("_BD_", "BTLD", "LOG_VENDING", "CROWD_LIVE")):
        return False
    return any(
        x in u
        for x in (
            "NAR_ECHO",
            "LOG_ECHO",
            "NAR_DROIDECHO",
            "NAR_LIVE",
            "NAR_COM",
            "NAR_INTRO",
            "NAR_OUTRO",
        )
    )


def _count_loc_narrative_state(loc_list: list[Path]) -> tuple[int, int]:
    """Return (already_hollow, still_has_subs) for narrative leaves."""
    hollow = 0
    fat = 0
    for upk in loc_list:
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
            if not is_narrative_leaf(leaf):
                continue
            try:
                serial = parse_soundnode_serial(
                    bytes(pkg.data[e.serial_offset : e.serial_offset + e.serial_size]),
                    names,
                )
                recover_hub_subtitle_tags(serial.props, names)
            except Exception:
                continue
            props = {p.name for p in serial.props if not p.is_none}
            if props & {"Subtitles", "LocalizedSubtitles"}:
                fat += 1
            else:
                hollow += 1
    return hollow, fat


def discover_loc_upks(cooked: Path) -> list[Path]:
    out: list[Path] = []
    for dlc in DLC_IDS:
        maps = cooked / dlc / "Maps"
        if maps.is_dir():
            for p in sorted(maps.glob("*_LOC_INT.upk")):
                out.append(p)
        pkgs = cooked / dlc / "Packages"
        if pkgs.is_dir():
            for p in sorted(pkgs.rglob("*_LOC_INT.upk")):
                if p.name.lower().endswith(FOREIGN):
                    continue
                out.append(p)
    seen: set[Path] = set()
    uniq: list[Path] = []
    for p in out:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            uniq.append(p)
    return uniq


def check_game(paths: GamePaths, log: LogFn | None = None) -> list[str]:
    """Return human-readable check lines; raises on fatal."""
    lines: list[str] = []
    lines.append(f"游戏根目录: {paths.game_root}")
    lines.append(f"WillowGame: {paths.willow}")
    if not paths.cooked.is_dir():
        raise FileNotFoundError(f"缺少 CookedPC: {paths.cooked}")
    locs = discover_loc_upks(paths.cooked)
    lines.append(f"找到 DLC LOC_INT: {len(locs)} 个")
    if not locs:
        raise FileNotFoundError("未找到 DLC1/3/4 的 *_LOC_INT.upk，请确认 DLC 已安装。")
    vo_ok = 0
    for rel, _ in VO_INT_PAIRS:
        if paths.cooked.joinpath(*rel.split("/")).is_file():
            vo_ok += 1
    lines.append(f"叙事 VO 包: {vo_ok}/{len(VO_INT_PAIRS)}")
    if vo_ok == 0:
        raise FileNotFoundError("未找到任何叙事 VO WAV 包。")
    int_hits = 0
    for _, name in VO_INT_PAIRS:
        ip = paths.int_dir / name
        if ip.is_file():
            int_hits += 1
    lines.append(f"天邈相关 .int: {int_hits}/{len(VO_INT_PAIRS)}")
    if int_hits == 0:
        lines.append("警告: 未检测到天邈 VO .int。空壳后字幕仍可能是英文，请先装天邈汉化。")
    # probe chinese in one int
    sample = paths.int_dir / "dlc3_vo_narrative_wav.int"
    if sample.is_file():
        try:
            t = sample.read_bytes()
            text = t.decode("utf-16") if t[:2] == b"\xff\xfe" else t.decode("utf-16-le", "replace")
            if re.search(r"[\u4e00-\u9fff]", text):
                lines.append("抽检: dlc3_vo_narrative_wav.int 含中文 ✓")
            else:
                lines.append("警告: dlc3 .int 未见中文，请确认天邈已正确安装。")
        except Exception as ex:
            lines.append(f"警告: 读取 .int 失败: {ex}")
    for line in lines:
        _log(log, line)
    return lines


def _is_under(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _backup_file(src: Path, backup_root: Path, cooked: Path | None, int_dir: Path | None) -> Path:
    if cooked and _is_under(src, cooked):
        dest = backup_root / "CookedPC" / src.relative_to(cooked)
    elif int_dir and _is_under(src, int_dir):
        dest = backup_root / "Localization_INT" / src.name
    else:
        dest = backup_root / "other" / src.name
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        shutil.copy2(src, dest)
    return dest


def make_empty_tail(serial_off: int, props_end: int) -> bytes:
    out = bytearray(N_HDR * HDR)
    base = serial_off + props_end
    for i in range(N_HDR):
        struct.pack_into("<4i", out, i * HDR, 0, 0, 0, base + (i + 1) * HDR)
    return bytes(out)


def tail_needs_sanitize(tail: bytes) -> bool:
    if len(tail) < HDR:
        return False
    max_bytes = min(len(tail), N_HDR * HDR)
    for i in range(0, max_bytes, HDR):
        _flags, count, size, _off = struct.unpack_from("<4i", tail, i)
        if size > 0 or count > 0:
            return True
    return False


def sanitize_loc_file(upk: Path) -> int:
    """Wipe dirty Bulk headers on any 112-byte SoundNodeWave tail.

    Not limited to narrative leaves: shop/broadcast (SAL_*/LOG_Vending) stubs
    can keep size>0 ofiles after package shrink; those OOB pointers can prevent
    DLC hub maps (e.g. T-Bone Junction) from loading.
    """
    data = bytearray(upk.read_bytes())
    pkg = load_package(data)
    names = names_from_pkg(pkg)
    n = 0
    for e in pkg.exports:
        if e.serial_size < 200:
            continue
        try:
            serial = parse_soundnode_serial(
                bytes(pkg.data[e.serial_offset : e.serial_offset + e.serial_size]),
                names,
            )
        except Exception:
            continue
        if len(serial.tail) != N_HDR * HDR:
            continue
        if not tail_needs_sanitize(serial.tail):
            continue
        new_tail = make_empty_tail(e.serial_offset, serial.props_end)
        lo = e.serial_offset + serial.props_end
        pkg.data[lo : lo + len(new_tail)] = new_tail
        n += 1
    if n:
        errs = validate_package(pkg)
        if errs:
            raise RuntimeError(f"{upk.name}: {errs}")
        upk.write_bytes(pkg.data)
    return n


def _promote_locsubs0_to_subtitles(serial, names: list[str]) -> bool:
    has_subs = any(p.name == "Subtitles" for p in serial.props if not p.is_none)
    loc = next((p for p in serial.props if p.name == "LocalizedSubtitles"), None)
    if has_subs or loc is None or not loc.elements:
        return False
    slot0 = loc.elements[0]
    inner = next(
        (
            sp
            for sp in slot0
            if sp.name == "Subtitles" and sp.type_name == "ArrayProperty"
        ),
        None,
    )
    if inner is None:
        return False
    try:
        sub_ni = names.index("Subtitles")
        arr_ti = names.index("ArrayProperty")
    except ValueError:
        return False
    new_sub = Prop(
        name_index=sub_ni,
        name_number=0,
        type_index=arr_ti,
        type_number=0,
        array_index=0,
        type_name="ArrayProperty",
        name="Subtitles",
        value=inner.value,
        raw_value=inner.raw_value,
        prefer_raw=True,
        elements=inner.elements,
    )
    out_props: list = []
    inserted = False
    for p in serial.props:
        if p.name == "LocalizedSubtitles" and not inserted:
            out_props.append(new_sub)
            inserted = True
            continue
        if p.name == "LocalizedSubtitles":
            continue
        out_props.append(p)
    if not inserted:
        return False
    serial.props = out_props
    return True


def strip_vo_file(upk: Path) -> dict:
    data = bytearray(upk.read_bytes())
    pkg = load_package(data)
    names = names_from_pkg(pkg)
    jobs: list[tuple[int, bytes, str]] = []
    for e in pkg.exports:
        if e.serial_size < 200:
            continue
        leaf = export_leaf(pkg, e)
        if not is_narrative_leaf(leaf):
            continue
        blob = bytes(pkg.data[e.serial_offset : e.serial_offset + e.serial_size])
        try:
            serial = parse_soundnode_serial(blob, names)
        except Exception:
            continue
        recover_hub_subtitle_tags(serial.props, names)
        if not any(p.name == "LocalizedSubtitles" for p in serial.props):
            continue
        # promote if needed, then drop LocSubs
        _promote_locsubs0_to_subtitles(serial, names)
        keep = [p for p in serial.props if p.name != "LocalizedSubtitles"]
        if not any(p.name == "Subtitles" for p in keep if not p.is_none):
            # still no Subtitles after promote — skip rather than brick
            continue
        out = write_soundnode_serial(
            ExportSerial(
                net_index=serial.net_index,
                props=keep,
                props_end=0,
                tail=serial.tail,
                serial_size=0,
            )
        )
        if out != blob:
            jobs.append((e.index, out, leaf))
    jobs.sort(key=lambda j: pkg.exports[j[0]].serial_offset, reverse=True)
    for idx, new_blob, _leaf in jobs:
        replace_export_serial(pkg, idx, new_blob)
    errs = validate_package(pkg)
    if errs:
        raise RuntimeError(f"{upk.name}: {errs}")
    if jobs:
        upk.write_bytes(pkg.data)
    return {"patched": len(jobs)}


def pretty_leaf(leaf: str) -> list[str]:
    alts = [leaf]
    low = leaf.lower()
    for prefix, repl in (
        ("dlc1_nar_echo_", "DLC1_NAR_Echo_"),
        ("dlc1_nar_live_", "DLC1_NAR_Live_"),
        ("dlc3_nar_echo_", "DLC3_NAR_Echo_"),
        ("dlc3_nar_live_", "DLC3_NAR_Live_"),
        ("dlc3_nar_com_", "DLC3_NAR_COM_"),
        ("dlc4_nar_", "DLC4_NAR_"),
        ("dlc1_", "DLC1_"),
        ("dlc3_", "DLC3_"),
        ("dlc4_", "DLC4_"),
    ):
        if low.startswith(prefix):
            rest = leaf[len(prefix) :]
            pretty = repl + "_".join(
                w[:1].upper() + w[1:] for w in rest.split("_") if w
            )
            alts.append(pretty)
            break
    return alts


def boost_int_file(src: Path, pkg_stem: str, backup_root: Path) -> dict:
    if not src.is_file():
        return {"error": "missing", "extra_blocks": 0}
    raw = src.read_bytes()
    text = (
        raw.decode("utf-16")
        if raw[:2] == b"\xff\xfe"
        else raw.decode("utf-16-le", errors="replace")
    )
    if "; === phaseC boosted keys ===" in text:
        return {"skipped": "already_boosted", "extra_blocks": 0}
    _backup_file(src, backup_root, None, src.parent)
    extras: list[str] = []
    for m in _SECTION_RE.finditer(text):
        header = m.group("header").strip()
        body = m.group("body")
        if "SoundNodeWave" not in header:
            continue
        tm = re.search(r'Subtitles\[0\]=\(Text="([^"]*)"', body)
        if not tm:
            continue
        chinese = tm.group(1)
        key = header.split(" SoundNodeWave")[0].strip()
        alts: list[str] = []
        if "." in key:
            outer, leaf = key.split(".", 1)
            alts += [key, leaf, f"{pkg_stem}.{key}", f"{pkg_stem}.{leaf}"]
            for pl in pretty_leaf(leaf):
                alts += [
                    pl,
                    f"{outer}.{pl}",
                    f"{pkg_stem}.{outer}.{pl}",
                    f"{pkg_stem}.{pl}",
                ]
        else:
            alts.append(key)
            for pl in pretty_leaf(key):
                alts += [pl, f"{pkg_stem}.{pl}"]
        seen: set[str] = set()
        uniq: list[str] = []
        for a in alts:
            k = a.lower()
            if k not in seen:
                seen.add(k)
                uniq.append(a)
        loc_line = (
            f'LocalizedSubtitles[0].Subtitles[0]=(Text="{chinese}")\r\n'
            f"LocalizedSubtitles[0].bMature=False\r\n"
            f"LocalizedSubtitles[0].bManualWordWrap=False\r\n"
        )
        for a in uniq:
            extras.append(
                f"[{a} SoundNodeWave]\r\n"
                f"bManualWordWrap=False\r\n"
                f"bMature=False\r\n"
                f'Comment=""\r\n'
                f'SpokenText=""\r\n'
                f'Subtitles[0]=(Text="{chinese}")\r\n'
                f"{loc_line}\r\n"
            )
    out_text = (
        text.rstrip()
        + "\r\n\r\n; === phaseC boosted keys ===\r\n\r\n"
        + "".join(extras)
    )
    src.write_bytes(b"\xff\xfe" + out_text.encode("utf-16-le"))
    return {"extra_blocks": len(extras)}


def run_pipeline(
    game_path: str | Path,
    *,
    boost_int: bool = True,
    backup_parent: Path | None = None,
    log: LogFn | None = None,
) -> PipelineResult:
    result = PipelineResult()
    try:
        paths = GamePaths.resolve(game_path)
        check_game(paths, log)

        # writability probe
        probe = paths.cooked / "DLC3" / "Maps"
        if probe.is_dir():
            for p in probe.glob("*_LOC_INT.upk"):
                try:
                    with open(p, "r+b"):
                        pass
                except PermissionError as ex:
                    raise PermissionError(
                        f"无法写入 {p.name}（游戏可能仍在运行，请完全退出后再试）"
                    ) from ex
                break

        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_parent = backup_parent or (ROOT / "_knoxx_echo_backup" / "phaseC_gui")
        backup_dir = backup_parent / stamp
        backup_dir.mkdir(parents=True, exist_ok=True)
        result.backup_dir = backup_dir
        _log(log, f"备份目录: {backup_dir}")

        # ----- 1) LOC hollow -----
        _log(log, "")
        _log(log, "======== 1/4 LOC 空壳化 ========")
        loc_bak = backup_dir / "loc_before_hollow"
        loc_list = discover_loc_upks(paths.cooked)
        already_hollow, still_fat = _count_loc_narrative_state(loc_list)
        _log(
            log,
            f"扫描: 叙事对象已空壳 {already_hollow}，仍含字幕 {still_fat}",
        )
        for upk in loc_list:
            _backup_file(upk, backup_dir / "CookedPC_full", paths.cooked, None)
            # 叙事对象一律去字幕属性；长尾（LOC 内嵌音频）也去字、保留音频尾。
            # 战斗 BD/BTLD 已被 is_narrative_leaf 排除，不再用 max_tail 误伤 Zed 等。
            r = hollow_package(
                upk,
                apply=True,
                backup_dir=loc_bak,
                only=None,
                max_tail=None,
                leaf_pred=is_narrative_leaf,
            )
            if r["patched"]:
                rel = upk.relative_to(paths.cooked)
                _log(
                    log,
                    f"  {rel}: hollowed={r['patched']} delta={r['total_delta']}",
                )
                result.loc_hollowed += r["patched"]
        if result.loc_hollowed == 0 and already_hollow > 0:
            _log(
                log,
                "提示: LOC 本已是空壳（多半上次已跑过，或未真正重装游戏），"
                "本次无需再削。VO 去槽仍会执行。",
            )
        _log(log, f"LOC 空壳合计: {result.loc_hollowed}")

        # ----- 2) sanitize tails -----
        _log(log, "")
        _log(log, "======== 2/4 清洗 LOC Bulk 尾 ========")
        for upk in loc_list:
            n = sanitize_loc_file(upk)
            if n:
                _log(log, f"  {upk.relative_to(paths.cooked)}: sanitized={n}")
                result.loc_sanitized += n
        _log(log, f"LOC 清洗合计: {result.loc_sanitized}")

        # ----- 3) VO strip -----
        _log(log, "")
        _log(log, "======== 3/4 VO 去掉 LocalizedSubtitles ========")
        for rel, _int_name in VO_INT_PAIRS:
            upk = paths.cooked.joinpath(*rel.split("/"))
            if not upk.is_file():
                _log(log, f"  跳过（不存在）: {rel}")
                continue
            _backup_file(upk, backup_dir / "CookedPC_full", paths.cooked, None)
            r = strip_vo_file(upk)
            _log(log, f"  {upk.name}: stripped={r['patched']}")
            result.vo_stripped += r["patched"]
        _log(log, f"VO 去槽合计: {result.vo_stripped}")

        # ----- 4) int boost -----
        if boost_int:
            _log(log, "")
            _log(log, "======== 4/4 加强 .int 键名 ========")
            for rel, int_name in VO_INT_PAIRS:
                ip = paths.int_dir / int_name
                if not ip.is_file():
                    _log(log, f"  跳过（无 .int）: {int_name}")
                    continue
                stem = Path(rel).stem
                info = boost_int_file(ip, stem, backup_dir)
                if info.get("skipped"):
                    _log(log, f"  {int_name}: 已加强过，跳过")
                elif info.get("error"):
                    _log(log, f"  {int_name}: {info}")
                else:
                    _log(log, f"  {int_name}: +{info.get('extra_blocks', 0)} blocks")
                    result.int_boosted += 1
        else:
            _log(log, "跳过 .int 加强（未勾选）")

        result.ok = True
        result.message = (
            f"完成。LOC空壳 {result.loc_hollowed}，Bulk清洗 {result.loc_sanitized}，"
            f"VO去槽 {result.vo_stripped}，.int加强 {result.int_boosted} 个文件。\n"
            f"备份: {backup_dir}\n"
            "请完全退出后重进游戏测试。勿点 Steam「验证游戏文件」。"
        )
        _log(log, "")
        _log(log, result.message)
        return result
    except Exception as ex:
        result.ok = False
        result.message = f"失败: {ex}"
        _log(log, result.message)
        return result


def main() -> int:
    ap = argparse.ArgumentParser(description="阶段 C 一键空壳流水线")
    ap.add_argument(
        "--game",
        required=True,
        help="BorderlandsGOTYEnhanced 或 WillowGame 路径",
    )
    ap.add_argument("--no-int-boost", action="store_true")
    ap.add_argument("--check-only", action="store_true")
    args = ap.parse_args()
    paths = GamePaths.resolve(args.game)
    check_game(paths)
    if args.check_only:
        return 0
    r = run_pipeline(args.game, boost_int=not args.no_int_boost)
    return 0 if r.ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
