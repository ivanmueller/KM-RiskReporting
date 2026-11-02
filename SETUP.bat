@echo off
setlocal EnableExtensions DisableDelayedExpansion
title KM Risk Reporting - setup

rem ======================================================================
rem  KM Risk Reporting - SETUP
rem
rem  Double-click this file to install the program, and again at any time
rem  to update it. It needs no administrator rights: everything is
rem  installed for your Windows user only.
rem
rem    Python  -> %LOCALAPPDATA%\KM-RiskReporting\python
rem               (only if Python 3.11 - 3.13 is not already installed)
rem    Git     -> %LOCALAPPDATA%\KM-RiskReporting\git
rem               (only if Git is not already installed)
rem    Program -> the folder you choose (default C:\RiskCode\KM-RiskReporting);
rem               run from inside a copy, it updates that copy without asking
rem ======================================================================

set "REPO_URL=https://github.com/ivanmueller/KM-RiskReporting.git"
set "BRANCH=claude/risk-reporting-phase-1-x8u26q"
set "INSTALL_DIR=C:\RiskCode\KM-RiskReporting"
set "TOOLS=%LOCALAPPDATA%\KM-RiskReporting"
set "PY_VERSIONS=3.13.5 3.13.3 3.12.10"
set "GIT_VERSIONS=2.49.0 2.47.1 2.45.2"
set "DL=%TEMP%\km-riskreporting-setup"

rem An update can replace this file while it runs, which confuses Windows.
rem So run from a copy in the TEMP folder and remember where the original is.
if /i "%~1"=="--from-temp" goto :started_from_temp
if not exist "%DL%" mkdir "%DL%"
copy /y "%~f0" "%DL%\setup-running.bat" >nul
"%DL%\setup-running.bat" --from-temp "%~dp0."

:started_from_temp
for %%I in ("%~2") do set "HERE=%%~fI"

echo.
echo  KM Risk Reporting - setup
echo  =========================
echo  This takes a few minutes the first time. Leave this window open.
echo.

if not exist "%TOOLS%" mkdir "%TOOLS%"
if not exist "%DL%" mkdir "%DL%"

rem ---------------------------------------------------------------- 1
echo [1/6] Program folder
rem Run from inside an existing copy: update that copy, no question asked.
set "APP=%HERE%"
if exist "%APP%\.git\" if exist "%APP%\pyproject.toml" goto :app_dir_known
rem Otherwise ask where to install. The default is the last place used, if any.
set "DEFAULT_APP=%INSTALL_DIR%"
if exist "%TOOLS%\install-path.txt" set /p DEFAULT_APP=<"%TOOLS%\install-path.txt"
:ask_app_dir
echo.
echo       Where should the program be installed?
echo       Press Enter for: %DEFAULT_APP%
echo       or type or paste another folder, for example D:\Work\KM-RiskReporting
set "ANSWER=%DEFAULT_APP%"
set /p "ANSWER=      Folder: "
set "ANSWER=%ANSWER:"=%"
if "%ANSWER%"=="" set "ANSWER=%DEFAULT_APP%"
for %%I in ("%ANSWER%") do set "APP=%%~fI"
rem a drive such as D:\ gets its own subfolder; otherwise drop a trailing backslash
if "%APP:~1%"==":\" set "APP=%APP%KM-RiskReporting"
if "%APP:~-1%"=="\" set "APP=%APP:~0,-1%"
rem an existing folder that is not a copy of the program and not empty: install inside it
if exist "%APP%\.git\" goto :confirm_app_dir
if not exist "%APP%\" goto :confirm_app_dir
dir /b /a "%APP%" 2>nul | findstr . >nul
if not errorlevel 1 set "APP=%APP%\KM-RiskReporting"
:confirm_app_dir
echo.
echo       The program will be in: %APP%
choice /c YN /n /m "      Is that right? Press Y for yes or N to choose again: "
if errorlevel 2 goto :ask_app_dir
if exist "%APP%\" goto :app_dir_known
mkdir "%APP%" 2>nul
if exist "%APP%\" goto :app_dir_known
echo       That folder cannot be created - choose another one.
goto :ask_app_dir
:app_dir_known
echo       %APP%

rem ---------------------------------------------------------------- 2
echo [2/6] Git
set "GIT="
where git >nul 2>&1 && set "GIT=git"
if not defined GIT if exist "%TOOLS%\git\cmd\git.exe" set "GIT=%TOOLS%\git\cmd\git.exe"
if not defined GIT call :install_git
if not defined GIT goto :fail_git
echo       using %GIT%

rem ---------------------------------------------------------------- 3
echo [3/6] Program files
if exist "%APP%\.git\" goto :update_app
if not exist "%APP%\" goto :clone_app
dir /b /a "%APP%" 2>nul | findstr . >nul
if not errorlevel 1 goto :fail_app_dir_not_empty
:clone_app
echo       downloading the program ...
"%GIT%" clone --quiet --branch "%BRANCH%" "%REPO_URL%" "%APP%"
if exist "%APP%\pyproject.toml" goto :app_downloaded
echo       retrying with the Windows certificate store ...
"%GIT%" -c http.sslBackend=schannel clone --quiet --branch "%BRANCH%" "%REPO_URL%" "%APP%"
if not exist "%APP%\pyproject.toml" goto :fail_clone
:app_downloaded
call :safe_dir
goto :app_ready

:update_app
call :safe_dir
echo       checking for updates ...
"%GIT%" -C "%APP%" fetch --quiet origin "%BRANCH%" 2>nul
if not errorlevel 1 goto :fetched
"%GIT%" -C "%APP%" -c http.sslBackend=schannel fetch --quiet origin "%BRANCH%"
if not errorlevel 1 goto :fetched
echo       WARNING: could not reach GitHub, so keeping the version you already have.
goto :app_ready
:fetched
"%GIT%" -C "%APP%" checkout --quiet "%BRANCH%"
if errorlevel 1 goto :update_blocked
"%GIT%" -C "%APP%" merge --ff-only --quiet FETCH_HEAD
if not errorlevel 1 goto :updated
:update_blocked
echo.
echo       WARNING: the update could not be applied because files in the program
echo       folder were changed on this computer. The files are listed below.
echo       Your version still works. Send this screen to the developer.
"%GIT%" -C "%APP%" status --short
echo.
goto :app_ready
:updated
echo       up to date
:app_ready

rem ---------------------------------------------------------------- 4
echo [4/6] Python
set "PY="
if exist "%TOOLS%\python\python.exe" set "PY=%TOOLS%\python\python.exe"
if not defined PY call :find_python
if not defined PY call :install_python
if not defined PY goto :fail_python
for /f "delims=" %%v in ('call "%PY%" -c "import sys; print(sys.version.split()[0])"') do echo       using Python %%v

rem ---------------------------------------------------------------- 5
echo [5/6] Program packages
set "VPY=%APP%\.venv\Scripts\python.exe"
if not exist "%VPY%" goto :make_venv
"%VPY%" -c "import sys" >nul 2>&1
if not errorlevel 1 goto :venv_ok
echo       the program environment is broken, so rebuilding it ...
rmdir /s /q "%APP%\.venv"
:make_venv
echo       creating the program environment ...
"%PY%" -m venv "%APP%\.venv"
if not exist "%VPY%" goto :fail_venv
:venv_ok
echo       installing packages - the first time takes a few minutes ...
call :pip install -r "%APP%\requirements\lock.txt"
if errorlevel 1 goto :fail_pip
call :pip install --no-deps -e "%APP%"
if errorlevel 1 goto :fail_pip

rem ---------------------------------------------------------------- 6
echo [6/6] Final check
> "%TOOLS%\install-path.txt" echo %APP%
if not exist "%APP%\contracts\" mkdir "%APP%\contracts"
"%APP%\.venv\Scripts\riskparse.exe" review --help >nul 2>&1
if errorlevel 1 goto :fail_check
echo       the program starts correctly
if exist "%APP%\raw-data-format.xlsx" echo       NOTE: raw-data-format.xlsx in the main folder is no longer used. The columns now come from config\raw-data-format.xlsx. You can delete the old file.
if exist "%APP%\contracts-review\" echo       NOTE: results now go to contracts-data. The old contracts-review folder can be moved or deleted.

echo.
echo  ======================================================================
echo   Setup finished.
echo.
echo   Program folder: %APP%
echo.
echo   1. Put the confirmations in the "contracts" folder.
echo   2. Double-click RUN.bat.
echo.
echo   Run SETUP.bat again at any time to get the latest version.
echo  ======================================================================
echo.
start "" "%APP%"
pause
exit /b 0


rem ======================================================================
rem  Helpers
rem ======================================================================

:pip
rem pip with the Windows certificate store as a fallback (company networks)
"%VPY%" -m pip --disable-pip-version-check --quiet %*
if not errorlevel 1 exit /b 0
echo       retrying with the Windows certificate store ...
"%VPY%" -m pip --disable-pip-version-check --quiet --use-feature=truststore %*
exit /b %errorlevel%

:download
rem %1 = web address, %2 = file to create
if exist "%~2" del /q "%~2"
curl.exe --fail --location --silent --retry 2 --output "%~2" "%~1" >nul 2>&1
if not errorlevel 1 if exist "%~2" exit /b 0
if exist "%~2" del /q "%~2"
powershell -NoProfile -Command "$ProgressPreference='SilentlyContinue'; try { [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12 } catch {}; Invoke-WebRequest -UseBasicParsing -Uri '%~1' -OutFile '%~2'" >nul 2>&1
if not errorlevel 1 if exist "%~2" exit /b 0
if exist "%~2" del /q "%~2"
exit /b 1

:check_signature
rem %1 = file, %2 = publisher that must have signed it
powershell -NoProfile -Command "$s = Get-AuthenticodeSignature -LiteralPath '%~1'; if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -like '*%~2*') { exit 0 }; Write-Host ('      signature check failed: ' + $s.Status); exit 1"
exit /b %errorlevel%

:install_git
for %%V in (%GIT_VERSIONS%) do if not exist "%TOOLS%\git\cmd\git.exe" call :try_git %%V
if exist "%TOOLS%\git\cmd\git.exe" set "GIT=%TOOLS%\git\cmd\git.exe"
exit /b 0

:try_git
echo       downloading Git %1 ...
set "GITZIP=%DL%\MinGit-%1-64-bit.zip"
call :download "https://github.com/git-for-windows/git/releases/download/v%1.windows.1/MinGit-%1-64-bit.zip" "%GITZIP%"
if errorlevel 1 exit /b 1
if exist "%TOOLS%\git\" rmdir /s /q "%TOOLS%\git"
mkdir "%TOOLS%\git"
tar -xf "%GITZIP%" -C "%TOOLS%\git" >nul 2>&1
if exist "%TOOLS%\git\cmd\git.exe" exit /b 0
powershell -NoProfile -Command "Expand-Archive -LiteralPath '%GITZIP%' -DestinationPath '%TOOLS%\git' -Force" >nul 2>&1
if exist "%TOOLS%\git\cmd\git.exe" exit /b 0
echo       could not unpack Git %1
exit /b 1

:safe_dir
rem stops git's "dubious ownership" refusal for the program folder
set "APP_FWD=%APP:\=/%"
"%GIT%" config --global --get-all safe.directory 2>nul | findstr /x /i /c:"%APP_FWD%" >nul
if errorlevel 1 "%GIT%" config --global --add safe.directory "%APP_FWD%"
exit /b 0

:find_python
for %%V in (3.13 3.12 3.11) do if not defined PY call :try_py_launcher %%V
if defined PY exit /b 0
for /f "delims=" %%P in ('python -c "import sys; print(sys.executable if sys.version_info[:2] in [(3, 11), (3, 12), (3, 13)] else str())" 2^>nul') do set "PY=%%P"
if defined PY if not exist "%PY%" set "PY="
exit /b 0

:try_py_launcher
for /f "delims=" %%P in ('py -%1 -c "import sys; print(sys.executable)" 2^>nul') do set "PY=%%P"
if defined PY if not exist "%PY%" set "PY="
exit /b 0

:install_python
for %%V in (%PY_VERSIONS%) do if not exist "%TOOLS%\python\python.exe" call :try_python %%V
if exist "%TOOLS%\python\python.exe" set "PY=%TOOLS%\python\python.exe"
exit /b 0

:try_python
echo       downloading Python %1 ...
set "PYEXE=%DL%\python-%1-amd64.exe"
call :download "https://www.python.org/ftp/python/%1/python-%1-amd64.exe" "%PYEXE%"
if errorlevel 1 exit /b 1
call :check_signature "%PYEXE%" "Python Software Foundation"
if errorlevel 1 exit /b 1
echo       installing Python %1 for your user only - about a minute ...
start "" /wait "%PYEXE%" /quiet InstallAllUsers=0 Include_launcher=0 Include_test=0 Include_doc=0 Include_tcltk=0 Shortcuts=0 AssociateFiles=0 PrependPath=0 TargetDir="%TOOLS%\python"
if exist "%TOOLS%\python\python.exe" exit /b 0
echo       the Python installer did not finish
exit /b 1


rem ======================================================================
rem  Failures: say what happened and what to do, then stop.
rem ======================================================================

:fail_git
echo.
echo  SETUP STOPPED: Git could not be downloaded.
echo  The computer may be blocking downloads from github.com.
echo  Ask IT to install "Git for Windows", then run SETUP.bat again.
goto :stop

:fail_app_dir_not_empty
echo.
echo  SETUP STOPPED: %APP% already exists but is not a copy of the program.
echo  Run SETUP.bat again and choose another folder.
goto :stop

:fail_clone
echo.
echo  SETUP STOPPED: the program could not be downloaded from GitHub.
echo  Check the internet connection, then run SETUP.bat again.
goto :stop

:fail_python
echo.
echo  SETUP STOPPED: Python could not be installed.
echo  The computer may be blocking downloads from python.org or programs in
echo  your user folder. Ask IT to install Python 3.13 for Windows, 64-bit,
echo  then run SETUP.bat again.
goto :stop

:fail_venv
echo.
echo  SETUP STOPPED: the program environment could not be created in
echo  %APP%\.venv
goto :stop

:fail_pip
echo.
echo  SETUP STOPPED: the program packages could not be downloaded.
echo  The computer may be blocking pypi.org. Ask IT to allow pypi.org and
echo  files.pythonhosted.org, then run SETUP.bat again.
goto :stop

:fail_check
echo.
echo  SETUP STOPPED: the program was installed but does not start.
"%APP%\.venv\Scripts\riskparse.exe" review --help
goto :stop

:stop
echo.
echo  Nothing on this computer was harmed. Send a screenshot of this window
echo  to the developer if it keeps happening.
echo.
pause
exit /b 1
