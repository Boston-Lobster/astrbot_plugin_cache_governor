"""Pure request-layout rules; no AstrBot imports, stored messages or LLM calls.

An anchor is a fingerprint of authoritative history, never an alternate history.
Removed, truncated or summarized messages are never restored for a cache hit.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any

MAX_MESSAGES = 1024
MAX_TEXT = 1_000_000
_MEMORY_ROOT = re.compile(r"\A(<system_context>\s*)(.*?)(\s*</system_context>)\Z", re.S)
_SECTION = re.compile(r"<(user_profiles|memories|notes|soul_state)>(.*?)</\1>", re.S)
_TAG = re.compile(r"</?(?:user_profiles|memories|notes|soul_state|system_context)(?:>|\s)")
_ORDER = {'user_profiles': 0, 'memories': 1, 'notes': 2, 'soul_state': 3}


def text_content(message: Any) -> str | None:
    if not isinstance(message, dict):
        return None
    value = message.get('content')
    if isinstance(value, str):
        return value
    if isinstance(value, list) and value and all(
        isinstance(p, dict) and p.get('type') == 'text'
        and isinstance(p.get('text'), str) and set(p) <= {'type', 'text'}
        for p in value
    ):
        return '\n'.join(p['text'] for p in value)
    return None


def background_kind(message: Any) -> str | None:
    if not isinstance(message, dict) or message.get('role') != 'user' or message.get('_no_save') is not True:
        return None
    if any(message.get(k) for k in ('tool_calls', 'tool_call_id', 'function_call', 'name')):
        return None
    text = text_content(message)
    if text is None:
        return None
    if message.get('sender_id') == 'angelheart-reply-length' and text.startswith('<system_reminder>') and text.endswith('</system_reminder>'):
        return 'reply_length'
    if message.get('sender_id') == 'angelheart-work-ledger' and text.startswith('<system_reminder>') and text.endswith('</system_reminder>'):
        return 'work_state'
    if text.startswith('<system_context>') and text.endswith('</system_context>'):
        return 'memory'
    if text.startswith('<emotion_context>') and text.endswith('</emotion_context>'):
        return 'emotion'
    return None


def normalize_memory_text(text: str) -> tuple[str, bool]:
    """Reorder only a fully recognized collection of independent sibling blocks.

    Exact inner payload and original separators are retained. Ambiguous, duplicate,
    nested, unknown or partially recognized markup is left untouched.
    """
    root = _MEMORY_ROOT.fullmatch(text)
    if not root:
        return text, False
    body = root[2]
    matches = list(_SECTION.finditer(body))
    if not matches or len({m[1] for m in matches}) != len(matches):
        return text, False
    previous = 0
    separators = []
    for m in matches:
        separator = body[previous:m.start()]
        if separator.strip() or _TAG.search(m[2]):
            return text, False
        separators.append(separator)
        previous = m.end()
    if body[previous:].strip():
        return text, False
    ordered = sorted(matches, key=lambda m: _ORDER[m[1]])
    if [m[1] for m in matches] == [m[1] for m in ordered]:
        return text, False
    rewritten = ''.join(separators[i] + m[0] for i, m in enumerate(ordered)) + body[previous:]
    return root[1] + rewritten + root[3], True


def normalize_contexts(contexts: Any, *, order_background: bool = True, memory_sections: bool = True) -> tuple[Any, dict]:
    counts = {'background_reordered': 0, 'memory_reordered': 0, 'recognized_background': 0}
    if not isinstance(contexts, list) or len(contexts) > MAX_MESSAGES:
        return contexts, counts
    if sum(len(text_content(m) or '') for m in contexts) > MAX_TEXT:
        return contexts, counts
    result = list(contexts)
    kinds = [background_kind(m) for m in result]
    counts['recognized_background'] = sum(k is not None for k in kinds)
    if memory_sections:
        for i, kind in enumerate(kinds):
            if kind != 'memory' or not isinstance(result[i].get('content'), str):
                continue
            content, changed = normalize_memory_text(result[i]['content'])
            if changed:
                result[i] = {**result[i], 'content': content}
                counts['memory_reordered'] += 1
    if order_background:
        # Unknown messages, durable history, system messages and tools are fences.
        # No message crosses a fence and no historical message changes position.
        rank = {'reply_length': 0, 'memory': 1, 'emotion': 2, 'work_state': 3}
        start = 0
        while start < len(result):
            if kinds[start] is None:
                start += 1
                continue
            end = start + 1
            while end < len(result) and kinds[end] is not None:
                end += 1
            indices = sorted(range(start, end), key=lambda i: rank[kinds[i]])
            counts['background_reordered'] += sum(i != j for i, j in zip(range(start, end), indices))
            result[start:end] = [result[i] for i in indices]
            start = end
    return result, counts


def stable_toolset(toolset: Any) -> tuple[Any, bool]:
    tools = getattr(toolset, 'tools', None)
    if not isinstance(tools, list) or len(tools) > 512:
        return toolset, False
    names = [getattr(tool, 'name', None) for tool in tools]
    if not all(isinstance(n, str) and n for n in names) or len(set(names)) != len(names):
        return toolset, False
    indices = sorted(range(len(tools)), key=lambda i: names[i])
    if indices == list(range(len(tools))):
        return toolset, False
    result = copy.copy(toolset)
    result.tools = [tools[i] for i in indices]
    return result, True


def _digest(value: Any) -> tuple[str, int]:
    raw = json.dumps(value, ensure_ascii=False, separators=(',', ':'), default=lambda _: '<opaque>').encode()
    return hashlib.sha256(raw).hexdigest(), len(raw)


def _message_fingerprint(message: Any) -> tuple[str, int]:
    if isinstance(message, dict):
        # Ignore request-only metadata that is not sent as model content.
        message = {k: v for k, v in message.items() if k in {'role','content','tool_calls','tool_call_id','name','reasoning_content'}}
    return _digest(message)


@dataclass
class Anchor:
    updated: float
    system: tuple[str, int]
    tools: tuple[str, int]
    history: list[tuple[str, int]]
    messages: list[tuple[str, int]]


class PrefixTracker:
    """Bounded hashes only. Session ID includes conversation/provider/persona scope."""
    def __init__(self, capacity: int = 128, ttl: int = 1800):
        self.capacity = min(512, max(1, int(capacity)))
        self.ttl = min(86400, max(30, int(ttl)))
        self.states: OrderedDict[str, Anchor] = OrderedDict()

    def observe(self, scope: str, system: str, tools: Any, contexts: list, *, now: float | None = None) -> dict:
        now = time.monotonic() if now is None else now
        for key in list(self.states):
            if now - self.states[key].updated > self.ttl:
                del self.states[key]
        messages = [_message_fingerprint(m) for m in contexts[:MAX_MESSAGES]]
        history = [_message_fingerprint(m) for m in contexts[:MAX_MESSAGES] if not (isinstance(m, dict) and m.get('_no_save'))]
        cur = Anchor(now, _digest(system), _digest(tools), history, messages)
        prev = self.states.pop(scope, None)
        output = {'prefix_state': 'cold', 'shared_context_messages': 0, 'shared_serialized_bytes': 0}
        if prev is not None:
            if prev.system != cur.system:
                output['prefix_state'] = 'system_changed'
            elif prev.tools != cur.tools:
                output['prefix_state'] = 'tools_changed'
            else:
                n = 0
                for a, b in zip(prev.messages, cur.messages):
                    if a != b:
                        break
                    n += 1
                output['shared_context_messages'] = n
                output['shared_serialized_bytes'] = sum(size for _, size in cur.messages[:n])
                if len(history) >= len(prev.history) and history[:len(prev.history)] == prev.history:
                    output['prefix_state'] = 'history_append' if len(history) > len(prev.history) else 'history_stable'
                else:
                    output['prefix_state'] = 'history_changed'
        self.states[scope] = cur
        while len(self.states) > self.capacity:
            self.states.popitem(last=False)
        return output

    def clear(self):
        self.states.clear()
