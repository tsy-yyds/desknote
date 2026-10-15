@echo off
chcp 65001 >nul
echo ========================================
echo   悬浮便签 exe 打包工具
echo ========================================
echo.
cd /d "%~dp0"
echo 正在打包，请耐心等待（约1-3分钟）...
"C:\Users\Administrator\AppData\Local\Programs\Python\Python314\python.exe" -m PyInstaller --noconfirm --onefile --windowed --name "悬浮便签" --icon "%~dp0icon.ico" "%~dp0desknote.py"
echo.
if exist "%~dp0dist\悬浮便签.exe" (
    echo 打包成功！exe 位于 dist\悬浮便签.exe
    echo 双击即可运行，无需 Python 环境，无黑窗口。
) else (
    echo 打包失败，请检查上方报错信息。
)
pause