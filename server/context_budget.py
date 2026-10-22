"""Token budgets for text Chat Completions, including schemas and output reserve.

DeepSeek's server usage is authoritative. cl100k is an explicitly labelled,
padded estimate, never advertised as the DeepSeek tokenizer. Missing tokenizer
uses a conservative UTF-8 byte bound. No model/runtime imports or network here.
"""
import json
import math
import hashlib
import os
import tempfile
from functools import lru_cache
from pathlib import Path


class ContextBudgetExceeded(ValueError):
    pass


@lru_cache(maxsize=1)
def _encoding():
    try:
        import tiktoken
        # Never download tokenizer assets synchronously on a production request.
        url = "https://openaipublic.blob.core.windows.net/encodings/cl100k_base.tiktoken"
        cache = Path(os.environ.get("TIKTOKEN_CACHE_DIR") or os.environ.get("DATA_GYM_CACHE_DIR")
                     or str(Path(tempfile.gettempdir()) / "data-gym-cache"))
        if not (cache / hashlib.sha1(url.encode()).hexdigest()).is_file():
            return None
        return tiktoken.get_encoding("cl100k_base")
    except Exception:
        # An absent/unavailable tokenizer must not stop an otherwise valid task.
        return None


def token_measure(value):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    encoding = _encoding()
    if encoding is None:
        return {"tokens": len(text.encode("utf-8")) + 32,
                "method": "utf8_byte_bound", "estimated": True}
    return {"tokens": math.ceil(len(encoding.encode(text, disallowed_special=())) * 1.25) + 32,
            "method": "cl100k_padded_estimate", "estimated": True}


def request_tokens(messages, tools=None):
    return token_measure({"messages": messages, "tools": tools or []})["tokens"]


def limits(cfg, contour, output_tokens):
    default, maximum = (16384, 32768) if contour == "orchestrator" else (65536, 131072)
    normal = int(cfg.get(contour + "_input_tokens", default))
    expanded = int(cfg.get(contour + "_expanded_input_tokens", maximum))
    # Unknown/custom endpoints must explicitly declare their window before expansion.
    declared = cfg.get("context_window_tokens")
    official = str(cfg.get("base_url", "")).rstrip("/") in {
        "https://api.deepseek.com", "https://api.deepseek.com/v1"}
    v4 = str(cfg.get("model", "")).startswith(("deepseek-v4", "deepseek-flash"))
    window = int(declared) if declared is not None else 1_000_000 if official and v4 else normal + output_tokens + 1024
    hard = min(expanded, window - output_tokens - 1024)
    if not 2048 <= normal <= expanded <= 131072 or hard < 2048:
        raise ContextBudgetExceeded("invalid_context_token_budget")
    return min(normal, hard), hard


def check_request(cfg, contour, messages, tools, output_tokens):
    normal, hard = limits(cfg, contour, output_tokens)
    count = request_tokens(messages, tools)
    if count > hard:
        raise ContextBudgetExceeded("context_input_budget_exceeded")
    return {**token_measure({"messages": messages, "tools": tools or []}),
            "normal_budget": normal, "effective_budget": hard if count > normal else normal,
            "expanded": count > normal, "output_reserve": output_tokens}
