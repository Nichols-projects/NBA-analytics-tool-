@echo off
cd /d "%~dp0"
echo.
echo  ==== DK Analytics Terminal: NBA.com morning data setup ====
echo.
set "PY="
where py >nul 2>nul && set "PY=py"
if not defined PY where python >nul 2>nul && set "PY=python"
if not defined PY goto nopython

if exist github_token.txt goto havetoken
echo  Paste your GitHub token (it starts with github_pat_) and press Enter:
set /p "TOKEN= Token: "
>github_token.txt echo %TOKEN%
:havetoken

echo.
echo  Step 1 of 2: running the first NBA.com download now (about 1-2 minutes)...
%PY% run_nba_com.py
echo.
echo  Step 2 of 2: scheduling it for every morning at 7:30 AM...
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a=New-ScheduledTaskAction -Execute '%~dp0run_nba_com.bat'; $t=New-ScheduledTaskTrigger -Daily -At 7:30am; $s=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries; Register-ScheduledTask -TaskName 'DK Analytics NBA.com data' -Action $a -Trigger $t -Settings $s -Force | Out-Null; Write-Host '  Scheduled: DK Analytics NBA.com data, daily 7:30 AM'"
echo.
echo  All set. If the laptop is off at 7:30, it runs the next time you turn it on.
echo  A log of each run is saved in nba_com_log.txt in this folder.
echo.
pause
exit /b 0

:nopython
echo  Python isn't installed yet. Opening the download page...
start "" https://www.python.org/downloads/windows/
echo.
echo  Install it (on the first screen tick "Add python.exe to PATH"),
echo  then double-click setup.bat again.
echo.
pause
exit /b 1
