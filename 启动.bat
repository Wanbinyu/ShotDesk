@echo off
chcp 65001 >nul
cd /d "%~dp0"
python app.py
if errorlevel 1 (
  echo 启动失败。需要已安装的 Python 3.11，以及 ffmpeg。
  pause
)
