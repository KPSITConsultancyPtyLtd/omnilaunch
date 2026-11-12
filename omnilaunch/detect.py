"""Detect which platform an app was built for by inspecting its bytes."""

from __future__ import annotations

import os
import plistlib
import struct
from dataclasses import dataclass
from enum import Enum


class Platform(str, Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    SCRIPT = "script"
    ARCHIVE = "archive"    # .dmg / .zip etc: needs extracting before it can run
    UNKNOWN = "unknown"


@dataclass
class AppInfo:
    path: str            # what the user gave us
    executable: str      # the actual file to run
    platform: Platform
    arch: str = "unknown"
    kind: str = ""       # e.g. "ELF executable", "PE32+ (GUI)", "Mach-O universal"
    archs: tuple = ()    # all architectures in a universal (fat) Mac binary
    interpreter: str = ""  # shebang line for scripts, e.g. "/usr/bin/env python3"


ELF_MACHINES = {0x03: "x86", 0x3E: "x86_64", 0x28: "arm", 0xB7: "arm64", 0xF3: "riscv"}
PE_MACHINES = {0x14C: "x86", 0x8664: "x86_64", 0x1C0: "arm", 0x1C4: "arm", 0xAA64: "arm64"}
MACHO_CPUS = {7: "x86", 0x01000007: "x86_64", 12: "arm", 0x0100000C: "arm64"}

MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce": ">", b"\xce\xfa\xed\xfe": "<",   # 32-bit
    b"\xfe\xed\xfa\xcf": ">", b"\xcf\xfa\xed\xfe": "<",   # 64-bit
}
FAT_MAGIC = b"\xca\xfe\xba\xbe"
FAT_MAGIC_64 = b"\xca\xfe\xba\xbf"
WINDOWS_EXTS = (".msi", ".bat", ".cmd", ".lnk")
ARCHIVE_EXTS = (".dmg", ".pkg", ".zip", ".7z", ".tar.gz", ".tgz", ".tar.xz")


def detect(path: str) -> AppInfo:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    # macOS .app bundles are directories; find the real binary via Info.plist.
    if os.path.isdir(path):
        if path.rstrip("/").endswith(".app"):
            exe = _macos_bundle_executable(path)
            if not exe:
                return AppInfo(path, path, Platform.MACOS, kind="app bundle (no binary found)")
            info = detect(exe)
            info.path = path
            if info.platform == Platform.SCRIPT:   # some bundles launch via a shell script
                info.platform = Platform.MACOS
            return info
        return AppInfo(path, path, Platform.UNKNOWN, kind="directory")

    try:
        with open(path, "rb") as f:
            head = f.read(65536)  # large enough for PE headers behind big DOS stubs
    except OSError as e:
        return AppInfo(path, path, Platform.UNKNOWN, kind=f"unreadable ({e.strerror})")

    lower = path.lower()
    try:
        if head.startswith(b"\x7fELF"):
            return _elf(path, head)
        if head.startswith(b"MZ"):
            return _pe(path, head)
        if head[:4] in MACHO_MAGICS:
            return _macho(path, head)
        if head[:4] in (FAT_MAGIC, FAT_MAGIC_64) and _looks_like_fat_macho(head):
            return _fat_macho(path, head)
    except (struct.error, IndexError):
        return AppInfo(path, path, Platform.UNKNOWN, kind="truncated or corrupt binary")

    if head.startswith(b"#!"):
        line = head.split(b"\n", 1)[0][2:].decode(errors="replace").strip()
        return AppInfo(path, path, Platform.SCRIPT, kind="script", interpreter=line)
    if lower.endswith(WINDOWS_EXTS):
        return AppInfo(path, path, Platform.WINDOWS, kind=os.path.splitext(lower)[1].lstrip(".").upper())
    if lower.endswith(ARCHIVE_EXTS) or head.startswith(b"PK\x03\x04"):
        ext = next((e for e in ARCHIVE_EXTS if lower.endswith(e)), ".zip")
        return AppInfo(path, path, Platform.ARCHIVE, kind=ext.lstrip(".").upper())
    return AppInfo(path, path, Platform.UNKNOWN, kind="unrecognised format")


def _elf(path: str, head: bytes) -> AppInfo:
    if len(head) < 20:
        raise IndexError("ELF header too short")
    endian = "<" if head[5] == 1 else ">"
    etype, machine = struct.unpack_from(endian + "HH", head, 16)
    kind = {2: "ELF executable", 3: "ELF shared object/PIE"}.get(etype, "ELF")
    # AppImages are ELF files with an "AI" marker at offset 8.
    if head[8:10] == b"AI":
        kind = "AppImage"
    return AppInfo(path, path, Platform.LINUX, ELF_MACHINES.get(machine, hex(machine)), kind)


def _pe(path: str, head: bytes) -> AppInfo:
    if len(head) < 0x40:
        return AppInfo(path, path, Platform.WINDOWS, kind="DOS/MZ executable")
    pe_off = struct.unpack_from("<I", head, 0x3C)[0]
    if pe_off + 24 + 70 > len(head) or head[pe_off:pe_off + 4] != b"PE\0\0":
        return AppInfo(path, path, Platform.WINDOWS, kind="DOS/MZ executable")
    machine = struct.unpack_from("<H", head, pe_off + 4)[0]
    characteristics = struct.unpack_from("<H", head, pe_off + 22)[0]
    opt = pe_off + 24
    magic = struct.unpack_from("<H", head, opt)[0]
    subsystem = struct.unpack_from("<H", head, opt + 68)[0]
    kind = ("PE32+" if magic == 0x20B else "PE32")
    if characteristics & 0x2000:
        kind += " DLL"
    else:
        kind += {2: " (GUI)", 3: " (console)"}.get(subsystem, "")
    return AppInfo(path, path, Platform.WINDOWS, PE_MACHINES.get(machine, hex(machine)), kind)


def _macho(path: str, head: bytes) -> AppInfo:
    endian = MACHO_MAGICS[head[:4]]
    cpu = struct.unpack_from(endian + "i", head, 4)[0] & 0xFFFFFFFF
    arch = MACHO_CPUS.get(cpu, hex(cpu))
    return AppInfo(path, path, Platform.MACOS, arch, "Mach-O", archs=(arch,))


def _fat_macho(path: str, head: bytes) -> AppInfo:
    n = struct.unpack_from(">I", head, 4)[0]
    entry = 32 if head[:4] == FAT_MAGIC_64 else 20
    archs = []
    for i in range(n):
        off = 8 + i * entry
        if off + 4 > len(head):
            break
        cpu = struct.unpack_from(">I", head, off)[0]
        archs.append(MACHO_CPUS.get(cpu, hex(cpu)))
    return AppInfo(path, path, Platform.MACOS, "universal", "Mach-O universal", archs=tuple(archs))


def _looks_like_fat_macho(head: bytes) -> bool:
    # Java .class files share the CAFEBABE magic; fat Mach-O has a small arch count.
    if len(head) < 8:
        return False
    n = struct.unpack_from(">I", head, 4)[0]
    return 0 < n < 20


def _macos_bundle_executable(bundle: str) -> str | None:
    plist = os.path.join(bundle, "Contents", "Info.plist")
    name = None
    if os.path.exists(plist):
        try:
            with open(plist, "rb") as f:
                name = plistlib.load(f).get("CFBundleExecutable")
        except Exception:
            pass
    macos_dir = os.path.join(bundle, "Contents", "MacOS")
    if name and os.path.exists(os.path.join(macos_dir, name)):
        return os.path.join(macos_dir, name)
    if os.path.isdir(macos_dir):
        files = sorted(f for f in os.listdir(macos_dir)
                       if os.path.isfile(os.path.join(macos_dir, f)))
        if files:
            return os.path.join(macos_dir, files[0])
    return None
