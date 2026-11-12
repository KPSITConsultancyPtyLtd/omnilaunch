# Testing OmniLaunch on Windows

About 15 minutes for steps 1–4. Step 5 needs WSL and is optional.
Use **Command Prompt** or **PowerShell**. When something doesn't match "Expect", copy the output.

## 1. Get the code

1. Install **Python 3.9 or newer** from https://www.python.org/downloads/ (tick
   "Add python.exe to PATH") or from the Microsoft Store.
2. Install **Git for Windows** from https://git-scm.com/download/win.
3. Then:

```bat
cd %USERPROFILE%
git clone https://github.com/KPSITConsultancyPtyLtd/omnilaunch
cd omnilaunch
python --version
```

Expect: Python 3.9 or newer. (In PowerShell, use `cd $HOME` instead of `cd %USERPROFILE%`.)

## 2. Run the automated tests

```bat
python -m unittest discover -s tests
```

Expect: `OK` at the end (about 65 tests; a few are skipped on Windows).

## 3. Health check

```bat
bin\omnilaunch doctor
```

Expect: `Windows on x86_64` (or `arm64`), then `[OK ] Windows apps`. Lines for WSL and
containers show `--` until you set them up; `Mac apps` always shows "not possible on Windows".

## 4. Windows apps (works with nothing extra installed)

```bat
bin\omnilaunch info C:\Windows\System32\notepad.exe
```
Expect: `Platform: windows`, `Arch: x86_64` (or `arm64`), `Format: PE32+ (GUI)`, `Runner: native`.

```bat
bin\omnilaunch run C:\Windows\System32\notepad.exe
```
Expect: Notepad opens.

```bat
bin\omnilaunch run C:\Windows\System32\notepad.exe --dry-run
bin\omnilaunch run C:\Windows\System32\cmd.exe /c echo hello from omnilaunch
```
Expect: the first prints the command without opening Notepad; the second prints `hello from omnilaunch`.

Error handling (no crashes, a plain explanation each time):

```bat
echo hi > %TEMP%\fake.dmg
bin\omnilaunch run %TEMP%\fake.dmg
bin\omnilaunch run C:\does-not-exist.exe
bin\omnilaunch run C:\Windows\System32\kernel32.dll
```
Expect: "is an archive (DMG)", then "no such file", then "is a Windows library (DLL)".

## 5. Linux apps through WSL (optional)

1. If `doctor` showed `--` for WSL: open **Terminal as administrator**, run `wsl --install`,
   restart, then finish the Ubuntu setup (pick a username and password).
2. `bin\omnilaunch doctor` should now show `[OK ] Linux apps (WSL)` and list Ubuntu.
3. Copy a real Linux program out of WSL to test with:

```bat
mkdir %USERPROFILE%\omni-test
wsl cp /bin/ls /mnt/c/Users/%USERNAME%/omni-test/ls-linux
bin\omnilaunch info %USERPROFILE%\omni-test\ls-linux
bin\omnilaunch run %USERPROFILE%\omni-test\ls-linux -la
```

Expect: `info` says `linux`, `ELF`; `run` prints a directory listing of `omni-test`.

4. **Windows 11 only:** a graphical Linux app (opens in its own window through WSLg):

```bat
wsl sudo apt install -y x11-apps
wsl cp /usr/bin/xeyes /mnt/c/Users/%USERNAME%/omni-test/xeyes
bin\omnilaunch run %USERPROFILE%\omni-test\xeyes
```

Expect: a pair of eyes following your mouse.

5. Optional, double-click support for Linux `.AppImage` files:

```bat
bin\omnilaunch integrate
```

Expect: "Done. Double-clicking .AppImage files now opens them with OmniLaunch."
Undo it with `bin\omnilaunch integrate --remove`.

## What to send back

- The output of `bin\omnilaunch doctor`
- Which steps matched "Expect" and which didn't, with the output of any that didn't
