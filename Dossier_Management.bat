@echo off
setlocal

cd /d "%~dp0"

REM ===========================================================================
REM  Dossier_Management - one-click bootstrap
REM
REM  Runtime strategy, in priority order:
REM    A) SELF-CONTAINED  - the python\ folder next to this .bat holds a
REM       portable Python with all dependencies already installed. Nothing is
REM       downloaded, nothing is written outside the project folder, no
REM       administrator rights and no access to the K:\ share are needed.
REM       This is the mode we ship to colleagues.
REM    B) MACHINE PYTHON  - a normal Python 3.12/3.13/3.14 installation found
REM       machine. A project virtualenv (venv\) is created and the
REM       dependencies are pip-installed into it (needs pypi.org).
REM    C) NO PYTHON       - optionally install Python from an offline
REM       installer placed in installer\ (or from the K:\ share), then
REM       continue with B.
REM
REM  COUPLING CONTRACT - this script depends on the project through
REM  exactly TWO things:
REM      a) the entry point file name "main.py"
REM      b) that file exposing the "serve" subcommand
REM
REM  Everything else is delegated:
REM    - dependencies come solely from requirements.txt; no package or import
REM      names are duplicated here, so adding or upgrading a dependency never
REM      requires editing this file
REM    - pip install is idempotent, so re-running this script stays fast
REM    - in mode A the script touches no network at all after the first
REM      successful run (see _deps_ready.flag below)
REM  Changes inside src/, static/, classify/, queries/ and prompt/ never
REM  require touching this file either. The only things you would ever change
REM  here are PORT_CANDIDATES below, or the entry point / subcommand names if
REM  those are ever renamed.
REM
REM  HOW TO BUILD THE SELF-CONTAINED python\ FOLDER
REM  What is shipped today: CPython 3.14.7 plus the 25 dependency packages the
REM  project virtualenv was already validated against. Any CPython 3.12/3.13/3.14
REM  works, but ship the version you actually tested. Do this ONCE, on a machine
REM  that has network access; afterwards the python\ folder travels with the
REM  project (zip it if you need to move it around):
REM    1. install Python for the current user only. A FULL install is required -
REM       the "embeddable" zip has neither pip nor venv:
REM         python-3.14.7-amd64.exe /quiet InstallAllUsers=0 PrependPath=0
REM    2. copy that installation into the project. NOTE: /XD matches directory
REM       NAMES, not full paths - passing a full path silently excludes nothing:
REM         robocopy "%LOCALAPPDATA%\Programs\Python\Python314" "%~dp0python" /E /XD Doc test /XF NEWS.txt /R:1 /W:1
REM    3. replace the interpreter's own global site-packages with the package set
REM       the project was validated on. This is offline, keeps versions identical,
REM       and avoids shipping unrelated global packages:
REM         robocopy "%~dp0venv\Lib\site-packages" "%~dp0python\Lib\site-packages" /E
REM    4. delete every python\Scripts\*.exe. Those launchers hard-code the
REM       absolute path of the machine that built them, so left in place they
REM       would install into THAT interpreter instead of the project one.
REM       Always invoke pip as:  python\python.exe -m pip ...
REM    5. run this .bat once - it writes python\_deps_ready.flag, and from then
REM       on every machine skips pip entirely and starts fully offline.
REM  Optional cleanup: python\Doc, python\Tools and python\Lib\test are not
REM  needed at runtime (roughly 105 MB). __pycache__ dirs may be kept: they only
REM  carry the builder absolute path inside them and they speed up first start.
REM ===========================================================================

set "PORT_CANDIDATES=8000 8001 8080 8888 9000"

set "EMBED_DIR=%~dp0python"
set "EMBED_PY=%EMBED_DIR%\python.exe"
set "DEPS_FLAG=%EMBED_DIR%\_deps_ready.flag"

set "VENV_DIR=%~dp0venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "REQ=%~dp0requirements.txt"

REM Optional offline installer: drop python-3.12.x-amd64.exe into installer\.
set "INSTALLER_DIR=%~dp0installer"
set "SHARE_INSTALLER=K:\Software\Python\python-3.12.6-amd64.exe"
set "SHARE_LOCAL=%USERPROFILE%\python_install"

echo.
echo ==== Dossier_Management bootstrap ====
echo.

REM ---------------------------------------------------------------------------
REM 1. Locate a Python interpreter
REM ---------------------------------------------------------------------------

:locate_python
echo [1/3] Looking for Python ...
set "BASE_PY="
set "BASE_MODE="
if exist "%EMBED_PY%" goto :found_embedded
REM No bundled interpreter - scan the usual per-user and machine-wide install
REM locations, newest minor version first.
call :scan_user_python
REM NOTE: this code sits on the MAIN execution path, not inside a called
REM routine, so it has to hand over with goto. A stray "exit /b" here would
REM end the whole batch at once and close the window before anyone can read
REM the message - that was the cause of the "flashes and disappears" report
REM on machines that do not ship the bundled python\ folder.
if defined BASE_PY goto :python_found
goto :python_missing

:scan_one_python
if defined BASE_PY exit /b 0
for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python%~1*") do (
    if exist "%%~D\python.exe" (
        set "BASE_PY=%%~D\python.exe"
        set "BASE_MODE=user"
        exit /b 0
    )
)
for /d %%D in ("%ProgramFiles%\Python%~1*") do (
    if exist "%%~D\python.exe" (
        set "BASE_PY=%%~D\python.exe"
        set "BASE_MODE=user"
        exit /b 0
    )
)
for /d %%D in ("C:\Python%~1*") do (
    if exist "%%~D\python.exe" (
        set "BASE_PY=%%~D\python.exe"
        set "BASE_MODE=user"
        exit /b 0
    )
)
exit /b 1

REM Scans every supported minor version, newest first. Leaves BASE_PY /
REM BASE_MODE set when an interpreter is found, and leaves BASE_PY empty
REM otherwise. Called from three places, so it lives in one spot.
:scan_user_python
for %%V in (314 313 312) do call :scan_one_python "%%V"
exit /b 0

:found_embedded
set "BASE_PY=%EMBED_PY%"
set "BASE_MODE=embedded"
goto :python_found

:python_found
echo [i] Using interpreter: %BASE_PY%
"%BASE_PY%" --version
if "%BASE_MODE%"=="embedded" goto :setup_embedded
goto :setup_venv

REM ---------------------------------------------------------------------------
REM 2a. Mode A: project-local Python. It is already isolated, so no venv is
REM     created and no network is touched once the dependencies are bundled.
REM ---------------------------------------------------------------------------

:setup_embedded
echo.
echo [2/3] Preparing the runtime (project-local Python) ...
set "TARGET_PY=%EMBED_PY%"
set "PTH_FOUND="
for %%F in ("%EMBED_DIR%\python3*._pth") do if exist "%%~F" set "PTH_FOUND=%%~nxF"
if defined PTH_FOUND (
    echo [!] Found %PTH_FOUND% - this is the "embeddable" Python package,
    echo     which ships without pip. Copy a FULL Python installation into
    echo     python\ instead - see the header of this file for the commands.
)
if exist "%DEPS_FLAG%" (
    echo [i] Dependencies are already bundled in python\ - skipping pip.
    goto :start_server
)
echo [i] Dependencies are not bundled yet - installing them once now ...
goto :install_deps

REM ---------------------------------------------------------------------------
REM 2b. Mode B: machine Python. Isolate the project in a venv so we never
REM     pollute the user's global Python.
REM ---------------------------------------------------------------------------

:setup_venv
echo.
echo [2/3] Preparing a project virtual environment ...
if exist "%VENV_PY%" (
    echo [i] Existing virtualenv found at %VENV_DIR% - skipping creation
    goto :venv_deps
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

:venv_deps
set "TARGET_PY=%VENV_PY%"
goto :install_deps

REM ---------------------------------------------------------------------------
REM 2c. Dependency installation, shared by both modes
REM ---------------------------------------------------------------------------

:install_deps
echo [i] Installing dependencies from requirements.txt ...
REM Network on the target machines is flaky but all dependencies do install
REM fine once the connection holds, so retry the whole pip pass up to 10 times.
REM pip also retries internally (--retries 5) and caches wheels, so each retry
REM after a partial download gets faster.
set "MAX_RETRY=10"
set "ATTEMPT=0"
:install_loop
set /a ATTEMPT+=1
echo [i] Installing dependencies - attempt %ATTEMPT% of %MAX_RETRY% ...
"%TARGET_PY%" -m pip install --disable-pip-version-check --no-input --retries 5 --timeout 60 -r "%REQ%"
if not errorlevel 1 goto :deps_ok
echo [!] Dependency installation failed on attempt %ATTEMPT% (network may be flaky).
if %ATTEMPT%==%MAX_RETRY% goto :deps_failed
echo     Retrying in 5 seconds ...
timeout /t 5 /nobreak >nul
goto :install_loop

:deps_ok
if "%BASE_MODE%"=="embedded" (
    >"%DEPS_FLAG%" echo ready
    echo [i] Marked python\ as ready - later runs will skip pip completely.
)
goto :start_server

:deps_failed
echo.
echo [!] Dependency installation failed after %MAX_RETRY% attempts.
echo     Check that pypi.org is reachable, or copy the python\ folder from a
echo     colleague who already has this project running.
echo.
set "ANSWER=Y"
set /p "ANSWER=    Start the server anyway? [Y/n] "
if /i "%ANSWER%"=="n" goto :abort
if /i "%ANSWER%"=="no" goto :abort
goto :start_server

:abort
echo     The server has NOT been started.
pause
exit /b 1

REM ---------------------------------------------------------------------------
REM 3. No interpreter at all - try an offline installer, else point at the
REM    self-contained option, which needs no K:\ access at all.
REM ---------------------------------------------------------------------------

:python_missing
echo [!] No Python interpreter found on this machine.
echo.
echo     Easiest fix: copy the python\ folder from a colleague who already
echo     has this project running, then start this .bat again. No install,
echo     no network share and no administrator rights are needed.
echo.
call :find_installer
if not defined PY_SETUP goto :no_installer
echo [i] Found an offline installer: %PY_SETUP%
echo [i] Running a silent install - no administrator rights are needed ...
"%PY_SETUP%" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 /log "%TEMP%\py_install.log"
echo [i] Install finished. Log file: %TEMP%\py_install.log
set "BASE_PY="
call :scan_user_python
if defined BASE_PY goto :python_found
echo [!] The silent install did not produce a usable interpreter.
set "ANSWER=N"
set /p "ANSWER=    Open the installer window and finish it by hand? [y/N] "
if /i not "%ANSWER%"=="y" goto :no_installer
echo.
echo     Leave everything at its default, press Install, wait for
echo     "Setup was successful", then close it. This window continues after.
echo.
pause
start "" /wait "%PY_SETUP%"
set "BASE_PY="
call :scan_user_python
if defined BASE_PY goto :python_found
echo [!] Could not find the Python you installed.
echo     Run this .bat again, or copy Python into the project's python\
echo     folder manually.
pause
exit /b 1

:find_installer
set "PY_SETUP="
for %%F in ("%INSTALLER_DIR%\python-3.12*-amd64.exe") do (
    if exist "%%~F" set "PY_SETUP=%%~F"
)
if defined PY_SETUP exit /b 0
if not exist "%SHARE_INSTALLER%" exit /b 1
if not exist "%SHARE_LOCAL%" mkdir "%SHARE_LOCAL%"
echo [i] Copying the installer from the network share ...
copy /Y "%SHARE_INSTALLER%" "%SHARE_LOCAL%" >nul
if not exist "%SHARE_LOCAL%\python-3.12.6-amd64.exe" exit /b 1
set "PY_SETUP=%SHARE_LOCAL%\python-3.12.6-amd64.exe"
exit /b 0

:no_installer
echo [!] No offline installer found.
echo     Put python-3.12.x-amd64.exe into the installer\ folder next to this
echo     .bat, or copy the python\ folder from a colleague, then run again.
pause
exit /b 1

REM ---------------------------------------------------------------------------
REM 4. Start the server and open the UI
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
    echo     Free one manually, then run this .bat again.
    pause
    exit /b 1
)

echo [i] Using port %PORT% ...
start "Dossier_Management Server" cmd /c ""%TARGET_PY%" "%~dp0main.py" serve --port %PORT%"
echo [i] Waiting for the server to come up ...
timeout /t 5 /nobreak >nul
start "" http://localhost:%PORT%
echo.
echo [ok] Done. The UI should now be open in your browser (http://localhost:%PORT%).
echo      The server runs in the "Dossier_Management Server" window.
echo      Close that window to stop it.
echo.
timeout /t 5
