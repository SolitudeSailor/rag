@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title RAG - Environment Setup
pushd "%~dp0"
if errorlevel 1 goto directory_failed

echo [环境] 检查并安装项目依赖，不会启动测评。
if not exist "requirements.txt" goto files_missing
if not exist "scripts\project_runner.py" goto files_missing
if exist ".venv\Scripts\python.exe" goto check_environment
if exist ".venv" goto broken_environment

py -3 -c "import venv" >nul 2>&1
if not errorlevel 1 goto create_with_launcher
python -c "import venv" >nul 2>&1
if not errorlevel 1 goto create_with_python
echo [失败] 未找到可用的 Python 3，请先安装 Python，并勾选 Add Python to PATH。
goto failed

:create_with_launcher
echo [环境] 使用已有 Python 3 创建项目 .venv。
py -3 -m venv ".venv"
if errorlevel 1 goto creation_failed
goto check_environment

:create_with_python
echo [环境] 使用已有 Python 创建项目 .venv。
python -m venv ".venv"
if errorlevel 1 goto creation_failed
goto check_environment

:check_environment
".venv\Scripts\python.exe" --version
if errorlevel 1 goto broken_environment
".venv\Scripts\python.exe" -m pip --version >nul 2>&1
if not errorlevel 1 goto install_dependencies
echo [环境] 为项目 .venv 补齐 pip。
".venv\Scripts\python.exe" -m ensurepip --upgrade
if errorlevel 1 goto failed

:install_dependencies
".venv\Scripts\python.exe" -X utf8 -u "scripts\project_runner.py" --task setup
if errorlevel 1 goto failed
echo.
echo [成功] 项目环境和依赖检查通过，可以运行 run_30.py。
echo [配置] 首次使用请按 README.md 配置 .env 中的模型参数。
set "setup_result=0"
goto finished

:files_missing
echo [失败] 项目文件不完整，请完整解压，并将本文件放在 run_30.py 同一目录。
goto failed

:broken_environment
echo [失败] 现有 .venv 无法使用。请检查该环境，本脚本不会覆盖或删除它。
goto failed

:creation_failed
echo [失败] 创建 .venv 失败，请检查上方错误及项目目录的写入权限。

:failed
echo.
echo [未完成] 请查看上方报错；依赖下载失败时检查网络后重新双击本文件。
set "setup_result=1"

:finished
popd
echo.
pause
exit /b %setup_result%

:directory_failed
echo [失败] 无法进入项目目录，请先完整解压项目到可访问的位置。
pause
exit /b 1
