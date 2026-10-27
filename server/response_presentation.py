"""Human presentation of an existing Worker Report; no execution or model calls."""
import ntpath
import posixpath
import logging
from pydantic import ValidationError

try:
    from . import database as db
    from .worker_reports import build_worker_report, operation_facts, saved_worker_report, WorkerReport
    from .controller_trust import accepted_result_text
    from .tool_registry import CANONICAL_TOOL_NAMES
except ImportError:
    import database as db
    from worker_reports import build_worker_report, operation_facts, saved_worker_report, WorkerReport
    from controller_trust import accepted_result_text
    from tool_registry import CANONICAL_TOOL_NAMES

# These are tool contracts, not a classifier of the user's words.
OBSERVATION_TOOLS = {
    "web_search", "web.tabs", "web.read", "web.elements", "web.wait",
    "read_file", "list_dir", "get_file_content", "get_file_link",
    "window.list", "window.find", "window.verify", "app.verify_launch",
    "device.get_passport", "device.refresh_state", "device.check_runtime",
    "memory.list_facts", "memory.get_stats", "system.list_tools", "system.get_last_run_summary",
}
FILE_TYPES = {".pptx":"Презентация", ".ppt":"Презентация", ".docx":"Документ", ".doc":"Документ",
              ".xlsx":"Таблица", ".xls":"Таблица", ".html":"Страница сайта", ".pdf":"PDF-документ"}
ERRORS = {
    "mixed_diagnostic_evidence":"Часть диагностических проверок завершилась с ошибками; данные этих шагов нельзя считать полностью подтверждёнными.",
    "model_output_truncated":"Модель исчерпала выходной бюджет; ответ был обрезан и не исполнялся.",
    "recovery_exhausted":"Попытки исправления ошибки шага исчерпаны.",
    "confirmation_expired":"Время подтверждения истекло; команда не выполнялась.",
    "iteration_limit":"Шаг исчерпал бюджет итераций.",
    "no_progress":"Выполнение остановлено: новые результаты не получены. Более точная причина в сохранённом результате отсутствует.",
    "permission_denied":"Нет доступа к файлу или папке.", "access_denied":"Нет доступа к файлу или папке.",
    "source_not_found":"Не удалось найти исходный файл.", "file_not_found":"Не удалось найти файл.",
    "target_exists":"Файл с таким именем уже есть. Он не перезаписан.",
    "browser_offline":"Браузер сейчас недоступен.", "target_disconnected":"Целевое устройство отключилось.",
    "device_not_owned_or_unavailable":"Устройство сейчас недоступно.",
    "hash_mismatch":"Полученный файл не прошёл проверку целостности.",
}


def _tool(command):
    name=command.get("tool_name") or command.get("action") or ""
    return CANONICAL_TOOL_NAMES.get(name,name)


def _artifacts(task, report):
    allowed=set(report["target_device_ids"])
    return [a for a in report["artifacts"] if isinstance(a,dict) and a.get("verified") is True
            and a.get("device_id") in allowed and isinstance(a.get("path"),str) and a["path"]]


def _desktop_location(task, artifacts):
    if not artifacts or not task.get("user_id"):return ""
    nested=False
    for artifact in artifacts:
        try:
            profile=db.get_device_profile(artifact["device_id"],user_id=task["user_id"]) or {}
        except Exception as exc:
            logging.getLogger("iru.presentation").warning("desktop_location_unavailable type=%s",type(exc).__name__)
            return ""
        desktop=profile.get("desktop_path")
        if not isinstance(desktop,str) or not desktop:return ""
        path=artifact["path"]
        paths=ntpath if ntpath.splitdrive(path)[0] or "\\" in path else posixpath
        folder=paths.normcase(paths.normpath(paths.dirname(path)))
        base=paths.normcase(paths.normpath(desktop))
        try:
            if paths.commonpath([folder,base]) != base:return ""
        except ValueError:return ""
        nested=nested or folder!=base
    return "Файлы в папке на рабочем столе." if nested and len(artifacts)>1 else "Файл в папке на рабочем столе." if nested else "Файлы на рабочем столе." if len(artifacts)>1 else "Файл на рабочем столе."


def _artifact_result(artifacts):
    kinds=list(dict.fromkeys(FILE_TYPES.get(ntpath.splitext(a["path"])[1].lower(),"Файл") for a in artifacts))
    if len(kinds)==1 and len(artifacts)==1:return kinds[0]+" готова." if kinds[0] in {"Презентация","Таблица","Страница сайта"} else kinds[0]+" готов."
    if len(artifacts)>1:return "Файлы готовы."
    return ""


def _reason(task, report):
    code=report.get("error_code")
    if code in ERRORS:return ERRORS[code]
    # Prefer the last current failure; historical recovered errors are not the outcome.
    for command in reversed(task.get("commands") or []):
        if not isinstance(command,dict):continue
        result=command.get("result")
        if not isinstance(result,dict) or command.get("status") not in {"failed","error","blocked"}:continue
        for key in ("error_code","reason","error"):
            code=result.get(key)
            if isinstance(code,str) and code in ERRORS:return ERRORS[code]
        break
    return ""


def saved_step_reports(task):
    """Read accepted step terminals as historical material, never new execution proof."""
    commands = task.get("commands") or []
    reports = []
    for index, command in enumerate(commands):
        payload = command.get("result") or {}
        if (_tool(command) != "answer.text" or command.get("status") != "terminal"
                or not isinstance(payload, dict) or payload.get("answer_type") not in {"grounded_report", "pure_text", "partial_report", "result"}):
            continue
        text = payload.get("text")
        # Readability of a historical report is independent of whole-goal
        # completion. Failed measurements are not promoted to positive evidence.
        if isinstance(text, str) and accepted_result_text(text, commands[:index + 1]):
            reports.append({"step_id": command.get("step_id"), "text": text,
                            "device_id": command.get("target_device_id") or command.get("device_id"),
                            "observed_at": command.get("collected_at") or command.get("created_at")})
    return reports


def failure_explanation(task, report=None, *, include_reports=False):
    """Explain persisted steps. Historical content is material, never fresh execution proof."""
    receipt = task.get("task_receipt") or {}
    steps = receipt.get("step_results") or [s for plan in task.get("tasks") or [] for s in plan.get("steps") or []]
    done = [s.get("title") or s.get("description") for s in steps if s.get("status") in {"done", "recovered"}]
    failed = [s.get("title") or s.get("description") for s in steps if s.get("status") == "failed"]
    pending = [s.get("title") or s.get("description") for s in steps if s.get("status") in {"blocked", "pending"}]
    lines = ["Задачу закончить не удалось; исходное поручение не завершено."]
    if done: lines.append("Завершённые этапы: " + "; ".join(filter(None, done)) + ".")
    if failed: lines.append("Остановка на этапе: " + "; ".join(filter(None, failed)) + ".")
    reason = _reason(task, report or {})
    lines.append("Причина: " + (reason or "точная причина в сохранённых данных не указана."))
    failed_commands = receipt.get("commands_failed") or []
    if failed_commands:
        last = failed_commands[-1]
        if isinstance(last, dict):
            detail = last.get("error_type") or last.get("error") or (
                f"returncode={last['returncode']}" if last.get("returncode") not in (None, 0, "0") else "")
            if detail:
                lines.append("Последняя ошибка инструмента: " + str(detail)[:260])
    if pending: lines.append("Не выполнялись: " + "; ".join(filter(None, pending)) + ".")
    reports = saved_step_reports(task)
    if reports:
        lines.append("Доступен сохранённый промежуточный отчёт. Это исходный материал; достоверность каждого утверждения отдельно не подтверждается.")
        if include_reports:
            lines.extend(r["text"] for r in reports)
    artifacts = _artifacts(task, report) if report else []
    if artifacts: lines.append("Подтверждённые при выполнении файлы: " + "; ".join(a["path"] for a in artifacts) + ". Перед открытием нужна текущая проверка.")
    else: lines.append("Подтверждённых созданных файлов в результате нет.")
    return "\n\n".join(lines)


def normalized_worker_report(task, report=None):
    return saved_worker_report(task, report)



def running_progress(task):
    """Project existing PLAN progress; never infer it from generated answer text."""
    steps = [step for plan in task.get("tasks") or [] if isinstance(plan, dict)
             for step in plan.get("steps") or [] if isinstance(step, dict)]
    done = [str(step["title"])[:200] for step in steps
            if step.get("status") in {"done", "completed", "recovered"} and step.get("title")]
    current = next((str(step["title"])[:200] for step in steps
                    if step.get("status") == "running" and step.get("title")), "")
    parts = ["Работа ещё идёт."]
    if done:
        parts.append("Завершены этапы: " + "; ".join(done[:8]) + ".")
    if current:
        parts.append("Текущий этап: " + current + ".")
    elif isinstance(task.get("current_step"), str) and task["current_step"].strip():
        parts.append("Сейчас: " + task["current_step"].strip()[:300])
    return "\n\n".join(parts)


def worker_presentation(task, report=None):
    return render_worker_report(task, normalized_worker_report(task, report))


def render_worker_report(task, report):
    """Render an already canonical report; never re-evaluate it."""
    answer = task.get('answer') if isinstance(task.get('answer'),str) else ''
    if report.get('error_code') in {'artifact_receipt_missing','worker_report_binding_mismatch','required_file_change_reverted','artifact_postcondition_unconfirmed','clarification_required','clarification_binding_mismatch'} or report['status'] in {'failed','partial','unknown'}:
        answer = report['summary']
    if report['status'] in {'partial','unknown'} and len(report['target_device_ids'])>1:
        answer = report['summary']
    status = report['status']
    if status=='success' and not accepted_result_text(answer,task.get('commands') or []):
        answer=report['summary']
    if status == 'running': human = running_progress(task)
    elif status in {'queued','waiting_dependency','waiting_confirmation','waiting_input'}: human = report['summary']
    elif report.get('error_code')=='clarification_required':human=report['summary']
    else:
        human = answer or report['summary']
        if answer and status != 'success':
            label = {'partial':'Поручение выполнено частично.', 'unknown':'Итог поручения не подтверждён.',
                     'failed':'Поручение завершилось с ошибкой.', 'cancelled':'Поручение отменено.',
                     'blocked':'Поручение остановлено.'}[status]
            human = label + '\n\n' + answer
    if status in {'unknown','partial'} and any(_tool(c)=='remember_fact' for c in task.get('commands') or []):
        try:
            from .task_summary import recovered_execution_summary
        except ImportError:
            from task_summary import recovered_execution_summary
        human=recovered_execution_summary(task)
    details = task.get('answer') or ''
    if status in {'partial','unknown','failed','cancelled','blocked'}:
        history = saved_step_reports(task)
        if history:
            details='Это сохранённый исходный материал, не подтверждение завершения.\n\n' + '\n\n'.join(r['text'] for r in history)
    else:details=''
    return {'conversational_response':human,'execution_details':details}




def worker_spoken_response(task, report=None):
    try:
        from .orchestrator import present_worker_result
    except ImportError:
        from orchestrator import present_worker_result
    return present_worker_result(task, report)['conversational_response']
