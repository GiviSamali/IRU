import re
from urllib.parse import urlparse


SAFE_DOWNLOAD_LINK_ERROR = (
    "Ссылка не была сформирована системой. Повторите запрос или используйте проводник файлов."
)

_URL_RE = re.compile(r"(https?://[^\s<>()\"']+|/api/download/[A-Za-z0-9_-]+)")
_SUCCESS_CLAIM_RE = re.compile(
    r"(?iu)(готово|выполнен(?:о|а|ы)?|создан(?:о|а|ы|)\b|файл создан|держи ссылк|ссылка готова)"
)
_ERROR_TEXT_RE = re.compile(r"(?iu)(ошиб|не удалось|невозможно|сбой|problem|failed|error)")
_MEMORY_REMEMBER_CLAIM_RE = re.compile(
    r"(?iu)(\bзапомнил[аи]?\b|\bсохранил[аи]?\b.{0,40}\bпамят)"
)
_MEMORY_FORGET_CLAIM_RE = re.compile(
    r"(?iu)(\bудалил[аи]?\b.{0,40}\bпамят|\bудал[её]н[ао]?\b.{0,40}\bпамят|\bстер[ла]?\b.{0,40}\bпамят|\bзабыл[аи]?\b)"
)
_DOWNLOAD_HOST_HINTS = (
    "storage.yandexcloud.net",
    "amazonaws.com",
    "storage.googleapis.com",
    "blob.core.windows.net",
)


def _infer_action(command_entry: dict) -> str | None:
    action = command_entry.get("action")
    if action:
        return action

    command = str(command_entry.get("command", "") or "")
    if command.startswith("[скачать]"):
        return "get_file_link"
    if command.startswith("[write]") or command.startswith("[append]"):
        return "write_content"
    if command:
        return "execute_cmd"
    return None


def _extract_error_text(result: dict) -> str:
    if not isinstance(result, dict):
        return "Неизвестная ошибка"

    error = str(result.get("error") or "").strip()
    if error:
        return error

    stderr = str(result.get("stderr") or "").strip()
    if stderr:
        return stderr

    returncode = result.get("returncode")
    if returncode not in (None, 0, "0"):
        return f"код возврата {returncode}"

    return "Неизвестная ошибка"


def _is_failed_action(command_entry: dict) -> bool:
    action = _infer_action(command_entry)
    result = command_entry.get("result")
    if not isinstance(result, dict):
        return False

    if action == "write_content":
        return bool(result.get("error"))

    if action == "execute_cmd":
        if result.get("error"):
            return True
        returncode = result.get("returncode")
        return returncode not in (None, 0, "0")

    return False


def _allowed_download_urls(commands_log: list[dict]) -> list[str]:
    urls: list[str] = []
    for entry in commands_log or []:
        if _infer_action(entry) != "get_file_link":
            continue
        result = entry.get("result")
        if not isinstance(result, dict):
            continue
        url = result.get("url")
        if isinstance(url, str) and url.startswith("/api/download/") and not result.get("error"):
            urls.append(url)
    return urls


def _looks_like_download_url(url: str) -> bool:
    if url.startswith("/api/download/"):
        return True

    parsed = urlparse(url)
    netloc = parsed.netloc.lower()
    path = parsed.path.lower()

    if any(hint in netloc for hint in _DOWNLOAD_HOST_HINTS):
        return True
    if "agent-files" in netloc or "agent-files" in path:
        return True
    if "/download" in path or "/downloads" in path or "/file" in path or "/files" in path:
        return True
    return False


def _sanitize_download_urls(answer: str, commands_log: list[dict]) -> str:
    matches = list(_URL_RE.finditer(answer or ""))
    if not matches:
        return answer

    allowed_urls = list(dict.fromkeys(_allowed_download_urls(commands_log)))
    invalid_urls = []
    for match in matches:
        url = match.group(0)
        if url in allowed_urls:
            continue
        if _looks_like_download_url(url):
            invalid_urls.append(url)

    if not invalid_urls:
        return answer

    if len(allowed_urls) == 1:
        safe_url = allowed_urls[0]
        sanitized = answer
        for invalid_url in invalid_urls:
            sanitized = sanitized.replace(invalid_url, safe_url)
        return sanitized

    return SAFE_DOWNLOAD_LINK_ERROR


def _build_failure_answer(command_entry: dict) -> str:
    action = _infer_action(command_entry)
    error_text = _extract_error_text(command_entry.get("result") or {})

    if action == "write_content":
        return f"Не удалось записать файл: {error_text}"
    if action == "execute_cmd":
        return f"Команда завершилась с ошибкой: {error_text}"
    return f"Действие завершилось с ошибкой: {error_text}"


def _has_successful_memory_action(commands_log: list[dict], action_name: str) -> bool:
    for entry in commands_log or []:
        if _infer_action(entry) != action_name:
            continue
        result = entry.get("result")
        if isinstance(result, dict) and result.get("status") == "ok" and not result.get("error"):
            return True
    return False


def _sanitize_memory_claims(answer: str, commands_log: list[dict]) -> str:
    has_remember_claim = bool(_MEMORY_REMEMBER_CLAIM_RE.search(answer or ""))
    has_forget_claim = bool(_MEMORY_FORGET_CLAIM_RE.search(answer or ""))
    if not has_remember_claim and not has_forget_claim:
        return answer
    if has_remember_claim and not _has_successful_memory_action(commands_log, "remember_fact"):
        return "Я не менял память: для этого нужно подтверждённое действие памяти."
    if has_forget_claim and not _has_successful_memory_action(commands_log, "forget_fact"):
        return "Я не менял память: для этого нужно подтверждённое действие памяти."
    return answer


def enforce_trusted_answer(answer: str, commands_log: list[dict] | None) -> str:
    commands_log = commands_log or []
    safe_answer = _sanitize_download_urls(answer or "", commands_log)
    if safe_answer == SAFE_DOWNLOAD_LINK_ERROR:
        return safe_answer
    safe_answer = _sanitize_memory_claims(safe_answer, commands_log)

    failed_actions = [entry for entry in commands_log if _is_failed_action(entry)]
    if not failed_actions:
        return safe_answer

    if _SUCCESS_CLAIM_RE.search(safe_answer) or not _ERROR_TEXT_RE.search(safe_answer):
        return _build_failure_answer(failed_actions[0])

    return safe_answer
