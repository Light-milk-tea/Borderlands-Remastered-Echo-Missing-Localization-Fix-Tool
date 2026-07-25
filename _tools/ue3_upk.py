#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Borderlands GOTY Enhanced (UE3 pkg ver 594 / licensee 58) UPK helpers.

Parse Name/Export tables exactly; insert bytes while updating only real
Export SerialSize / SerialOffset fields (no heuristic header scans).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field


TAG = 0x9E2A83C1


@dataclass
class NameEntry:
    name: str
    flags: int
    offset: int


@dataclass
class ExportEntry:
    index: int
    class_index: int
    super_index: int
    outer_index: int
    name_index: int
    name_number: int
    archetype: int
    object_flags: int
    serial_size: int
    serial_offset: int
    export_flags: int
    net_count: int
    guid: bytes
    unknown1: int
    entry_offset: int
    size_field_off: int
    offset_field_off: int
    entry_end: int


@dataclass
class Package:
    data: bytearray
    names: list[NameEntry] = field(default_factory=list)
    exports: list[ExportEntry] = field(default_factory=list)
    header_size: int = 0
    name_count: int = 0
    name_offset: int = 0
    export_count: int = 0
    export_offset: int = 0
    import_count: int = 0
    import_offset: int = 0
    file_ver: int = 0
    lic_ver: int = 0

    def name_str(self, idx: int) -> str:
        if 0 <= idx < len(self.names):
            return self.names[idx].name
        return f"#{idx}"


def _read_fstring(data: bytes, off: int) -> tuple[str, int]:
    n = struct.unpack_from("<i", data, off)[0]
    off += 4
    if n > 0:
        raw = data[off : off + n]
        off += n
        s = raw.split(b"\x00", 1)[0].decode("latin1", "replace")
    elif n < 0:
        nbytes = (-n) * 2
        raw = data[off : off + nbytes]
        off += nbytes
        s = raw[:-2].decode("utf-16-le", "replace") if nbytes >= 2 else ""
    else:
        s = ""
    return s, off


def load_package(data: bytes | bytearray) -> Package:
    if isinstance(data, bytes):
        data = bytearray(data)
    if struct.unpack_from("<I", data, 0)[0] != TAG:
        raise ValueError("not a UE3 package")

    file_ver = struct.unpack_from("<H", data, 4)[0]
    lic_ver = struct.unpack_from("<H", data, 6)[0]
    header_size = struct.unpack_from("<i", data, 8)[0]

    off = 12
    _folder, off = _read_fstring(data, off)
    off += 4  # PackageFlags
    name_count = struct.unpack_from("<i", data, off)[0]
    off += 4
    name_offset = struct.unpack_from("<i", data, off)[0]
    off += 4
    export_count = struct.unpack_from("<i", data, off)[0]
    off += 4
    export_offset = struct.unpack_from("<i", data, off)[0]
    off += 4
    import_count = struct.unpack_from("<i", data, off)[0]
    off += 4
    import_offset = struct.unpack_from("<i", data, off)[0]

    pkg = Package(
        data=data,
        header_size=header_size,
        name_count=name_count,
        name_offset=name_offset,
        export_count=export_count,
        export_offset=export_offset,
        import_count=import_count,
        import_offset=import_offset,
        file_ver=file_ver,
        lic_ver=lic_ver,
    )

    noff = name_offset
    for _ in range(name_count):
        nstart = noff
        s, noff = _read_fstring(data, noff)
        flags = struct.unpack_from("<Q", data, noff)[0]
        noff += 8
        pkg.names.append(NameEntry(s, flags, nstart))

    eoff = export_offset
    for i in range(export_count):
        entry_off = eoff
        class_index = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        super_index = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        outer_index = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        name_index = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        name_number = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        archetype = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        object_flags = struct.unpack_from("<Q", data, eoff)[0]
        eoff += 8
        size_field_off = eoff
        serial_size = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        offset_field_off = eoff
        serial_offset = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        export_flags = struct.unpack_from("<I", data, eoff)[0]
        eoff += 4
        net_count = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        if net_count < 0 or net_count > 100000:
            raise ValueError(f"export[{i}] bad net_count={net_count} @ {entry_off}")
        guid = bytes(data[eoff : eoff + 16])
        eoff += 16
        unknown1 = struct.unpack_from("<i", data, eoff)[0]
        eoff += 4
        eoff += 4 * net_count
        pkg.exports.append(
            ExportEntry(
                index=i,
                class_index=class_index,
                super_index=super_index,
                outer_index=outer_index,
                name_index=name_index,
                name_number=name_number,
                archetype=archetype,
                object_flags=object_flags,
                serial_size=serial_size,
                serial_offset=serial_offset,
                export_flags=export_flags,
                net_count=net_count,
                guid=guid,
                unknown1=unknown1,
                entry_offset=entry_off,
                size_field_off=size_field_off,
                offset_field_off=offset_field_off,
                entry_end=eoff,
            )
        )
    return pkg


def find_export_for_offset(pkg: Package, pos: int) -> ExportEntry | None:
    best = None
    for e in pkg.exports:
        if e.serial_size <= 0:
            continue
        if e.serial_offset <= pos < e.serial_offset + e.serial_size:
            if best is None or e.serial_size < best.serial_size:
                best = e
    return best


def insert_bytes(pkg: Package, insert_at: int, raw: bytes = b"\x00") -> None:
    """
    Insert `raw` at insert_at.

    Updates ONLY verified Export table SerialSize / SerialOffset fields.
    Does not heuristically rewrite other int32s (that caused Bad name index before).
    """
    n = len(raw)
    if n <= 0:
        return
    owner = find_export_for_offset(pkg, insert_at - 1) or find_export_for_offset(
        pkg, insert_at
    )
    if owner is None:
        raise RuntimeError(f"insert_at {insert_at} not inside any export")

    data = pkg.data
    data[insert_at:insert_at] = raw

    for e in pkg.exports:
        if e.index == owner.index:
            e.serial_size += n
            struct.pack_into("<i", data, e.size_field_off, e.serial_size)
        elif e.serial_offset >= insert_at:
            e.serial_offset += n
            struct.pack_into("<i", data, e.offset_field_off, e.serial_offset)




# UE3 FByteBulkData: Flags, ElementCount, SizeOnDisk, OffsetInFile (16 bytes).
# LOC stubs often have up to ~7 empty headers (112 bytes). VO packages usually
# have 1–2 real headers then inline PCM — scanning into PCM corrupts audio/GPF.
_BULK_HDR = 16
_BULK_HDR_MAX = 7
_BULK_OFF_FIELD = 12  # OffsetInFile within each header
_BULK_FLAGS_MASK_MAX = 0xFFFF


def _looks_like_bulk_header(flags: int, count: int, size: int) -> bool:
    if flags < 0 or flags > _BULK_FLAGS_MASK_MAX:
        return False
    if count < 0 or size < 0:
        return False
    if count > 80_000_000 or size > 80_000_000:
        return False
    if count == 0 and size == 0:
        return True
    if count == size:
        return True
    # rare compressed: sizes differ but stay sane
    if count > 0 and size > 0 and size <= count * 2:
        return True
    return False


def iter_bulk_offset_field_rel(tail: bytes) -> list[int]:
    """Relative offsets (into tail) of each leading BulkData OffsetInFile field."""
    out: list[int] = []
    max_bytes = min(len(tail), _BULK_HDR_MAX * _BULK_HDR)
    for i in range(0, max_bytes, _BULK_HDR):
        if i + _BULK_HDR > len(tail):
            break
        flags, count, size, _off = struct.unpack_from("<4i", tail, i)
        if not _looks_like_bulk_header(flags, count, size):
            break
        out.append(i + _BULK_OFF_FIELD)
    return out


def adjust_soundnode_bulk_offsets(
    pkg: Package,
    threshold: int,
    delta: int,
    *,
    self_lo: int | None = None,
    self_hi: int | None = None,
) -> int:
    """
    After a serial-region shrink/grow, fix absolute BulkData file offsets that live
    in SoundNodeWave tails (bytes after property None). Only touches export serial
    tails — never Name/Import/Export tables.

    - Offsets >= threshold: data after the replaced export shifted by delta.
    - Offsets in [self_lo, self_hi): payload/metadata that lived inside the replaced
      export's leftover (tail) and moved with the props shrink/grow.

    Only OffsetInFile of plausible leading FByteBulkData headers is touched;
    embedded audio after the headers is left intact.

    Returns number of int32 fields adjusted.
    """
    if delta == 0:
        return 0
    # Lazy import to avoid circular deps at module load for simple uses
    from ue3_props import names_from_pkg, parse_soundnode_serial

    names = names_from_pkg(pkg)
    data = pkg.data
    adjusted = 0
    for e in pkg.exports:
        if e.serial_size < 200:
            continue
        try:
            blob = bytes(data[e.serial_offset : e.serial_offset + e.serial_size])
            serial = parse_soundnode_serial(blob, names)
        except Exception:
            continue
        if len(serial.tail) < _BULK_HDR:
            continue
        base = e.serial_offset + serial.props_end
        for rel in iter_bulk_offset_field_rel(serial.tail):
            off = base + rel
            v = struct.unpack_from("<i", data, off)[0]
            if v >= threshold and v < len(data) + 50_000_000:
                struct.pack_into("<i", data, off, v + delta)
                adjusted += 1
            elif (
                self_lo is not None
                and self_hi is not None
                and self_lo <= v < self_hi
            ):
                struct.pack_into("<i", data, off, v + delta)
                adjusted += 1
    return adjusted


def replace_export_serial(pkg: Package, export_index: int, new_serial: bytes) -> int:
    """
    Replace one export serial blob. Updates SerialSize for that export and
    shifts later exports' SerialOffset by delta. Returns size delta (new-old).
    """
    if export_index < 0 or export_index >= len(pkg.exports):
        raise IndexError(export_index)
    e = pkg.exports[export_index]
    old_off = e.serial_offset
    old_size = e.serial_size
    if old_size < 0:
        raise ValueError("negative serial size")
    new_size = len(new_serial)
    delta = new_size - old_size
    data = pkg.data

    # Capture old leftover window before rewrite (for in-export BulkData ptrs)
    old_props_end = None
    if delta != 0 and old_size >= 200:
        try:
            from ue3_props import names_from_pkg, parse_soundnode_serial

            old_serial = parse_soundnode_serial(
                bytes(data[old_off : old_off + old_size]), names_from_pkg(pkg)
            )
            old_props_end = old_serial.props_end
        except Exception:
            old_props_end = None

    if delta == 0:
        data[old_off : old_off + old_size] = new_serial
    else:
        del data[old_off : old_off + old_size]
        data[old_off:old_off] = new_serial

    e.serial_size = new_size
    struct.pack_into("<i", data, e.size_field_off, e.serial_size)

    if delta != 0:
        threshold = old_off + old_size
        for other in pkg.exports:
            if other.index == e.index:
                continue
            if other.serial_offset >= threshold:
                other.serial_offset += delta
                struct.pack_into("<i", data, other.offset_field_off, other.serial_offset)
        # Keep SoundNodeWave FByteBulkData absolute offsets coherent after shift.
        # Critical: also rewrite ptrs that pointed into THIS export's old leftover
        # (they are < threshold and were previously left stale → broken portraits).
        self_lo = self_hi = None
        if old_props_end is not None:
            self_lo = old_off + old_props_end
            self_hi = old_off + old_size
        adjust_soundnode_bulk_offsets(
            pkg, threshold, delta, self_lo=self_lo, self_hi=self_hi
        )

    return delta


def validate_package(pkg: Package) -> list[str]:
    errs: list[str] = []
    for e in pkg.exports:
        if e.serial_size < 0:
            errs.append(f"export[{e.index}] negative size")
        elif e.serial_size > 0:
            if e.serial_offset < pkg.header_size:
                errs.append(f"export[{e.index}] offset in header")
            if e.serial_offset + e.serial_size > len(pkg.data):
                errs.append(f"export[{e.index}] OOB")
    # re-parse roundtrip
    try:
        pkg2 = load_package(bytes(pkg.data))
        if len(pkg2.exports) != len(pkg.exports):
            errs.append("reparse export count changed")
        if len(pkg2.names) != len(pkg.names):
            errs.append("reparse name count changed")
        for a, b in zip(pkg.exports, pkg2.exports):
            if a.serial_size != b.serial_size or a.serial_offset != b.serial_offset:
                errs.append(f"reparse mismatch export[{a.index}]")
                break
    except Exception as ex:
        errs.append(f"reparse failed: {ex}")
    return errs


if __name__ == "__main__":
    from pathlib import Path

    p = Path(
        r"c:\Users\23625\Desktop\BGOTYECNv1.0fix\_knoxx_echo_backup"
        r"\wav_batch_backup\DLC3_VO_Narrative_WAV.upk.bak"
    )
    pkg = load_package(p.read_bytes())
    print(f"ver={pkg.file_ver}/{pkg.lic_ver} names={len(pkg.names)} exports={len(pkg.exports)}")
    print("validate:", validate_package(pkg) or "OK")
    i = bytes(pkg.data).find(b"You're late.  We must speak immediately")
    e = find_export_for_offset(pkg, i)
    print("Athena @", i, "export", e.index, pkg.name_str(e.name_index), e.name_number)
    # smoke insert
    insert_bytes(pkg, i + 263, b"\x00")
    print("after insert size", len(pkg.data), "validate:", validate_package(pkg) or "OK")
    e2 = find_export_for_offset(pkg, i)
    print("owner size", e2.serial_size, "was", e.serial_size)
