@echo off
chcp 65001 >nul
setlocal

cd /d "%~dp0"

set "PY=python"
if exist ".venv\Scripts\python.exe" set "PY=.venv\Scripts\python.exe"

echo ────────────────────────────────────────────
echo  ATB 打包（PyInstaller onefile）
echo  解释器: %PY%
echo ────────────────────────────────────────────

if not exist "atb.ico" (
    echo [i] 未找到 atb.ico，正在生成图标...
    %PY% make_icon.py
    if errorlevel 1 (
        echo [x] 图标生成失败，请检查 Pillow 是否安装。
        exit /b 1
    )
)

%PY% -c "import PyInstaller" >nul 2>nul
if errorlevel 1 (
    echo [x] 未找到 PyInstaller。
    echo     请先执行: %PY% -m pip install pyinstaller
    exit /b 1
)

%PY% -m PyInstaller ATB.spec --noconfirm --clean
if errorlevel 1 (
    echo.
    echo [x] 构建失败，请查看上方日志。
    exit /b 1
)

echo.
echo [✓] 构建完成
for %%F in (dist\ATB.exe) do (
    echo     产物: %%~fF
    echo     体积: %%~zF 字节
)
echo.
echo 直接运行: dist\ATB.exe read "某文件" --md
echo MCP 模式: dist\ATB.exe mcp
endlocal
