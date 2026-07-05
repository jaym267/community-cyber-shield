@echo off
REM Quick startup script for Sentinal (formerly EJMapper)
REM Run this file to start both frontend and backend

echo Starting Sentinal servers...
echo.

REM Start backend in a new window
cd /d "%~dp0ejm-apper\backend"
start "Sentinal Backend" cmd /k "venv\Scripts\python.exe -m uvicorn main:app --reload --port 8001"

REM Wait a second for backend to start
timeout /t 2 /nobreak

REM Start frontend in a new window
cd /d "%~dp0ejm-apper\frontend"
start "Sentinal Frontend" cmd /k "npm run dev"

echo.
echo ✓ Backend running on http://127.0.0.1:8001
echo ✓ Frontend running on http://127.0.0.1:5173
echo.
echo Close these windows to stop the servers.
