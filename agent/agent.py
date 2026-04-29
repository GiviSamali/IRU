"""
Thin entrypoint for the IRU agent runtime.

Responsibilities:
- load and migrate config
- initialize logging and diagnostics state
- collect first-run setup if config is incomplete
- run update check before headless start or inside the Windows shell flow
- launch the Windows shell or headless runtime
"""

from __future__ import annotations

import os
import platform
import sys

from core.config import (
    DEFAULT_CONFIG,
    detect_paths,
    is_config_complete,
    load_config,
    merge_config,
    save_config,
)
from core.logging_utils import configure_logging
from core.runtime import AgentRuntime
from core.state import AgentSnapshot, AgentState
from core.update import check_for_update
from core.version import read_agent_version
from ui.setup import collect_setup, gui_available


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass


def build_state(agent_version: str, config: dict, paths) -> AgentState:
    return AgentState(
        AgentSnapshot(
            version=agent_version,
            device_id=config.get("device_id", ""),
            server_url=config.get("server_url", ""),
            config_path=str(paths.config_path),
            logs_dir=str(paths.logs_dir),
            log_path=str(paths.log_path),
        )
    )


def ensure_config(config: dict, paths, logger, state: AgentState) -> dict | None:
    merged = merge_config(config)
    if is_config_complete(merged):
        return merged

    logger.info("[agent] config is incomplete, opening setup flow")
    setup = collect_setup(merged or DEFAULT_CONFIG)
    if not setup:
        state.mark_config_error("Настройка отменена пользователем.")
        logger.warning("[agent] setup cancelled by user")
        return None

    merged = save_config(paths, {**merged, **setup}, logger=logger)
    state.set_config(merged.get("device_id", ""), merged.get("server_url", ""))
    logger.info("[agent] setup finished, config saved")
    return merged


def should_launch_windows_shell() -> bool:
    if platform.system() != "Windows":
        return False
    return gui_available()


def main() -> int:
    paths = detect_paths()
    logger = configure_logging(paths)
    config = load_config(paths, logger=logger)
    agent_version = read_agent_version()
    state = build_state(agent_version, config, paths)

    config = ensure_config(config, paths, logger, state)
    if not config:
        return 1

    runtime = AgentRuntime(config=config, agent_version=agent_version, logger=logger, state=state)

    if should_launch_windows_shell():
        try:
            from ui.shell import launch_windows_shell

            def startup_update_check() -> bool:
                return check_for_update(config["server_url"], agent_version, paths, logger, state)

            return int(
                launch_windows_shell(
                    runtime,
                    state,
                    config,
                    paths,
                    logger,
                    startup_update_check=startup_update_check,
                )
            )
        except Exception:
            logger.exception("[agent] failed to launch Windows shell, falling back to headless mode")

    if check_for_update(config["server_url"], agent_version, paths, logger, state):
        logger.info("[agent] updater launched, exiting current process")
        return 0

    runtime.run_headless()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
