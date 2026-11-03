"""Native object observations prove paths; WorkerReport does not judge Assignments.

Ordinary opening needs OS acceptance only. These tests concern the separate,
explicit observation contract, including shell/unsafe-object rejection.
"""
import pytest
from server.tool_completion import object_open_proven, confirmed_window_effect

DOC = 'C:/Desktop/Presentation.pptx'


def observed(path=DOC, **fields):
    return {'observed_path':path,'kind':'file','title':'Presentation - Office',
        'process':'office.exe','class_name':'PPTFrameClass',**fields}


def proven(path=DOC, observation=None, *, kind='file', verified=True):
    return object_open_proven({'type':kind,'path':path}, observation,
        {'object_open_verified':verified})


def test_exact_native_observation_proves_loading():
    assert proven(observation=observed())


@pytest.mark.parametrize('path',['C:/Other/Presentation.pptx','C:/Desktop/Presentation2.pptx',
    'C:/Desktop/Other.docx',''])
def test_window_title_does_not_replace_exact_observed_path(path):
    assert not proven(observation=observed(path))


@pytest.mark.parametrize('fields',[{'class_name':'ThumbnailDeviceHelperWnd'},
    {'class_name':'Progman'},{'title':'Program Manager'},{'title':''},{'process':'explorer.exe'}])
def test_helper_or_shell_window_is_not_a_loaded_document(fields):
    assert not proven(observation=observed(**fields))


def test_without_native_verification_fields_no_loading_claim_is_proven():
    assert not proven(observation=None)
    assert not proven(observation=observed(),verified=False)


@pytest.mark.parametrize('suffix',['.py','.pyw','.lnk','.hta','.vbs','.js','.reg','.exe','.ps1'])
def test_declared_file_type_does_not_disguise_code(suffix):
    path='C:/Desktop/object'+suffix
    assert not proven(path,observed(path))


def test_folder_observation_requires_its_own_explorer_window():
    path='C:/Desktop/Work'
    result=observed(path,kind='folder',process='explorer.exe',class_name='CabinetWClass',hwnd='123')
    assert proven(path,result,kind='folder')
    assert not proven(path,{**result,'hwnd':''},kind='folder')
    assert not proven(path,{**result,'observed_path':'C:/Desktop/Other'},kind='folder')


def test_os_acceptance_is_not_a_native_loading_observation():
    receipt={'status':'success','os_open_accepted':True,'launch_state':'accepted',
        'requested_object':{'type':'file','path':DOC},'object_open_verified':False,'observed_object':None}
    assert confirmed_window_effect({'tool_name':'app.launch','result':receipt})=='open'
    assert not object_open_proven(receipt['requested_object'],None,receipt)
