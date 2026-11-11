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
    UNKNOWN = "unknown"


@dataclass
class AppInfo:
    path: str            # what the user gave us
    executable: str      # the actual file to run
    platform: Platform
    arch: str = "unknown"
    kind: str = ""       # e.g. "ELF executable", "PE32+ (GUI)", "Mach-O universal"


ELF_MACHINES = {0x03: "x86", 0x3E: "x86_64", 0x28: "arm", 0xB7: "arm64", 0xF3: "riscv"}
PE_MACHINES = {0x14C: "x86", 0x8664: "x86_64", 0x1C0: "arm", 0xAA64: "arm64"}
MACHO_CPUS = {7: "x86", 0x01000007: "x86_64", 12: "arm", 0x0100000C: "arm64"}

MACHO_MAGICS = {
    b"\xfe\xed\xfa\xce": ">", b"\xce\xfa\xed\xfe": "<",   # 32-bit
    b"\xfe\xed\xfa\xcf": ">", b"\xcf\xfa\xed\xfe": "<",   # 64-bit
}
FAT_MAGIC = b"\xca\xfe\xba\xbe"


def detect(path: str) -> AppInfo:
    path = os.path.abspath(os.path.expanduser(path))
    if not os.path.exists(path):
        raise FileNotFoundError(path)

    # macOS .app bundles are directories; find the real binary via Info.plist.
    if os.path.isdir(path):
        if path.rstrip("/").endswith(".app"):
            exe = _macos_bundle_executable(path)
            info = detect(exe) if exe else AppInfo(path, path, Platform.MACOS, kind="app bundle (no binary)")
            info.path = path
            return info
        return AppInfo(path, path, Platform.UNKNOWN, kind="directory")

    with open(path, "rb") as f:
        head = f.read(4096)

    if head.startswith(b"\x7fELF"):
        return _elf(path, head)
    if head.startswith(b"MZ"):
        return _pe(path, head)
    if head[:4] in MACHO_MAGICS:
        return _macho(path, head)
    if head[:4] == FAT_MAGIC and _looks_like_fat_macho(head):
        return AppInfo(path, path, Platform.MACOS, "universal", "Mach-O universal")
    if head.startswith(b"#!"):
        return AppInfo(path, path, Platform.SCRIPT, kind=head.split(b"\n", 1)[0][2:].decode(errors="replace").strip())
    if path.lower().endswith((".msi", ".bat", ".cmd")):
        return AppInfo(path, path, Platform.WINDOWS, kind=os.path.splitext(path)[1].lstrip(".").upper())
    return AppInfo(path, path, Platform.UNKNOWN, kind="unrecognised format")


def _elf(path: str, head: bytes) -> AppInfo:
    endian = "<" if head[5] == 1 else ">"
    machine = struct.unpack_from(endian + "H", head, 18)[0]
    etype = struct.unpack_from(endian + "H", head, 16)[0]
    kind = {2: "ELF executable", 3: "ELF shared object/PIE"}.get(etype, "ELF")
    return AppInfo(path, path, Platform.LINUX, ELF_MACHINES.get(machine, hex(machine)), kind)


def _pe(path: str, head: bytes) -> AppInfo:
    try:
        pe_off = struct.unpack_from("<I", head, 0x3C)[0]
        if head[pe_off:pe_off + 4] != b"PE\0\0":
            return AppInfo(path, path, Platform.WINDOWS, kind="DOS/MZ executable")
        machine = struct.unpack_from("<H", head, pe_off + 4)[0]
        opt = pe_off + 24
        magic = struct.unpack_from("<H", head, opt)[0]
        subsystem = struct.unpack_from("<H", head, opt + 68)[0]
        kind = ("PE32+" if magic == 0x20B else "PE32") + {2: " (GUI)", 3: " (console)"}.get(subsystem, "")
        return AppInfo(path, path, Platform.WINDOWS, PE_MACHINES.get(machine, hex(machine)), kind)
    except struct.error:
        return AppInfo(path, path, Platform.WINDOWS, kind="truncated PE")


def _macho(path: str, head: bytes) -> AppInfo:
    endian = MACHO_MAGICS[head[:4]]
    cpu = struct.unpack_from(endian + "i", head, 4)[0] & 0xFFFFFFFF
    return AppInfo(path, path, Platform.MACOS, MACHO_CPUS.get(cpu, hex(cpu)), "Mach-O")


def _looks_like_fat_macho(head: bytes) -> bool:
    # Java .class files share the CAFEBABE magic; fat Mach-O has a small arch count.
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
        files = sorted(os.listdir(macos_dir))
        if files:
            return os.path.join(macos_dir, files[0])
    return None
