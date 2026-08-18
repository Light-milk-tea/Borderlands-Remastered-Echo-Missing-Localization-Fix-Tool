from __future__ import annotations

if True:
    assert __import__("mods_base").__version_info__ >= (1, 10), "Please update the Willow1 SDK"
    assert __import__("unrealsdk").__version_info__ >= (1, 3, 0), "Please update the Willow1 SDK"

import re
import sys
import time
from pathlib import Path
from typing import Any

import unrealsdk
from mods_base import (
    BoolOption,
    ButtonOption,
    CoopSupport,
    Game,
    build_mod,
    hook,
)
from unrealsdk import logging
from unrealsdk.hooks import Type
from unrealsdk.unreal import BoundFunction, UObject, WrappedStruct

try:
    from unrealsdk.unreal import make_struct
except ImportError:
    make_struct = None  # type: ignore[misc, assignment]

_SECTION_RE = re.compile(
    r"^\[(?P<header>[^\]]+)\]\s*\r?\n(?P<body>.*?)(?=\r?\n\[|\Z)",
    re.S | re.M,
)
_TEXT_RE = re.compile(
    r"(?:LocalizedSubtitles\[\d+\]\.)?Subtitles\[(\d+)\]=\(Text=\"((?:[^\"\\]|\\.)*)\"",
)
_SKIP_LEAVES = ("_BD_", "BTLD", "LOG_VENDING", "CROWD_LIVE")
_NARRATIVE_MARKS = (
    "NAR_ECHO",
    "LOG_ECHO",
    "NAR_DROIDECHO",
    "NAR_LIVE",
    "NAR_COM",
    "NAR_INTRO",
    "NAR_OUTRO",
)

# leaf.lower() -> {cue_index: chinese}
_INT_TEXTS: dict[str, dict[int, str]] = {}
# pathname -> snapshot taken before first overwrite
_ORIGINALS: dict[str, dict[str, Any]] = {}
_INT_DIR: Path | None = None
_LAST_SCAN = 0.0
_APPLIED_COUNT = 0


def is_narrative_leaf(leaf: str, *, include_sal: bool) -> bool:
    u = leaf.upper()
    if any(x in u for x in _SKIP_LEAVES):
        return False
    if u.startswith("SAL_") or "_SAL_" in u:
        return include_sal
    return any(x in u for x in _NARRATIVE_MARKS)


def pretty_leaf_keys(leaf: str) -> list[str]:
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
            pretty = repl + "_".join(w[:1].upper() + w[1:] for w in rest.split("_") if w)
            alts.append(pretty)
            break
    return alts


def _unescape_int_text(raw: str) -> str:
    return raw.replace('\\"', '"').replace("\\n", "\n")


def find_int_dir() -> Path | None:
    seen: set[Path] = set()
    roots: list[Path] = []
    try:
        roots.append(Path(sys.executable).resolve())
    except OSError:
        pass
    try:
        roots.append(Path.cwd().resolve())
    except OSError:
        pass
    for start in roots:
        for parent in (start, *start.parents):
            if parent in seen:
                continue
            seen.add(parent)
            for rel in (
                Path("WillowGame") / "Localization" / "INT",
                Path("Localization") / "INT",
            ):
                candidate = parent / rel
                if candidate.is_dir():
                    return candidate
    return None


def _parse_int_file(path: Path) -> int:
    raw = path.read_bytes()
    if raw[:2] == b"\xff\xfe":
        text = raw.decode("utf-16")
    elif raw[:2] == b"\xfe\xff":
        text = raw.decode("utf-16-be")
    else:
        text = raw.decode("utf-16-le", errors="replace")

    added = 0
    for match in _SECTION_RE.finditer(text):
        header = match.group("header").strip()
        if "SoundNodeWave" not in header:
            continue
        key = header.split(" SoundNodeWave")[0].strip()
        leaf = key.rsplit(".", 1)[-1]
        cues: dict[int, str] = {}
        for tm in _TEXT_RE.finditer(match.group("body")):
            idx = int(tm.group(1))
            if idx not in cues:
                cues[idx] = _unescape_int_text(tm.group(2))
        if not cues:
            continue
        for alias in {key, leaf, *pretty_leaf_keys(leaf)}:
            _INT_TEXTS[alias.lower()] = cues
        added += 1
    return added


def load_int_table() -> int:
    global _INT_DIR
    _INT_TEXTS.clear()
    _INT_DIR = find_int_dir()
    if _INT_DIR is None:
        logging.error("Echo CN Fix: 找不到 WillowGame\\Localization\\INT，请确认天邈汉化已安装。")
        return 0
    total = 0
    for path in sorted(_INT_DIR.glob("*.int")):
        try:
            total += _parse_int_file(path)
        except OSError as ex:
            logging.dev_warning(f"Echo CN Fix: 读取 {path.name} 失败: {ex}")
    logging.info(f"Echo CN Fix: 从 {_INT_DIR} 载入 {total} 条 SoundNodeWave 译文")
    return total


def lookup_cues(leaf: str) -> dict[int, str] | None:
    low = leaf.lower()
    if low in _INT_TEXTS:
        return _INT_TEXTS[low]
    for alias in pretty_leaf_keys(leaf):
        found = _INT_TEXTS.get(alias.lower())
        if found:
            return found
    return None


def _obj_name(obj: UObject) -> str:
    try:
        return str(obj.Name)
    except Exception:
        return ""


def _path_name(obj: UObject) -> str:
    for attr in ("_path_name", "PathName"):
        fn = getattr(obj, attr, None)
        if callable(fn):
            try:
                return str(fn())
            except Exception:
                continue
    try:
        return str(obj)
    except Exception:
        return _obj_name(obj)


def _arr_len(arr: Any) -> int:
    try:
        return len(arr)
    except Exception:
        return 0


def _read_cue_texts(arr: Any) -> list[str]:
    out: list[str] = []
    for i in range(_arr_len(arr)):
        try:
            out.append(str(arr[i].Text))
        except Exception:
            out.append("")
    return out


def _write_cue_texts(arr: Any, cues: dict[int, str]) -> int:
    changed = 0
    n = _arr_len(arr)
    for idx, text in cues.items():
        if idx < n:
            try:
                if str(arr[idx].Text) != text:
                    arr[idx].Text = text
                    changed += 1
                else:
                    changed += 1
            except Exception as ex:
                logging.dev_warning(f"Echo CN Fix: 写 Subtitles[{idx}] 失败: {ex}")
    return changed


def _snapshot(wave: UObject) -> dict[str, Any]:
    spoken = ""
    try:
        spoken = str(wave.SpokenText)
    except Exception:
        pass
    loc_slots: list[list[str]] = []
    try:
        loc = wave.LocalizedSubtitles
        for i in range(_arr_len(loc)):
            loc_slots.append(_read_cue_texts(loc[i].Subtitles))
    except Exception:
        pass
    return {
        "spoken": spoken,
        "subs": _read_cue_texts(getattr(wave, "Subtitles", None)),
        "loc": loc_slots,
    }


def _restore_one(wave: UObject, snap: dict[str, Any]) -> None:
    try:
        if snap.get("spoken") is not None:
            wave.SpokenText = snap["spoken"]
    except Exception:
        pass
    try:
        _write_cue_texts(wave.Subtitles, dict(enumerate(snap.get("subs") or [])))
    except Exception:
        pass
    try:
        loc = wave.LocalizedSubtitles
        slots = snap.get("loc") or []
        for i, texts in enumerate(slots):
            if i < _arr_len(loc):
                _write_cue_texts(loc[i].Subtitles, dict(enumerate(texts)))
    except Exception:
        pass


def _ensure_subtitle_array(wave: UObject, cues: dict[int, str]) -> None:
    """If Subtitles is empty but we have Chinese, try to append a cue."""
    if make_struct is None:
        return
    try:
        arr = wave.Subtitles
    except Exception:
        return
    if _arr_len(arr) > 0 or 0 not in cues:
        return
    try:
        arr.append(make_struct("SubtitleCue", Text=cues[0], Time=0.0))
    except Exception:
        try:
            arr.append(make_struct("SubtitleCue"))
            arr[0].Text = cues[0]
            arr[0].Time = 0.0
        except Exception as ex:
            logging.dev_warning(f"Echo CN Fix: 无法新建 SubtitleCue: {ex}")


def apply_one(wave: UObject, leaf: str | None = None) -> bool:
    leaf = leaf or _obj_name(wave)
    if not leaf or leaf.startswith("Default"):
        return False
    if not is_narrative_leaf(leaf, include_sal=include_sal.value):
        return False
    cues = lookup_cues(leaf)
    if not cues:
        return False

    path = _path_name(wave)
    if path not in _ORIGINALS:
        _ORIGINALS[path] = _snapshot(wave)

    _ensure_subtitle_array(wave, cues)
    wrote = 0
    try:
        wrote += _write_cue_texts(wave.Subtitles, cues)
    except Exception:
        pass
    try:
        loc = wave.LocalizedSubtitles
        for i in range(_arr_len(loc)):
            wrote += _write_cue_texts(loc[i].Subtitles, cues)
    except Exception:
        pass
    if wrote == 0 and 0 in cues:
        try:
            wave.SpokenText = cues[0]
            wrote = 1
        except Exception:
            pass
    return wrote > 0


def apply_all() -> int:
    global _APPLIED_COUNT, _LAST_SCAN
    if not _INT_TEXTS:
        load_int_table()
    count = 0
    try:
        waves = unrealsdk.find_all("SoundNodeWave")
    except Exception as ex:
        logging.error(f"Echo CN Fix: find_all(SoundNodeWave) 失败: {ex}")
        return 0
    for wave in waves:
        try:
            if apply_one(wave):
                count += 1
        except Exception as ex:
            logging.dev_warning(f"Echo CN Fix: 处理 {_obj_name(wave)} 失败: {ex}")
    _APPLIED_COUNT = count
    _LAST_SCAN = time.monotonic()
    logging.info(f"Echo CN Fix: 已覆盖 {count} 条叙事字幕")
    return count


def apply_all_throttled() -> int:
    if time.monotonic() - _LAST_SCAN < 1.0:
        return 0
    return apply_all()


def restore_all() -> int:
    count = 0
    try:
        waves = unrealsdk.find_all("SoundNodeWave")
    except Exception:
        waves = []
    for wave in waves:
        path = _path_name(wave)
        snap = _ORIGINALS.get(path)
        if snap is None:
            continue
        try:
            _restore_one(wave, snap)
            count += 1
        except Exception as ex:
            logging.dev_warning(f"Echo CN Fix: 还原 {_obj_name(wave)} 失败: {ex}")
    _ORIGINALS.clear()
    logging.info(f"Echo CN Fix: 已还原 {count} 条字幕")
    return count


def _iter_sound_nodes(node: UObject | None, seen: set[int] | None = None):
    if node is None:
        return
    if seen is None:
        seen = set()
    key = id(node)
    if key in seen:
        return
    seen.add(key)
    try:
        cls = str(node.Class.Name)
    except Exception:
        return
    if cls == "SoundNodeWave":
        yield node
        return
    children = getattr(node, "ChildNodes", None)
    for i in range(_arr_len(children)):
        try:
            yield from _iter_sound_nodes(children[i], seen)
        except Exception:
            continue


def _is_wave(obj: UObject | None) -> bool:
    if obj is None:
        return False
    try:
        return str(obj.Class.Name) == "SoundNodeWave"
    except Exception:
        return False


def _patch_from_cue(cue: UObject | None) -> None:
    if cue is None:
        return
    if _is_wave(cue):
        apply_one(cue)
        return
    start = getattr(cue, "FirstNode", None)
    for wave in _iter_sound_nodes(start):
        apply_one(wave)


def _notify(msg: str) -> None:
    logging.info(f"Echo CN Fix: {msg}")
    try:
        from ui_utils import show_hud_message

        show_hud_message("Echo CN Fix", msg)
    except Exception:
        pass


def _on_sal_change(_option: BoolOption, _value: bool) -> None:
    if not _ORIGINALS:
        return
    restore_all()
    apply_all()


include_sal = BoolOption(
    identifier="Fix SAL proximity lines",
    value=True,
    true_text="开",
    false_text="关",
    display_name="同时修复靠近台词 (SAL)",
    description="靠近 NPC 触发的闲聊/旁白。关掉则只处理 Echo / NAR 叙事句。",
    on_change=_on_sal_change,
)

status_line = ButtonOption(
    identifier="Status",
    display_name="当前状态",
    description="启用模组后会扫描已加载的语音对象，并把天邈 .int 中文写进字幕槽。",
)


def _on_rescan(_option: ButtonOption) -> None:
    n = apply_all()
    missing = "（未找到 .int，请确认已装天邈汉化）" if not _INT_TEXTS else ""
    _notify(f"已覆盖 {n} 条字幕{missing}")


rescan_btn = ButtonOption(
    identifier="Rescan",
    display_name="重新扫描并写入中文",
    description="进图后如果仍有英文，点这个再听一遍该句。",
    on_press=_on_rescan,
)


@hook("Engine.WorldInfo:PostBeginPlay", Type.POST)
def on_world_ready(
    _obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    apply_all_throttled()


@hook("Engine.AudioComponent:Play")
def on_audio_play(
    obj: UObject,
    _args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    cue = getattr(obj, "SoundCue", None) or getattr(obj, "Cue", None)
    _patch_from_cue(cue)


@hook("Engine.Actor:PlaySound")
def on_play_sound(
    _obj: UObject,
    args: WrappedStruct,
    _ret: Any,
    _func: BoundFunction,
) -> None:
    cue = None
    for attr in ("InSoundCue", "SoundCue", "ASound"):
        if hasattr(args, attr):
            cue = getattr(args, attr)
            if cue is not None:
                break
    _patch_from_cue(cue)


def enable() -> None:
    n_int = load_int_table()
    n = apply_all()
    if n_int == 0:
        _notify("未读到天邈 .int。请先安装 BGOTYECNv1.0fix。")
    else:
        _notify(f"已启用，覆盖 {n} 条字幕")


def disable() -> None:
    n = restore_all()
    _notify(f"已关闭，还原 {n} 条字幕")


def _function_exists(path: str) -> bool:
    try:
        return unrealsdk.find_object("Function", path) is not None
    except Exception:
        return False


def _usable_hooks() -> list:
    hooks = []
    for item in (on_world_ready, on_audio_play, on_play_sound):
        paths = [fn for fn, _typ in item.hook_funcs]
        if all(_function_exists(path) for path in paths):
            hooks.append(item)
        else:
            logging.dev_warning(f"Echo CN Fix: 跳过不存在的 hook {paths}")
    if not hooks:
        logging.error("Echo CN Fix: 没有可用 hook，将只在启用时扫描一次。")
    return hooks


build_mod(
    on_enable=enable,
    on_disable=disable,
    hooks=_usable_hooks(),
    options=[include_sal, rescan_btn, status_line],
    supported_games=Game.BL1E,
    coop_support=CoopSupport.ClientSide,
)
