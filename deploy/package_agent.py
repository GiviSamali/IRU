"""Create a portable agent ZIP with forward-slash entry names on every platform."""
import argparse
import json
import os
from pathlib import Path
import tempfile
import zipfile


def package_agent(source: Path, output: Path, version: str) -> None:
    source, output = source.resolve(), output.resolve()
    if output.is_relative_to(source):
        raise ValueError("ZIP destination must be outside the source directory")
    actual = (source / "VERSION.txt").read_text(encoding="utf-8-sig").strip()
    if actual != version:
        raise ValueError(f"Build version {actual!r} differs from requested {version!r}")
    info = json.loads((source / "BUILD_INFO.json").read_text(encoding="utf-8-sig"))
    if info.get("version") != version or info.get("artifact") != "IruAgent":
        raise ValueError("BUILD_INFO.json does not match the agent build")
    with (source / "IruAgent.exe").open("rb") as executable:
        if executable.read(2) != b"MZ":
            raise ValueError("Missing Windows executable")
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".agent-package-", suffix=".zip", dir=output.parent)
    os.close(fd)
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for path in sorted(source.rglob("*")):
                if path.is_symlink():
                    raise ValueError("Symlinks are not supported in agent packages")
                archive.write(path, "IruAgent/" + path.relative_to(source).as_posix())
        with zipfile.ZipFile(temporary) as archive:
            if archive.testzip() is not None:
                raise ValueError("ZIP integrity check failed")
        os.replace(temporary, output)
    finally:
        Path(temporary).unlink(missing_ok=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()
    package_agent(args.source, args.output, args.version)
    print(f"Packaged {args.version}: {args.output}")


if __name__ == "__main__":
    main()
