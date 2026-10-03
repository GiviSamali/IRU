"""Small reusable response policy and conservative ordinary-window classifier."""
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

_OBJECT = r'(?:это окно|окно|его|word|ворд|excel|эксель|браузер|блокнот|vs code|vscode|chrome|comet|steam|gpt|chatgpt|чатгпт|проводник)'
_DEVICE = r'(?: на (?:[\w-]+|первом пк|втором пк))?'
_CLAUSE = re.compile(r'(?:сверни|разверни|восстанови|активируй|покажи|закрой|верни) ' + _OBJECT + r'(?: в обычный размер)?' + _DEVICE + r'|' + _OBJECT + r' (?:вправо|влево|справа|слева)' + _DEVICE + r'|поставь ' + _OBJECT + r' (?:слева|справа|на левую половину экрана|на правую половину экрана)' + _DEVICE + r'|перенеси ' + _OBJECT + r' на (?:второй|первый|\d+) монитор' + _DEVICE + r'|открой (?:gpt|chatgpt|чатгпт)' + _DEVICE)

_BARE = {'сверни': 'minimize', 'разверни': 'maximize', 'восстанови': 'restore',
         'верни': 'restore', 'разверни обратно': 'restore', 'верни обратно': 'restore',
         'восстанови обратно': 'restore', 'на весь экран': 'maximize', 'в обычный размер': 'restore'}

def normalize_window_request(message):
    text = re.sub(r'\s+', ' ', message.lower()).strip().rstrip('.!?')
    return re.sub(r'^(?:(?:отлично|хорошо|ладно|а теперь|теперь|пожалуйста)[, ]+)+', '', text)

def unsupported_virtual_desktop_request(message):
    text = normalize_window_request(message)
    virtual = re.search(r'виртуальн\w* (?:рабоч\w* )?стол\w*', text)
    return bool(virtual and re.match(r'(?:перенеси|перемести|отправь|переключи|поставь|проводник|окно|браузер|word|excel|gpt|chatgpt)\b', text))

def ordinary_window_request(message):
    text = normalize_window_request(message)
    if text in _BARE or re.fullmatch(r'открой (?:gpt|chatgpt|чатгпт)' + _DEVICE, text) or unsupported_virtual_desktop_request(message):
        return True
    if text in {'какие окна сейчас открыты', 'какие окна открыты', 'какое окно сейчас активно', 'какое окно активно'}:
        return True
    text = re.sub(r'^на (?:первом пк|втором пк|[\w-]+) ', '', text)
    clauses = re.split(r'\s*[,;]\s*(?:а\s+)?|\s+а\s+|\s+и\s+', text)
    return bool(clauses) and all(_CLAUSE.fullmatch(c) for c in clauses)


def window_action_sequence(message):
    """Expected action order for the conservative fast path; resolution stays with LLM/agent."""
    text = normalize_window_request(message)
    if text in _BARE:
        return [_BARE[text]]
    if re.fullmatch(r'открой (?:gpt|chatgpt|чатгпт)' + _DEVICE, text):
        return ['activate']
    if not ordinary_window_request(message) or unsupported_virtual_desktop_request(message):
        return []
    text = re.sub(r'^на (?:первом пк|втором пк|[\w-]+) ', '', normalize_window_request(message))
    clauses = re.split(r'\s*[,;]\s*(?:а\s+)?|\s+а\s+|\s+и\s+', text)
    actions = []
    for clause in clauses:
        if clause.startswith('сверни '): action = 'minimize'
        elif clause.startswith('разверни '): action = 'maximize'
        elif clause.startswith(('верни ', 'восстанови ')): action = 'restore'
        elif clause.startswith(('активируй ', 'покажи ', 'открой ')): action = 'activate'
        elif clause.startswith('закрой '): action = 'close'
        elif clause.startswith('перенеси '): action = 'move_monitor'
        elif re.search(r'(?:вправо|справа|правую половину экрана)$', re.sub(_DEVICE + '$', '', clause)): action = 'right'
        elif re.search(r'(?:влево|слева|левую половину экрана)$', re.sub(_DEVICE + '$', '', clause)): action = 'left'
        else: return []
        actions.append(action)
    return actions


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
    text = normalize_window_request(message)
    if text not in _BARE:
        return None
    args = {'action': _BARE[text], 'target': 'current'}
    if text in {'на весь экран', 'в обычный размер', 'верни', 'восстанови', 'разверни', 'разверни обратно', 'верни обратно', 'восстанови обратно'}:
        # Follow-up references require one observed window from the immediately preceding assistant turn.
        assistant = next((m for m in reversed(history or []) if m.get('role') == 'assistant'), None)
        mutations = [command for command in (assistant or {}).get('commands', [])
                     if (command.get('result') or {}).get('completion_state') == 'success']
        previous_context = recent_window_context([{'role': 'assistant', 'commands': mutations}])
        current_context = [row for row in previous_context if row['device_id'] == device_id]
        if len(previous_context) > 1:
            return {'action': _BARE[text], 'target': 'last', '_clarify': True}
        if previous_context and not current_context:
            # Let the compact LLM route resolve the previous device; never act on the current one as fallback.
            return None
        if len(current_context) == 1:
            args = {'action': _BARE[text], 'window_id': current_context[0]['window_id']}
    return args
