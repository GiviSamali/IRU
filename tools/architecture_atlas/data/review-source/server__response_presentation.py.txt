"""Human presentation of an existing Worker Report; no execution or model calls."""
import ntpath
import posixpath
import logging
from pydantic import ValidationError

try:
    from . import database as db
    from .worker_reports import build_worker_report, positive_evidence, WorkerReport
    from .controller_trust import has_grounded_terminal_answer, has_validated_terminal_answer
    from .tool_registry import CANONICAL_TOOL_NAMES
except ImportError:
    import database as db
    from worker_reports import build_worker_report, positive_evidence, WorkerReport
    from controller_trust import has_grounded_terminal_answer, has_validated_terminal_answer
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
                or not isinstance(payload, dict) or payload.get("answer_type") not in {"grounded_report", "pure_text", "partial_report"}):
            continue
        text = payload.get("text")
        validator = has_grounded_terminal_answer if payload.get("answer_type") == "grounded_report" else has_validated_terminal_answer
        if isinstance(text, str) and validator(text, commands[:index + 1]):
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
    try:
        report=WorkerReport.model_validate(report or build_worker_report(task)).model_dump()
    except ValidationError:
        # Invalid cached presentation cannot replace the current journal/receipt.
        logging.getLogger("iru.presentation").warning("worker_report_cache_invalid")
        report=build_worker_report(task)
    if report["task_id"]!=task["task_id"]:
        logging.getLogger("iru.presentation").warning("worker_report_cache_task_mismatch")
        report=build_worker_report(task)
    raw_negative={"error":"failed","failed":"failed","partial":"partial","blocked":"blocked","unknown":"unknown","cancelled":"cancelled","interrupted":"unknown"}
    status_changed=task.get("status") in raw_negative and report["status"]!=raw_negative[task["status"]]
    attention_status={"confirm":"waiting_confirmation", "waiting_input":"waiting_input"}.get(task.get("status"))
    attention_changed=attention_status is not None and report["status"]!=attention_status
    incomplete=report["status"]=="success" and (task.get("task_receipt") or {}).get("goal_completed") is False
    if report["status"]=="success" or status_changed or attention_changed or task.get("cancel_requested") or incomplete:
        # A cache cannot grant completion after the authoritative journal/receipt
        # changed or an older validation contract was replaced.
        report=build_worker_report(task)
    return report


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
    """Keep raw protocol text unchanged; derive prose only from normalized evidence."""
    report=normalized_worker_report(task,report)
    answer=task.get("answer") if isinstance(task.get("answer"),str) else ""
    receipt=task.get("task_receipt") or {}
    if (report["status"]=="success" and receipt.get("answer_source")=="audited_terminal"
            and receipt.get("goal_completed") is True and receipt.get("final_verification_status")=="verified"
            and has_grounded_terminal_answer(answer,task.get("commands") or [])):
        # A legacy presentation cache or helper artifact cannot replace an audited result.
        return {"conversational_response":answer,"execution_details":""}
    if (report.get('error_code') in {'confirmed_transport_outcome_unknown','confirmation_expired'}
            and receipt.get('answer_source')=='confirmation_result'
            and has_validated_terminal_answer(answer,task.get('commands') or [])):
        return {'conversational_response':answer,'execution_details':''}
    saved=task.get("history_metadata")
    if report["status"] != "running" and isinstance(saved,dict) and saved.get("workerReport")==report and isinstance(saved.get("conversationalResponse"),str):
        details=saved.get("executionDetails") or ""
        if (details or saved["conversationalResponse"])==answer:
            return {"conversational_response":saved["conversationalResponse"],"execution_details":details}
    status=report["status"]
    commands=[c for c in task.get("commands") or [] if isinstance(c,dict)]
    operations=[c for c in commands if not _tool(c).startswith("answer.")]
    artifacts=_artifacts(task,report)
    result=_artifact_result(artifacts)
    if task.get("plan_suggestion"):
        human=answer or "Предлагаю составить план. Запустить?"
    elif status=="waiting_input":human=answer or "Какое окно выбрать?"
    elif status=="waiting_confirmation":human="Нужно твоё подтверждение, прежде чем продолжить."
    elif status=="queued":human="Поручение в очереди. Начну после текущей задачи."
    elif status=="waiting_dependency":human="Жду подтверждённого результата связанного поручения."
    elif status=="running":human=running_progress(task)
    elif status=="success":
        terminal=next((c for c in reversed(commands) if _tool(c)=="answer.text" and c.get("status")=="terminal"),{})
        payload=terminal.get("result") if isinstance(terminal.get("result"),dict) else {}
        self_check=payload.get("self_check") if isinstance(payload.get("self_check"),dict) else {}
        informational=self_check.get("claims_completed_action") is False
        receipt=task.get("task_receipt") or {}
        audited_command_report=(receipt.get("answer_source")=="audited_terminal"
            and receipt.get("goal_completed") is True
            and any(_tool(c)=="execute_cmd" and c.get("step_id") in (payload.get("basis") or []) for c in operations))
        if operations and has_grounded_terminal_answer(answer,commands) and (informational or audited_command_report or all(_tool(c) in OBSERVATION_TOOLS for c in operations)):
            # A validated informational result can include execute_cmd processing.
            # No guessing intent from shell text and no extra classifier/model.
            human=answer
        elif any(_tool(c)=="transfer_file" and positive_evidence(c) and c["result"].get("sha256_verified") is True for c in operations):
            human="Файл передан."
            location=_desktop_location(task,artifacts)
            if location:human+=" "+location
        elif result:
            human=result
            location=_desktop_location(task,artifacts)
            if location:human+=" "+location
        else:
            actions=[_tool(c) for c in operations if positive_evidence(c) and _tool(c) not in OBSERVATION_TOOLS]
            human={"app.launch":"Приложение открыто.","app.open_url":"Страница открыта.",
                   "web.focus":"Вкладка выбрана.","remember_fact":"Запомнила."}.get(actions[-1] if actions else "")
            if not human:
                human=answer if has_grounded_terminal_answer(answer,commands) else report["summary"]
    elif status=="partial":
        if result:human=result+" Но задача выполнена не полностью."
        elif any(positive_evidence(c) for c in commands):human="Получилось выполнить только часть задачи."
        else:human="Не получилось завершить задачу полностью."
    elif status=="failed":human=(result+" Но закончить задачу не удалось.") if result else "Не получилось завершить задачу."
    elif status=="blocked":
        if report.get('error_code')=='clarification_required':
            terminal=next((c for c in reversed(commands) if c.get('status')=='terminal'),{})
            payload=terminal.get('result') or {}
            if terminal.get('tool_name')=='answer.ask_clarification' and payload.get('question')==answer:
                options=payload.get('options')
                suffix='\n\n'+'\n'.join(f'{i}. {option}' for i,option in enumerate(options,1)) if isinstance(options,list) and options and all(isinstance(o,str) for o in options) else ''
                return {'conversational_response':answer+suffix,'execution_details':''}
        receipt=task.get("task_receipt") or {}
        if receipt.get("command_outcome")=="success" and any(positive_evidence(c) for c in commands):
            human="Команда выполнена, но остальную задачу завершить не удалось."
        else:human="Не могу продолжить без твоего решения."
    elif status=="cancelled":human="Задача отменена."
    else:
        human={
            'action_evidence_unconfirmed':'Не могу подтвердить результат: проверка не связала изменение с доказательствами текущей задачи. Подробности сохранены в ходе выполнения.',
            'goal_not_completed':'Не могу подтвердить результат: полученный ответ не завершает исходное поручение. Подробности сохранены в ходе выполнения.',
            'terminal_answer_missing':'Не могу подтвердить результат: выполнение остановилось без проверенного итогового ответа. Уже выполненные действия автоматически не повторяю.',
        }.get(report.get('error_code'),'Не могу подтвердить результат. Подробности сохранены в ходе выполнения.')
    if status=="blocked" and (task.get("task_receipt") or {}).get("answer_source")=="trust_guard":
        return {"conversational_response":answer,"execution_details":""}
    if status in {"partial","failed","blocked"}:
        terminal=next((c for c in reversed(commands) if _tool(c)=="answer.text" and c.get("status")=="terminal"),{})
        payload=terminal.get("result") if isinstance(terminal.get("result"),dict) else {}
        terminal_status={"partial_report":"partial","error_report":"failed","failure":"failed","clarification":"blocked"}.get(payload.get("answer_type"))
        receipt=task.get("task_receipt") or {}
        continuation_partial=(status=="blocked" and terminal_status=="partial"
            and receipt.get("answer_source")=="confirmation_result"
            and receipt.get("task_status")=="partial" and receipt.get("continuation_status")=="unavailable")
        if (terminal_status==status or continuation_partial) and has_validated_terminal_answer(answer,commands):
            return {"conversational_response":answer,"execution_details":""}
        reason=_reason(task,report)
        if reason:human+=" "+reason
    if status in {"partial", "failed"} and ((receipt.get("step_results") or task.get("tasks"))):
        human = failure_explanation(task, report)
    return {"conversational_response":human,"execution_details":answer if answer!=human else ""}



def worker_spoken_response(task, report=None):
    """Compose from verified structured outcomes; never rewrite model prose by replacements."""
    report=normalized_worker_report(task,report)
    human=worker_presentation(task,report)["conversational_response"]
    # A ready clarification is the actual question, not a generic incomplete-task summary.
    if report.get('error_code')=='clarification_required':return human
    # Incomplete/permission-sensitive results stay protected; informational answers stay intact.
    if report["status"]!="success":
        if len(human)>420:
            # Reuse the existing structured negative summary for concise speech.
            # The canonical terminal text stays intact in chat and storage.
            return worker_presentation({**task,"answer":""},report)["conversational_response"]
        return human
    commands=[c for c in task.get("commands") or [] if isinstance(c,dict)]
    operations=[c for c in commands if not _tool(c).startswith("answer.")]
    terminal=next((c for c in reversed(commands) if _tool(c)=="answer.text" and c.get("status")=="terminal"),{})
    payload=terminal.get("result") if isinstance(terminal.get("result"),dict) else {}
    check=payload.get("self_check") if isinstance(payload.get("self_check"),dict) else {}
    informational=check.get("claims_completed_action") is False
    if operations and has_grounded_terminal_answer(task.get("answer") or "",commands) and (informational or all(_tool(c) in OBSERVATION_TOOLS for c in operations)):
        return human
    artifacts=_artifacts(task,report)
    if any(isinstance(c,dict) and _tool(c)=="transfer_file" and positive_evidence(c) and c["result"].get("sha256_verified") is True for c in task.get("commands") or []):
        return "Передала файл. "+(_desktop_location(task,artifacts) or "Копия проверена.")
    if artifacts:
        # Location is only the owner-scoped, verified location. Do not infer a write from existence.
        return human
    actions=[_tool(c) for c in task.get("commands") or [] if isinstance(c,dict) and positive_evidence(c) and _tool(c) not in OBSERVATION_TOOLS]
    if len(actions)==1 and actions[0]=="app.launch":return "Открыла приложение."
    if len(actions)==1 and actions[0]=="app.open_url":return "Открыла страницу."
    if len(actions)==1 and actions[0]=="web.focus":return "Переключила вкладку."
    if len(actions)==1 and actions[0]=="remember_fact":return "Запомнила."
    return human
