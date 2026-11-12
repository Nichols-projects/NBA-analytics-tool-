@echo off
cd /d "%~dp0"
where py >nul 2>nul && (py run_nba_com.py >> nba_com_log.txt 2>&1) || (python run_nba_com.py >> nba_com_log.txt 2>&1)
