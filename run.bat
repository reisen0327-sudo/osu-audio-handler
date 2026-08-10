@echo off
setlocal
cd /d "%~dp0"

rem 优先使用打包好的 exe(免装 Python)
if exist "dist\osu-audio-handler.exe" (
    start "" "dist\osu-audio-handler.exe"
    goto :eof
)

rem 回退:直接运行 Python 源码(需已安装 Python 3)
where python >nul 2>nul
if %errorlevel%==0 (
    python osu_audio_handler.py
    goto :eof
)

where py >nul 2>nul
if %errorlevel%==0 (
    py -3 osu_audio_handler.py
    goto :eof
)

echo [错误] 未找到 Python,且未找到打包好的 exe。
echo 请安装 Python 3:https://www.python.org/downloads/ (勾选 Add python.exe to PATH)
pause
