# PyInstaller: `pyinstaller DeskPet.spec` (build.ps1 and build.sh do it). Two programs over the same files: DeskPet,
# the pet (no console), and DeskPetHelper, which Claude Code runs for the pet's hook, tools and status line, and
# which runs the conversation's terminal (a console program).
import sys

from PyInstaller.utils.hooks import collect_submodules

a = Analysis(
    ["run.py"],
    datas=[("deskpet/assets", "deskpet/assets")],
    hiddenimports=collect_submodules("deskpet"),
    excludes=["tkinter"],
)
pyz = PYZ(a.pure)
icon = "deskpet/assets/icon.ico" if sys.platform == "win32" else None
pet = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DeskPet", console=False, icon=icon)
helper = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DeskPetHelper", console=True, icon=icon)
coll = COLLECT(pet, helper, a.binaries, a.datas, name="DeskPet")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="DeskPet.app", bundle_identifier="com.deskpet.app",
                 info_plist={"LSUIElement": True})  # no Dock icon
