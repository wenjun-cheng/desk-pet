# Builds dist\DeskPet\DeskPet.exe and zips it as dist\DeskPet-<version>-win64.zip
# Usage: create .venv, install requirements.txt and pyinstaller into it, then run .\build.ps1
# No $ErrorActionPreference = "Stop": PyInstaller logs to stderr, which Windows PowerShell 5.1
# would treat as an error. Exit codes are checked after each step instead.
Set-Location $PSScriptRoot
$py = ".venv\Scripts\python.exe"

& $py -m PyInstaller --noconfirm --clean --windowed --name DeskPet --icon assets\icon.ico `
    --exclude-module tkinter app.py
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$ver = (Select-String -Path app.py -Pattern '__version__ = "(.+)"').Matches[0].Groups[1].Value
$zip = "dist\DeskPet-$ver-win64.zip"
Compress-Archive -Path dist\DeskPet -DestinationPath $zip -Force
"Built $zip ({0:N1} MB)" -f ((Get-Item $zip).Length / 1MB)
