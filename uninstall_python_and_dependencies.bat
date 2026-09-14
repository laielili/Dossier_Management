@echo off
setlocal

cd /d "%~dp0"

REM ===========================================================================
REM  Dossier_Management - project-scoped uninstaller
REM
REM  Scope: this script removes ONLY the artifacts that the Dossier_Management
REM  bootstrap creates inside this project folder. It never touches any Python
REM  installation on the machine.
REM
REM  What gets removed, if present:
REM    1. the project virtual environment - venv - which holds every pip
REM       dependency installed from requirements.txt
REM    2. the cached installer folder in %USERPROFILE%\python_install
REM    3. any running "Dossier_Management Server" window
REM
REM  What is intentionally left alone:
REM    - every Python interpreter on the machine, including the one the
REM      bootstrap may have installed
REM    - your source code, data and generated output
REM
REM  WRITING RULES - same as Dossier_Management.bat:
REM    - no literal parentheses inside an if/for block body
REM    - every terminating path ends in pause, never a bare close
REM    - nothing destructive happens before the user confirms
REM ===========================================================================

echo.
echo ==== Dossier_Management uninstaller - project scope ====
echo.
echo This removes only project-local artifacts. It does NOT touch Python.
echo.
echo The following will be removed if present:
echo   - project virtual environment: %~dp0venv
echo   - cached installer folder:      %USERPROFILE%\python_install
echo   - running server window titled  Dossier_Management Server
echo.
echo Your Python installs and project source/data are left untouched.
echo.

choice /C YN /M "Continue and remove the items listed above"
if errorlevel 2 goto :aborted

REM ---------------------------------------------------------------------------
REM 1. Stop any running server window
REM ---------------------------------------------------------------------------

echo [1/3] Stopping any running server window ...
taskkill /FI "WINDOWTITLE eq Dossier_Management Server" >nul 2>&1
echo [i] Server stop requested - a "no process found" note here is normal.

REM ---------------------------------------------------------------------------
REM 2. Remove the project virtual environment
REM ---------------------------------------------------------------------------

echo [2/3] Removing the virtual environment ...
if exist "%~dp0venv" (
    rmdir /s /q "%~dp0venv"
    if exist "%~dp0venv" (
        echo [!] The venv folder could not be removed - close any program using it, then run again.
    ) else (
        echo [ok] venv removed.
    )
) else (
    echo [i] No venv folder found - nothing to remove.
)

REM ---------------------------------------------------------------------------
REM 3. Remove the cached installer folder
REM ---------------------------------------------------------------------------

echo [3/3] Removing the cached installer folder ...
if exist "%USERPROFILE%\python_install" (
    rmdir /s /q "%USERPROFILE%\python_install"
    if exist "%USERPROFILE%\python_install" (
        echo [!] The cached installer folder could not be removed - close Explorer there, then run again.
    ) else (
        echo [ok] cached installer folder removed.
    )
) else (
    echo [i] No cached installer folder found - nothing to remove.
)

echo.
echo [done] Project-scoped cleanup finished.
echo        The venv and cached installer are removed.
echo        Your Python installation was NOT changed.
echo.
pause
exit /b 0

:aborted
echo.
echo [aborted] Nothing was removed.
echo.
pause
exit /b 1
