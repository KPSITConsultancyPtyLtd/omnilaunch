"""Give launchers the real app's icon, read from the Windows program's own resources.

A Windows .exe stores its icons in its resource section: an RT_GROUP_ICON entry lists the sizes,
and each size is an RT_ICON entry (a PNG, or a bitmap without a file header). This rebuilds the
first icon group as a standard .ico file, which macOS (sips) and Linux desktops can read.
"""

from __future__ import annotations

import os
import shutil
import struct
import subprocess
import tempfile

RT_ICON, RT_GROUP_ICON = 3, 14


def _sections(data: bytes):
    pe = struct.unpack_from("<I", data, 0x3C)[0]
    if data[pe:pe + 4] != b"PE\0\0":
        raise ValueError("not a PE file")
    count = struct.unpack_from("<H", data, pe + 6)[0]
    opt_size = struct.unpack_from("<H", data, pe + 20)[0]
    opt = pe + 24
    magic = struct.unpack_from("<H", data, opt)[0]
    dirs = opt + (112 if magic == 0x20B else 96)
    rsrc_rva, rsrc_size = struct.unpack_from("<II", data, dirs + 2 * 8)
    table = opt + opt_size
    secs = []
    for i in range(count):
        s = table + i * 40
        vsize, vaddr, rawsize, rawptr = struct.unpack_from("<IIII", data, s + 8)
        secs.append((vaddr, max(vsize, rawsize), rawptr))
    return rsrc_rva, rsrc_size, secs


def _rva_to_offset(rva: int, secs) -> int:
    for vaddr, size, rawptr in secs:
        if vaddr <= rva < vaddr + size:
            return rva - vaddr + rawptr
    raise ValueError(f"RVA {rva:#x} is outside every section")


def _entries(data: bytes, base: int, offset: int):
    """(id or None for named entries, offset of child, is_directory) for one resource directory."""
    named, ids = struct.unpack_from("<HH", data, base + offset + 12)
    out = []
    for i in range(named + ids):
        name, child = struct.unpack_from("<II", data, base + offset + 16 + 8 * i)
        out.append((None if name & 0x80000000 else name, child & 0x7FFFFFFF, bool(child & 0x80000000)))
    return out


def _leaf(data: bytes, base: int, offset: int, secs) -> bytes:
    """Follow the first entry down to a data entry and return its bytes."""
    while True:
        entries = _entries(data, base, offset)
        if not entries:
            raise ValueError("empty resource directory")
        _, child, is_dir = entries[0]
        if not is_dir:
            rva, size = struct.unpack_from("<II", data, base + child)
            start = _rva_to_offset(rva, secs)
            return data[start:start + size]
        offset = child


def extract_ico(exe_path: str) -> bytes | None:
    """The program's main icon as .ico bytes, or None if it has none."""
    try:
        with open(exe_path, "rb") as f:
            data = f.read()
        rsrc_rva, rsrc_size, secs = _sections(data)
        if not rsrc_rva:
            return None
        base = _rva_to_offset(rsrc_rva, secs)
        types = {tid: child for tid, child, is_dir in _entries(data, base, 0) if is_dir and tid is not None}
        if RT_GROUP_ICON not in types or RT_ICON not in types:
            return None
        group = _leaf(data, base, types[RT_GROUP_ICON], secs)
        icons = {}
        for iid, child, is_dir in _entries(data, base, types[RT_ICON]):
            if iid is not None:
                icons[iid] = _leaf(data, base, child, secs) if is_dir else None
        _, kind, count = struct.unpack_from("<HHH", group, 0)
        if kind != 1 or count == 0:
            return None
        images, entries = [], []
        for i in range(count):
            w, h, colors, _r, planes, bits, size, iid = struct.unpack_from("<BBBBHHIH", group, 6 + 14 * i)
            img = icons.get(iid)
            if img:
                entries.append((w, h, colors, planes, bits))
                images.append(img)
        if not images:
            return None
        header = struct.pack("<HHH", 0, 1, len(images))
        offset = 6 + 16 * len(images)
        dirs = b""
        for (w, h, colors, planes, bits), img in zip(entries, images):
            dirs += struct.pack("<BBBBHHII", w, h, colors, 0, planes, bits, len(img), offset)
            offset += len(img)
        return header + dirs + b"".join(images)
    except (OSError, ValueError, struct.error, IndexError):
        return None


def largest_png(ico: bytes) -> bytes | None:
    """Modern icons store their big sizes as PNG inside the .ico; return the largest one."""
    try:
        count = struct.unpack_from("<H", ico, 4)[0]
        best = None
        for i in range(count):
            w, h, _c, _r, _p, _b, size, off = struct.unpack_from("<BBBBHHII", ico, 6 + 16 * i)
            img = ico[off:off + size]
            px = (w or 256) * (h or 256)
            if img.startswith(b"\x89PNG") and (best is None or px > best[0]):
                best = (px, img)
        return best[1] if best else None
    except struct.error:
        return None


def mac_set_app_icon(app: str, ico: bytes) -> bool:
    """Replace an applet's icon with the program's icon (converted to .icns with macOS's sips)."""
    tmp = tempfile.mkdtemp(prefix="omnilaunch-icon-")
    try:
        src = os.path.join(tmp, "icon.ico")
        with open(src, "wb") as f:
            f.write(ico)
        png = os.path.join(tmp, "icon.png")
        icns = os.path.join(tmp, "applet.icns")
        r1 = subprocess.run(["/usr/bin/sips", "-s", "format", "png", src, "--out", png], capture_output=True)
        r2 = subprocess.run(["/usr/bin/sips", "-z", "512", "512", png], capture_output=True) if r1.returncode == 0 else r1
        r3 = subprocess.run(["/usr/bin/sips", "-s", "format", "icns", png, "--out", icns], capture_output=True) \
            if r2.returncode == 0 else r2
        if r3.returncode != 0 or not os.path.exists(icns):
            return False
        dest = os.path.join(app, "Contents", "Resources", "applet.icns")
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        shutil.copyfile(icns, dest)
        os.utime(app, None)   # nudge Finder to show the new icon
        return True
    except OSError:
        return False
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def save_icon_file(ico: bytes, folder: str, name: str) -> str | None:
    """Linux: save the icon (PNG when the program has one, else .ico) and return its path."""
    try:
        os.makedirs(folder, exist_ok=True)
        png = largest_png(ico)
        path = os.path.join(folder, name + (".png" if png else ".ico"))
        with open(path, "wb") as f:
            f.write(png or ico)
        return path
    except OSError:
        return None
