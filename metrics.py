"""Numeric-only measurements. No prompts, IDs, keys, outputs or raw exceptions."""
from __future__ import annotations

import datetime
import json
import os
import pathlib
import threading
from collections import Counter


def get(value, key, default=None):
    return value.get(key, default) if isinstance(value, dict) else getattr(value, key, default)


def number(value):
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def usage_record(response):
    if get(response, 'is_chunk', False):
        return None
    raw = get(response, 'raw_completion', response)
    usage = get(raw, 'usage')
    hit, miss = number(get(usage, 'prompt_cache_hit_tokens')), number(get(usage, 'prompt_cache_miss_tokens'))
    source = 'deepseek_raw'
    total = number(get(usage, 'prompt_tokens'))
    if hit is None or miss is None:
        hit = number(get(get(usage, 'prompt_tokens_details'), 'cached_tokens'))
        if hit is None:
            hit = number(get(usage, 'cached_tokens'))
        miss = total - hit if hit is not None and total is not None and total >= hit else None
        source = 'openai_raw'
    output = number(get(usage, 'completion_tokens'))
    if hit is None or miss is None:
        normalized = get(response, 'usage')
        hit, miss = number(get(normalized, 'input_cached')), number(get(normalized, 'input_other'))
        output = number(get(normalized, 'output'))
        source = 'astrbot_normalized'
    if hit is None or miss is None or hit + miss == 0:
        return None
    return {'input_tokens': hit + miss, 'cache_hit_tokens': hit, 'cache_miss_tokens': miss,
            'output_tokens': output, 'usage_source': source}


class NumericStore:
    def __init__(self, directory, limit=4 * 1024 * 1024):
        self.directory = pathlib.Path(directory)
        self.limit = limit
        self.lock = threading.Lock()
        self.counts = Counter()
        self.prefix_states = Counter()
        self.storage_failures = 0
        # StarTools may create this plugin's directory with inherited permissions.
        # Restrict that dedicated directory on POSIX; never change shared parents.
        if os.name == 'posix':
            try:
                if self.directory.is_dir():
                    self.directory.chmod(0o700)
            except OSError:
                self.storage_failures += 1
        try:
            saved = json.loads((self.directory / 'summary.json').read_text())
            if isinstance(saved, dict):
                counts, prefixes = saved.get('counts', {}), saved.get('prefix_states', {})
                if isinstance(counts, dict):
                    self.counts.update({k: v for k, v in counts.items() if number(v) is not None})
                if isinstance(prefixes, dict):
                    self.prefix_states.update({k: v for k, v in prefixes.items() if number(v) is not None})
        except (OSError, ValueError, TypeError):
            pass

    def snapshot(self):
        with self.lock:
            return self._snapshot()

    def _snapshot(self):
        total = self.counts['input_tokens']
        return {'counts': dict(self.counts), 'prefix_states': dict(self.prefix_states),
                'cache_hit_rate': self.counts['cache_hit_tokens'] / total if total else None,
                'storage_failures': self.storage_failures,
                'scope': 'main-agent final-response hook; not every tool request or auxiliary provider call'}

    def record(self, kind, values):
        allowed = {'background_reordered','memory_reordered','recognized_background','tools_reordered',
                   'context_messages','system_chars','shared_context_messages','shared_serialized_bytes',
                   'input_tokens','cache_hit_tokens','cache_miss_tokens','output_tokens',
                   'prefix_state','usage_source'}
        row = {k: v for k, v in values.items() if k in allowed and
               (number(v) is not None or v is None or
                k == 'prefix_state' and v in {'cold','system_changed','tools_changed','history_append','history_stable','history_changed'} or
                k == 'usage_source' and v in {'deepseek_raw','openai_raw','astrbot_normalized'})}
        row['kind'] = kind if kind in {'layout','response','response_without_usage','error'} else 'error'
        row['time_utc'] = datetime.datetime.now(datetime.timezone.utc).isoformat()
        with self.lock:
            self.counts[row['kind'] + '_count'] += 1
            if row['kind'] == 'layout':
                for k in ['background_reordered','memory_reordered','tools_reordered']:
                    self.counts[k] += row.get(k, 0)
                self.prefix_states[row.get('prefix_state','cold')] += 1
            if row['kind'] == 'response':
                for k in ['input_tokens','cache_hit_tokens','cache_miss_tokens','output_tokens']:
                    self.counts[k] += row.get(k) or 0
            try:
                self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
                path = self.directory/'metrics.jsonl'
                if path.exists() and path.stat().st_size >= self.limit:
                    path.replace(self.directory/'metrics.jsonl.1')
                fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
                try:
                    os.write(fd, (json.dumps(row, ensure_ascii=True) + '\n').encode())
                finally:
                    os.close(fd)
                tmp = self.directory/'summary.tmp'
                fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                with os.fdopen(fd, 'w', encoding='utf-8') as f:
                    json.dump(self._snapshot(), f)
                tmp.replace(self.directory/'summary.json')
            except OSError:
                self.storage_failures += 1
