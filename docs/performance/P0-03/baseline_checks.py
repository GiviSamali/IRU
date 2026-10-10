import ast,asyncio,json,math,types,re,sys
from pathlib import Path
import pytest
BASE=Path(__file__).parent
REPO=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(REPO))
REPORT=json.loads((BASE/'IRU-P0-03-metrics.json').read_text(encoding='utf-8'))
ROWS={row['scenario']:row for row in REPORT['scenarios']}
EXPECTED={'S1':(3,0),'S2':(3,0),'S3':(4,1),'S4':(3,0),'S5_50':(3,0),'S5_500':(3,0),'S5_5000':(3,0),
'S6_1':(3,0),'S6_5':(3,0),'S6_20':(3,0),'S7':(10,3),'S8_short':(3,0),'S8_long':(4,0),
'S9_correction':(4,0),'S9_repair':(23,0),'S9_recovery':(5,2),'S9_tool_correction':(5,1),'S9_auditor_correction':(5,0),
'S10_100x100':(3,0),'S10_1000x100':(3,0),'S10_1000x2000':(3,0)}
@pytest.mark.parametrize('label',sorted(EXPECTED))
def test_fixture_flow_and_numeric_sizes(label):
    row=ROWS[label];calls,tools=EXPECTED[label]
    assert row['samples']==30
    assert row['llm_call_count_per_request']==calls
    assert row['tool_call_count_per_request']==tools
    assert row['outcomes']=={'done':30}
    assert row['estimated_cost_usd'] is None
    for call in row['calls_first_sample']:
        assert sum(call['context'].values())==call['serialized_input_bytes']
        assert call['serialized_input_bytes']>=call['serialized_input_chars']>0
        assert call['provider_usage']=='unknown'
        assert call['prompt_tokens'] is call['completion_tokens'] is call['cached_tokens'] is None
        assert call['ttft_ms']=='not_measured'
        assert call['route']!='unknown'
    for latency in row['latency'].values():
        assert latency['n']==30 and latency['p95_ms'] is not None
        assert latency['p95_ms']>=latency['p50_ms']>=0

def test_head_and_isolation():
    assert REPORT['head']=='a55512b2e21a6448689bbdeda4d35ff6b4136589'
    assert REPORT['source_changed'] is False
    assert REPORT['environment']['auditor']=='enabled'
    assert REPORT['environment']['network']=='httpx.MockTransport only'

def test_history_bound():
    for label in ('S5_50','S5_500','S5_5000'):
        row=ROWS[label]
        main=next(c for c in row['calls_first_sample'] if c['category']=='main')
        assert main['message_count']==51 # system + 49 prior turns + current user turn
        assert row['operations_by_stage_first_sample']['runtime']['get_messages']==1

def test_profile_growth_and_duplicate_manifest():
    for n in (1,5,20):
        operations=ROWS[f'S6_{n}']['operations_by_stage_first_sample']['runtime']
        assert operations['get_device_profile']==2*n
        assert operations['build_minimal_llm_context']==2

def test_plan_handoff_and_no_final_call_on_happy_path():
    row=ROWS['S7']
    assert row['components_first_sample_sum_bytes']['handoff_bytes']>0
    assert row['operations_by_stage_first_sample']['runtime']['build_minimal_llm_context']==6
    assert row['operations_by_stage_first_sample']['runtime']['get_memory_stats']==5
    assert not any(c['phase']=='pipeline.final' for c in row['calls_first_sample'])

def test_poll_reads_full_facts_twice():
    row=ROWS['S10_1000x2000']
    assert row['fact_rows_by_stage_first_sample']=={'runtime':1000,'poll':10000}
    assert row['operations_by_stage_first_sample']['poll']['get_memory_stats']==10
    assert len(row['poll_response_bytes_first_sample'])==5
    assert min(row['poll_response_bytes_first_sample'])>2_000_000

def test_memory_bound_and_large_fact_omission():
    for label in ('S10_100x100','S10_1000x100','S10_1000x2000'):
        assert ROWS[label]['builder_peak_output_chars_first_sample']['build_memory_block']<=2048
    assert ROWS['S10_1000x2000']['builder_peak_output_chars_first_sample']['build_memory_block']<300

def test_voice_extra_call_and_cache():
    assert ROWS['S8_short']['calls_per_request']['other']==0
    row=ROWS['S8_long']
    assert sum(c['phase']=='voice_brief' for c in row['calls_first_sample'])==1
    assert row['operations_by_stage_first_sample']['post_result']['add_llm_usage_event']==1

def test_numeric_export_privacy():
    text=json.dumps(REPORT,ensure_ascii=False)
    for marker in ('FIXTURE_FACT_','SYNTHETIC_HISTORY_','Fixture raw content','C:\\Users\\Fixture','api_key','Authorization','stdout','stderr','facts_list'):
        assert marker not in text

def proposed_source(name):
    """Reconstruct a preview in memory; never apply the patch to the checkout."""
    lines=(BASE/'IRU-P0-03-proposed-instrumentation.diff').read_text(encoding='utf-8').splitlines(keepends=True)
    begin=next(i for i,line in enumerate(lines) if line.startswith('--- a/'+name+'\n'))
    end=next((i for i in range(begin+1,len(lines)) if lines[i].startswith('--- a/')),len(lines))
    original=(REPO/name).read_text(encoding='utf-8').splitlines(keepends=True)
    output=[];cursor=0;active=False
    for line in lines[begin+2:end]:
        if line.startswith('@@ '):
            start=int(re.match(r'@@ -(\d+)',line).group(1))-1
            output.extend(original[cursor:start]);cursor=start;active=True
        elif active and line.startswith(' '):
            assert original[cursor]==line[1:];output.append(line[1:]);cursor+=1
        elif active and line.startswith('-'):
            assert original[cursor]==line[1:];cursor+=1
        elif active and line.startswith('+'):
            output.append(line[1:])
    output.extend(original[cursor:])
    return ''.join(output)

def preview_usage():
    mod=types.ModuleType('server.llm_usage_preview');mod.__package__='server'
    source=proposed_source('server/llm_usage.py')
    exec(compile(source,'llm_usage_preview','exec'),mod.__dict__)
    return mod

def test_proposal_default_disabled(monkeypatch):
    monkeypatch.delenv('IRU_PERFORMANCE_BASELINE',raising=False)
    mod=preview_usage();ctx={'route':'fixture'}
    assert mod.performance_context(ctx,{}) is ctx

def test_proposal_numeric_metadata_and_unknown_usage(monkeypatch):
    monkeypatch.setenv('IRU_PERFORMANCE_BASELINE','1');mod=preview_usage()
    payload={'messages':[{'role':'user','content':'secret_fixture'}],'tools':[]}
    ctx=mod.performance_context({'metadata':{'existing':1}},payload)
    mod.performance_usage(ctx,{})
    metadata=mod.performance_metadata(ctx,{'existing':1})
    assert metadata['existing']==1
    fields=metadata['performance'];assert fields['prompt_tokens_known']==0
    assert 'secret_fixture' not in json.dumps(metadata)
    assert all(type(v) in (int,float) for v in fields.values())
    mod.performance_usage(ctx,{'usage':{'prompt_tokens':0}})
    assert mod.performance_metadata(ctx,{})['performance']['prompt_tokens_known']==1

def test_proposal_retry_counter_and_exception_preserved(monkeypatch):
    monkeypatch.setenv('IRU_PERFORMANCE_BASELINE','1');mod=preview_usage();ctx=mod.performance_context({}, {})
    class Fake:
        async def post(self,*args,**kwargs):raise ValueError('fixture')
    for _ in range(2):
        with pytest.raises(ValueError,match='fixture'):asyncio.run(mod.performance_post(Fake(),ctx,'https://fixture.invalid',json={}))
    assert mod.performance_metadata(ctx,{})['performance']['http_attempt_count']==2

def test_proposal_classifier_payload_identical():
    before=ast.parse((REPO/'server/controller.py').read_text(encoding='utf-8'))
    after=ast.parse(proposed_source('server/controller.py'))
    fn=next(n for n in before.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='classify_task_complexity')
    post=next(n for n in ast.walk(fn) if isinstance(n,ast.Call) and isinstance(n.func,ast.Attribute) and n.func.attr=='post')
    old=next(k.value for k in post.keywords if k.arg=='json')
    fn2=next(n for n in after.body if isinstance(n,ast.AsyncFunctionDef) and n.name=='classify_task_complexity')
    new=next(n.value for n in ast.walk(fn2) if isinstance(n,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='classification_json' for t in n.targets))
    assert ast.dump(old)==ast.dump(new)