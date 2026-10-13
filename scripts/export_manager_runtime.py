import shutil
from pathlib import Path

ROOT = Path("/manager-root")
PACKAGES = (
    "libbz2-1.0",
    "libdb5.3t64",
    "libffi8",
    "libgdbm6t64",
    "liblzma5",
    "libsqlite3-0",
)


def main() -> None:
    (ROOT / "usr").mkdir(parents=True)
    shutil.copytree("/usr/local", ROOT / "usr/local", symlinks=True)
    library = ROOT / "usr/local/lib/python3.14"
    # The service does not use interactive terminals; uuid has a stdlib fallback.
    for pattern in ("_curses*.so", "_uuid*.so", "readline*.so"):
        for extension in (library / "lib-dynload").glob(pattern):
            extension.unlink()
    for name in ("ensurepip", "idlelib", "tkinter"):
        shutil.rmtree(library / name, ignore_errors=True)
    inventory = ROOT / "var/lib/dpkg/status.d"
    inventory.mkdir(parents=True)
    statuses = {
        paragraph.split("Package: ", 1)[1].splitlines()[0]: paragraph + "\n"
        for paragraph in Path("/var/lib/dpkg/status").read_text().split("\n\n")
        if "Package: " in paragraph
    }
    for package in PACKAGES:
        manifests = list(Path("/var/lib/dpkg/info").glob(f"{package}:*.list"))
        if not manifests:
            manifests = [Path(f"/var/lib/dpkg/info/{package}.list")]
        if len(manifests) != 1:
            raise RuntimeError(f"ambiguous package inventory for {package}")
        files = manifests[0].read_text().splitlines()
        for filename in files:
            source = Path(filename)
            if source.is_symlink() or source.is_file():
                destination = ROOT / source.relative_to("/")
                destination.parent.mkdir(parents=True, exist_ok=True)
                if source.is_symlink():
                    destination.symlink_to(source.readlink())
                else:
                    shutil.copy2(source, destination)
        # Keep the real Debian inventory so scanners can identify copied libraries.
        (inventory / package).write_text(statuses[package])


if __name__ == "__main__":
    main()
