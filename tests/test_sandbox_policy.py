from pathlib import Path

import pytest

from server import sandbox_policy

BASE = Path.cwd() / "sandbox_policy_test_root"


def test_child_path_inside_sandbox_returns_true():
    sandbox = BASE / "sandbox"
    child = sandbox / "nested" / "file.txt"

    assert sandbox_policy.is_path_inside(sandbox, child) is True


def test_same_path_returns_true():
    sandbox = BASE / "sandbox"

    assert sandbox_policy.is_path_inside(sandbox, sandbox) is True


def test_traversal_outside_sandbox_returns_false():
    sandbox = BASE / "sandbox"
    child = sandbox / ".." / "outside.txt"

    assert sandbox_policy.is_path_inside(sandbox, child) is False


def test_assert_path_inside_sandbox_raises_for_outside_path():
    sandbox = BASE / "sandbox"
    outside = BASE / "outside.txt"

    with pytest.raises(ValueError, match="outside sandbox"):
        sandbox_policy.assert_path_inside_sandbox(sandbox, outside)


def test_make_sandbox_subpath_rejects_absolute_path():
    with pytest.raises(ValueError, match="must be relative"):
        sandbox_policy.make_sandbox_subpath(BASE / "sandbox", BASE / "outside.txt")


def test_make_sandbox_subpath_rejects_traversal():
    with pytest.raises(ValueError, match="outside sandbox"):
        sandbox_policy.make_sandbox_subpath(BASE / "sandbox", "../outside.txt")


def test_make_sandbox_subpath_accepts_normal_relative_path():
    sandbox = BASE / "sandbox"

    assert sandbox_policy.make_sandbox_subpath(sandbox, "nested/file.txt") == (sandbox / "nested" / "file.txt").resolve()
