"""Token accounting for complete requests, not model routing or execution policy."""
import json
import math
from urllib.parse import urlparse


# Official DeepSeek V4 release: https://deepseek.com/en/news/v4-preview/
# Use only for the official endpoint and these exact model ids, not proxies/aliases.
OFFICIAL_WINDOWS = {"deepseek-v4-flash": 1_000_000, "deepseek-v4-pro": 1_000_000}


def estimate_tokens(value):
    """UTF-8 byte upper estimate + framing margin; NOT an exact tokenizer.

    No tokenizer assets are installed/downloaded implicitly. JSON escaping and
    non-text fields are included. Provider usage is recorded separately.
    """
    return math.ceil(len(json.dumps(value, ensure_ascii=False, separators=(',', ':')).encode('utf-8')) * 1.1) + 64


def limits(cfg, model, output_tokens):
    configured = (cfg.get('model_context_windows') or {}).get(model)
    if configured is None and model == cfg.get('model'):
        configured = cfg.get('context_window_tokens')
    official = urlparse(cfg.get('base_url', '')).hostname == 'api.deepseek.com'
    def integer(value,name,minimum):
        if isinstance(value,bool):raise ValueError('invalid_'+name)
        try:result=int(value)
        except (TypeError,ValueError,OverflowError):raise ValueError('invalid_'+name)
        if result<minimum or isinstance(value,float) and value!=result:raise ValueError('invalid_'+name)
        return result
    window=integer(configured,'context_window',1) if configured is not None else OFFICIAL_WINDOWS.get(model) if official else None
    output_tokens=integer(output_tokens,'output_reserve',1)
    reasoning=integer(cfg.get('context_reasoning_reserve_tokens',0),'reasoning_reserve',0)
    reserve=output_tokens+reasoning
    safety=integer(cfg.get('context_safety_tokens',max(256,int(window*.02)) if window else 1024),'context_safety',0)
    # An unknown backend needs a declared window to promise provider capacity.
    available=window-reserve-safety if window else integer(cfg.get('unknown_model_input_tokens',262144),'local_input_ceiling',1)
    if available <= 0:
        raise ValueError('context_reserve_exceeds_window')
    return {'window_tokens': window, 'window_source': 'configuration' if configured is not None else 'official_catalog' if window else 'unknown',
            'input_limit_tokens': available, 'output_reserve_tokens': int(output_tokens),
            'reasoning_reserve_tokens': reserve-int(output_tokens), 'safety_tokens': safety,
            'counter': 'conservative_utf8_bytes_v1','count_is_exact':False}


def request_budget(request, cfg):
    result = limits(cfg, request['model'], request.get('max_tokens') or cfg.get('max_tokens', 32768))
    result['estimated_input_tokens'] = math.ceil(estimate_tokens(request)*float(cfg.get('_context_calibration',1)))
    result['calibration_factor'] = float(cfg.get('_context_calibration',1))
    result['endpoint'] = urlparse(cfg.get('base_url','')).hostname
    return result


def fit_request(request, cfg, protected_user_index=None):
    """Drop only historical turns, as whole tool-call/result groups.

    Never slice current human input, system instructions, tool schemas, or the
    latest tool observation. An impossible mandatory request fails before send.
    Original loop messages/journals remain unchanged.
    """
    request = {**request, 'messages': list(request['messages'])}
    if protected_user_index is not None:
        if type(protected_user_index) is not int or not 0<=protected_user_index<len(request['messages']) or request['messages'][protected_user_index].get('role')!='user':
            raise ValueError('invalid_current_human_boundary')
    removed=0
    while True:
        budget=request_budget(request,cfg)
        if budget['estimated_input_tokens']<=budget['input_limit_tokens']:
            budget['removed_history_messages']=removed
            budget['current_human_boundary_explicit']=protected_user_index is not None
            return request,budget
        messages=request['messages']
        # No boundary means no guesses from role=user: all input text is kept.
        start=next((i for i,m in enumerate(messages[:protected_user_index]) if m.get('role')!='system'),None) if protected_user_index is not None else None
        end=None
        if start is not None:
            end=start+1
            if messages[start].get('tool_calls'):
                while end<len(messages) and messages[end].get('role')=='tool':end+=1
                if end>protected_user_index:raise ValueError('context_tool_group_crosses_human_boundary')
        else:
            groups=[i for i,m in enumerate(messages) if m.get('role')=='assistant' and m.get('tool_calls')]
            for index in groups[:-1]:
                stop=index+1
                while stop<len(messages) and messages[stop].get('role')=='tool':stop+=1
                expected={call['id'] for call in messages[index]['tool_calls']}
                actual={m.get('tool_call_id') for m in messages[index+1:stop]}
                if expected==actual and stop>index+1:
                    start,end=index,stop;break
        if start is None:raise ValueError('context_mandatory_request_exceeds_budget')
        removed+=end-start;del messages[start:end]
        if protected_user_index is not None and start<protected_user_index:protected_user_index-=end-start


def configured_context_budget(output_tokens=2400):
    try:
        from .controller import load_llm_config
    except ImportError:
        from controller import load_llm_config
    try:
        cfg = load_llm_config()
    except FileNotFoundError:
        cfg = {}
    return limits(cfg, cfg.get('model', ''), output_tokens)['input_limit_tokens']


def clip_tokens(text, budget):
    text = str(text or '')
    if estimate_tokens(text) <= budget:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo+hi+1)//2
        if estimate_tokens(text[:mid]+' […]') <= budget: lo = mid
        else: hi = mid-1
    return text[:lo]+' […]' if lo else ''


def calibrated_config(cfg, model):
    """Only increase the estimate after observed undercounts on this endpoint."""
    try:
        try:from . import database as db
        except ImportError:import database as db
        with db.get_db() as c:
            rows=c.execute('SELECT metadata FROM llm_usage_events WHERE model=? AND request_ok=1 ORDER BY id DESC LIMIT 32',(model,)).fetchall()
        factor=max(1.0,float(cfg.get("_context_calibration",1)))
        endpoint=urlparse(cfg.get('base_url','')).hostname
        for row in rows:
            try:
                metadata=json.loads(row[0] or '{}')
                previous=metadata.get('context_budget') or {}
                if previous.get('endpoint')!=endpoint:continue
                estimated=float(previous.get('estimated_input_tokens',0));actual=float(previous.get('actual_input_tokens',0))
                old_factor=float(previous.get('calibration_factor',1))
                if estimated>0 and actual>estimated and all(math.isfinite(x) for x in (estimated,actual,old_factor)):
                    factor=max(factor,actual/estimated*max(1,old_factor)*1.1)
            except (ValueError,TypeError,AttributeError):continue
        return {**cfg,'_context_calibration':factor}
    except Exception:
        # Missing test DB/usage table cannot silently substitute a model window.
        return cfg
