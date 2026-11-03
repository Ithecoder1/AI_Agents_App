@echo off
setlocal
if exist "%~dp0project\backend\.venv\Scripts\python.exe" (
    "%~dp0project\backend\.venv\Scripts\python.exe" "%~dp0antigravity.py" %*
) else (
    python "%~dp0antigravity.py" %*
)
endlocal
