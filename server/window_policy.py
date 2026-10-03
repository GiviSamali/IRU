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

_OBJECT = r'(?:это окно|окно|его|word|ворд|excel|эксель|браузер|блокнот|vs code|vscode|chrome|comet|steam)'
_DEVICE = r'(?: на (?:[\w-]+|первом пк|втором пк))?'
_CLAUSE = re.compile(r'(?:сверни|разверни|восстанови|активируй|покажи|закрой|верни) ' + _OBJECT + r'(?: в обычный размер)?' + _DEVICE + r'|' + _OBJECT + r' (?:вправо|влево|справа|слева)' + _DEVICE + r'|поставь ' + _OBJECT + r' (?:слева|справа|на левую половину экрана|на правую половину экрана)' + _DEVICE + r'|перенеси ' + _OBJECT + r' на (?:второй|первый|\d+) монитор' + _DEVICE)

def ordinary_window_request(message):
    text = message.lower().strip().rstrip('.!?')
    if text in {'какие окна сейчас открыты', 'какие окна открыты', 'какое окно сейчас активно', 'какое окно активно'}:
        return True
    text = re.sub(r'^на (?:первом пк|втором пк|[\w-]+) ', '', text)
    clauses = re.split(r'\s*[,;]\s*(?:а\s+)?|\s+а\s+|\s+и\s+', text)
    return bool(clauses) and all(_CLAUSE.fullmatch(c) for c in clauses)


def window_action_sequence(message):
    """Expected action order for the conservative fast path; resolution stays with LLM/agent."""
    if not ordinary_window_request(message):
        return []
    text = re.sub(r'^на (?:первом пк|втором пк|[\w-]+) ', '', message.lower().strip().rstrip('.!?'))
    clauses = re.split(r'\s*[,;]\s*(?:а\s+)?|\s+а\s+|\s+и\s+', text)
    actions = []
    for clause in clauses:
        if clause.startswith('сверни '): action = 'minimize'
        elif clause.startswith('разверни '): action = 'maximize'
        elif clause.startswith(('верни ', 'восстанови ')): action = 'restore'
        elif clause.startswith(('активируй ', 'покажи ')): action = 'activate'
        elif clause.startswith('закрой '): action = 'close'
        elif clause.startswith('перенеси '): action = 'move_monitor'
        elif re.search(r'(?:вправо|справа|правую половину экрана)$', re.sub(_DEVICE + '$', '', clause)): action = 'right'
        elif re.search(r'(?:влево|слева|левую половину экрана)$', re.sub(_DEVICE + '$', '', clause)): action = 'left'
        else: return []
        actions.append(action)
    return actions
