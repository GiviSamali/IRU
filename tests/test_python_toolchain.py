from server.python_toolchain import (
    PythonToolchainReceipt,
    build_python_toolchain_block,
    remember_python_toolchain,
    resolve_python_toolchain,
    rewrite_python_command,
    validate_toolchain_fact_against_receipt,
)


def _cmd(command, stdout="", stderr="", returncode=0):
    return {
        "action": "execute_cmd",
        "command": command,
        "result": {"stdout": stdout, "stderr": stderr, "returncode": returncode},
    }


def test_windowsapps_stub_classified_as_broken_stub():
    receipt = resolve_python_toolchain(
        {"device_id": "win-1"},
        [
            _cmd(
                "python --version",
                stdout="Python",
                stderr="",
                returncode=9009,
            ),
            _cmd(
                "Get-Command python",
                stdout=r"C:\Users\tester\AppData\Local\Microsoft\WindowsApps\python.exe Source version 0.0.0.0",
                returncode=0,
            ),
        ],
    )

    assert receipt.status == "broken_stub"
    assert receipt.interpreter_path is None
    assert any("broken_alias:python" in item for item in receipt.raw_evidence)


def test_py_or_program_files_python_wins_over_windowsapps_stub():
    receipt = resolve_python_toolchain(
        {"device_id": "win-2"},
        [
            _cmd("python --version", stdout="Python", returncode=1),
            _cmd(
                'py -3 -c "import sys; print(sys.executable); print(sys.version)"',
                stdout="C:\\Program Files\\Python311\\python.exe\n3.11.9 (tags/v3.11.9:de54cf5)\n",
                returncode=0,
            ),
            _cmd(
                '& "C:\\Program Files\\Python311\\python.exe" -m pip --version',
                stdout="pip 24.0 from C:\\Program Files\\Python311\\Lib\\site-packages\\pip (python 3.11)",
                returncode=0,
            ),
        ],
    )

    assert receipt.status == "ok"
    assert receipt.interpreter_path == r"C:\Program Files\Python311\python.exe"
    assert receipt.version == "3.11.9"
    assert receipt.pip_available is True


def test_rewrite_uses_explicit_interpreter_after_resolve():
    receipt = PythonToolchainReceipt(
        device_id="win-3",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
    )

    pip_cmd, err = rewrite_python_command("python -m pip install PyQt5 numpy matplotlib", receipt)
    run_cmd, err2 = rewrite_python_command("python app.py", receipt)

    assert err is None and err2 is None
    assert pip_cmd == r'& "C:\Program Files\Python311\python.exe" -m pip install PyQt5 numpy matplotlib'
    assert run_cmd == r'& "C:\Program Files\Python311\python.exe" app.py'
    block = build_python_toolchain_block(receipt)
    assert "resolved_python_path: C:\\Program Files\\Python311\\python.exe" in block
    assert "Use resolved_python_path, not bare python, if provided." in block


def test_pyqt5_installed_via_resolved_interpreter_recorded_as_package_installed():
    receipt = resolve_python_toolchain(
        {"device_id": "win-4"},
        [
            _cmd(
                '& "C:\\Program Files\\Python311\\python.exe" -c "import sys; print(sys.executable); print(sys.version)"',
                stdout="C:\\Program Files\\Python311\\python.exe\nPython 3.11.9\n",
                returncode=0,
            ),
            _cmd(
                '& "C:\\Program Files\\Python311\\python.exe" -c "import PyQt5"',
                stdout="",
                returncode=0,
            ),
        ],
    )

    assert receipt.status == "ok"
    assert receipt.packages["PyQt5"] == "installed"


def test_bare_python_failure_after_resolve_does_not_make_python_missing():
    receipt = resolve_python_toolchain(
        {"device_id": "win-5"},
        [
            _cmd(
                '& "C:\\Program Files\\Python311\\python.exe" -c "import sys; print(sys.executable); print(sys.version)"',
                stdout="C:\\Program Files\\Python311\\python.exe\nPython 3.11.9\n",
                returncode=0,
            ),
            _cmd("python --version", stdout="Python", stderr="", returncode=1),
        ],
    )

    assert receipt.status == "ok"
    assert receipt.interpreter_path == r"C:\Program Files\Python311\python.exe"


def test_memory_fact_wrong_python_version_is_corrected_from_receipt():
    receipt = PythonToolchainReceipt(
        device_id="win-6",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
        packages={"PyQt5": "installed"},
    )

    allowed, corrected = validate_toolchain_fact_against_receipt(
        "Python 3.13 and PyQt5 are installed",
        receipt,
    )

    assert allowed is True
    assert "3.11.9" in corrected
    assert "3.13" not in corrected


def test_memory_fact_python_not_installed_rejected_when_receipt_ok():
    receipt = PythonToolchainReceipt(
        device_id="win-7",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
    )

    allowed, corrected = validate_toolchain_fact_against_receipt(
        "Python/PyQt are not installed",
        receipt,
    )

    assert allowed is False
    assert corrected is None


def test_non_pipeline_prompt_includes_resolved_python_path_from_receipt():
    from server.controller import LLMRuntimeContext, _build_non_pipeline_system_prompt

    remember_python_toolchain(
        PythonToolchainReceipt(
            device_id="prompt-win",
            status="ok",
            interpreter_path=r"C:\Program Files\Python311\python.exe",
            version="3.11.9",
            pip_available=True,
            packages={"PyQt5": "installed"},
        )
    )
    runtime = LLMRuntimeContext(
        cfg={},
        os_info="Windows",
        hostname="box",
        os_version="11",
        devices_block="prompt-win",
        profile_block="",
        memory_block="",
        target_device_block="## Target device context\ndevice_id: \nhostname: box\nos: Windows 11",
        os_rules="windows",
        current_datetime_msk="2026-05-13 12:00",
        machine_guid=None,
        mem_user_id=None,
        python_toolchain_block=build_python_toolchain_block(
            resolve_python_toolchain({"device_id": "prompt-win"})
        ),
    )

    prompt = _build_non_pipeline_system_prompt(runtime=runtime, device_id="prompt-win")

    assert "resolved_python_path: C:\\Program Files\\Python311\\python.exe" in prompt
    assert "python_version: 3.11.9" in prompt
    assert "packages: PyQt5=installed" in prompt
