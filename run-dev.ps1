$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location -LiteralPath $ProjectRoot
& "$ProjectRoot\.venv\Scripts\python.exe" manage.py runserver

