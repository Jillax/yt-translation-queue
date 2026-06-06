@echo off
chcp 65001 >nul
echo.
echo ================================================
echo   YT Translation Queue - YouTube 翻译待译库
echo ================================================
echo.

:: 检查 Python
python --version >nul 2>&1
if errorlevel 1 (
    echo [错误] 未找到 Python，请先安装 Python 3.8+
    pause
    exit /b 1
)

:: 检查并安装依赖
echo [1/2] 检查依赖...
pip show flask >nul 2>&1
if errorlevel 1 (
    echo      正在安装依赖...
    pip install -r requirements.txt
    echo.
)

:: 启动应用
echo [2/2] 启动应用...
echo.
echo   访问地址: http://127.0.0.1:5000
echo   按 Ctrl+C 停止服务
echo.
echo ================================================
echo.

python app.py
pause