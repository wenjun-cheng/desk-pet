# PyInstaller: `pyinstaller DeskPet.spec` (build.ps1 and build.sh do it). Two programs over the same files: DeskPet,
# the pet (no console), and DeskPetHelper, which Claude Code runs for the pet's hook, tools and status line, and
# which runs the conversation's terminal (a console program).
import sys

from PyInstaller.utils.hooks import collect_data_files, collect_dynamic_libs, collect_submodules

a = Analysis(
    ["run.py"],
    # voice input: Whisper's voice detector, the Chinese script tables, CTranslate2's libraries
    datas=[("deskpet/assets", "deskpet/assets"), *collect_data_files("faster_whisper"),
           *collect_data_files("opencc")],
    binaries=collect_dynamic_libs("ctranslate2"),
    hiddenimports=collect_submodules("deskpet"),
    excludes=["tkinter", "av"],  # (PyAV: FFmpeg for audio files, which the pet doesn't read: see voice.py)
)
pyz = PYZ(a.pure)
icon = "deskpet/assets/icon.ico" if sys.platform == "win32" else None
pet = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DeskPet", console=False, icon=icon)
helper = EXE(pyz, a.scripts, [], exclude_binaries=True, name="DeskPetHelper", console=True, icon=icon)
coll = COLLECT(pet, helper, a.binaries, a.datas, name="DeskPet")
if sys.platform == "darwin":
    app = BUNDLE(coll, name="DeskPet.app", bundle_identifier="com.deskpet.app",
                 info_plist={"LSUIElement": True,  # no Dock icon
                             "NSMicrophoneUsageDescription": "To hear what you say to Claude (voice input)."})
