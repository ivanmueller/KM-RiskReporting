@echo off
setlocal EnableExtensions DisableDelayedExpansion
title KM Risk Reporting - run
cd /d "%~dp0"

rem ======================================================================
rem  KM Risk Reporting - RUN
rem
rem  Double-click this file. It reads every confirmation in the contracts
rem  folder chosen in SETUP.bat (which can be on L:), checks them against the
rem  monthly trade sheet and Libby's trade sheet, writes raw-data.xlsx, review.xlsx and dashboard.html
rem  into a new dated folder in the results folder, opens that folder, and
rem  shows the dashboard in the browser until this window is closed.
rem  To change the folders, run SETUP.bat again.
rem ======================================================================

if exist ".venv\Scripts\riskparse.exe" goto :installed
echo.
echo  The program is not set up yet. Double-click SETUP.bat first.
echo.
pause
exit /b 1
:installed

set "SETTINGS=%LOCALAPPDATA%\KM-RiskReporting"
set "CONTRACTS=%CD%\contracts"
set "RESULTS="
set "TRADESHEET="
set "LIBBY="
if exist "%SETTINGS%\contracts-folder.txt" set /p CONTRACTS=<"%SETTINGS%\contracts-folder.txt"
if exist "%SETTINGS%\results-folder.txt" set /p RESULTS=<"%SETTINGS%\results-folder.txt"
if exist "%SETTINGS%\tradesheet-folder.txt" set /p TRADESHEET=<"%SETTINGS%\tradesheet-folder.txt"
if exist "%SETTINGS%\libby-sheet.txt" set /p LIBBY=<"%SETTINGS%\libby-sheet.txt"
if defined RESULTS goto :results_known
for %%I in ("%CONTRACTS%\..") do set "RESULTS=%%~fI"
if "%RESULTS:~-1%"=="\" set "RESULTS=%RESULTS:~0,-1%"
set "RESULTS=%RESULTS%\contracts-data"
:results_known
set "TS_ARG="
if not defined TRADESHEET goto :ts_known
if /i "%TRADESHEET%"=="none" goto :ts_known
set TS_ARG=--trade-sheet "%TRADESHEET%"
:ts_known
set "LIBBY_ARG="
if not defined LIBBY goto :libby_known
if /i "%LIBBY%"=="none" goto :libby_known
set LIBBY_ARG=--libby-sheet "%LIBBY%"
:libby_known

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
".venv\Scripts\riskparse.exe" review "%CONTRACTS%" --results "%RESULTS%" %TS_ARG% %LIBBY_ARG%
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
echo   raw-data.xlsx  - the trades in the firm's format, plus the checks sheet
echo   review.xlsx    - the same, with the trade sheet check, bank swaps,
echo                    evidence, names, documents and issues
echo   dashboard.html - the risk dashboard, opening in your browser now
echo  ======================================================================
echo.
if not defined LATEST goto :no_dashboard
start "" "%RESULTS%\%LATEST%"
".venv\Scripts\riskparse.exe" dashboard "%RESULTS%"
exit /b 0
:no_dashboard
pause
exit /b 0
