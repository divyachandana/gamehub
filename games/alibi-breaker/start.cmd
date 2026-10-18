@echo off
rem Double-click to start alibi-breaker on http://localhost:3000
cd /d "%~dp0"
if not exist node_modules call npm install
start "" http://localhost:3000
node --env-file-if-exists=.env server.js
pause
