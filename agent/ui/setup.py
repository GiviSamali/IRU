from __future__ import annotations

import os
import platform
import re
import sys

from core.config import DEFAULT_CONFIG, merge_config

DEVICE_ID_RE = re.compile(r"^[A-Za-z0-9_-]+$")


def gui_available() -> bool:
    if sys.platform == "win32":
        return True
    return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))


def collect_setup(initial_config: dict | None = None) -> dict | None:
    config = merge_config(initial_config or DEFAULT_CONFIG)
    if gui_available():
        attempted, setup = _collect_setup_gui(config)
        if attempted:
            return setup
    return _collect_setup_cli(config)


def _collect_setup_gui(initial_config: dict) -> tuple[bool, dict | None]:
    try:
        from PySide6 import QtCore, QtWidgets
    except Exception:
        return False, None

    class SetupDialog(QtWidgets.QDialog):
        def __init__(self, config: dict):
            super().__init__()
            self.config = config
            self.setWindowTitle("IRU Agent - Setup")
            self.setModal(True)
            self.setMinimumWidth(460)

            layout = QtWidgets.QVBoxLayout(self)
            title = QtWidgets.QLabel("Первичная настройка агента")
            title.setStyleSheet("font-size: 18px; font-weight: 600;")
            subtitle = QtWidgets.QLabel("Укажи токен, имя устройства и при необходимости адрес сервера.")
            subtitle.setWordWrap(True)
            subtitle.setStyleSheet("color: #64748b;")
            layout.addWidget(title)
            layout.addWidget(subtitle)

            form = QtWidgets.QFormLayout()
            form.setLabelAlignment(QtCore.Qt.AlignmentFlag.AlignLeft)
            saved_token = bool(config.get("user_token"))
            self.token_edit = QtWidgets.QLineEdit("")
            self.token_edit.setPlaceholderText(
                "Токен доступа" if not saved_token else "Оставь пустым, чтобы сохранить текущий токен"
            )
            self.token_edit.setEchoMode(QtWidgets.QLineEdit.EchoMode.Password)
            form.addRow("Токен", self.token_edit)

            self.device_edit = QtWidgets.QLineEdit(config.get("device_id") or platform.node())
            self.device_edit.setPlaceholderText("DEVICE-01")
            form.addRow("Устройство", self.device_edit)
            layout.addLayout(form)

            self.advanced_toggle = QtWidgets.QToolButton()
            self.advanced_toggle.setText("Дополнительно")
            self.advanced_toggle.setCheckable(True)
            self.advanced_toggle.setArrowType(QtCore.Qt.ArrowType.RightArrow)
            self.advanced_toggle.toggled.connect(self._toggle_advanced)
            layout.addWidget(self.advanced_toggle)

            self.advanced_box = QtWidgets.QGroupBox()
            self.advanced_box.setVisible(False)
            advanced_form = QtWidgets.QFormLayout(self.advanced_box)
            self.server_edit = QtWidgets.QLineEdit(config.get("server_url", DEFAULT_CONFIG["server_url"]))
            self.server_edit.setPlaceholderText("wss://irumode.ru")
            advanced_form.addRow("Server URL", self.server_edit)
            layout.addWidget(self.advanced_box)

            self.error_label = QtWidgets.QLabel("")
            self.error_label.setStyleSheet("color: #dc2626;")
            self.error_label.setWordWrap(True)
            layout.addWidget(self.error_label)

            buttons = QtWidgets.QDialogButtonBox(
                QtWidgets.QDialogButtonBox.StandardButton.Ok
                | QtWidgets.QDialogButtonBox.StandardButton.Cancel
            )
            buttons.accepted.connect(self._submit)
            buttons.rejected.connect(self.reject)
            layout.addWidget(buttons)

            self.token_edit.setFocus()

        def _toggle_advanced(self, expanded: bool) -> None:
            self.advanced_toggle.setArrowType(
                QtCore.Qt.ArrowType.DownArrow if expanded else QtCore.Qt.ArrowType.RightArrow
            )
            self.advanced_box.setVisible(expanded)

        def _submit(self) -> None:
            device_id = self.device_edit.text().strip()
            user_token = self.token_edit.text().strip() or self.config.get("user_token", "").strip()
            server_url = self.server_edit.text().strip() or DEFAULT_CONFIG["server_url"]

            if not user_token:
                self.error_label.setText("Нужен токен доступа.")
                return
            if not device_id:
                self.error_label.setText("Нужно имя устройства.")
                return
            if not DEVICE_ID_RE.match(device_id):
                self.error_label.setText("Имя устройства: только латиница, цифры, - и _.")
                return

            self.result_payload = {
                "user_token": user_token,
                "device_id": device_id,
                "server_url": server_url,
            }
            self.accept()

    app = None
    owns_app = False
    try:
        app = QtWidgets.QApplication.instance()
        if app is None:
            app = QtWidgets.QApplication(sys.argv[:1])
            owns_app = True
            app.setQuitOnLastWindowClosed(False)

        dialog = SetupDialog(initial_config)
        accepted = dialog.exec() == QtWidgets.QDialog.DialogCode.Accepted
        result = dialog.result_payload if accepted else None

        return True, result
    except Exception:
        return False, None


def _collect_setup_cli(initial_config: dict) -> dict | None:
    try:
        print("[agent] GUI setup недоступен, переходим в CLI-setup.")
        token_prompt = "Токен доступа"
        if initial_config.get("user_token"):
            token_prompt += " [сохранен, Enter оставить]"
        user_token = input(f"{token_prompt}: ").strip()
        device_id = input(f"Имя устройства [{initial_config.get('device_id', platform.node())}]: ").strip()
        server_url = input(f"Server URL [{initial_config.get('server_url', DEFAULT_CONFIG['server_url'])}]: ").strip()
    except (EOFError, KeyboardInterrupt):
        return None

    payload = {
        "user_token": user_token or initial_config.get("user_token", ""),
        "device_id": device_id or initial_config.get("device_id") or platform.node(),
        "server_url": server_url or initial_config.get("server_url", DEFAULT_CONFIG["server_url"]),
    }
    if not payload["user_token"]:
        print("[agent] токен обязателен.")
        return None
    if not DEVICE_ID_RE.match(payload["device_id"]):
        print("[agent] имя устройства должно содержать только латиницу, цифры, - и _.")
        return None
    return payload
