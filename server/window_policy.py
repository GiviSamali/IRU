"""Observed-window context, explicit list choices and response policy; no intent keyword routing."""
import re

def silent_window_success(task):
    # Information, failed attempts, pending save dialogs and other tools must stay audible.
    if task.get('status') not in {'done', 'completed', 'completed_with_recovery'} or task.get('plan_suggestion'):
        return False
    actions = [c for c in task.get('commands', []) if c.get('tool_type') != 'answer'
               and (c.get('tool_name') or c.get('action')) not in {'answer_text', 'answer.text'}]
    return bool(actions) and all(
        (c.get('tool_name') or c.get('action')) in {'window_control', 'window.control'}
        and (c.get('result') or {}).get('status') == 'success'
        and (c.get('result') or {}).get('response_policy') == 'silent_on_success'
        for c in actions)

def normalize_window_request(message):
    return re.sub(r'\s+', ' ', str(message or '')).strip().casefold()


def ordinary_window_request(message):
    """Retired route hint: natural-language intent belongs to Orchestrator."""
    return False


def window_action_sequence(message):
    return []


def unsupported_virtual_desktop_request(message):
    return False

def recent_window_context(history, *, device_id=None):
    """Keep only observed opaque IDs and device-scoped window evidence, never legacy HWNDs."""
    rows = []
    for message in reversed(history or []):
        if message.get('role') != 'assistant':
            continue
        for command in reversed(message.get('commands') or []):
            target = command.get('target_device_id') or command.get('device_id')
            if device_id is not None and target != device_id:
                continue
            result = command.get('result') or {}
            if (command.get('tool_name') or command.get('action')) not in {'window.control', 'window_control'}:
                continue
            if result.get('status') != 'success':
                continue
            windows = [result.get('window')] if result.get('window') else result.get('windows') or []
            for window in windows:
                identifier = window.get('window_id')
                if not isinstance(identifier, str) or not re.fullmatch(r'[0-9a-f]{32}', identifier):
                    continue
                row = {'device_id': target, **{key: window[key] for key in ('window_id', 'pid', 'title', 'process_name', 'bounds', 'minimized', 'maximized') if key in window}}
                row['title'] = str(row.get('title') or '')[:160]
                if not any(item['device_id'] == target and item['window_id'] == identifier for item in rows):
                    rows.append(row)
                if len(rows) == 6:
                    return rows
    return rows


def direct_window_action(message, history, device_id):
    return None


def window_input_required(args, result, journal, device_id):
    """Only a not-yet-executed activation can have this bounded continuation."""
    import time
    import uuid
    if args.get('action') != 'activate' or result.get('status') != 'ambiguous':
        return None
    candidates = result.get('candidates') or []
    if not 2 <= len(candidates) <= 20 or not all(
        isinstance(row, dict) and isinstance(row.get('window_id'), str)
        and len(row['window_id']) == 32 and type(row.get('pid')) is int
        and row.get('process_name') and row.get('class_name') for row in candidates
    ):
        return None
    # No completed/unknown side effect can be replayed by this slice.
    for entry in journal[:-1]:
        outcome = entry.get('result') or {}
        if ((entry.get('tool_name') or entry.get('action')) not in {'window_control','window.control'}
                or (entry.get('window_request') or {}).get('action', outcome.get('action')) not in {'list','find','active','monitors'}
                or outcome.get('status') != 'success'):
            return None
    if len({row['window_id'] for row in candidates}) != len(candidates):
        return None
    options = [{key: row[key] for key in ('window_id','pid','process_name','class_name','title') if key in row}
               for row in candidates]
    question = 'Найдено несколько окон. Какое выбрать?\n' + '\n'.join(
        f"{index + 1}. {row.get('title') or row['process_name']}" for index, row in enumerate(options))
    return {'type':'input_required','question_id':uuid.uuid4().hex,'question':question,
            'options':options,'operation':{'action':'activate'},'device_id':device_id,
            'basis':[journal[-1]['step_id']], 'phase':'waiting','expires_at':time.time()+120}


def window_input_choice(text, options):
    # Parse a choice in this exact displayed list, not an independent user intent.
    value = (text or '').strip().casefold().rstrip('.!')
    ordinals = ('первое','второе','третье','четвёртое','пятое')
    if value in ordinals:
        index = ordinals.index(value)
    elif value.isdecimal():
        index = int(value)-1
    else:
        matches = [i for i, row in enumerate(options) if value == str(row.get('title') or '').casefold()]
        if len(matches) != 1:
            raise ValueError('invalid_input_choice')
        index = matches[0]
    if not 0 <= index < len(options):
        raise ValueError('invalid_input_choice')
    return index
