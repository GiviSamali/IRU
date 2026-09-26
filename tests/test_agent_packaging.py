import json
import zipfile
import pytest
from deploy.package_agent import package_agent
from server.agent_release import validate_zip


def build(tmp_path):
    source = tmp_path / "build"
    source.mkdir()
    (source / "VERSION.txt").write_text("3.13.5")
    (source / "BUILD_INFO.json").write_text(json.dumps({"version": "3.13.5", "artifact": "IruAgent"}))
    (source / "IruAgent.exe").write_bytes(b"MZ" + b"x" * 1500)
    (source / "_internal").mkdir()
    (source / "_internal" / "data.dll").write_bytes(b"payload")
    return source


def test_portable_zip_paths_and_release_validation(tmp_path):
    source = build(tmp_path)
    destination = tmp_path / "agent.zip"
    package_agent(source, destination, "3.13.5")
    with zipfile.ZipFile(destination) as archive:
        assert all("\\" not in entry.orig_filename for entry in archive.infolist())
        assert archive.read("IruAgent/_internal/data.dll") == b"payload"
    assert validate_zip(destination, "3.13.5") == "3.13.5"


def test_wrong_build_version_preserves_existing_archive(tmp_path):
    source = build(tmp_path)
    destination = tmp_path / "agent.zip"
    destination.write_bytes(b"existing archive")
    with pytest.raises(ValueError, match="differs"):
        package_agent(source, destination, "3.13.6")
    assert destination.read_bytes() == b"existing archive"


def test_backslash_archive_rejected_on_windows_too(tmp_path):
    destination = tmp_path / "old.zip"
    # Construct original ZIP names explicitly; ZipInfo otherwise normalizes on Windows.
    with zipfile.ZipFile(destination, "w") as archive:
        info = zipfile.ZipInfo("IruAgent/VERSION.txt")
        info.filename = "IruAgent\\VERSION.txt"
        archive.writestr(info, "3.13.5")
    with pytest.raises(ValueError, match="Unsafe archive path"):
        validate_zip(destination, "3.13.5")
