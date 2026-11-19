import io
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

from advisor import claude_code as cc
from advisor.storage import connect, events, setting


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv('TASK_ADVISOR_HOME',str(tmp_path/'state'))
    return tmp_path


def invoke(monkeypatch, data, kind='user-prompt'):
    output=io.StringIO()
    monkeypatch.setattr(sys,'stdin',io.StringIO(json.dumps(data)))
    monkeypatch.setattr(sys,'stdout',output)
    assert cc.hook_main(kind,worker=True)==0
    return output.getvalue()


def test_prompt_privacy_and_rating(home,monkeypatch):
    data={'session_id':'s1','prompt':'SECRET_CANARY_3 write a short greeting','model':{'id':'claude-opus-4-6'}}
    output=json.loads(invoke(monkeypatch,data))
    assert 'you are on opus' in output['systemMessage']
    assert 'additionalContext' not in json.dumps(output) and 'SECRET_CANARY' not in json.dumps(output)
    with connect() as db:
        state=setting(db,cc.session_key(data))
        assert state['pending']['model']=='opus'
    data['transcript_path']=str(home/'transcript.jsonl')
    Path(data['transcript_path']).write_text('')
    invoke(monkeypatch,data,'stop')
    data['prompt']='-vw'
    output=json.loads(invoke(monkeypatch,data))
    assert output['decision']=='block' and output['systemMessage']=='rated'
    assert events()[0]['rating']==0 and events()[0]['tags']==['v','w']
    data['prompt']='/rate +o'
    assert json.loads(invoke(monkeypatch,data))['decision']=='block'
    assert events()[0]['rating']==1 and events()[0]['tags']==['o']
    for path in (home/'state').rglob('*'):
        if path.is_file(): assert b'SECRET_CANARY' not in path.read_bytes()


@pytest.mark.parametrize('text',['+','-vw',' /rate +o ','/rate -c'])
def test_rating_syntax(text):
    assert cc.rating_only(text) is not None


@pytest.mark.parametrize('text',['+ do this','-wrong','/rate','/rate a','++','hello'])
def test_nonrating_syntax(text):
    assert cc.rating_only(text) is None


def test_session_isolation_and_status(home,monkeypatch):
    data={'session_id':'a','prompt':'hello'}
    invoke(monkeypatch,data)
    out=invoke(monkeypatch,{'session_id':'a','model':{'id':'claude-sonnet-4-6'},'effort':{'level':'low'}},'statusline')
    assert 'on sonnet' in out
    out=json.loads(invoke(monkeypatch,{'session_id':'b','prompt':'+'}))
    assert out['decision']=='block' and events()==[]
    out=json.loads(invoke(monkeypatch,data))
    assert 'on sonnet' in out['systemMessage']


def test_stop_fixture_turn_boundaries(home,monkeypatch):
    fixture=Path(__file__).with_name('fixtures')/'claude-hook.jsonl'
    transcript=home/'transcript.jsonl'
    transcript.write_text('{"type":"assistant","message":{"usage":{"input_tokens":99999}}}\n')
    data={'session_id':'s','prompt':'hello','transcript_path':str(transcript)}
    invoke(monkeypatch,data)
    with transcript.open('a') as stream: stream.write(fixture.read_text())
    invoke(monkeypatch,data,'stop')
    row=events()[0]
    assert row['model']=='sonnet'
    for key,value in dict(input_tokens=100,output_tokens=30,cached_tokens=40,cache_creation_tokens=20,tool_calls=1,tool_failures=1,latency_ms=2000).items():
        assert row['indicators'][key]==value
    invoke(monkeypatch,data,'stop')
    assert len(events())==1


def test_installer_merge_idempotence_uninstall(home,capsys):
    path=home/'settings.json'
    original={'model':'opus','hooks':{'Stop':[{'matcher':'','hooks':[{'type':'command','command':'echo keep'}]}]},'statusLine':{'type':'command','command':'echo status'}}
    path.write_text(json.dumps(original))
    command="'C:/a b/advisor.exe'"
    proposed=cc.settings_change('install',path=path,command=command,dry_run=True,status=True)
    assert json.loads(path.read_text())==original and not list(home.glob('*.advisor-bak-*'))
    assert '+ ' in capsys.readouterr().out
    cc.settings_change('install',path=path,command=command,status=True)
    assert json.loads(path.read_text())==proposed
    assert proposed['hooks']['Stop'][0]==original['hooks']['Stop'][0]
    assert proposed['statusLine']==original['statusLine']
    before=path.read_bytes()
    cc.settings_change('install',path=path,command=command,status=True)
    assert path.read_bytes()==before and len(list(home.glob('*.advisor-bak-*')))==1
    cc.settings_change('uninstall',path=path,command=command)
    result=json.loads(path.read_text())
    assert result['hooks']['Stop']==original['hooks']['Stop']
    assert not result['hooks']['UserPromptSubmit']
    assert result['statusLine']==original['statusLine']


def test_optional_statusline(home):
    path=home/'settings.json'
    cc.settings_change('install',path=path,command='/bin/advisor',status=True)
    assert json.loads(path.read_text())['statusLine']['command']=='/bin/advisor statusline'
    cc.settings_change('uninstall',path=path,command='/bin/advisor')
    assert 'statusLine' not in json.loads(path.read_text())


@pytest.mark.parametrize('kind',['user-prompt','stop','statusline'])
def test_errors_silent(home,monkeypatch,kind):
    assert invoke(monkeypatch,{},kind)==''
    monkeypatch.setattr(sys,'stdin',io.StringIO('bad json'))
    output=io.StringIO();monkeypatch.setattr(sys,'stdout',output)
    assert cc.hook_main(kind,worker=True)==0 and output.getvalue()==''


def test_real_stdin_and_deadline(home):
    start=time.perf_counter()
    proc=subprocess.run([sys.executable,'-m','advisor','hook','user-prompt'],input=json.dumps({'session_id':'timed','prompt':'hello'}),text=True,capture_output=True)
    assert proc.returncode==0 and proc.stderr==''
    # The watchdog may intentionally suppress output on a slow cold start.
    if proc.stdout: assert json.loads(proc.stdout)['systemMessage'].startswith('advisor:')
    assert time.perf_counter()-start < .3


def test_threshold_default_and_reset_independence(home):
    from advisor.features import featurize
    from advisor.policy import choose
    from advisor.config import DEFAULTS
    from advisor.quota import scarcity
    f=featurize('hello',fast=True)
    f['base_predictions']={'cheap':.9,'mid':.99,'frontier':.999}
    kwargs=dict(exploration=0,weights={},baseline={},rows=[])
    pick=choose(f,'claude',**kwargs)
    assert (pick['id'],pick['effort'])==('haiku','low')
    assert choose(f,'claude',objective='ev',**kwargs)['tier']!='cheap'
    assert all(scarcity(u,0,300)==scarcity(u,300,300) for u in range(101))
    assert all(scarcity(u,1,300)<=scarcity(u+1,1,300) for u in range(100))

def test_usage_bias_latency_and_effort_ties(home,monkeypatch):
    from advisor.features import featurize
    from advisor.policy import choose
    from advisor.config import DEFAULTS
    from advisor.catalog import options
    import advisor.policy as policy
    import advisor.quota as quota
    f=featurize('hello',fast=True)
    f['base_predictions']={'cheap':.99,'mid':.99,'frontier':.99}
    monkeypatch.setattr(policy,'options',lambda _:options('claude')[:3])
    monkeypatch.setattr(policy,'estimate',lambda o,f:1.)
    monkeypatch.setattr(quota,'price',lambda o,*args:dict(cost=1.,api_cost=1.,quota_impact=0.,latency={'low':2,'medium':1,'high':1}[o['effort']]))
    kwargs=dict(weights={},baseline={},rows=[])
    assert choose(f,'claude',cfg=DEFAULTS,**kwargs)['effort']=='medium'
    assert choose(f,'claude',cfg={**DEFAULTS,'usage_bias':'neutral'},**kwargs)['effort']=='high'
    monkeypatch.setattr(quota,'price',lambda o,*args:dict(cost=1.,api_cost=1.,quota_impact=0.,latency=1.))
    assert choose(f,'claude',cfg=DEFAULTS,**kwargs)['effort']=='low'
