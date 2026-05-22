from server.python_toolchain import (
    PythonToolchainReceipt,
    build_python_toolchain_block,
    python_toolchain_from_runtime_summary,
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
        confidence=0.95,
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
        confidence=0.95,
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
        confidence=0.95,
    )

    allowed, corrected = validate_toolchain_fact_against_receipt(
        "Python/PyQt are not installed",
        receipt,
    )

    assert allowed is False
    assert corrected is None


def test_memory_fact_russian_python_not_installed_rejected_when_receipt_ok():
    receipt = PythonToolchainReceipt(
        device_id="win-ru-python-negative",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
        confidence=0.95,
    )

    allowed, corrected = validate_toolchain_fact_against_receipt(
        "Python не установлен на этом ПК",
        receipt,
    )

    assert allowed is False
    assert corrected is None


def test_memory_fact_russian_pyqt_not_found_rejected_when_package_installed():
    receipt = PythonToolchainReceipt(
        device_id="win-ru-pyqt-negative",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
        packages={"PyQt5": "installed"},
        confidence=0.95,
    )

    allowed, corrected = validate_toolchain_fact_against_receipt("PyQt5 не найден", receipt)

    assert allowed is False
    assert corrected is None


def test_memory_fact_russian_wrong_python_version_corrected_from_receipt():
    receipt = PythonToolchainReceipt(
        device_id="win-ru-version",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
        confidence=0.95,
    )

    allowed, corrected = validate_toolchain_fact_against_receipt("Python 3.13 установлен", receipt)

    assert allowed is True
    assert "3.11.9" in corrected
    assert "3.13" not in corrected


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
            confidence=0.95,
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


def test_real_log_windowsapps_python_version_only_is_not_ok():
    receipt = resolve_python_toolchain(
        {"device_id": "desktop-ja4oseo-version-only"},
        [
            _cmd(
                "Get-Command python",
                stdout=r"C:\Users\Zerkxxx\AppData\Local\Microsoft\WindowsApps\python.exe Source version 0.0.0.0",
                returncode=0,
            ),
            _cmd(
                "$env:Path='C:\\Program Files\\Python311'; python --version",
                stdout="Python 3.11.9",
                returncode=0,
            ),
        ],
    )

    assert receipt.status == "broken_stub"
    assert receipt.interpreter_path is None


def test_version_only_python_output_without_sys_executable_is_missing():
    receipt = resolve_python_toolchain(
        {"device_id": "win-version-only"},
        [
            _cmd(
                "$env:Path='C:\\Program Files\\Python311'; python --version",
                stdout="Python 3.11.9",
                returncode=0,
            ),
        ],
    )

    assert receipt.status == "missing"
    assert receipt.interpreter_path is None


def test_py_discovery_produces_verified_canonical_receipt():
    receipt = resolve_python_toolchain(
        {"device_id": "desktop-ja4oseo-py"},
        [
            _cmd("python --version", stdout="Python", returncode=1),
            _cmd(
                'py -3 -c "import sys; print(sys.executable); print(sys.version)"',
                stdout=r"C:\Program Files\Python311\python.exe" + "\n3.11.9 (main, Apr  2 2024)\n",
                returncode=0,
            ),
        ],
    )

    assert receipt.status == "ok"
    assert receipt.interpreter_path == r"C:\Program Files\Python311\python.exe"
    assert receipt.version == "3.11.9"
    assert receipt.confidence >= 0.9


def test_rewrite_prefixed_powershell_python_commands():
    receipt = PythonToolchainReceipt(
        device_id="win-prefix",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
        confidence=0.95,
    )

    set_location, err = rewrite_python_command(r'Set-Location "C:\work"; python main.py', receipt)
    env_path, err2 = rewrite_python_command(r'$env:Path="C:\Program Files\Python311"; pip install PyQt5', receipt)

    assert err is None and err2 is None
    assert set_location == r'Set-Location "C:\work"; & "C:\Program Files\Python311\python.exe" main.py'
    assert env_path == r'$env:Path="C:\Program Files\Python311"; & "C:\Program Files\Python311\python.exe" -m pip install PyQt5'


def test_broken_alias_blocks_prefixed_bare_python():
    receipt = PythonToolchainReceipt(
        device_id="win-broken",
        status="broken_stub",
        raw_evidence=["broken_alias:python:WindowsApps"],
    )

    rewritten, err = rewrite_python_command(r'Set-Location "C:\work"; python main.py', receipt)

    assert rewritten == r'Set-Location "C:\work"; python main.py'
    assert "known WindowsApps stub" in err


def test_verified_receipt_blocks_call_operator_bare_python():
    receipt = PythonToolchainReceipt(
        device_id="win-call-operator",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
        confidence=0.95,
    )

    rewritten, err = rewrite_python_command("& python main.py", receipt)

    assert rewritten == "& python main.py"
    assert "could not be safely rewritten" in err


def test_verified_receipt_blocks_nested_if_bare_python():
    receipt = PythonToolchainReceipt(
        device_id="win-if-block",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
        confidence=0.95,
    )

    rewritten, err = rewrite_python_command("if ($true) { python main.py }", receipt)

    assert rewritten == "if ($true) { python main.py }"
    assert "could not be safely rewritten" in err


def test_memory_pyqt_fact_requires_package_installed():
    receipt = PythonToolchainReceipt(
        device_id="win-no-pyqt",
        status="ok",
        interpreter_path=r"C:\Program Files\Python311\python.exe",
        version="3.11.9",
        packages={},
        confidence=0.95,
    )

    allowed, corrected = validate_toolchain_fact_against_receipt("PyQt5 installed", receipt)

    assert allowed is False
    assert corrected is None


def test_any_python_fact_requires_verified_receipt():
    allowed, corrected = validate_toolchain_fact_against_receipt(
        r"Python lives in C:\Python311",
        None,
    )

    assert allowed is False
    assert corrected is None


def test_managed_runtime_summary_has_priority_for_rewrite():
    receipt = python_toolchain_from_runtime_summary(
        {
            "runtime_status": "ok",
            "venv_python": r"C:\Users\tester\AppData\Local\IRU\runtime\venv\Scripts\python.exe",
            "python_version": "3.11.9",
            "pip_status": "ok",
        },
        device_id="givi",
    )

    rewritten, err = rewrite_python_command("python app.py", receipt)

    assert err is None
    assert rewritten == r'& "C:\Users\tester\AppData\Local\IRU\runtime\venv\Scripts\python.exe" app.py'
