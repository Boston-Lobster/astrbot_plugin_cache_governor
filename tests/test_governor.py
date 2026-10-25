import copy
import importlib.util
import json
import pathlib
import sys
import tempfile
import types
import unittest
import uuid
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor

ROOT = pathlib.Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('astrbot_plugin_cache_governor', ROOT/'__init__.py', submodule_search_locations=[str(ROOT)])
package = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = package
spec.loader.exec_module(package)
from astrbot_plugin_cache_governor.engine import (
    PrefixTracker, background_kind, normalize_contexts, normalize_memory_text, stable_toolset,
)
from astrbot_plugin_cache_governor.metrics import NumericStore, usage_record


@contextmanager
def test_directory():
    # Ordinary inherited Windows ACLs also work inside the restricted executor.
    # Leave bounded synthetic artifacts in the project for verification.
    directory = ROOT/'test-artifacts'/('case-'+uuid.uuid4().hex)
    directory.mkdir(parents=True)
    yield directory


def memory(soul='0.50', memories='合成记忆', notes='合成笔记'):
    return {'role':'user', 'content':f'<system_context>\n<soul_state>{soul}</soul_state>\n\n<user_profiles>合成画像</user_profiles>\n\n<memories>{memories}</memories>\n\n<notes>{notes}</notes>\n</system_context>', '_no_save':True}


def emotion(value='0.5'):
    return {'role':'user','content':f'<emotion_context>{value}</emotion_context>','_no_save':True}


class LayoutTests(unittest.TestCase):
    def test_memory_payload_and_outer_format_preserved(self):
        original = memory()['content']
        actual, changed = normalize_memory_text(original)
        self.assertTrue(changed)
        self.assertEqual(actual, '<system_context>\n<user_profiles>合成画像</user_profiles>\n\n<memories>合成记忆</memories>\n\n<notes>合成笔记</notes>\n\n<soul_state>0.50</soul_state>\n</system_context>')
        self.assertEqual(len(actual), len(original))
        self.assertEqual(normalize_memory_text(actual), (actual,False))

    def test_unknown_or_ambiguous_memory_is_unchanged(self):
        cases = ['<system_context>unknown</system_context>', '<system_context><other>x</other></system_context>',
            '<system_context><soul_state>x</soul_state><soul_state>y</soul_state></system_context>',
            '<system_context><memories>payload</memories>extra</system_context>',
            '<system_context><memories><notes>nested</notes></memories></system_context>',
            '<system_context><memories>text</system_context>', 'prefix'+memory()['content']]
        for case in cases:
            with self.subTest(case=case): self.assertEqual(normalize_memory_text(case), (case,False))

    def test_background_order_improves_shared_prefix_on_realistic_states(self):
        old=[{'role':'user','content':'固定历史'},emotion('0.5'),memory('0.50', '长期记忆'*300)]
        new=[{'role':'user','content':'固定历史'},emotion('0.6'),memory('0.60', '长期记忆'*300)]
        def common(a,b):
            a=json.dumps(a,ensure_ascii=False);b=json.dumps(b,ensure_ascii=False)
            return next((i for i,(x,y) in enumerate(zip(a,b)) if x!=y),min(len(a),len(b)))
        before=common(old,new)
        na,c=normalize_contexts(old);nb,_=normalize_contexts(new)
        self.assertGreater(common(na,nb), before+1000)
        self.assertEqual(c['memory_reordered'],1)
        self.assertEqual(c['background_reordered'],2)
        self.assertEqual(old[1]['content'],emotion('0.5')['content'])

    def test_unknown_durable_and_tool_messages_are_fences(self):
        fence_variants=[{'role':'user','content':'普通历史'}, {'role':'system','content':'真实系统规则'},
            {'role':'tool','tool_call_id':'t1','content':'原始工具结果'},
            {'role':'assistant','tool_calls':[{'id':'t1'}],'content':None},
            {'role':'user','_no_save':True,'content':'其他插件未知背景'},
            {'role':'user','_no_save':True,'content':[{'type':'image_url','image_url':{'url':'synthetic://image'}}]}]
        for fence in fence_variants:
            original=[emotion(),fence,memory()]
            updated,_=normalize_contexts(original)
            self.assertIs(updated[1],fence)
            self.assertEqual(updated[0],original[0])
            self.assertEqual(len(updated),len(original))

    def test_no_current_prompt_system_or_durable_changes(self):
        durable=[{'role':'user','content':'历史1'}, {'role':'assistant','content':'历史2'}]
        contexts=durable+[emotion(),memory()]
        result,_=normalize_contexts(contexts)
        self.assertEqual(result[:2],durable)
        self.assertTrue(all(m['_no_save'] for m in result[2:]))
        self.assertEqual([m for m in contexts if not m.get('_no_save')], [m for m in result if not m.get('_no_save')])

    def test_multimodal_and_non_user_do_not_match(self):
        for m in [dict(emotion(),role='system'),dict(emotion(),_no_save=False),
                  dict(emotion(),content=[{'type':'text','text':'<emotion_context>x</emotion_context>'},{'type':'image_url'}]),
                  dict(emotion(),tool_call_id='1')]:
            self.assertIsNone(background_kind(m))

    def test_empty_disabled_oversize_idempotence(self):
        self.assertEqual(normalize_contexts(None)[0],None)
        source=[emotion(),memory()]
        disabled,_=normalize_contexts(source,order_background=False,memory_sections=False)
        self.assertEqual(disabled,source)
        first,_=normalize_contexts(source)
        second,c=normalize_contexts(first)
        self.assertEqual(first,second)
        self.assertEqual(c['memory_reordered']+c['background_reordered'],0)
        large=[emotion()]*1025
        self.assertIs(normalize_contexts(large)[0],large)

    def test_tools_are_copied_not_global_mutated(self):
        a,b=types.SimpleNamespace(name='a'),types.SimpleNamespace(name='b')
        original=types.SimpleNamespace(tools=[b,a])
        actual,changed=stable_toolset(original)
        self.assertTrue(changed);self.assertEqual(original.tools,[b,a])
        self.assertEqual(actual.tools,[a,b]);self.assertIs(actual.tools[0],a)
        self.assertIs(stable_toolset(actual)[0],actual)
        duplicate=types.SimpleNamespace(tools=[a,a])
        self.assertIs(stable_toolset(duplicate)[0],duplicate)


class TrackerTests(unittest.TestCase):
    def test_append_reset_and_scope(self):
        tracker=PrefixTracker()
        a={'role':'user','content':'history1'};b={'role':'assistant','content':'history2'}
        self.assertEqual(tracker.observe('s','sys',[],[a],now=0)['prefix_state'],'cold')
        self.assertEqual(tracker.observe('s','sys',[],[a,b],now=1)['prefix_state'],'history_append')
        self.assertEqual(tracker.observe('s','sys',[],[b],now=2)['prefix_state'],'history_changed')
        self.assertEqual(tracker.observe('new-conversation','sys',[],[a],now=3)['prefix_state'],'cold')
        self.assertEqual(tracker.observe('s','new-sys',[],[b],now=3)['prefix_state'],'system_changed')
        self.assertEqual(tracker.observe('s','new-sys',['new-tool'],[b],now=4)['prefix_state'],'tools_changed')
        self.assertEqual(tracker.observe('s','new-sys',['new-tool'],[],now=5)['prefix_state'],'history_changed')

    def test_bound_ttl_and_no_plaintext(self):
        tracker=PrefixTracker(capacity=2,ttl=30)
        for i in range(3):tracker.observe(str(i),'secret-system',[],[{'role':'user','content':'secret-user'}],now=i)
        self.assertEqual(len(tracker.states),2)
        self.assertNotIn('secret-user',repr(tracker.states));self.assertNotIn('secret-system',repr(tracker.states))
        tracker.observe('3','x',[],[],now=100)
        self.assertEqual(len(tracker.states),1)
        tracker.clear();self.assertEqual(len(tracker.states),0)


class MetricsTests(unittest.TestCase):
    def test_usage_formats(self):
        responses=[{'raw_completion':{'usage':{'prompt_cache_hit_tokens':80,'prompt_cache_miss_tokens':20,'completion_tokens':5}}},
            {'raw_completion':{'usage':{'prompt_tokens':100,'prompt_tokens_details':{'cached_tokens':80},'completion_tokens':5}}},
            types.SimpleNamespace(usage=types.SimpleNamespace(input_cached=80,input_other=20,output=5))]
        for r in responses:
            row=usage_record(r);self.assertEqual(row['input_tokens'],100);self.assertEqual(row['cache_hit_tokens'],80)

    def test_unknown_zero_chunk_negative_is_not_fake_hit(self):
        for response in [{}, {'is_chunk':True}, {'usage':{'input_cached':0,'input_other':0}},
            {'raw_completion':{'usage':{'prompt_cache_hit_tokens':-5,'prompt_cache_miss_tokens':2}}},
            {'raw_completion':{'usage':{'prompt_tokens':2,'prompt_tokens_details':{'cached_tokens':8}}}}]:
            self.assertIsNone(usage_record(response))

    def test_rotation_persistence_and_privacy(self):
        with test_directory() as d:
            store=NumericStore(d,limit=50)
            store.record('layout',{'prefix_state':'cold','background_reordered':2,'prompt':'DO_NOT_STORE','user_id':'123456789'})
            store.record('response',{'input_tokens':100,'cache_hit_tokens':80,'cache_miss_tokens':20,'output_tokens':5,'usage_source':'deepseek_raw'})
            self.assertTrue((pathlib.Path(d)/'metrics.jsonl.1').exists())
            disk=''.join(p.read_text() for p in pathlib.Path(d).iterdir())
            self.assertNotIn('DO_NOT_STORE',disk);self.assertNotIn('123456789',disk)
            self.assertEqual(NumericStore(d).snapshot()['cache_hit_rate'],0.8)
            (pathlib.Path(d)/'summary.json').write_text('[]')
            self.assertIsNone(NumericStore(d).snapshot()['cache_hit_rate'])

    def test_concurrency_and_io_failure(self):
        with test_directory() as d:
            store=NumericStore(d)
            with ThreadPoolExecutor(max_workers=4) as pool:
                list(pool.map(lambda _:store.record('response',{'input_tokens':10,'cache_hit_tokens':8,'cache_miss_tokens':2}), range(12)))
            self.assertEqual(store.snapshot()['counts']['input_tokens'],120)
            file=pathlib.Path(d)/'not-a-directory';file.write_text('x')
            bad=NumericStore(file);bad.record('layout',{})
            self.assertEqual(bad.snapshot()['storage_failures'],1)


if __name__=='__main__':
    (ROOT/'test-artifacts').mkdir(exist_ok=True)
    unittest.main()
