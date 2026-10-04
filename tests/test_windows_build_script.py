from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "build_windows.ps1"


def _source() -> str:
    return SCRIPT.read_text(encoding="utf-8-sig")


def test_windows_build_keeps_agent_upload_contract():
    source = _source()

    assert "$uri = \"$Server/api/agent/upload?version=$Version\"" in source
    assert "IruAgent.zip" in source
    assert "IruAgent-debug.zip" in source
    assert "curl.exe" in source
    assert '"-H", "X-Token: $Token"' in source


def test_windows_build_still_builds_iru_agent_artifact():
    source = _source()

    assert '"--name", "IruAgent"' in source
    assert 'Join-Path $agentDir "agent.py"' in source
    assert 'Join-Path $stagingDistDir "IruAgent\\IruAgent.exe"' in source
    assert 'Join-Path $stagingDistDir "IruAgent\\VERSION.txt"' in source
    assert 'Publish-AgentBuild -SourceDir (Join-Path $stagingDistDir "IruAgent")' in source


def test_windows_build_supports_separate_shell_artifact():
    source = _source()

    assert "[switch]$BuildShell" in source
    assert "[string]$ShellWebUrl" in source
    assert "[switch]$SkipShellZip" in source
    assert '"--name", "IruShell"' in source
    assert 'Join-Path $agentDir "shell\\main.py"' in source
    assert 'Join-Path $stagingDistDir "IruShell\\IruShell.exe"' in source
    assert 'ArtifactName "IruShell"' in source
    assert "IruShell.zip" in source


def test_windows_build_does_not_upload_shell_to_agent_update_endpoint():
    source = _source()
    shell_block = source.split("# -- Optional Agent Shell build", 1)[1].split("if (Test-Path $buildDir)", 1)[0]

    assert '$uri = "$Server/api/agent/upload?version=$Version"' not in shell_block
    assert "curl.exe" not in shell_block
    assert "Invoke-WebRequest" not in shell_block
    assert "IruShell не загружается" in shell_block


def test_windows_build_does_not_write_token_to_shell_config_or_build_info():
    source = _source()
    shell_block = source.split("# -- Optional Agent Shell build", 1)[1].split("if (Test-Path $buildDir)", 1)[0]
    build_info_function = source.split("function Write-BuildInfo", 1)[1].split("$repoRoot", 1)[0]

    assert "$Token" not in shell_block
    assert "token" not in shell_block.lower()
    assert "$Token" not in build_info_function
    assert "password" not in build_info_function.lower()


def test_upload_verification_executes_without_building(tmp_path):
    """Run the real upload tail with a synthetic ZIP and a mocked curl executable."""
    import hashlib
    import os
    import shutil
    import subprocess
    import json
    from deploy.package_agent import package_agent
    import pytest

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is required for the Windows publisher smoke")
    archive = tmp_path / "agent.zip"
    source = tmp_path / "build"
    source.mkdir()
    (source / "VERSION.txt").write_text("3.13.5")
    (source / "BUILD_INFO.json").write_text(json.dumps({"version": "3.13.5", "artifact": "IruAgent"}))
    (source / "IruAgent.exe").write_bytes(b"MZ" + b"x" * 1500)
    package_agent(source, archive, "3.13.5")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    tail = "# Verify the archive itself" + _source().split("# Verify the archive itself", 1)[1]
    for failure in ("none", "upload", "verify"):
        prefix = """
$ErrorActionPreference = 'Stop'
$Version = '3.13.5'
$Server = 'https://example.invalid'
$Token = 'test-only'
$UploadResolveIp = '192.0.2.1'
$script:postSeen = $false
$script:verifySeen = $false
function curl.exe {
    if ($args -notcontains '--resolve' -or $args -notcontains 'example.invalid:443:192.0.2.1') { throw 'Wrong target' }
    $global:LASTEXITCODE = 0
    $hash = $expectedHash
    if ($args -contains 'POST') {
        $script:postSeen = $true
        if ($failure -eq 'upload') { $hash = 'wrong' }
    } else {
        $script:verifySeen = $true
        if ($failure -eq 'verify') { $hash = 'wrong' }
    }
    @{ version = '3.13.5'; sha256 = $hash } | ConvertTo-Json -Compress
}
"""
        prefix += "$zipPath = '" + str(archive).replace("'", "''") + "'\n"
        prefix += "$expectedHash = '" + digest + "'\n$failure = '" + failure + "'\n"
        script = tmp_path / (failure + ".ps1")
        script.write_text(prefix + tail + "\nif (-not $script:postSeen -or -not $script:verifySeen) { throw 'Missing verification' }\n", encoding="utf-8-sig")
        env = {key: value for key, value in os.environ.items() if key.lower() != "psmodulepath"}
        result = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)], capture_output=True, env=env)
        assert (result.returncode == 0) == (failure == "none"), result.stderr.decode(errors="replace")


def test_windows_desktop_build_packages_webview2_and_excludes_qt_browser():
    source = _source()
    imports = source.split("$qtHiddenImports = @(", 1)[1].split("\n)", 1)[0]
    excluded = source.split("$qtExcludedModules = @(", 1)[1].split("\n)", 1)[0]
    for module in ("PySide6.QtCore", "PySide6.QtGui", "PySide6.QtWidgets"):
        assert module in imports and module not in excluded
    for module in ("PySide6.QtWebEngineCore", "PySide6.QtWebEngineWidgets", "PySide6.QtWebChannel"):
        assert module not in imports and module in excluded
    assert '"pywebview==6.2.1"' in source and '"pythonnet==3.2.0"' in source
    for requirement in ('"--hidden-import", "clr"', '"--hidden-import", "webview"',
                        '"--collect-data", "webview"', '"--collect-binaries", "webview"',
                        '"--collect-all", "pythonnet"', '"--collect-all", "clr_loader"'):
        assert requirement in source


def test_desktop_build_excludes_other_installed_qt_bindings_from_pyinstaller_args(tmp_path):
    """Execute the real argument assembly, including its exclusion loop."""
    import os
    import shutil
    import subprocess
    import pytest

    powershell = shutil.which("powershell.exe") or shutil.which("pwsh")
    if not powershell:
        pytest.skip("PowerShell is required for the argument assembly check")
    source = _source()
    assembly = source.split("$qtHiddenImports = @(", 1)[1].split("# Точка входа", 1)[0]
    script = tmp_path / "qt-args.ps1"
    script.write_text(
        "$ErrorActionPreference = 'Stop'\n$qtHiddenImports = @("
        + assembly
        + "\nforeach ($name in @('PyQt5','PyQt6','PySide2')) {\n"
          "  $found = $false\n"
          "  for ($i = 0; $i -lt ($pyiArgs.Count - 1); $i++) {\n"
          "    if ($pyiArgs[$i] -eq '--exclude-module' -and $pyiArgs[$i + 1] -eq $name) { $found = $true }\n"
          "  }\n"
          "  if (-not $found) { throw ('Missing Qt exclusion: ' + $name) }\n"
          "}\nif ($qtExcludedModules -contains 'PySide6') { throw 'Required Qt binding excluded' }\n"
          "Write-Output 'Qt argument guard passed'\n",
        encoding="utf-8-sig",
    )
    env = {key: value for key, value in os.environ.items() if key.lower() != "psmodulepath"}
    result = subprocess.run([powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(script)],
                            capture_output=True, env=env)
    assert result.returncode == 0, result.stderr.decode(errors="replace")
    assert b"Qt argument guard passed" in result.stdout
