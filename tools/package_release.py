"""Build a verified portable ZIP from an explicit list of public artifacts."""

import hashlib
from pathlib import Path
import shutil
import zipfile


def main():
    repo = Path(__file__).resolve().parent.parent
    release = repo / "release"
    release.mkdir(exist_ok=True)
    exe = repo / "dist" / "PULSE.exe"
    files = {"PULSE.exe": exe}
    for name in ("README.md", "LICENSE", "NOTICE.md", "CHANGELOG.md"):
        files[name] = repo / name
    for name in (
        "ARCHITECTURE.md",
        "VALIDATION.md",
        "DEVICE_SUPPORT.md",
        "KEYBOARD_SUPPORT.md",
        "ZOWIE_SUPPORT.md",
        "U2_8008_INVESTIGATION.md",
        "u2-service-evidence.json",
        "PROTOCOLS.md",
        "REFERENCES.md",
        "UPSTREAM_README.md",
        "RK_R98_RESEARCH.md",
        "firmware-evidence.json",
        "rk98-evidence.json",
    ):
        files["docs/" + name] = repo / "docs" / name
    archive = release / "PULSE-portable.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as package:
        for name, path in files.items():
            package.write(path, name)
    with zipfile.ZipFile(archive) as package:
        assert package.testzip() is None
        assert set(package.namelist()) == set(files)
        assert package.read("PULSE.exe") == exe.read_bytes()
    shutil.copy2(exe, release / "PULSE.exe")
    checksums = "".join(
        f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.name}\n"
        for path in (release / "PULSE.exe", archive)
    )
    (release / "SHA256SUMS.txt").write_text(checksums, encoding="utf-8")
    print(checksums)


if __name__ == "__main__":
    main()
