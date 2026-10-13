import shutil
from pathlib import Path

ROOT = Path("/manager-root")


def main() -> None:
    library = ROOT / "usr/lib/python3.14"
    for name in ("ensurepip", "idlelib", "tkinter"):
        shutil.rmtree(library / name, ignore_errors=True)
    for wheel in (ROOT / "usr/share/python-wheels").glob("pip-*.whl"):
        wheel.unlink()
    venv = Path("/opt/adk-venv")
    for name in ("python", "python3", "python3.14"):
        executable = venv / "bin" / name
        executable.unlink(missing_ok=True)
        executable.symlink_to("/usr/bin/python3.14")
    (venv / "pyvenv.cfg").write_text(
        "home = /usr/bin\ninclude-system-site-packages = false\nversion = 3.14.8\n"
    )


if __name__ == "__main__":
    main()
