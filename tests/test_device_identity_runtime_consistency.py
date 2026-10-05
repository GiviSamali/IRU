"""Typed identity and verified runtime freshness regressions, no state repair hacks."""
import json
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
import pytest
from server import task_runtime as runtime
from server.python_runtime import current_runtime_summary, compact_python_runtime_summary
from server.device_context import build_minimal_llm_context
from server.routers.devices import _device_api_item
from server.tool_registry import compact_device_passport
from test_python_runtime import _valid_runtime_receipt

AGENT_DIR=Path(__file__).resolve().parents[1]/'agent'
if str(AGENT_DIR) not in sys.path:sys.path.insert(0,str(AGENT_DIR))


def registered():
    return {'user_id':7,'ws':object(),'info':{'hostname':'GIVI','os':'Windows','machine_guid':'registry-id','machine_guid_type':'windows_machine_guid'}}


@pytest.mark.parametrize('side',['agent','server'])
def test_same_machine_uses_registry_identity_and_keeps_smbios_separate(monkeypatch,side):
    from core import actions
    info=registered()['info']
    snapshot={'observed_hostname':'GIVI','observed_machine_guid':'REGISTRY-ID','machine_guid_type':'windows_machine_guid','system_uuid':'different-smbios-id'}
    if side=='server':value=runtime._build_identity_receipt(device_id='7:givi',dev=registered(),observed=snapshot)
    else:
        monkeypatch.setattr(actions,'collect_system_info',lambda **kw:info)
        value=actions._identity_receipt('givi',snapshot,'now')
    assert value['identity_status']=='ok'
    assert value['observed_machine_guid']=='REGISTRY-ID'
    assert value['observed_system_uuid']=='different-smbios-id'


@pytest.mark.parametrize('side',['agent','server'])
def test_different_typed_guid_still_mismatches_even_with_same_hostname(monkeypatch,side):
    from core import actions
    snapshot={'observed_hostname':'GIVI','observed_machine_guid':'foreign-id','machine_guid_type':'windows_machine_guid'}
    if side=='server':value=runtime._build_identity_receipt(device_id='7:givi',dev=registered(),observed=snapshot)
    else:
        monkeypatch.setattr(actions,'collect_system_info',lambda **kw:registered()['info'])
        value=actions._identity_receipt('givi',snapshot,'now')
    assert value['identity_status']=='mismatch'


def test_different_identifier_types_are_not_compared_as_machine_guid():
    snapshot={'observed_hostname':'GIVI','observed_machine_guid':'smbios-id','machine_guid_type':'smbios_uuid'}
    assert runtime._build_identity_receipt(device_id='7:givi',dev=registered(),observed=snapshot)['identity_status']!='mismatch'


def test_one_matching_hardware_id_cannot_erase_another_mismatch():
    device=registered();device['info'].update(system_uuid='same',bios_serial='registered')
    value=runtime._build_identity_receipt(device_id='7:givi',dev=device,observed={
        'system_uuid':'same','bios_serial':'foreign','observed_hostname':'GIVI'})
    assert value['identity_status']=='mismatch'


def test_missing_identity_stays_unknown_and_uuid_alias_never_becomes_guid():
    value=runtime._build_identity_receipt(device_id='7:givi',dev=registered(),observed={'uuid':'hardware'})
    assert value['identity_status']=='unknown' and value['observed_machine_guid'] is None


def test_windows_snapshot_commands_both_use_registry64_and_typed_smbios(monkeypatch):
    from core import actions
    monkeypatch.setattr(actions.platform,'system',lambda:'Windows')
    for command in (actions._agent_snapshot_command()[0],runtime._snapshot_command_for_device({'os':'Windows'})):
        assert 'Registry64' in command and "GetValue('MachineGuid')" in command
        assert 'observed_machine_guid=$mid' in command and 'system_uuid=$prod.UUID' in command
        assert 'observed_machine_guid=$prod.UUID' not in command


def fresh_receipt():
    receipt=_valid_runtime_receipt();receipt['created_at']=datetime.now(timezone.utc).isoformat()
    return receipt


def test_fresh_runtime_agrees_across_manifest_api_and_passport():
    receipt=fresh_receipt();device=registered();device.update(activation_summary={
        'activation_status':'activated','runtime_status':'missing','python_capability':'missing',
        'capabilities_summary':{'execute_cmd':'available','python':'missing'}},python_runtime_receipt=receipt)
    summary=compact_python_runtime_summary(receipt)
    profile={'python_runtime_summary':{'runtime_status':'missing','last_runtime_check':'2000-01-01T00:00:00Z'}}
    for value in (build_minimal_llm_context('givi',{'givi':device},profile)['current_device'],
                  _device_api_item('givi',device,profile),compact_device_passport('givi',device,profile)):
        assert value['runtime_status']=='ok' and value['runtime_fresh'] is True
        assert value['python_version']==summary['python_version']
        assert value['pip_status']=='ok' and value['venv_python']==summary['venv_python']
        assert 'python' in value['capabilities_summary']
    assert device['activation_summary']['runtime_status']=='missing'  # Historical source is unchanged.


@pytest.mark.parametrize('age',[None,48*3600,-3600])
def test_unverified_stale_or_future_runtime_is_not_current(age):
    receipt=fresh_receipt()
    if age is None:receipt['created_at']='unknown'
    else:receipt['created_at']=(datetime.now(timezone.utc)-timedelta(seconds=age)).isoformat()
    value=current_runtime_summary({'python_runtime_receipt':receipt})
    assert value['runtime_fresh'] is False and value['runtime_status']=='unknown'
    assert value['python_version'] is None and value['venv_python'] is None
    assert value['last_known_runtime_status']=='ok'


def test_newer_verified_failure_wins_over_old_success():
    old=fresh_receipt();old['created_at']=(datetime.now(timezone.utc)-timedelta(minutes=10)).isoformat()
    newer=fresh_receipt();newer['status']='broken';newer['pip']['status']='broken'
    value=current_runtime_summary({'python_runtime_receipt':newer,'python_runtime_summary':compact_python_runtime_summary(old)})
    assert value['runtime_status']=='broken' and value['runtime_fresh'] is True


def test_local_passport_reconciles_runtime_without_rewriting_activation(monkeypatch,tmp_path):
    from core import actions,local_state
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path));monkeypatch.setattr(local_state.platform,'system',lambda:'Windows')
    local_state.write_json_state('device_passport',{'device_id':'givi','activation_summary':{
        'activation_status':'activated','runtime_status':'missing','python_capability':'missing'},'capabilities':{'python':'missing'}})
    activation={'runtime':{'managed_python_status':'missing'}};local_state.write_json_state('activation_receipt',activation)
    receipt=fresh_receipt();actions._save_runtime_receipt(tmp_path/'IRU',receipt)
    value=actions.get_cached_passport('givi')['passport']
    assert value['activation_summary']['runtime_status']=='ok'
    assert value['capabilities']['python']=='available' and value['runtime_fresh'] is True
    assert value['runtime_summary']['python_version']==receipt['python']['venv_version']
    assert local_state.read_json_state('activation_receipt')==activation
    receipt['created_at']='2000-01-01T00:00:00Z';local_state.write_json_state('runtime_receipt',receipt)
    # The newer verified cache still wins over the now older full receipt.
    assert local_state.load_device_passport_cache()['runtime_fresh'] is True
    cached=local_state.read_json_state('device_passport')
    cached['runtime_summary']['last_runtime_check']='2000-01-01T00:00:00Z'
    local_state.write_json_state('device_passport',cached)
    stale=local_state.load_device_passport_cache()
    assert stale['runtime_fresh'] is False and stale['capabilities']['python']=='unknown'


def test_activation_checks_system_backed_venv_not_only_managed_python_folder(monkeypatch,tmp_path):
    from core import actions
    receipt=fresh_receipt();calls=[]
    def check(**kw):calls.append(kw);return receipt
    monkeypatch.setattr(actions,'prepare_runtime',check)
    result=actions._runtime_receipt(tmp_path,'givi')
    assert not (tmp_path/'runtime/python/python.exe').exists()
    assert result['managed_python_status']=='ok' and result['pip_status']=='ok'
    assert result['venv_python']==receipt['paths']['venv_python']
    assert calls==[{'mode':'check','device_id':'givi'}]


def test_runtime_summary_cannot_seed_a_current_toolchain_when_stale():
    from server.python_toolchain import python_toolchain_from_runtime_summary
    receipt=fresh_receipt();receipt['created_at']='2000-01-01T00:00:00Z'
    assert python_toolchain_from_runtime_summary(compact_python_runtime_summary(receipt),device_id='givi',user_id=7) is None


def test_old_toolchain_cache_expires_without_refreshing_verification_time():
    from server import python_toolchain as toolchain
    receipt=toolchain.PythonToolchainReceipt(device_id='expired-test',status='ok',interpreter_path=r'C:\Python\python.exe',version='3.13.7',confidence=1)
    toolchain.remember_python_toolchain(receipt,user_id=7,verified_at=datetime.now(timezone.utc)-timedelta(days=2))
    assert toolchain.get_cached_python_toolchain({'user_id':7,'device_id':'expired-test'}) is None


def test_activation_summary_keeps_actual_receipt_time():
    from server.device_activation import compact_activation_summary
    receipt={'activation_version':1,'device_id':'givi','activation_status':'ok',
        'identity':{'hostname':'GIVI'},'paths':{'iru_home':'test'},'runtime':{},'capabilities':{},'created_at':'2000-01-01T00:00:00Z'}
    assert compact_activation_summary(receipt)['last_activation_check']==receipt['created_at']


def test_agent_log_formatter_does_not_dump_commands_paths_or_literal_payloads():
    from core.runtime import AgentRuntime
    agent=object.__new__(AgentRuntime)
    for action in ('execute_cmd','write_content','get_file_content','window.control'):
        formatted=agent._format_params_for_log(action,{'command':'PRIVATE_COMMAND TOKEN','content':'PRIVATE_CONTENT','path':'PRIVATE_FILE','title_contains':'PRIVATE_TITLE'})
        assert 'PRIVATE' not in formatted and 'TOKEN' not in formatted
