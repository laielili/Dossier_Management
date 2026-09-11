@echo off
setlocal

cd /d "%~dp0"

REM  Set when this window was started by the hand-over at :hand_over.
if /i "%~1"=="--restart" set "RESTARTED=1"

REM ===========================================================================
REM  Dossier_Management - one-click bootstrap
REM
REM  What this script does:
REM    1. locate a Python 3.12+ interpreter through the "py" launcher only.
REM       The launcher is the one stable entry point on a target machine. The
REM       bare names "python" and "python3" can be hijacked by the Windows
REM       Store app-execution alias and open the Store instead of running, so
REM       they are never used here. "py -0p" lists every registered
REM       interpreter together with its version, so detection and the version
REM       check happen in a single pass.
REM    2. if nothing qualifies, fall back to the installer on the central
REM       network share K:\Software\Python. If that share is not reachable
REM       from this machine, download the same installer from python.org.
REM    2b. if an installer had to be run, hand over to a fresh window and stop
REM        there; that window re-detects the launcher and carries on. The
REM        --restart marker tells it not to download or install a second time.
REM    3. create a project-local virtualenv - venv - and install the
REM       dependencies listed in requirements.txt into it.
REM    4. start "main.py serve" with the venv interpreter and open the UI.
REM
REM  COUPLING CONTRACT - this script depends on the project through exactly
REM  TWO things:
REM      a) the entry point file name "main.py"
REM      b) that file exposing the "serve" subcommand
REM
REM  Dependencies come solely from requirements.txt, so adding or upgrading a
REM  dependency never requires editing anything here.
REM
REM  A vendored python\ folder used to be the offline fallback. It is no
REM  longer shipped and must not be probed for again.
REM
REM  WRITING RULES FOR THIS FILE - these are the reason the window once
REM  flashed and vanished on double-click. Keep them when editing:
REM    - a literal open or close parenthesis inside an if/for BLOCK body
REM      closes the block early and turns the rest of the line into stray
REM      tokens. Never put unescaped parentheses inside a block body.
REM    - a label on the main flow must never end in "exit /b", which kills
REM      the whole script. Hand over with goto instead. "exit /b" is only
REM      correct inside a called subroutine.
REM    - every terminating path must end in pause or timeout, never a bare
REM      close, or the window disappears before the message can be read.
REM    - never place a command that might not exist on the left of a pipe.
REM      cmd aborts the whole script with exit code 255 instead of reporting
REM      an error, and it does so even inside a called subroutine. Write the
REM      output to a file first, then chain only always-present commands.
REM    - never call the launcher by bare name either. Resolve py.exe through
REM      an absolute path, as :probe_py does: after a fresh install the
REM      launcher may live in a folder that is not on PATH, and a window
REM      opened before the install keeps the environment it started with.
REM    - wait for the installer to finish. It hands the work to a worker
REM      process and returns early, so probing right after it exits reports
REM      a failure while the install is in fact still running.
REM ===========================================================================

set "PORT_CANDIDATES=8000 8001 8080 8888 9000"
set "VENV_DIR=%~dp0venv"
set "VENV_PY=%VENV_DIR%\Scripts\python.exe"
set "MIN_MAJOR=3"
set "MIN_MINOR=12"

REM  Installer sources, tried in this order.
set "SHARE_INSTALLER=K:\Software\Python\python-3.12.6-amd64.exe"
set "WEB_INSTALLER_URL=https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
set "LOCAL_DIR=%USERPROFILE%\python_install"
set "LOCAL_PATH=%LOCAL_DIR%\python-3.12.10-amd64.exe"
REM  Anything smaller than this is an error page, not an installer.
set "MIN_INSTALLER_BYTES=1000000"
REM  A per-user install drops py.exe here, an all-users one puts it in the
REM  Windows directory. :probe_py looks in both, by absolute path, so that
REM  detection never depends on PATH.
set "LAUNCHER_DIR=%LOCALAPPDATA%\Programs\Python\Launcher"

echo.
echo ==== Dossier_Management bootstrap ====
echo.

REM ---------------------------------------------------------------------------
REM 1. Locate a suitable interpreter through the py launcher
REM ---------------------------------------------------------------------------

:locate_python
echo [1/3] Looking for a Python interpreter ...
set "PYARG="
set "PYVER="
call :probe_py
if defined PYARG goto :python_found
goto :python_missing

:python_found
echo [i] Using the py launcher - Python %PYVER%
"%PYEXE%" %PYARG% --version
if not errorlevel 1 goto :setup_venv
REM  The launcher can still list an interpreter whose files were deleted, so
REM  a registered version is not proof that it starts. Fall back to a fresh
REM  install, but only once - otherwise a stale entry would loop forever.
if defined STALE_TRIED goto :install_failed
set "STALE_TRIED=1"
echo [!] Python %PYVER% is registered but will not start.
echo     Installing a fresh copy instead.
goto :python_missing

REM ---------------------------------------------------------------------------
REM 1b. Nothing usable - obtain an installer and install it
REM ---------------------------------------------------------------------------

:python_missing
echo [!] The py launcher found no interpreter at or above %MIN_MAJOR%.%MIN_MINOR%.
echo.
REM  A restarted window already installed Python once. If the launcher still
REM  sees nothing, running the installer again would only repeat the failure.
if defined RESTARTED goto :install_failed
call :fetch_installer
if not exist "%LOCAL_PATH%" goto :install_failed
call :check_installer_size
if not defined INSTALLER_OK goto :install_failed
call :run_silent_install
if not defined PYARG goto :install_failed
REM  A brand new interpreter is not visible to a running window: it keeps the
REM  environment block it was started with. Hand over to a fresh one, which
REM  re-detects the launcher and continues with the venv setup.
if defined RESTARTED goto :python_found
goto :hand_over

:hand_over
echo.
echo [i] Python %PYVER% is installed. Continuing in a new window ...
REM  Pass the launcher folder down by hand. The child process inherits this
REM  environment block, and the install that just ran did not touch it.
for %%F in ("%PYEXE%") do set "PATH=%%~dpF;%PATH%"
timeout /t 3 /nobreak >nul
start "Dossier_Management" cmd /c ""%~f0" --restart"
exit /b 0

:install_failed
echo.
echo [!] Could not obtain a working Python interpreter on this machine.
echo     Tried the py launcher, the network share and python.org.
echo.
echo     Next steps, easiest first:
echo       1. ask IT to install Python %MIN_MAJOR%.%MIN_MINOR% or newer
echo       2. copy the installer to %LOCAL_DIR% by hand, then run this again
echo       3. if the installer just ran, close this window and start again
echo.
pause
exit /b 1

REM ---------------------------------------------------------------------------
REM 2. Create the virtualenv once, then install dependencies into it
REM ---------------------------------------------------------------------------

:setup_venv
echo.
echo [2/3] Preparing the virtual environment ...

if exist "%VENV_PY%" goto :deps_install
echo [i] Creating it in %VENV_DIR% ...
"%PYEXE%" %PYARG% -m venv "%VENV_DIR%"
if not exist "%VENV_PY%" goto :venv_failed

:deps_install
echo [i] Installing dependencies from requirements.txt into the venv ...
REM Network on the target machines is flaky, but every dependency does
REM install fine once the connection holds, so retry the whole pip pass.
REM pip also retries internally and caches wheels, so each retry after a
REM partial download is faster than the one before.
set "MAX_RETRY=10"
set "ATTEMPT=0"
:install_deps
set /a ATTEMPT+=1
echo [i] Installing dependencies - attempt %ATTEMPT% of %MAX_RETRY% ...
"%VENV_PY%" -m pip install --disable-pip-version-check --no-input --retries 5 --timeout 60 -r "%~dp0requirements.txt"
if not errorlevel 1 goto :deps_done
echo [!] Dependency installation failed on attempt %ATTEMPT%, the network may be flaky.
if %ATTEMPT%==%MAX_RETRY% goto :deps_failed
echo     Retrying in 5 seconds ...
timeout /t 5 /nobreak >nul
goto :install_deps
:deps_done
echo [i] Dependencies are ready.
goto :start_server

:deps_failed
echo.
echo [!] Dependency installation failed after %MAX_RETRY% attempts.
echo     Check that pypi.org is reachable from this machine, then run this
echo     script again. The server has NOT been started.
pause
exit /b 1

:venv_failed
echo.
echo [!] Failed to create the virtual environment with Python %PYVER%.
echo     Delete the venv folder and run this script again.
pause
exit /b 1

REM ---------------------------------------------------------------------------
REM 3. Start the server with the venv interpreter and open the UI
REM ---------------------------------------------------------------------------

:start_server
echo.
echo [3/3] Finding a free port and starting the web UI ...

REM Pick the first candidate port that is not currently in use. netstat lists
REM active connections, so a port counts as busy if it shows up in any line.
REM If netstat is unavailable the check silently passes and 8000 is assumed
REM free, which the port scan below will then confirm or reject.
set "PORT="
for %%P in (%PORT_CANDIDATES%) do (
    if not defined PORT (
        netstat -ano 2>nul | findstr /r ":%%P[^0-9]" >nul
        if errorlevel 1 set "PORT=%%P"
    )
)
if not defined PORT goto :no_port

echo [i] Using port %PORT% ...
start "Dossier_Management Server" cmd /c ""%VENV_PY%" "%~dp0main.py" serve --port %PORT%"
echo [i] Waiting for the server to come up ...
timeout /t 5 /nobreak >nul
start "" http://localhost:%PORT%
echo.
echo [ok] Done. The UI should now be open in your browser:
echo      http://localhost:%PORT%
echo.
echo      The server runs in the "Dossier_Management Server" window.
echo      Close that window to stop it.
echo.
timeout /t 5
exit /b 0

:no_port
echo [!] Could not find a free port among: %PORT_CANDIDATES%
echo     Free one manually, then run this script again.
pause
exit /b 1

REM ---------------------------------------------------------------------------
REM Subroutines - never reached by falling through, only via call
REM ---------------------------------------------------------------------------

REM  :probe_py - sets PYARG, for example "-3.13", and PYVER, for example
REM  "3.13", when a registered interpreter at or above the minimum is usable.
REM  Entries whose path contains WindowsApps are dropped on purpose: that is
REM  the Store build, which hides behind a very long path and has unreliable
REM  venv support. If py is missing entirely, it writes to stderr, the filter
REM  below discards everything and PYARG stays empty.
:probe_py
set "PYLIST=%TEMP%\dm_py_list.txt"
set "PYLIST_OK=%TEMP%\dm_py_ok.txt"
REM  Find the launcher by absolute path instead of by bare name. A fresh
REM  install drops py.exe either into the Windows directory, which is always
REM  on PATH, or into the per-user launcher folder, which is not on PATH
REM  unless the installer was told to update it. Checking both keeps
REM  detection independent of PATH and working in an already-open window.
set "PYEXE="
if exist "%SystemRoot%\py.exe" set "PYEXE=%SystemRoot%\py.exe"
if not defined PYEXE if exist "%LAUNCHER_DIR%\py.exe" set "PYEXE=%LAUNCHER_DIR%\py.exe"
if not defined PYEXE goto :probe_py_done
REM  The scan is split into two steps on purpose. Putting a command that may
REM  not exist - py, on a machine that never had Python - on the left of a
REM  pipe makes cmd abort the entire script with exit code 255 instead of
REM  reporting an ordinary error, and it does so even inside a subroutine.
REM  So the raw listing goes to a file first, and only commands that always
REM  exist are ever chained together after that.
"%PYEXE%" -0p > "%PYLIST%" 2>nul
if not exist "%PYLIST%" goto :probe_py_done
findstr /r /c:"-V:" "%PYLIST%" | findstr /v /i "WindowsApps" > "%PYLIST_OK%" 2>nul
if not exist "%PYLIST_OK%" goto :probe_py_done
for /f "usebackq tokens=1" %%A in ("%PYLIST_OK%") do call :consider_py "%%A"
:probe_py_done
del "%PYLIST%" >nul 2>&1
del "%PYLIST_OK%" >nul 2>&1
exit /b

REM  :consider_py - keeps the first qualifying entry. py lists interpreters
REM  newest first, so the first match is also the highest version available.
REM  Third-party builds carry tags such as Vendor/CPython3.12.14; the strict
REM  two-number check below rejects those, keeping only plain X.Y tags which
REM  are what the official installer registers.
:consider_py
if defined PYARG exit /b
set "TAG=%~1"
if not "%TAG:~0,3%"=="-V:" exit /b
set "TAG=%TAG:~3%"
echo(%TAG%|findstr /r /c:"^[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 exit /b
for /f "tokens=1,2 delims=." %%a in ("%TAG%") do (set "CUR_MAJ=%%a" & set "CUR_MIN=%%b")
REM  Guard both halves before comparing. If either variable were empty the
REM  comparison line would collapse to something like "if 3 LSS  exit /b";
REM  cmd would then read the command name as the right hand operand and try
REM  to run the command name itself as a program, which surfaces as a stray
REM  token error instead of a useful message. Hence the two guards.
if not defined CUR_MAJ exit /b
if not defined CUR_MIN exit /b
if %CUR_MAJ% LSS %MIN_MAJOR% exit /b
if %CUR_MAJ% GTR %MIN_MAJOR% goto :consider_accept
if %CUR_MIN% LSS %MIN_MINOR% exit /b
:consider_accept
set "PYARG=-%TAG%"
set "PYVER=%TAG%"
exit /b

REM  :fetch_installer - puts the installer at LOCAL_PATH. The network share
REM  is tried first because it needs no internet access; a direct download
REM  from python.org is the fallback. curl.exe ships with Windows 10 1803 and
REM  later, certutil is the older fallback. Both are called by full path so a
REM  curl or tar from Git or MSYS on the PATH can never shadow them.
:fetch_installer
if not exist "%LOCAL_DIR%" mkdir "%LOCAL_DIR%"

echo [i] Looking for the installer on the network share ...
if not exist "%SHARE_INSTALLER%" goto :share_absent
copy /Y "%SHARE_INSTALLER%" "%LOCAL_PATH%" >nul 2>&1
if exist "%LOCAL_PATH%" goto :fetch_done
echo [!] The installer is on the share but could not be copied - access denied?
goto :fetch_web

:share_absent
echo [i] The share is not reachable from this machine, skipping it.

:fetch_web
echo [i] Downloading the installer from python.org ...
echo     %WEB_INSTALLER_URL%
if exist "%SystemRoot%\System32\curl.exe" goto :fetch_curl
goto :fetch_certutil
:fetch_curl
"%SystemRoot%\System32\curl.exe" -L --fail --retry 3 --retry-delay 3 --connect-timeout 20 --max-time 600 -o "%LOCAL_PATH%" "%WEB_INSTALLER_URL%"
if exist "%LOCAL_PATH%" goto :fetch_done
echo [!] curl did not succeed, trying certutil ...
:fetch_certutil
certutil -urlcache -split -f "%WEB_INSTALLER_URL%" "%LOCAL_PATH%" >nul 2>&1
:fetch_done
exit /b

REM  :check_installer_size - sets INSTALLER_OK when the downloaded file is
REM  big enough to be a real installer rather than a redirect or error page.
:check_installer_size
set "INSTALLER_OK="
set "FSIZE=0"
for %%F in ("%LOCAL_PATH%") do set "FSIZE=%%~zF"
if %FSIZE% LSS %MIN_INSTALLER_BYTES% exit /b
set "INSTALLER_OK=1"
exit /b

REM  :run_silent_install - installs from LOCAL_PATH, then waits for the
REM  launcher to answer. InstallAllUsers=0 needs no administrator rights.
REM  PrependPath=0 leaves the user PATH untouched, which costs nothing
REM  here: :probe_py finds the launcher by absolute path, not via PATH.
:run_silent_install
echo [i] Installing Python silently, this can take a minute ...
REM  /wait is not optional. Without it the installer returns the moment it
REM  has handed the work to its worker process, and the launcher is still
REM  missing when we go looking for it - on a slow machine that gap is wide
REM  enough to make a perfectly good install look like a failure.
start "Python installer" /wait "%LOCAL_PATH%" /quiet InstallAllUsers=0 PrependPath=0 Include_test=0 /log "%LOCAL_DIR%\install_log.txt"
echo [i] Installer finished, re-checking the py launcher ...
REM  Probe again, and keep probing for a minute. This covers the case where
REM  the installer really does exit before its work is done.
set "WAITED=0"
:wait_for_launcher
set "PYARG="
set "PYVER="
call :probe_py
if defined PYARG exit /b 0
set /a WAITED+=1
if %WAITED% GEQ 12 exit /b 0
echo [i] The launcher is not ready yet, checking again in 5 seconds ...
timeout /t 5 /nobreak >nul
goto :wait_for_launcher
