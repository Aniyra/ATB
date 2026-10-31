@echo off
rem ATB 便捷启动器：优先使用项目内 .venv，回退到系统 Python
setlocal
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    ".venv\Scripts\python.exe" main.py %*
) else (
    python main.py %*
)
endlocal
