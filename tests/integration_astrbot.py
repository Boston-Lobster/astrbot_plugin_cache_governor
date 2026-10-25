"""Run only under the installed AstrBot interpreter, with synthetic data."""
import asyncio
import copy
import json
import pathlib
import sys
import types

ROOT=pathlib.Path(__file__).resolve().parents[1]
import importlib.util
spec = importlib.util.spec_from_file_location('astrbot_plugin_cache_governor', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from astrbot.core.provider.entities import ProviderRequest, LLMResponse, TokenUsage
from astrbot.core.agent.message import bind_checkpoint_messages
from astrbot.core.agent.tool import FunctionTool, ToolSet
from astrbot_plugin_cache_governor.engine import normalize_contexts
from astrbot_plugin_cache_governor.main import CacheGovernor
from astrbot_plugin_cache_governor.metrics import NumericStore
from astrbot.api.star import StarTools


class FakeContext:
    def __init__(self):self.registered_web_apis=[]
    def register_web_api(self,route,handler,methods,desc):
        self.registered_web_apis[:]=[r for r in self.registered_web_apis if r[0]!=route]
        self.registered_web_apis.append((route,handler,methods,desc))
    async def get_current_chat_provider_id(self,umo):return 'synthetic-provider'


class FakeEvent:
    unified_msg_origin='synthetic:group:cache-contract'
    def __init__(self):self.extra={}
    def get_extra(self,key,default=None):return self.extra.get(key,default)
    def set_extra(self,key,value):self.extra[key]=value


def request():
    return ProviderRequest(prompt='本轮合成提问',system_prompt='固定合成人设',
        contexts=[{'role':'user','content':'合成历史'},
        {'role':'assistant','content':None,'tool_calls':[{'id':'synthetic-tool','type':'function','function':{'name':'alpha','arguments':'{}'}}]},
        {'role':'tool','content':'合成工具结果','tool_call_id':'synthetic-tool'},
        {'role':'user','content':'<emotion_context>当前合成情绪</emotion_context>','_no_save':True},
        {'role':'user','content':'<system_context>\n<soul_state>0.50</soul_state>\n\n<memories>合成记忆</memories>\n</system_context>','_no_save':True}],
        func_tool=ToolSet(tools=[FunctionTool(name='zulu',description='z',parameters={'type':'object','properties':{}}),FunctionTool(name='alpha',description='a',parameters={'type':'object','properties':{}})]))


async def main():
    data=ROOT/'test-artifacts'/'integration-data';data.mkdir(parents=True,exist_ok=True)
    original_get_data_dir=StarTools.get_data_dir
    StarTools.get_data_dir=classmethod(lambda cls,*a,**k:data)
    try:
        context=FakeContext();plugin=CacheGovernor(context,{})
        await plugin.initialize()
        e=FakeEvent();req=request();original=copy.deepcopy(req)
        await plugin.normalize_request(e,req)
        assert req.prompt==original.prompt and req.system_prompt==original.system_prompt
        assert req.image_urls==original.image_urls and req.extra_user_content_parts==original.extra_user_content_parts
        assert req.contexts[:3]==original.contexts[:3]
        assert req.func_tool.names()==['alpha','zulu'] and original.func_tool.names()==['zulu','alpha']
        assert req.func_tool.get_tool('alpha') is not None
        runtime=bind_checkpoint_messages(req.contexts)
        saved=[m.model_dump() for m in runtime if not (m.role in ('user','assistant') and m._no_save)]
        assert len(saved)==3 and '合成记忆' not in json.dumps(saved,ensure_ascii=False)
        assert runtime[-1]._no_save and runtime[-2]._no_save
        await plugin.audit_request(e,req)
        await plugin.record_response(e,LLMResponse(role='assistant',completion_text='合成输出',usage=TokenUsage(input_cached=80,input_other=20,output=5)))
        status=await plugin.status()
        assert status['data']['cache_hit_rate']==0.8 and status['data']['counts'].get('error_count',0)==0
        assert status['data']['tracked_sessions']==1
        old_status=plugin.status
        second=CacheGovernor(context,{})
        await second.initialize()
        await plugin.terminate()
        assert len(context.registered_web_apis)==1 and context.registered_web_apis[0][1]==second.status
        await second.terminate()
        assert not context.registered_web_apis
        disabled=CacheGovernor(context,{'enabled':False})
        req=request();before=copy.deepcopy(req)
        await disabled.normalize_request(FakeEvent(),req)
        assert req.contexts==before.contexts and req.func_tool.names()==before.func_tool.names()
        await disabled.terminate()
    finally:StarTools.get_data_dir=original_get_data_dir
    print('INTEGRATION_RESULT '+json.dumps({'astrbot_contract':True,'tools_preserved':True,'history_boundary_preserved':True,'transient_not_saved':True,'reload_cleanup':True,'disabled_identity':True,'qq_messages_sent':0}))


asyncio.run(main())
