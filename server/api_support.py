import re
import time
import hashlib
import json

from fastapi import HTTPException, Request

try:
    from .auth import is_jwt, verify_access_token
    from .database import get_user_by_id, get_user_by_token
    from .runtime_state import ip_rate_counters, rate_counters
except ImportError:
    from auth import is_jwt, verify_access_token
    from database import get_user_by_id, get_user_by_token
    from runtime_state import ip_rate_counters, rate_counters


ADMIN_USER_ID = 1


def _is_admin(user: dict) -> bool:
    """Admin access check by id, not by username."""
    return user.get("id") == ADMIN_USER_ID


RATE_LIMIT = 30
RATE_WINDOW = 60
IP_RATE_LIMIT = 10
IP_RATE_WINDOW = 60


def check_rate_limit(user_id: str) -> bool:
    """Return True when the per-user rate limit is still allowed."""
    now = time.time()
    window_start = now - RATE_WINDOW
    rate_counters[user_id] = [ts for ts in rate_counters[user_id] if ts > window_start]
    if len(rate_counters[user_id]) >= RATE_LIMIT:
        return False
    rate_counters[user_id].append(now)
    return True


def check_ip_rate_limit(ip: str) -> bool:
    """Return True when the per-IP rate limit is still allowed."""
    now = time.time()
    window_start = now - IP_RATE_WINDOW
    ip_rate_counters[ip] = [ts for ts in ip_rate_counters[ip] if ts > window_start]
    if len(ip_rate_counters[ip]) >= IP_RATE_LIMIT:
        return False
    ip_rate_counters[ip].append(now)
    return True


DANGEROUS_PATTERNS = [
    r"format\s+[a-z]:",
    r"diskpart",
    r"bcdedit",
    r"rm\s+-rf\s+/",
    r"rmdir\s+/s\s+/q\s+[a-z]:\\\\",
    r"del\s+/[sfq].*\\windows",
    r"reg\s+delete\s+hklm",
    r"net\s+stop\s+(windefend|mpssvc|wuauserv)",
    r"powershell.*downloadstring",
    r"powershell.*downloadfile.*\|.*iex",
    r"certutil.*-urlcache.*-split",
    r"bitsadmin.*transfer",
    r"net\s+user\s+.*\s+/add",
    r"net\s+localgroup\s+administrators",
    r"netsh\s+advfirewall\s+set.*state\s+off",
    r"cipher\s+/e",
]


DELETE_PATTERNS = [
    r"\bremove-item\b",
    r"\bdel\s+",
    r"\brd\s+",
    r"\brmdir\s+",
    r"\brm\s+",
    r"\bclear-content\b",
    r"\b(?:os\.(?:remove|unlink|rmdir)|shutil\.rmtree)\s*\(",
    r"\.(?:unlink|rmdir)\s*\(",
    r"\bIO\.(?:File|Directory)\]::Delete\s*\(",
]


CONFIRM_PATTERNS = DELETE_PATTERNS + [
    r"stop-process",
    r"\bkill\s+",
    r"taskkill",
    r"shutdown",
    r"restart-computer",
    r"uninstall",
]


_dangerous_re = [re.compile(pattern, re.IGNORECASE) for pattern in DANGEROUS_PATTERNS]
_confirm_re = [re.compile(pattern, re.IGNORECASE) for pattern in CONFIRM_PATTERNS]
_delete_re = [re.compile(pattern, re.IGNORECASE) for pattern in DELETE_PATTERNS]


def is_deletion_command(command: str) -> bool:
    """Recognize explicit deletion in command text, not arbitrary script contents."""
    return any(pattern.search(command) for pattern in _delete_re)


def is_command_safe(command: str) -> bool:
    """Return False if the command matches a forbidden pattern."""
    return not any(pattern.search(command) for pattern in _dangerous_re)


def needs_confirmation(command: str) -> bool:
    """Return True if the command should be user-confirmed."""
    return any(pattern.search(command) for pattern in _confirm_re)


def action_fingerprint(action, params):
    return hashlib.sha256(json.dumps([action, params], sort_keys=True, ensure_ascii=False,
                                    separators=(",", ":")).encode()).hexdigest()


def read_only_execute_command(params, *, device_os=None):
    """Recognize a closed literal observation grammar, not arbitrary shell code.

    No expressions, substitutions, redirection, statements, script blocks,
    aliases or executable paths. Unknown syntax stays behind confirmation.
    """
    if params.get('artifact_paths') or params.get('preserve_content_paths'):
        return False
    command=str(params.get('command') or '').strip()
    if command.casefold() in {'whoami','hostname','pwd'}:
        return True
    if device_os is not None and 'windows' not in device_os.casefold():
        return False
    if params.get('shell','auto') not in {'auto','powershell'}:
        return False
    if any(c in command for c in '$`;;&<>\r\n\x00(){}@#[]'):
        return False
    if any((c.isspace() and c not in ' \t') or (ord(c)<32 and c!='\t') for c in command):
        return False
    # PowerShell literal tokens: quotes must enclose a whole token. Backslash
    # is a path separator, never a quote escape. Unsupported quoting fails shut.
    stages=[[]]; i=0
    while i<len(command):
        if command[i].isspace(): i+=1; continue
        if command[i]=='|': stages.append([]); i+=1; continue
        if command[i] in {'"',"'"}:
            quote=command[i]; end=command.find(quote,i+1)
            if end<0 or end+1<len(command) and not command[end+1].isspace() and command[end+1]!='|':return False
            stages[-1].append(command[i+1:end]);i=end+1
        else:
            end=i
            while end<len(command) and not command[end].isspace() and command[end]!='|':end+=1
            token=command[i:end]
            if any(c in token for c in "\"'"):return False
            stages[-1].append(token);i=end
    specs={
        'get-childitem':({'file','directory','force','recurse','name'}, {'literalpath','path','filter','depth'}),
        'get-item':({'force'}, {'literalpath','path'}),
        'get-content':({'raw'}, {'literalpath','path','tail','totalcount','encoding'}),
        'get-filehash':(set(), {'literalpath','path','algorithm'}),
        'test-path':(set(), {'literalpath','path','pathtype'}),
        'get-location':(set(),set()),
        'select-object':(set(),{'property','first','last'}),
        'convertto-json':({'compress'}, {'depth'}),
    }
    properties={'name','fullname','extension','length','lastwritetime','attributes','psiscontainer','hash','algorithm','path'}
    seen_stages=set()
    for index,stage in enumerate(stages):
        if not stage:return False
        name=stage[0].casefold()
        if name not in specs or name in seen_stages:return False
        if index==0 and name in {'select-object','convertto-json'}:return False
        if index>0 and name not in {'select-object','convertto-json'}:return False
        if 'convertto-json' in seen_stages:return False
        seen_stages.add(name);flags,values=specs[name];seen=set();j=1
        while j<len(stage):
            token=stage[j];j+=1
            if token.startswith('-'):
                key=token[1:].casefold()
                if key in seen:return False
                seen.add(key)
                if key in flags:continue
                if key not in values or j==len(stage):return False
                value=stage[j];j+=1
            else:
                key='property' if name=='select-object' else 'path'
                if key not in values or key in seen:return False
                seen.add(key);value=token
            if not value or value.startswith('-'):return False
            if key in {'depth','tail','totalcount','first','last'}:
                if not value.isascii() or not value.isdecimal() or int(value)>10000:return False
            elif key=='property':
                if not all(p.casefold() in properties for p in value.split(',')):return False
            elif key in {'path','literalpath'}:
                # File system paths only: never PS providers/alternate streams.
                for path in value.split(','):
                    if ':' in path and not (len(path)>2 and path[0].isascii() and path[0].isalpha()
                            and path[1]==':' and path[2] in '/\\' and ':' not in path[2:]):return False
            elif key=='algorithm' and value.casefold() not in {'sha256','sha384','sha512'}:return False
            elif key=='encoding' and value.casefold() not in {'utf8','utf-8','unicode','ascii','default'}:return False
            elif key=='pathtype' and value.casefold() not in {'leaf','container','any'}:return False
    return bool(stages[0])


def assess_action(action, params, inspection=None):
    """Patterns can deny known commands; they cannot certify arbitrary code.

    Unclassified shell/interpreter code requires exact-action confirmation.
    This is deliberately conservative, not a semantic code sandbox. Worker risk
    labels, output markers and declared artifact paths cannot waive it.
    """
    command = str(params.get("command") or "") if action in {"execute_cmd","app.launch"} else ""
    read_only = action=='execute_cmd' and read_only_execute_command(params,
        device_os=(inspection.get('os') or 'unknown') if inspection is not None else None)
    denied = not read_only and any(pattern.search(command) for pattern in _dangerous_re)
    deletion = not read_only and any(pattern.search(command) for pattern in _delete_re)
    confirm = not read_only and (deletion or any(pattern.search(command) for pattern in _confirm_re))
    # Only the closed observation grammar can waive code confirmation.
    # It never classifies arbitrary PowerShell/Python as safe.
    opaque = action == 'execute_cmd' and not read_only
    if action=='app.launch':
        from pathlib import PureWindowsPath, PurePosixPath
        candidate=command.strip().strip('"').strip("'")
        # Only a literal absolute document target can use association opening
        # without consenting to opaque executable/interpreter code.
        document=(PureWindowsPath(candidate).is_absolute() or PurePosixPath(candidate).is_absolute()) and (
            PureWindowsPath(candidate).suffix.casefold() in {'.docx','.xlsx','.pptx','.pdf','.txt','.md','.csv','.rtf','.png','.jpg','.jpeg'})
        opaque=not document or bool(params.get('executable') or params.get('args') or params.get('env'))
    confirm = confirm or opaque
    decision = "DENY" if denied else "CONFIRM" if confirm else "ALLOW"
    kind = "deletion" if deletion else "dangerous" if denied or confirm else "command"
    return {"decision": decision, "reason_code": "command_forbidden" if denied else
            "command_confirmation_required" if confirm else "allowed",
            "findings": [{"kind": kind}] if denied or confirm else [],
            "fingerprint": action_fingerprint(action, params),
            "confirmation_channel": "button" if denied or confirm else "voice_or_button"}


class ActionConfirmationRequired(RuntimeError):
    def __init__(self, assessment):
        super().__init__("CONFIRM_REQUIRED: Команда требует подтверждения пользователя.")
        self.assessment = assessment


def is_recovery_observation(action, params, *, device_os=None):
    """Known observations only; shell/launch and Worker risk labels prove nothing."""
    try:
        from .tool_registry import canonical_tool_name
    except ImportError:
        from tool_registry import canonical_tool_name
    action = canonical_tool_name(action)
    if action=='execute_cmd':
        return read_only_execute_command(params,device_os=device_os)
    if action in {"office_docx", "office_xlsx", "office_pptx"}:
        return params.get("action") == "inspect"
    if action == "window.control":
        return params.get("action") in {"active", "list", "find", "monitors"}
    if action == "device.refresh_state":
        return params.get("mode", "snapshot") == "snapshot"
    return action in {"list_dir", "read_file", "get_file_content", "device.get_cached_passport", "device.get_passport",
                      "window.list", "window.find", "window.verify", "app.verify_launch",
                      "web_search", "web.tabs", "web.read", "web.elements", "web.wait", "memory.get_stats",
                      "memory.list_facts", "system.list_tools", "system.get_last_run_summary"}


def get_current_user(request: Request) -> dict:
    """
    Extract the user from:
      1. Authorization: Bearer <jwt>
      2. X-Token: <jwt_or_uuid>
      3. ?token=<uuid>
    """
    token = None
    auth_header = request.headers.get("Authorization", "")
    if auth_header.startswith("Bearer "):
        token = auth_header[7:]
    if not token:
        token = request.headers.get("X-Token") or request.query_params.get("token")
    if not token:
        raise HTTPException(status_code=401, detail="Требуется токен авторизации")

    if is_jwt(token):
        payload = verify_access_token(token)
        if not payload:
            raise HTTPException(status_code=401, detail="Токен истёк или недействителен")
        user = get_user_by_id(int(payload["sub"]))
        if not user:
            raise HTTPException(status_code=401, detail="Пользователь не найден")
        return user

    user = get_user_by_token(token)
    if not user:
        raise HTTPException(status_code=401, detail="Недействительный токен")
    return user


def _client_ip(request: Request) -> str:
    """Get client IP (supports X-Forwarded-For behind reverse proxy)."""
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
