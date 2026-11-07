@echo off
rem Stops the background bridge started by start_bridge.vbs
powershell -NoProfile -Command "Get-CimInstance Win32_Process -Filter \"Name='pythonw.exe'\" | Where-Object { $_.CommandLine -like '*minilab3_bridge.py*' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"
echo Bridge stopped.
