@echo off
echo Codex ^<-^> llama.cpp namespace proxy
echo Proxy   : http://127.0.0.1:8081/v1/
echo Upstream: http://127.0.0.1:8080/v1/
echo Press Ctrl+C to stop.
echo.
python "%~dp0proxy.py" %*
