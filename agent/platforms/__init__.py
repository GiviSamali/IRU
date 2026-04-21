"""
Платформенная абстракция агента ИРУ.

Поддерживаемые ОС: Windows, Linux.
При добавлении macOS — создать platforms/macos.py по тому же интерфейсу.

Интерфейс (см. base.py):
    get_platform() -> Platform
    Platform:
        .name: str                         — "Windows" | "Linux" | ...
        .get_system_info() -> dict         — CPU, GPU, RAM, диски
        .get_desktop_path() -> str         — путь к рабочему столу
        .get_username() -> str
        .execute_cmd(cmd, timeout, shell)  — выполнить команду
        .get_machine_guid() -> str
"""
import platform as _platform


def get_platform():
    """Вернуть платформо-специфичный модуль по текущей ОС."""
    system = _platform.system()
    if system == "Windows":
        from . import windows
        return windows
    elif system == "Linux":
        from . import linux
        return linux
    else:
        # Fallback — Linux-совместимый модуль (macOS тоже Unix-подобная)
        from . import linux
        return linux
