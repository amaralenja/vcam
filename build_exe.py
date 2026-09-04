#!/usr/bin/env python3
"""
Gera o dist/vcam.exe.

    python build_exe.py                 # exe leve, usa o ffmpeg do PATH
    python build_exe.py --with-ffmpeg   # embute o ffmpeg (bem maior, portatil)

Com --with-ffmpeg o executavel roda em qualquer PC Windows, sem precisar
instalar nada. Sem a opcao, o ffmpeg precisa estar no PATH da maquina.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--with-ffmpeg", action="store_true",
                        help="embutir ffmpeg e ffprobe no executavel")
    parser.add_argument("--download-url", metavar="URL",
                        help="https onde o vcam.exe ficara publicado")
    parser.add_argument("--notes", help="o que mudou nesta versao")
    args = parser.parse_args()

    try:
        import PyInstaller  # noqa: F401
    except ImportError:
        print("PyInstaller nao instalado. Rode:")
        print("  python -m pip install pyinstaller")
        return 1

    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm", "--onefile", "--windowed",
        "--name", "vcam",
        "--hidden-import", "vcam",
        "--clean",
    ]

    if args.with_ffmpeg:
        missing = []
        for tool in ("ffmpeg", "ffprobe"):
            path = shutil.which(tool)
            if path:
                cmd += ["--add-binary", "{}{}.".format(path, os.pathsep)]
                print("embutindo {}".format(path))
            else:
                missing.append(tool)
        if missing:
            print("nao achei no PATH: " + ", ".join(missing))
            return 1

    cmd.append(str(ROOT / "vcam_gui.py"))
    print("\n" + " ".join(cmd) + "\n")
    result = subprocess.run(cmd, cwd=str(ROOT))
    if result.returncode:
        return result.returncode

    exe = ROOT / "dist" / "vcam.exe"
    if not exe.exists():
        print("build terminou mas nao achei o exe")
        return 1
    print("\npronto: {}  ({:.1f} MB)".format(
        exe, exe.stat().st_size / 1048576))

    write_manifest(exe, args.download_url, args.notes)
    return 0


def write_manifest(exe, download_url, notes):
    """Gera o dist/update.json que os outros PCs vao consultar."""
    sys.path.insert(0, str(ROOT))
    import vcam

    digest = hashlib.sha256()
    with open(exe, "rb") as fh:
        for chunk in iter(lambda: fh.read(1048576), b""):
            digest.update(chunk)

    manifest = {
        "version": vcam.__version__,
        "url": download_url or "https://SUBSTITUA-PELA-URL-DO-SEU-VCAM.EXE",
        "sha256": digest.hexdigest(),
        "notes": notes or "",
    }
    path = exe.parent / "update.json"
    path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")

    print("manifesto: {}".format(path))
    print("  versao: {}".format(manifest["version"]))
    print("  sha256: {}".format(manifest["sha256"]))
    if not download_url:
        print("\n  [!] Falta a URL do executavel. Publique o vcam.exe e rode")
        print("      de novo com --download-url https://.../vcam.exe")
    if not vcam.UPDATE_URL:
        print("\n  [!] UPDATE_URL vazio no vcam.py: o .exe gerado nao sabe")
        print("      onde procurar atualizacao. Preencha antes de distribuir.")


if __name__ == "__main__":
    sys.exit(main())
