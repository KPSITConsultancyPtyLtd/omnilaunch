"""Detect which platform an app was built for by inspecting its bytes."""

from __future__ import annotations

import mmap
import os
import re
import plistlib
import struct
import zipfile
from dataclasses import dataclass
from enum import Enum


class Platform(str, Enum):
    LINUX = "linux"
    WINDOWS = "windows"
    MACOS = "macos"
    ANDROID = "android"
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
    gui: bool = False      # Linux apps: does it open windows (links X11/GTK/Qt/Wayland/SDL)?
    package: str = ""      # Android apps: package name, e.g. "org.mozilla.firefox"


# Libraries only games use (Steam, Epic, Unity, controllers). Direct3D alone isn't a sign: ordinary apps
# such as VS Code and Discord use it too.
GAME_LIBRARIES = re.compile(
    rb"(steam_api(64)?|eossdk-win(32|64)-shipping|unityplayer|gameoverlayrenderer(64)?|"
    rb"xinput1_[34]|xinput9_1_0|gameinput|galaxy(64)?|discord_game_sdk)\.dll", re.IGNORECASE)
GAME_SIBLINGS = ("unityplayer.dll", "steam_api64.dll", "steam_api.dll", "eossdk-win64-shipping.dll",
                 "galaxy64.dll", "steam_appid.txt")


def looks_like_game(path: str) -> bool:
    """A Windows game rather than an ordinary app or installer: it links game libraries, or sits next to
    a game engine's files (Unity's UnityPlayer.dll and <name>_Data folder, Unreal's Engine folder)."""
    folder = os.path.dirname(os.path.abspath(path))
    try:
        names = {n.lower() for n in os.listdir(folder)}
    except OSError:
        names = set()
    if names & set(GAME_SIBLINGS) or "engine" in names and any(n.endswith(".uproject") or n == "binaries" for n in names):
        return True
    stem = os.path.splitext(os.path.basename(path))[0].lower()
    if f"{stem}_data" in names:
        return True                                   # Unity games keep their assets in <name>_Data
    try:
        with open(path, "rb") as f:
            if os.fstat(f.fileno()).st_size == 0:
                return False
            data = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            try:
                return GAME_LIBRARIES.search(data) is not None
            finally:
                data.close()
    except (OSError, ValueError):
        return False


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
        if path.rstrip("/\\").endswith(".app"):
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
    if head.startswith(b"PK\x03\x04") or lower.endswith((".apk", ".xapk", ".apks", ".aab")):
        android = _android(path, lower)
        if android:
            return android
    if lower.endswith(ARCHIVE_EXTS) or head.startswith(b"PK\x03\x04"):
        ext = next((e for e in ARCHIVE_EXTS if lower.endswith(e)), ".zip")
        return AppInfo(path, path, Platform.ARCHIVE, kind=ext.lstrip(".").upper())
    return AppInfo(path, path, Platform.UNKNOWN, kind="unrecognised format")


MAX_MANIFEST_BYTES = 8 * 1024 * 1024
ANDROID_ABIS = {"arm64-v8a": "arm64", "armeabi-v7a": "arm", "armeabi": "arm", "x86_64": "x86_64", "x86": "x86"}


def _android(path: str, lower: str) -> AppInfo | None:
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if lower.endswith((".xapk", ".apks", ".aab")) or "AndroidManifest.xml" not in names:
                if lower.endswith((".xapk", ".apks", ".aab")):
                    return AppInfo(path, path, Platform.ANDROID, kind=f"{lower.rsplit('.', 1)[1].upper()} bundle")
                return None
            # A compressed manifest can claim to expand to gigabytes (a "zip bomb"); real ones are small.
            if z.getinfo("AndroidManifest.xml").file_size > MAX_MANIFEST_BYTES:
                return AppInfo(path, path, Platform.ANDROID, kind="APK")
            with z.open("AndroidManifest.xml") as f:
                manifest = f.read(MAX_MANIFEST_BYTES + 1)
            if len(manifest) > MAX_MANIFEST_BYTES:
                return AppInfo(path, path, Platform.ANDROID, kind="APK")
    except (zipfile.BadZipFile, OSError, KeyError, RuntimeError, NotImplementedError):
        return None
    abis = sorted({ANDROID_ABIS[n.split("/")[1]] for n in names
                   if n.startswith("lib/") and n.count("/") >= 2 and n.split("/")[1] in ANDROID_ABIS})
    info = AppInfo(path, path, Platform.ANDROID, "any" if not abis else (abis[0] if len(abis) == 1 else "multi"),
                   "APK", archs=tuple(abis))
    try:
        info.package = axml_manifest_package(manifest)
    except (struct.error, IndexError, ValueError):
        pass
    return info


def axml_manifest_package(data: bytes) -> str:
    """Read the package name from a compiled (binary XML) AndroidManifest.xml."""
    if struct.unpack_from("<H", data, 0)[0] != 0x0003:
        raise ValueError("not binary XML")
    strings: list[str] = []
    pos = struct.unpack_from("<H", data, 2)[0]
    while pos + 8 <= len(data):
        ctype, header_size, size = struct.unpack_from("<HHI", data, pos)
        if size < 8:
            break
        if ctype == 0x0001:                       # string pool
            strings = _axml_strings(data, pos)
        elif ctype == 0x0102:                     # start element
            ext = pos + header_size
            _ns, name, attr_start, attr_size, count = struct.unpack_from("<IIHHH", data, ext)
            if name < len(strings) and strings[name] == "manifest":
                for i in range(count):
                    a = ext + attr_start + i * attr_size
                    _ans, aname, raw = struct.unpack_from("<III", data, a)
                    if aname < len(strings) and strings[aname] == "package" and raw < len(strings):
                        return strings[raw]
                raise ValueError("manifest has no package attribute")
        pos += size
    raise ValueError("no manifest element")


def _axml_strings(data: bytes, chunk: int) -> list[str]:
    header_size = struct.unpack_from("<H", data, chunk + 2)[0]
    count, _styles, flags, strings_start = struct.unpack_from("<IIII", data, chunk + 8)
    if count > len(data) // 4:
        raise ValueError("string pool larger than the file")
    utf8 = bool(flags & 0x100)
    offsets = struct.unpack_from(f"<{count}I", data, chunk + header_size)
    base = chunk + strings_start
    out = []
    for off in offsets:
        p = base + off
        if utf8:
            p += 2 if data[p] & 0x80 else 1                  # UTF-16 length (unused)
            n = data[p]
            if n & 0x80:
                n = ((n & 0x7F) << 8) | data[p + 1]
                p += 2
            else:
                p += 1
            out.append(data[p:p + n].decode("utf-8", errors="replace"))
        else:
            n = struct.unpack_from("<H", data, p)[0]
            p += 2
            if n & 0x8000:
                n = ((n & 0x7FFF) << 16) | struct.unpack_from("<H", data, p)[0]
                p += 2
            out.append(data[p:p + 2 * n].decode("utf-16-le", errors="replace"))
    return out


def _elf(path: str, head: bytes) -> AppInfo:
    if len(head) < 20:
        raise IndexError("ELF header too short")
    endian = "<" if head[5] == 1 else ">"
    etype, machine = struct.unpack_from(endian + "HH", head, 16)
    kind = {2: "ELF executable", 3: "ELF shared object/PIE"}.get(etype, "ELF")
    # AppImages are ELF files with an "AI" marker at offset 8.
    if head[8:10] == b"AI":
        kind = "AppImage"
    gui = kind == "AppImage" or _links_gui_libraries(path)
    return AppInfo(path, path, Platform.LINUX, ELF_MACHINES.get(machine, hex(machine)), kind, gui=gui)


GUI_LIBRARIES = (b"libX11.so", b"libxcb.so", b"libgtk-", b"libQt5", b"libQt6", b"libwayland-client",
                 b"libSDL2", b"libGL.so", b"libEGL.so", b"libvulkan.so")


def _links_gui_libraries(path: str, limit: int = 8 * 1024 * 1024) -> bool:
    """Cheap check: GUI apps name their windowing libraries in the binary's dynamic section."""
    try:
        with open(path, "rb") as f:
            data = f.read(limit)
    except OSError:
        return False
    return any(lib in data for lib in GUI_LIBRARIES)


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
