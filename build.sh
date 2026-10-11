#!/bin/sh
# Builds dist/DeskPet.app (macOS) or dist/DeskPet (Linux) and zips it as dist/DeskPet-<version>-<macos|linux>.zip.
# First: python3 -m venv .venv && .venv/bin/pip install -r requirements-dev.txt
set -e
cd "$(dirname "$0")"
PY=.venv/bin/python
"$PY" -m PyInstaller --noconfirm --clean DeskPet.spec
VER=$("$PY" -c "import deskpet; print(deskpet.__version__)")
cd dist
if [ "$(uname)" = "Darwin" ]; then
    ZIP="DeskPet-$VER-macos.zip"
    ditto -c -k --keepParent DeskPet.app "$ZIP"
else
    ZIP="DeskPet-$VER-linux.zip"
    zip -qr "$ZIP" DeskPet
fi
echo "Built dist/$ZIP"
