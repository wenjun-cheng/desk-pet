# Builds dist\DeskPet (DeskPet.exe and its helper) and zips it as dist\DeskPet-<version>-win64.zip.
# First: py -m venv .venv; .venv\Scripts\pip install -r requirements-dev.txt
# (No $ErrorActionPreference = "Stop": PyInstaller logs to stderr, which Windows PowerShell 5.1 would take for an
# error. The exit codes are checked instead.)
Set-Location $PSScriptRoot
$py = ".venv\Scripts\python.exe"

& $py -m PyInstaller --noconfirm --clean DeskPet.spec
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$ver = & $py -c "import deskpet; print(deskpet.__version__)"
$zip = "dist\DeskPet-$ver-win64.zip"
Compress-Archive -Path dist\DeskPet -DestinationPath $zip -Force
"Built $zip ({0:N1} MB)" -f ((Get-Item $zip).Length / 1MB)
