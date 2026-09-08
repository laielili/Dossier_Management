@echo off
setlocal

REM ===========================================================================
REM  Dossier_Management - one-click bootstrap
REM
REM  What this script does:
REM    1. locate a Python interpreter (installs a project-local one from K:\Software\Python if missing)
REM    2. create a project-local virtualenv (venv/) and install the
REM       dependencies listed in requirements.txt into it
REM    3. start `main.py serve` (using the venv interpreter) and open the UI
REM
REM  COUPLING CONTRACT - this script depends on the project through
REM  exactly TWO things:
REM      a) the entry point file name "main.py"
REM      b) that file exposing the "serve" subcommand
REM
REM  Everything else is delegated:
REM    - dependencies come solely from requirements.txt; no package or
REM      import names are duplicated in this file, so upgrading or adding
REM      a dependency never requires editing anything here
REM    - the virtualenv is created once and re-used; pip install is
REM      idempotent, so re-running this script stays fast
REM    - the venv keeps the project's packages isolated from the user's
REM      global Python, so this script never pollutes their system
REM  Changes inside src/, static/, classify/, queries/ and prompt/ never
REM  require touching this file either. The only things you would ever
REM  change here are PORT_CANDIDATES below, or the entry point / subcommand names if
REM  those are ever renamed.
REM ===========================================================================

set "PORT_CANDIDATES=8000 8001 8080 8888 9000"
set "RESUME_FLAG=__after_python_install__"
set "VENV_DIR=%~dp0venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
REM  Path to the bundled offline Python installer. It ships inside the project
REM  folder (src\python-3.12.6-amd64.exe) so no drive letter / network is needed.
set "PY_INSTALLER=%~dp0src\python-3.12.6-amd64.exe"

if /i "%~1"=="%RESUME_FLAG%" goto :locate_python

echo.
echo ==== Dossier_Management bootstrap ====
echo.

REM ---------------------------------------------------------------------------
REM 1. Locate the project-local Python interpreter (installs one from the bundled installer if missing)
REM ---------------------------------------------------------------------------

:locate_python
echo [1/3] Looking for Python ...
set "BASE_PY="

if exist "%~dp0python\python.exe" (
    set "BASE_PY=%~dp0python\python.exe"
    goto :python_found
)

goto :python_missing

:python_found
"%BASE_PY%" --version
goto :setup_venv

:python_missing
echo [!] No usable Python interpreter found.
echo.
if /i "%~1"=="%RESUME_FLAG%" (
    echo [!] The automatic installation did not produce a usable Python.
    echo     Install it manually from https://www.python.org/downloads/
    echo     ^(tick "Add python.exe to PATH" during setup^), then run start.bat again.
    pause
    exit /b 1
)

set "PY_DIR=%~dp0python"
echo [i] Looking for Python installer at: %PY_INSTALLER%
if exist "%PY_INSTALLER%" (
    echo [i] Installing Python 3.12.6 into %PY_DIR% ...
    "%PY_INSTALLER%" /quiet InstallAllUsers=0 PrependPath=0 TargetDir="%PY_DIR%"
    set "BASE_PY=%PY_DIR%\python.exe"
    goto :setup_venv
)

echo [!] Python installer not found at:
echo       %PY_INSTALLER%
echo     Please copy python-3.12.6-amd64.exe into the project's src\ folder, then run start.bat again.
echo     Or install Python 3.12 manually from https://www.python.org/downloads/
echo     ^(choose "Install just for me", needs no administrator rights^).
pause
exit /b 1

REM ---------------------------------------------------------------------------
REM 2. Create the virtualenv (once) and install dependencies into it
REM ---------------------------------------------------------------------------

:setup_venv
echo.
echo [2/3] Preparing virtual environment and installing dependencies ...

if not exist "%VENV_PY%" (
    echo [i] Creating virtual environment in %VENV_DIR% ...
    "%BASE_PY%" -m venv "%VENV_DIR%"
    if errorlevel 1 (
        echo.
        echo [!] Failed to create the virtual environment.
        echo     Make sure Python 3.13 is installed and can run "python -m venv".
        pause
        exit /b 1
    )
)

echo [i] Installing dependencies from requirements.txt into the venv ...
"%VENV_PY%" -m pip install -r "%~dp0requirements.txt"
if errorlevel 1 (
    echo.
    echo [!] Dependency installation failed.
    echo     Check that pypi.org is reachable, then run start.bat again.
    echo     The server has NOT been started.
    pause
    exit /b 1
)

REM ---------------------------------------------------------------------------
REM 3. Start the server (using the venv interpreter) and open the UI
REM ---------------------------------------------------------------------------

:start_server
echo.
echo [3/3] Finding a free port and starting the web UI ...

REM Pick the first port from the candidate list that is not currently in use.
REM netstat lists active connections; we flag a port as occupied if it shows
REM up in any line. If netstat is unavailable the check is skipped and the
REM first candidate (8000) is assumed free.
set "PORT="
for %%P in (%PORT_CANDIDATES%) do (
    if not defined PORT (
        netstat -ano 2>nul | findstr /r ":%P%[^0-9]" >nul
        if errorlevel 1 set "PORT=%%P"
    )
)
if not defined PORT (
    echo [!] Could not find a free port among: %PORT_CANDIDATES%
    echo     Free one manually, then run start.bat again.
    pause
    exit /b 1
)

echo [i] Using port %PORT% ...
start "Dossier_Management Server" cmd /c ""%VENV_PY%" "%~dp0main.py" serve --port %PORT%"
echo [i] Waiting for the server to come up ...
timeout /t 5 /nobreak >nul
start "" http://localhost:%PORT%
echo.
echo [ok] Done. The UI should now be open in your browser (http://localhost:%PORT%).
echo      The server runs in the "Dossier_Management Server" window.
echo      Close that window to stop it.
echo.
pause
