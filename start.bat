@echo off
setlocal
pushd "%~dp0." || goto no_dir
set "VPY=%~dp0.venv\Scripts\python.exe"
set "PY="
set "OLD="

py -3 -c "import sys" >nul 2>&1
if %errorlevel% neq 0 goto try_python
py -3 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if %errorlevel% equ 0 goto use_py
for /f "delims=" %%v in ('py -3 -c "import platform; print(platform.python_version())"') do set "OLD=%%v"

:try_python
python -c "import sys" >nul 2>&1
if %errorlevel% neq 0 goto no_python
python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if %errorlevel% equ 0 goto use_python
for /f "delims=" %%v in ('python -c "import platform; print(platform.python_version())"') do set "OLD=%%v"
goto no_python

:use_py
set "PY=py -3"
goto venv

:use_python
set "PY=python"
goto venv

:venv
if not exist "%VPY%" goto make_venv
"%VPY%" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>&1
if %errorlevel% neq 0 goto make_venv
goto reqs

:make_venv
if exist ".venv" rmdir /s /q ".venv"
%PY% -m venv .venv
if %errorlevel% neq 0 goto no_venv
if not exist "%VPY%" goto no_venv

:reqs
"%VPY%" -c "import filecmp, sys; sys.exit(0 if filecmp.cmp('requirements.txt', '.venv/requirements.txt', shallow=False) else 1)" >nul 2>&1
if %errorlevel% equ 0 goto run
"%VPY%" -m pip install -r requirements.txt
if %errorlevel% neq 0 goto no_pip
copy /y "requirements.txt" ".venv\requirements.txt" >nul

:run
echo starting Mando
set "PYTHONUTF8=1"
"%VPY%" -m mirror
set "CODE=%ERRORLEVEL%"
if "%CODE%"=="0" goto done
if "%CODE%"=="-1073741510" goto done
pause

:done
popd
exit /b %CODE%

:no_python
if defined OLD goto old_python
echo python 3.10 or newer was not found, install it from https://www.python.org/downloads/ and tick add python to path
goto fail

:old_python
echo python %OLD% is too old, 3.10 or newer is needed
goto fail

:no_venv
echo could not create .venv
goto fail

:no_pip
echo pip install failed
goto fail

:no_dir
echo could not open the folder of start.bat
pause
exit /b 1

:fail
popd
pause
exit /b 1
