@echo off
setlocal

cd /d "%~dp0"

REM ===========================================================================
REM  Dossier_Management - one-click bootstrap
REM
REM  What this script does:
REM    1. locate a Python 3.12 interpreter - project-local first, then a
REM       user-level install; if neither exists, install one from the bundled
REM       installer in src\ (silently first, then via the setup wizard)
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
REM  Python installer is served from a central network share. We copy it to a
REM  local user directory first, then run the silent install from there.
set "PY_INSTALLER=K:\Software\Python\python-3.12.6-amd64.exe"
set "LOCAL_DIR=%USERPROFILE%\python_install"
set "LOCAL_PATH=%LOCAL_DIR%\python-3.12.6-amd64.exe"

if /i "%~1"=="%RESUME_FLAG%" goto :locate_python

echo.
echo ==== Dossier_Management bootstrap ====
echo.

REM ---------------------------------------------------------------------------
REM 1. Locate a Python interpreter. Preference order:
REM       a) python\ inside the project folder - the fully self-contained case
REM       b) a user-level Python 3.12 install, e.g. one left behind by the
REM          interactive installer wizard
REM       c) nothing found -> copy from the network share (K:\Software\Python)
REM          into a local user dir, then install silently; if that is blocked,
REM          fall back to the interactive installer wizard
REM ---------------------------------------------------------------------------

:locate_python
echo [1/3] Looking for Python ...
set "BASE_PY="
call :scan_python_paths
if defined BASE_PY goto :python_found
goto :python_missing

:scan_python_paths
REM Returns BASE_PY when a usable interpreter is found. Uses exit /b instead of
REM goto so the caller keeps control - jumping straight to a label from inside
REM a called subroutine leaves cmd's call stack dirty and replays the script.
if exist "%~dp0python\python.exe" (
    set "BASE_PY=%~dp0python\python.exe"
    exit /b 0
)
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python312*") do (
    if exist "%%~D\python.exe" (
        set "BASE_PY=%%~D\python.exe"
        exit /b 0
    )
)
for /d %%D in ("%ProgramFiles%\Python312*") do (
    if exist "%%~D\python.exe" (
        set "BASE_PY=%%~D\python.exe"
        exit /b 0
    )
)
for /d %%D in ("C:\Python312*") do (
    if exist "%%~D\python.exe" (
        set "BASE_PY=%%~D\python.exe"
        exit /b 0
    )
)
exit /b 1

:python_found
echo [i] Using interpreter: %BASE_PY%
"%BASE_PY%" --version
goto :setup_venv

:python_missing
echo [!] No usable Python interpreter found yet.
echo.
if not exist "%PY_INSTALLER%" goto :no_installer
if defined SILENT_TRIED goto :interactive_install

:try_silent_install_step
set "SILENT_TRIED=1"
echo [i] Trying a silent install from the network share K:\Software\Python ...
call :run_silent_install
if defined BASE_PY goto :python_found
echo [!] Silent installation was blocked on this machine.
echo     Log file, if any: %TEMP%\py_install.log

:interactive_install
echo.
echo ==========================================================================
echo  The Python installer window will open now. Please finish it by hand:
echo.
echo    1. Tick "Add python.exe to PATH" at the bottom of the first screen
echo    2. Leave everything else at its default
echo    3. Press Install, wait for "Setup was successful", then Close
echo.
echo  No administrator rights are needed. When it is done this window
echo  continues automatically.
echo ==========================================================================
echo.
pause
echo [i] Opening the Python installer wizard from %LOCAL_PATH% ...
start "" /wait "%LOCAL_PATH%"
echo.
echo [i] Looking for the interpreter you just installed ...
set "BASE_PY="
call :scan_python_paths
if defined BASE_PY goto :python_found
echo [!] Could not find the Python you installed.
echo     Run start.bat again after closing this window, or copy Python into
echo     the project's python\ folder manually.
pause
exit /b 1

:run_silent_install
REM Copy the installer from the central network share to a local user directory,
REM then run the silent install from the local copy (validated approach).
if not exist "%LOCAL_DIR%" mkdir "%LOCAL_DIR%"
if exist "%PY_INSTALLER%" (
    echo [i] Package found on the network share, copying to %LOCAL_PATH% ...
    copy /Y "%PY_INSTALLER%" "%LOCAL_PATH%" >nul
    if not exist "%LOCAL_PATH%" exit /b 1
    echo [i] Installing ...
    "%LOCAL_PATH%" /quiet InstallAllUsers=0 PrependPath=1 Include_test=0 /log "%LOCAL_DIR%\install_log.txt"
    if errorlevel 1 exit /b 1
    echo [i] Install finished - check %LOCAL_DIR%\install_log.txt or run python --version to verify.
    set "BASE_PY="
    call :scan_python_paths
    if defined BASE_PY exit /b 0
    exit /b 1
)
echo [!] Did not find the package at %PY_INSTALLER%.
echo     Please check the network connection / that the share is mapped.
pause
exit /b 1

:no_installer
echo [!] Did not find the package at %PY_INSTALLER%.
echo     Please check the network connection / that the share is mapped.
pause
exit /b 1

REM ---------------------------------------------------------------------------
REM 2. Create the virtualenv (once) and install dependencies into it
REM ---------------------------------------------------------------------------

:setup_venv
echo.
echo [2/3] Preparing virtual environment and installing dependencies ...

if exist "%VENV_PY%" (
    echo [i] Existing virtualenv found at %VENV_DIR% - skipping creation
    goto :deps_install
)
echo [i] Creating virtual environment in %VENV_DIR% ...
"%BASE_PY%" -m venv "%VENV_DIR%"
if errorlevel 1 (
    echo.
    echo [!] Failed to create the virtual environment.
    echo     Check that Python 3.12 runs correctly: %BASE_PY%
    pause
    exit /b 1
)

:deps_install
echo [i] Installing dependencies from requirements.txt into the venv ...
REM Network on the target machines is flaky but all dependencies do install
REM fine once the connection holds, so retry the whole pip pass up to 10 times.
REM pip also retries internally (--retries 5) and caches wheels, so each retry
REM after a partial download gets faster.
set "MAX_RETRY=10"
set "ATTEMPT=0"
:install_deps
set /a ATTEMPT+=1
echo [i] Installing dependencies - attempt %ATTEMPT% of %MAX_RETRY% ...
"%VENV_PY%" -m pip install --retries 5 --timeout 60 -r "%~dp0requirements.txt"
if not errorlevel 1 goto :deps_done
echo [!] Dependency installation failed on attempt %ATTEMPT% (network may be flaky).
if %ATTEMPT%==%MAX_RETRY% goto :deps_failed
echo     Retrying in 5 seconds ...
timeout /t 5 /nobreak >nul
goto :install_deps
:deps_failed
echo.
echo [!] Dependency installation failed after %MAX_RETRY% attempts.
echo     Check that pypi.org is reachable, then run start.bat again.
echo     The server has NOT been started.
pause
exit /b 1
:deps_done

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
        netstat -ano 2>nul | findstr /r ":%%P[^0-9]" >nul
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
