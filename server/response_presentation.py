"""Human presentation of an existing Worker Report; no execution or model calls."""

try:
    from .worker_reports import operation_facts, saved_worker_report
    from .controller_trust import accepted_result_text
    from .tool_registry import CANONICAL_TOOL_NAMES
except ImportError:
    from worker_reports import operation_facts, saved_worker_report
    from controller_trust import accepted_result_text
    from tool_registry import CANONICAL_TOOL_NAMES

# These are tool contracts, not a classifier of the user's words.
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
    """Project saved operation progress; never infer it from generated answer text."""
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
    """One Worker presentation entry point, including restored/voice paths."""
    report=normalized_worker_report(task,report)
    view=render_worker_report(task,report)
    status=report['status']
    if status in {'running','queued','waiting_dependency','waiting_input','waiting_confirmation','blocked','cancelled'}:
        return view
    operations=[c for c in task.get('commands') or [] if isinstance(c,dict)
        and c.get('tool_name') not in {'answer.text','answer_text'} and isinstance(c.get('result'),dict)]
    effects=task.get('window_effects') or []
    opened=None
    if effects and not task.get('assigned_artifacts') and status!='success':
        latest=next((c for c in reversed(operations) if c.get('tool_name') in
            {'window_control','window.control','app.launch','app_launch','app.open_url','app_open_url'}),None)
        human=('Достигнутое состояние окна не подтверждено; автоматически повторять действие не буду.'
               if status=='unknown' else 'Требуемое действие с окном не завершено.')
        if status!='unknown' and latest and isinstance(latest['result'].get('summary'),str):human+=' '+latest['result']['summary']
        view['conversational_response']=human
    elif status in {'success','partial'} and not task.get('assigned_artifacts'):
        opened=next((c for c in reversed(operations) if c.get('tool_name') in
            {'app.launch','app_launch','app.open_url','app_open_url'} and operation_facts(c)=='success'
            and c['result'].get('completion_state')=='success'),None)
        if opened:
            result=opened['result'];window=result.get('window') or {}
            human='Открытие подтверждено.'
            title=result.get('window_title') or window.get('title')
            if isinstance(title,str) and title:human+=' Окно: '+title+'.'
            if isinstance(result.get('url'),str):human+=' Адрес: '+result['url']+'.'
            view['conversational_response']=human
    if effects and status=='success' and not opened and not task.get('assigned_artifacts'):
        native=[c['result'].get('summary') for c in operations if c.get('tool_name') in {'window.control','window_control'}
                and operation_facts(c)=='success' and c['result'].get('completion_state')=='success']
        view['conversational_response']='\n'.join(value for value in native if isinstance(value,str) and value) or 'Запрошенное состояние окна подтверждено.'
    terminal=next((c for c in reversed(task.get('commands') or []) if c.get('tool_name') in {'answer.text','answer_text'}
        and c.get('status')=='terminal'),{})
    if (terminal.get('result') or {}).get('_result_contract')==3:
        if not effects and not opened:
            view['conversational_response']=report['summary']
            if (status in {'partial','unknown'}
                    and not task.get('assigned_artifacts')):
                try:
                    from .run_journal import unfinished_execution_result
                except ImportError:
                    from run_journal import unfinished_execution_result
                view['conversational_response']=unfinished_execution_result(task.get('commands') or [],
                    report.get('error_code') or 'goal_not_completed')['answer']
        view['execution_details']=(f"Технический статус: {status}.\n"
            f"Источники: {', '.join(report['evidence_refs']) or 'нет'}.\n\n"+(view.get('execution_details') or task.get('answer') or report['summary']))
    return view



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
    return worker_presentation(task, report)['conversational_response']
