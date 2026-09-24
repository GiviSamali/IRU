"""Guarded server update: never deploy a branch missing current main commits."""
import argparse
from pathlib import Path
import subprocess


DEPLOY_BRANCHES = {"main", "codex/deeptalk-integration"}


def git(repo, *args):
    return subprocess.check_output(["git", "-C", str(repo), *args], text=True, encoding="utf-8", errors="replace", stderr=subprocess.PIPE).strip()


def ancestor(repo, older, newer):
    result = subprocess.run(["git", "-C", str(repo), "merge-base", "--is-ancestor", older, newer], capture_output=True)
    if result.returncode not in (0, 1):
        raise RuntimeError("Cannot resolve branch history")
    return result.returncode == 0


def check_update(repo, branch, *, fetch=True):
    if branch not in DEPLOY_BRANCHES:
        raise RuntimeError("Choose the deployment branch explicitly: main or codex/deeptalk-integration")
    if git(repo, "branch", "--show-current") != branch:
        raise RuntimeError("Current branch differs from requested deployment branch; no automatic switch")
    if git(repo, "status", "--porcelain", "--untracked-files=no"):
        raise RuntimeError("Tracked files have local changes; update refused")
    if fetch:
        git(repo, "fetch", "origin")
    target = "origin/" + branch
    if not ancestor(repo, "origin/main", target):
        raise RuntimeError("Deployment branch is missing commits from origin/main; merge and test them first")
    if not ancestor(repo, "HEAD", target):
        raise RuntimeError("Local branch cannot fast-forward to origin; update refused")
    return git(repo, "rev-parse", target)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--branch", required=True, choices=sorted(DEPLOY_BRANCHES))
    parser.add_argument("--check", action="store_true", help="fetch and validate without updating or restarting")
    parser.add_argument("--restart", action="store_true", help="restart the iru service after successful update")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1]
    try:
        commit = check_update(repo, args.branch)
        if not args.check:
            git(repo, "merge", "--ff-only", commit)
            if args.restart:
                subprocess.run(["systemctl", "restart", "iru"], check=True)
        print(f"{'Checked' if args.check else 'Updated'} {args.branch}: {commit}")
    except (RuntimeError, subprocess.CalledProcessError) as exc:
        parser.exit(1, f"Update stopped: {exc}\n")


if __name__ == "__main__":
    main()
