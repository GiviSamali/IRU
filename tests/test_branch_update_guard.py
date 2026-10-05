import subprocess
import pytest
from tools.update_server import check_update


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, encoding="utf-8", errors="replace", stderr=subprocess.PIPE).strip()


@pytest.mark.parametrize("branch", ["codex/deeptalk-integration", "codex/agentshell-webview"])
def test_update_refuses_missing_main_and_accepts_integrated_history(tmp_path, branch):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.invalid")
    (repo / "base.txt").write_text("base")
    git(repo, "add", "."); git(repo, "commit", "-m", "base")
    git(repo, "branch", branch)
    (repo / "main.txt").write_text("sleep word and search")
    git(repo, "add", "."); git(repo, "commit", "-m", "production fix")
    git(repo, "update-ref", "refs/remotes/origin/main", "HEAD")
    git(repo, "checkout", branch)
    (repo / "voice.txt").write_text("voice changes")
    git(repo, "add", "."); git(repo, "commit", "-m", "voice work")
    git(repo, "update-ref", "refs/remotes/origin/" + branch, "HEAD")
    with pytest.raises(RuntimeError, match="missing commits"):
        check_update(repo, branch, fetch=False)
    git(repo, "merge", "--no-edit", "main")
    git(repo, "update-ref", "refs/remotes/origin/" + branch, "HEAD")
    assert check_update(repo, branch, fetch=False) == git(repo, "rev-parse", "HEAD")
    with pytest.raises(RuntimeError, match="differs"):
        check_update(repo, "main", fetch=False)
    (repo / "voice.txt").write_text("local edit")
    with pytest.raises(RuntimeError, match="local changes"):
        check_update(repo, branch, fetch=False)
    git(repo, "add", "."); git(repo, "commit", "-m", "local only")
    with pytest.raises(RuntimeError, match="cannot fast-forward"):
        check_update(repo, branch, fetch=False)
