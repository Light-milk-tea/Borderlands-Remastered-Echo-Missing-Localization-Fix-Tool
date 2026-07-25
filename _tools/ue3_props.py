#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
UE3 property stream reader/writer for Borderlands GOTY Enhanced (pkg 594).

R1:
  parse_props / write_props / parse_soundnode_serial / write_soundnode_serial
  byte-exact round-trip (prefer_raw)
  recover_hub_subtitle_tags() for HUB LOC mis-tagged Subtitles/LocalizedSubtitles
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any


def read_fstring(buf: bytes, off: int) -> tuple[str, int, str, bytes]:
    start = off
    n = struct.unpack_from("<i", buf, off)[0]
    off += 4
    if n > 0:
        raw = buf[off : off + n]
        off += n
        text = raw.split(b"\x00", 1)[0].decode("latin1", "replace")
        enc = "ansi"
    elif n < 0:
        nbytes = (-n) * 2
        raw = buf[off : off + nbytes]
        off += nbytes
        text = raw[:-2].decode("utf-16-le", "replace") if nbytes >= 2 else ""
        enc = "utf16"
    else:
        text, enc = "", "empty"
    return text, off, enc, buf[start:off]


def write_fstring(text: str, encoding: str = "utf16") -> bytes:
    if encoding == "empty":
        return struct.pack("<i", 0)
    if encoding == "ansi":
        raw = text.encode("latin1", "replace") + b"\x00"
        return struct.pack("<i", len(raw)) + raw
    # utf16 (default for Chinese)
    raw = text.encode("utf-16-le") + b"\x00\x00"
    return struct.pack("<i", -(len(raw) // 2)) + raw


@dataclass
class Prop:
    name_index: int
    name_number: int
    type_index: int
    type_number: int
    array_index: int
    type_name: str
    name: str
    value: Any = None
    raw_value: bytes = b""
    prefer_raw: bool = True
    elements: list[list["Prop"]] | None = None
    struct_name_index: int | None = None
    struct_name_number: int = 0
    struct_name: str | None = None
    enum_name_index: int | None = None
    enum_name_number: int = 0
    str_encoding: str | None = None
    is_none: bool = False

    def mark_dirty(self) -> None:
        self.prefer_raw = False


def _name(names: list[str], idx: int) -> str:
    if 0 <= idx < len(names):
        return names[idx]
    return f"#{idx}"


def looks_like_prop_stream(buf: bytes, off: int, names: list[str]) -> bool:
    if off + 8 > len(buf):
        return False
    ni = struct.unpack_from("<i", buf, off)[0]
    if not (0 <= ni < len(names)):
        return False
    n = names[ni]
    return n in (
        "Text", "Time", "Subtitles", "bMature", "bManualWordWrap", "None",
        "bStreamable", "Duration", "SourceFilePath",
    ) or n.startswith("b")


def parse_props(
    buf: bytes,
    names: list[str],
    start: int = 0,
    end: int | None = None,
    *,
    max_props: int = 5000,
) -> tuple[list[Prop], int]:
    if end is None:
        end = len(buf)
    off = start
    props: list[Prop] = []

    for _ in range(max_props):
        if off + 8 > end:
            break
        ni, nn = struct.unpack_from("<ii", buf, off)
        if not (0 <= ni < len(names)):
            raise ValueError(f"bad name index {ni} at {off}")
        name = names[ni]
        if name == "None":
            props.append(
                Prop(
                    name_index=ni, name_number=nn, type_index=0, type_number=0,
                    array_index=0, type_name="None", name="None", is_none=True,
                    prefer_raw=True, raw_value=buf[off : off + 8],
                )
            )
            return props, off + 8

        if off + 24 > end:
            raise ValueError(f"truncated property header for {name} at {off}")

        ti, tn, size, ai = struct.unpack_from("<iiii", buf, off + 8)
        tname = _name(names, ti)
        o = off + 24

        if tname == "BoolProperty":
            if o + 4 > end:
                raise ValueError(f"truncated bool at {off}")
            raw = buf[o : o + 4]
            val = struct.unpack_from("<i", raw, 0)[0]
            props.append(
                Prop(
                    name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                    array_index=ai, type_name=tname, name=name,
                    value=bool(val), raw_value=raw, prefer_raw=True,
                )
            )
            off = o + 4
            continue

        if o + size > end:
            raise ValueError(f"property {name} size {size} OOB at {off}")

        raw_val = bytes(buf[o : o + size])

        if tname == "IntProperty":
            value: Any = struct.unpack_from("<i", raw_val, 0)[0] if size >= 4 else 0
            prop = Prop(
                name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                array_index=ai, type_name=tname, name=name, value=value,
                raw_value=raw_val, prefer_raw=True,
            )
        elif tname == "FloatProperty":
            value = struct.unpack_from("<f", raw_val, 0)[0] if size >= 4 else 0.0
            prop = Prop(
                name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                array_index=ai, type_name=tname, name=name, value=value,
                raw_value=raw_val, prefer_raw=True,
            )
        elif tname == "StrProperty":
            text, _, enc, _fs = read_fstring(raw_val, 0)
            prop = Prop(
                name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                array_index=ai, type_name=tname, name=name, value=text,
                raw_value=raw_val, prefer_raw=True, str_encoding=enc,
            )
        elif tname == "NameProperty":
            a, b = struct.unpack_from("<ii", raw_val, 0) if size >= 8 else (0, 0)
            prop = Prop(
                name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                array_index=ai, type_name=tname, name=name, value=(a, b),
                raw_value=raw_val, prefer_raw=True,
            )
        elif tname == "ByteProperty":
            eni, enn = struct.unpack_from("<ii", buf, o)
            val_start = o + 8
            val_raw = bytes(buf[val_start : val_start + size])
            prop = Prop(
                name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                array_index=ai, type_name=tname, name=name,
                value=val_raw[0] if val_raw else 0, raw_value=val_raw,
                prefer_raw=True, enum_name_index=eni, enum_name_number=enn,
                struct_name=_name(names, eni),
            )
            props.append(prop)
            off = val_start + size
            continue
        elif tname == "StructProperty":
            sni, snn = struct.unpack_from("<ii", buf, o)
            data_start = o + 8
            data_end = data_start + size
            inner, _ = parse_props(buf, names, data_start, data_end)
            data_raw = bytes(buf[data_start:data_end])
            prop = Prop(
                name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                array_index=ai, type_name=tname, name=name, value=inner,
                raw_value=data_raw, prefer_raw=True,
                struct_name_index=sni, struct_name_number=snn,
                struct_name=_name(names, sni),
            )
            props.append(prop)
            off = data_end
            continue
        elif tname == "ArrayProperty":
            elems: list[list[Prop]] | None = None
            if size >= 4:
                count = struct.unpack_from("<i", raw_val, 0)[0]
                cur = 4
                if (
                    count > 0 and count <= 64 and cur < size
                    and looks_like_prop_stream(raw_val, cur, names)
                ):
                    elems = []
                    for _i in range(count):
                        ep, cur2 = parse_props(raw_val, names, cur, size)
                        elems.append(ep)
                        cur = cur2
            prop = Prop(
                name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                array_index=ai, type_name=tname, name=name, elements=elems,
                raw_value=raw_val, prefer_raw=True,
            )
        else:
            prop = Prop(
                name_index=ni, name_number=nn, type_index=ti, type_number=tn,
                array_index=ai, type_name=tname, name=name,
                raw_value=raw_val, prefer_raw=True,
            )

        props.append(prop)
        off = o + size

    return props, off


def _write_fname(index: int, number: int = 0) -> bytes:
    return struct.pack("<ii", index, number)


def _write_header(p: Prop, size: int) -> bytes:
    return (
        _write_fname(p.name_index, p.name_number)
        + _write_fname(p.type_index, p.type_number)
        + struct.pack("<ii", size, p.array_index)
    )


def write_prop(p: Prop) -> bytes:
    if p.is_none:
        return _write_fname(p.name_index, p.name_number)

    if p.type_name == "BoolProperty":
        payload = p.raw_value if p.prefer_raw and p.raw_value else struct.pack("<i", 1 if p.value else 0)
        return _write_header(p, 0) + payload

    if p.type_name == "ByteProperty":
        if p.enum_name_index is None:
            raise ValueError("ByteProperty missing enum name")
        val = p.raw_value if p.prefer_raw else bytes([int(p.value) & 0xFF])
        return _write_header(p, len(val)) + _write_fname(p.enum_name_index, p.enum_name_number) + val

    if p.type_name == "StructProperty":
        if p.struct_name_index is None:
            raise ValueError("StructProperty missing struct name")
        if p.prefer_raw and p.raw_value is not None:
            data = p.raw_value
        else:
            data = write_props(p.value or [])
        return _write_header(p, len(data)) + _write_fname(p.struct_name_index, p.struct_name_number) + data

    if p.type_name == "ArrayProperty":
        if p.prefer_raw and p.raw_value is not None:
            payload = p.raw_value
        elif p.elements is not None:
            body = b"".join(write_props(elem) for elem in p.elements)
            payload = struct.pack("<i", len(p.elements)) + body
        else:
            payload = p.raw_value
        return _write_header(p, len(payload)) + payload

    if p.prefer_raw and p.raw_value is not None:
        payload = p.raw_value
    elif p.type_name == "IntProperty":
        payload = struct.pack("<i", int(p.value))
    elif p.type_name == "FloatProperty":
        payload = struct.pack("<f", float(p.value))
    elif p.type_name == "StrProperty":
        payload = write_fstring(str(p.value), p.str_encoding or "utf16")
    elif p.type_name == "NameProperty":
        a, b = p.value
        payload = struct.pack("<ii", a, b)
    else:
        payload = p.raw_value
    return _write_header(p, len(payload)) + payload


def write_props(props: list[Prop]) -> bytes:
    return b"".join(write_prop(p) for p in props)


@dataclass
class ExportSerial:
    net_index: int
    props: list[Prop]
    props_end: int
    tail: bytes = b""
    serial_size: int = 0


def parse_soundnode_serial(blob: bytes, names: list[str]) -> ExportSerial:
    if len(blob) < 4:
        raise ValueError("serial too small")
    net_index = struct.unpack_from("<i", blob, 0)[0]
    props, end = parse_props(blob, names, 4, len(blob))
    return ExportSerial(
        net_index=net_index, props=props, props_end=end,
        tail=bytes(blob[end:]), serial_size=len(blob),
    )


def write_soundnode_serial(serial: ExportSerial) -> bytes:
    return struct.pack("<i", serial.net_index) + write_props(serial.props) + serial.tail


def names_from_pkg(pkg) -> list[str]:
    return [n.name for n in pkg.names]


def roundtrip_export_serial(blob: bytes, names: list[str]) -> tuple[bool, dict[str, Any]]:
    serial = parse_soundnode_serial(blob, names)
    out = write_soundnode_serial(serial)
    info: dict[str, Any] = {
        "orig_size": len(blob), "out_size": len(out),
        "props_end": serial.props_end, "tail": len(serial.tail),
        "prop_count": len(serial.props), "match": out == blob,
    }
    if out != blob:
        lim = min(len(out), len(blob))
        diff_at = next((i for i in range(lim) if out[i] != blob[i]), lim)
        info["first_diff"] = diff_at
        info["orig_at"] = blob[diff_at : diff_at + 16].hex()
        info["out_at"] = out[diff_at : diff_at + 16].hex()
    return out == blob, info


def _payload_looks_like_subtitle_array(raw: bytes, names: list[str]) -> bool:
    if len(raw) < 8:
        return False
    count = struct.unpack_from("<i", raw, 0)[0]
    if count < 0 or count > 64:
        return False
    if count == 0:
        return len(raw) == 4
    ni = struct.unpack_from("<i", raw, 4)[0]
    return 0 <= ni < len(names) and names[ni] == "Text"


def _payload_looks_like_localized_array(raw: bytes, names: list[str]) -> bool:
    if len(raw) < 8:
        return False
    count = struct.unpack_from("<i", raw, 0)[0]
    if count < 1 or count > 32:
        return False
    ni = struct.unpack_from("<i", raw, 4)[0]
    return 0 <= ni < len(names) and names[ni] == "Subtitles"


def recover_hub_subtitle_tags(props: list[Prop], names: list[str]) -> list[str]:
    """
    Rewrite HUB mis-tagged SourceFilePath/StrProperty entries that actually hold
    Subtitles / LocalizedSubtitles array payloads into proper ArrayProperty tags.
    Returns list of recovery notes.
    """
    notes: list[str] = []
    try:
        arr_ti = names.index("ArrayProperty")
        sub_ni = names.index("Subtitles")
        loc_ni = names.index("LocalizedSubtitles")
    except ValueError as e:
        raise KeyError("name table missing subtitle keys") from e

    for p in props:
        if p.is_none or p.type_name != "StrProperty" or p.name != "SourceFilePath":
            continue
        raw = p.raw_value
        if _payload_looks_like_localized_array(raw, names):
            logical, nidx = "LocalizedSubtitles", loc_ni
        elif _payload_looks_like_subtitle_array(raw, names):
            logical, nidx = "Subtitles", sub_ni
        else:
            continue

        count = struct.unpack_from("<i", raw, 0)[0]
        elems: list[list[Prop]] = []
        cur = 4
        for _i in range(count):
            ep, cur = parse_props(raw, names, cur, len(raw))
            elems.append(ep)

        notes.append(
            f"{p.name}/{p.type_name} -> {logical}/ArrayProperty "
            f"(count={count}, payload={len(raw)})"
        )
        p.name = logical
        p.name_index = nidx
        p.name_number = 0
        p.type_name = "ArrayProperty"
        p.type_index = arr_ti
        p.type_number = 0
        p.elements = elems
        p.value = None
        p.str_encoding = None
        # Keep payload bytes; only tags change if prefer_raw stays True
        p.prefer_raw = True
    return notes


def set_str_prop_text(p: Prop, text: str, encoding: str = "utf16") -> None:
    if p.type_name != "StrProperty":
        raise TypeError("not StrProperty")
    p.value = text
    p.str_encoding = encoding
    p.raw_value = write_fstring(text, encoding)
    p.mark_dirty()


def find_props_by_name(props: list[Prop], name: str) -> list[Prop]:
    return [p for p in props if p.name == name and not p.is_none]


def find_text_props_in_array(array_prop: Prop) -> list[Prop]:
    """Collect Text StrProperty nodes under an ArrayProperty of cues/structs."""
    out: list[Prop] = []
    if not array_prop.elements:
        return out
    for elem in array_prop.elements:
        for sp in elem:
            if sp.name == "Text" and sp.type_name == "StrProperty":
                out.append(sp)
            elif sp.name == "Subtitles" and sp.type_name == "ArrayProperty":
                out.extend(find_text_props_in_array(sp))
    return out


if __name__ == "__main__":
    from pathlib import Path
    import sys

    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from ue3_upk import load_package, find_export_for_offset

    ENH = Path(r"C:\downloadapps\sssteam\steamapps\common\BorderlandsGOTYEnhanced")
    paths = [
        ("HUB", ENH / "WillowGame/CookedPC/DLC3/Maps/dlc3_HUB_Dynamic_LOC_INT.upk"),
        ("VO", ENH / "WillowGame/CookedPC/DLC3/Packages/Audio/VO/DLC3_VO_Narrative_WAV.upk"),
    ]
    needle = b"Well, shoot, looks like the passenger seat"
    ok_all = True
    for label, path in paths:
        data = path.read_bytes()
        pkg = load_package(data)
        e = find_export_for_offset(pkg, data.find(needle))
        blob = bytes(data[e.serial_offset : e.serial_offset + e.serial_size])
        names = names_from_pkg(pkg)
        ok, info = roundtrip_export_serial(blob, names)
        print(f"[{label}] raw round-trip", "OK" if ok else "FAIL", info)
        ok_all = ok_all and ok

        # semantic rebuild of ArrayProperty trees (VO); HUB after recover
        serial = parse_soundnode_serial(blob, names)
        notes = recover_hub_subtitle_tags(serial.props, names)
        if notes:
            print(f"[{label}] recovered:", *notes, sep="\n  ")
            fixed = write_soundnode_serial(serial)
            # tag fix is same-size; payload unchanged with prefer_raw
            print(f"[{label}] tag-fix size", len(fixed), "same", len(fixed) == len(blob))
            # reparse fixed as proper arrays without needing recover on VO-like tags
            serial2 = parse_soundnode_serial(fixed, names)
            arr_names = [p.name for p in serial2.props if not p.is_none]
            print(f"[{label}] after fix top props:", arr_names)
            # semantic rebuild arrays
            for p in serial2.props:
                if p.type_name == "ArrayProperty" and p.elements is not None:
                    for elem in p.elements:
                        for sp in elem:
                            if sp.is_none:
                                continue
                            if sp.type_name == "ArrayProperty" and sp.elements is not None:
                                for e2 in sp.elements:
                                    for sp2 in e2:
                                        if not sp2.is_none:
                                            sp2.prefer_raw = False
                                sp.prefer_raw = False
                            else:
                                sp.prefer_raw = False
                    p.prefer_raw = False
                elif p.type_name in ("BoolProperty", "IntProperty", "FloatProperty", "StrProperty"):
                    if p.name != "SourceFilePath" or p.type_name != "StrProperty":
                        # rebuild ordinary fields; keep real SourceFilePath via raw or semantic
                        p.prefer_raw = False
            out2 = write_soundnode_serial(serial2)
            print(f"[{label}] semantic after recover match", out2 == fixed)
            ok_all = ok_all and (out2 == fixed)
        else:
            # VO path: dirty all rebuildable
            for p in serial.props:
                if p.is_none:
                    continue
                if p.type_name == "ArrayProperty" and p.elements is not None:
                    for elem in p.elements:
                        for sp in elem:
                            if sp.is_none:
                                continue
                            if sp.type_name == "ArrayProperty" and sp.elements is not None:
                                for e2 in sp.elements:
                                    for sp2 in e2:
                                        if not sp2.is_none:
                                            sp2.prefer_raw = False
                                sp.prefer_raw = False
                            else:
                                sp.prefer_raw = False
                    p.prefer_raw = False
                elif p.type_name in ("BoolProperty", "IntProperty", "FloatProperty", "StrProperty", "NameProperty"):
                    p.prefer_raw = False
            out = write_soundnode_serial(serial)
            print(f"[{label}] semantic rebuild", "OK" if out == blob else "FAIL")
            ok_all = ok_all and (out == blob)

    raise SystemExit(0 if ok_all else 1)