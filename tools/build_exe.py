"""Build dist/AltiumLibraryManager.exe - one file, no Python needed on the target PC.

    pip install pyinstaller pillow
    python tools/build_exe.py

Pillow is only used to (re)draw the icon; PyInstaller only to pack the app. Neither is needed
to run the app from source.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
NAME = "AltiumLibraryManager"


def version() -> str:
    text = (ROOT / "alm" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__\s*=\s*"([^"]+)"', text).group(1)


def version_file(ver: str) -> Path:
    nums = (ver.split(".") + ["0"] * 4)[:4]
    tup = ", ".join(str(int(n)) for n in nums)
    path = ROOT / "build" / "version_info.txt"
    path.parent.mkdir(exist_ok=True)
    path.write_text(f"""VSVersionInfo(
  ffi=FixedFileInfo(filevers=({tup}), prodvers=({tup})),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('CompanyName', 'Altium Library Manager (open source, MIT)'),
    StringStruct('FileDescription', 'Altium Library Manager'),
    StringStruct('FileVersion', '{ver}'),
    StringStruct('ProductName', 'Altium Library Manager'),
    StringStruct('ProductVersion', '{ver}'),
    StringStruct('LegalCopyright', 'MIT License'),
    StringStruct('OriginalFilename', '{NAME}.exe')])]),
    VarFileInfo([VarStruct('Translation', [1033, 1200])])]
)
""", encoding="utf-8")
    return path


def main() -> int:
    if not (ROOT / "alm" / "ui" / "app.ico").exists():
        subprocess.check_call([sys.executable, str(ROOT / "tools" / "make_icon.py")])
    sep = ";"   # PyInstaller --add-data separator on Windows
    args = [sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean", "--onefile",
            "--windowed", "--name", NAME,
            "--icon", str(ROOT / "alm" / "ui" / "app.ico"),
            "--version-file", str(version_file(version())),
            "--add-data", f"{ROOT / 'alm' / 'altium' / 'alm_import.pas'}{sep}alm/altium",
            "--add-data", f"{ROOT / 'alm' / 'altium' / 'ee_build.pas'}{sep}alm/altium",
            "--add-data", f"{ROOT / 'alm' / 'ui' / 'app.ico'}{sep}alm/ui",
            "--distpath", str(ROOT / "dist"), "--workpath", str(ROOT / "build"),
            "--specpath", str(ROOT / "build"),
            str(ROOT / "alm_app.py")]
    subprocess.check_call(args, cwd=ROOT)
    print("\nBuilt:", ROOT / "dist" / f"{NAME}.exe")
    return 0


if __name__ == "__main__":
    sys.exit(main())
