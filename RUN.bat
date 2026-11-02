@echo off
setlocal EnableExtensions DisableDelayedExpansion
title KM Risk Reporting - run
cd /d "%~dp0"

rem ======================================================================
rem  KM Risk Reporting - RUN
rem
rem  Double-click this file. It reads every confirmation in the contracts
rem  folder chosen in SETUP.bat (which can be on L:) and writes
rem  raw-data.xlsx and review.xlsx into a new folder in "contracts-data",
rem  next to the contracts folder, then opens that folder.
rem  To use another contracts folder, run SETUP.bat again.
rem ======================================================================

if exist ".venv\Scripts\riskparse.exe" goto :installed
echo.
echo  The program is not set up yet. Double-click SETUP.bat first.
echo.
pause
exit /b 1
:installed

set "CONTRACTS=%CD%\contracts"
set "SETTING=%LOCALAPPDATA%\KM-RiskReporting\contracts-folder.txt"
if exist "%SETTING%" set /p CONTRACTS=<"%SETTING%"
for %%I in ("%CONTRACTS%\..") do set "RESULTS=%%~fI"
if "%RESULTS:~-1%"=="\" set "RESULTS=%RESULTS:~0,-1%"
set "RESULTS=%RESULTS%\contracts-data"

if exist "%CONTRACTS%\" goto :folder_found
mkdir "%CONTRACTS%" 2>nul
if exist "%CONTRACTS%\" goto :folder_found
echo.
echo  The contracts folder cannot be reached:
echo    %CONTRACTS%
echo  If it is on L:, check that you are connected to the network, then run
echo  this again. To use another folder, run SETUP.bat.
echo.
pause
exit /b 1
:folder_found

set "HAS_FILES="
for /r "%CONTRACTS%" %%F in (*) do set "HAS_FILES=1"
if defined HAS_FILES goto :has_files
echo.
echo  The contracts folder is empty. It is opening now: put the
echo  confirmations in it - the Physical, Financial and Evergreen folders -
echo  then double-click RUN.bat again.
echo    %CONTRACTS%
echo.
start "" "%CONTRACTS%"
pause
exit /b 1
:has_files

echo.
echo  Reading the confirmations in %CONTRACTS% ...
echo.
".venv\Scripts\riskparse.exe" review "%CONTRACTS%"
set "RC=%ERRORLEVEL%"
echo.
if "%RC%"=="0" goto :done
echo  ======================================================================
echo   The run stopped - the message above says why - exit code %RC%.
echo   Send a screenshot of this window to the developer if it is unclear.
echo  ======================================================================
echo.
pause
exit /b %RC%

:done
set "LATEST="
for /f "delims=" %%D in ('dir /b /ad /o-d /tc "%RESULTS%" 2^>nul') do if not defined LATEST set "LATEST=%%D"
echo  ======================================================================
echo   Finished. The results are in:
echo   %RESULTS%\%LATEST%
echo.
echo   raw-data.xlsx - the trades in the firm's format, plus the checks sheet
echo   review.xlsx   - the same, with evidence, names, documents and issues
echo  ======================================================================
echo.
if defined LATEST start "" "%RESULTS%\%LATEST%"
pause
exit /b 0
