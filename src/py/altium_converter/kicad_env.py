"""Find a KiCad install and run the ``kicad_side`` scripts under its bundled Python.

``pcbnew`` only imports inside KiCad's own interpreter, so anything that loads
or saves a board through KiCad runs as a subprocess of that interpreter.
Override discovery with ``KICAD_BIN`` (the directory holding kicad-cli and python).
"""

from __future__ import annotations

import glob
import os
import subprocess
import sys
import threading
from pathlib import Path

KICAD_SIDE = Path(__file__).resolve().parent / "kicad_side"
EXE = ".exe" if os.name == "nt" else ""


def kicad_bin() -> Path:
    env = os.environ.get("KICAD_BIN")
    candidates = [Path(env)] if env else []
    if os.name == "nt":
        # newest first: "C:\Program Files\KiCad\10.0\bin"
        candidates += [Path(p) for p in sorted(glob.glob(r"C:\Program Files\KiCad\*\bin"), reverse=True)]
    else:
        candidates += [Path("/usr/bin"), Path("/Applications/KiCad/KiCad.app/Contents/MacOS")]
    for c in candidates:
        if (c / f"kicad-cli{EXE}").exists():
            return c
    raise FileNotFoundError("KiCad not found; set KICAD_BIN to the folder holding kicad-cli")


def kicad_python() -> Path:
    py = kicad_bin() / f"python{EXE}"
    if not py.exists():
        raise FileNotFoundError(f"no bundled python beside kicad-cli in {py.parent}")
    return py


def kicad_cli() -> Path:
    return kicad_bin() / f"kicad-cli{EXE}"


def run_side(script: str, *args, timeout: float | None = None) -> int:
    """Run kicad_side/<script> under KiCad's Python, streaming its output.

    Its noisy wx start-up lines ("Adding duplicate image handler") are dropped.
    A KiCad assert opens a hidden modal dialog and blocks forever at ~0 CPU, so
    callers on big boards should pass a timeout.
    """
    cmd = [str(kicad_python()), str(KICAD_SIDE / script), *map(str, args)]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            cwd=Path.home().anchor or None)
    timed_out = threading.Event()

    def kill():
        timed_out.set()
        proc.kill()

    killer = threading.Timer(timeout, kill) if timeout else None
    if killer:
        killer.start()
    try:
        for line in proc.stdout:
            if "duplicate image handler" not in line:
                sys.stdout.write(line)
        code = proc.wait()
    finally:
        if killer:
            killer.cancel()
    if timed_out.is_set():
        raise TimeoutError(f"{script} killed after {timeout:.0f} s (a KiCad assert dialog blocks at ~0 CPU)")
    return code
