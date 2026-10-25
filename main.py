from __future__ import annotations

import asyncio
import hashlib
import json

from astrbot.api import AstrBotConfig, logger
from astrbot.api.event import AstrMessageEvent, filter
from astrbot.api.provider import LLMResponse, ProviderRequest
from astrbot.api.star import Context, Star, StarTools, register

from .engine import PrefixTracker, normalize_contexts, stable_toolset
from .metrics import NumericStore, usage_record

NAME = 'astrbot_plugin_cache_governor'


@register(NAME, 'Boston-Lobster', '缓存规范器：稳定请求布局并审计真实缓存用量', '0.1.0')
class CacheGovernor(Star):
    def __init__(self, context: Context, config: AstrBotConfig):
        super().__init__(context)
        self.context = context
        self.config = config
        self.tracker = PrefixTracker(config.get('max_tracked_sessions', 128), config.get('fingerprint_ttl_seconds', 1800))
        self.metrics = NumericStore(StarTools.get_data_dir(NAME))
        self._active = True

    async def initialize(self):
        self.context.register_web_api('/' + NAME + '/status', self.status, ['GET'], '缓存规范器数值统计（无正文）')
        logger.info('缓存规范器 v0.1.0 已加载：规范优先级 -500，审计优先级 -1000000；保留上游历史边界。')

    async def status(self, *args, **kwargs):
        return {'status': 'ok', 'data': {'version': '0.1.0', 'active': self._active,
                'enabled': bool(self.config.get('enabled', True)),
                'tracked_sessions': len(self.tracker.states), **self.metrics.snapshot()}}

    @filter.on_llm_request(priority=-500)
    async def normalize_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._active or not self.config.get('enabled', True):
            return
        # The injection defense runs at -1000 and signs/checks the final layout.
        # All modifications are prepared first, then assigned without awaiting.
        try:
            contexts, counts = normalize_contexts(req.contexts,
                order_background=bool(self.config.get('normalize_background_order', True)),
                memory_sections=bool(self.config.get('normalize_memory_sections', True)))
            tools, changed = (stable_toolset(req.func_tool) if self.config.get('stable_tool_order', True) else (req.func_tool, False))
            counts['tools_reordered'] = int(changed)
            event.set_extra('_cache_governor_layout', counts)
            req.contexts, req.func_tool = contexts, tools
        except Exception:
            # No exception text: provider/request exceptions may contain user data.
            event.set_extra('_cache_governor_layout', {})
            if self.config.get('metrics_enabled', True):
                await asyncio.to_thread(self.metrics.record, 'error', {})

    @filter.on_llm_request(priority=-1000000)
    async def audit_request(self, event: AstrMessageEvent, req: ProviderRequest):
        if not self._active or not self.config.get('enabled', True) or not self.config.get('metrics_enabled', True):
            return
        try:
            conversation = getattr(req, 'conversation', None)
            provider_id = await self.context.get_current_chat_provider_id(event.unified_msg_origin)
            scope = hashlib.sha256(json.dumps([event.unified_msg_origin, getattr(conversation, 'cid', None),
                getattr(conversation, 'persona_id', None), provider_id, req.model], ensure_ascii=False).encode()).hexdigest()
            tools = req.func_tool.openai_schema() if req.func_tool else []
            contexts = req.contexts if isinstance(req.contexts, list) else []
            values = self.tracker.observe(scope, req.system_prompt or '', tools, contexts)
            values.update(event.get_extra('_cache_governor_layout', {}) or {})
            values.update(context_messages=len(contexts), system_chars=len(req.system_prompt or ''))
            await asyncio.to_thread(self.metrics.record, 'layout', values)
        except Exception:
            await asyncio.to_thread(self.metrics.record, 'error', {})

    @filter.on_llm_response(priority=-1000000)
    async def record_response(self, event: AstrMessageEvent, resp: LLMResponse):
        if not self._active or not self.config.get('enabled', True) or not self.config.get('metrics_enabled', True):
            return
        try:
            row = usage_record(resp)
            await asyncio.to_thread(self.metrics.record, 'response' if row else 'response_without_usage', row or {})
        except Exception:
            pass

    async def terminate(self):
        self._active = False
        self.tracker.clear()
        # Remove only this instance's registered handler; never unregister a newer reload.
        self.context.registered_web_apis[:] = [item for item in self.context.registered_web_apis
            if not (item[0] == '/' + NAME + '/status' and item[1] == self.status)]
