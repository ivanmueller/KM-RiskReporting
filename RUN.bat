@echo off
setlocal EnableExtensions DisableDelayedExpansion
title KM Risk Reporting - run
cd /d "%~dp0"

rem ======================================================================
rem  KM Risk Reporting - RUN
rem
rem  Double-click this file. It reads every confirmation in the "contracts"
rem  folder and writes raw-data.xlsx and review.xlsx into a new folder in
rem  "contracts-data", then opens that folder.
rem ======================================================================

if exist ".venv\Scripts\riskparse.exe" goto :installed
echo.
echo  The program is not set up yet. Double-click SETUP.bat first.
echo.
pause
exit /b 1
:installed

if not exist "contracts\" mkdir "contracts"
set "HAS_FILES="
for /r "contracts" %%F in (*) do set "HAS_FILES=1"
if defined HAS_FILES goto :has_files
echo.
echo  The "contracts" folder is empty. It is opening now: put the
echo  confirmations in it - the Physical, Financial and Evergreen folders -
echo  then double-click RUN.bat again.
echo.
start "" "contracts"
pause
exit /b 1
:has_files

echo.
echo  Reading the confirmations in "contracts" ...
echo.
".venv\Scripts\riskparse.exe" review contracts
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
for /f "delims=" %%D in ('dir /b /ad /o-n "contracts-data" 2^>nul') do if not defined LATEST set "LATEST=%%D"
echo  ======================================================================
echo   Finished. The results are in:
echo   %CD%\contracts-data\%LATEST%
echo.
echo   raw-data.xlsx - the trades in the firm's format, plus the checks sheet
echo   review.xlsx   - the same, with evidence, names, documents and issues
echo  ======================================================================
echo.
if defined LATEST start "" "contracts-data\%LATEST%"
pause
exit /b 0
