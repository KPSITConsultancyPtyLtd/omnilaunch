@echo off
rem Run OmniLaunch on Windows straight from a git checkout, without installing it.
where py >nul 2>nul
if not errorlevel 1 (
  py -3 "%~dp0omnilaunch" %*
) else (
  python "%~dp0omnilaunch" %*
)
exit /b %errorlevel%
