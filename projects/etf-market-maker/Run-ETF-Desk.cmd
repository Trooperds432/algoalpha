@echo off
cd /d "%~dp0"
".venv-ml\Scripts\python.exe" run.py --seed 7 --events 1000 --epochs 10 --mode paper
echo.
echo Open the report.html path printed above to view the results.
pause
