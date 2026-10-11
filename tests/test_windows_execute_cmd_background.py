"""Critical Windows subprocess checks. Prepared; run only by the owner."""
import importlib.util
import os
from pathlib import Path
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(sys.platform != 'win32', reason='Windows process/handle semantics')

SPEC = importlib.util.spec_from_file_location('iru_windows_commands', Path(__file__).parents[1] / 'agent/platforms/windows.py')
windows = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(windows)


def command(script):
    quote = lambda value: "'" + str(value).replace("'", "''") + "'"
    return f"& {quote(sys.executable)} {quote(script)}"


def test_shell_exit_does_not_wait_for_inherited_child_output(tmp_path):
    script = tmp_path / 'background.py'
    script.write_text("import subprocess, sys\nprint('before child', flush=True)\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\n", encoding='utf-8')
    started = time.monotonic()
    result = windows.execute_cmd(command(script), timeout=4)
    assert time.monotonic() - started < 6
    assert result['returncode'] == 0 and result['error'] is None
    assert result['stdout'] == 'before child'


def test_timeout_returns_even_when_child_keeps_output_open(tmp_path):
    script = tmp_path / 'timeout.py'
    script.write_text("import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', 'import time; time.sleep(8)'])\ntime.sleep(8)\n", encoding='utf-8')
    started = time.monotonic()
    result = windows.execute_cmd(command(script), timeout=2)
    assert time.monotonic() - started < 5
    assert result['returncode'] == -1
    assert result['error'].startswith('Таймаут:')
