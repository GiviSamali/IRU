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
    assert "-H \"X-Token: $Token\"" in source


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
