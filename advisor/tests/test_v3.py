import json
import math
from pathlib import Path
import random
import sqlite3
import sys
import time

import pytest

from advisor.baseline import fit, probability, train_baseline, fit_verification, verification
from advisor.catalog import options
from advisor.chat import chat
from advisor.config import DEFAULTS
from advisor.engines import command, parse_output
from advisor.features import featurize, validate, EXTRA_KEYS
from advisor.labels import parse_rating
from advisor.policy import choose, collection_distribution
from advisor.quota import scarcity, slopes, budget
from advisor.storage import connect, make_event, add, revise, events, pairs, add_pair, setting
from advisor.usage import rate_limits, rollout_usage


@pytest.fixture
def home(tmp_path,monkeypatch):
    home=tmp_path/'state'; monkeypatch.setenv('TASK_ADVISOR_HOME',str(home)); return home


def sample(model='gpt-6-luna', rating=1, propensity=.2):
    row=make_event(featurize('Write a small function'), 'codex', model,'low',{'collection':True},propensity=propensity)
    row['rating']=rating
    return row


def test_collection_distribution_and_guards(home):
    f=featurize('hello','high'); rng=random.Random(7)
    q=collection_distribution(options('codex'),[],f['category'])
    assert sum(q.values())==pytest.approx(1)
    row=sample(); q2=collection_distribution(options('codex'),[row]*50,row['features']['category'])
    assert q2['codex:gpt-6-luna:low']<q['codex:gpt-6-luna:low']
    covered=set()
    for _ in range(60):
        pick=choose(f,'codex',mode='collect',rows=[],weights={},baseline={},rng=rng)
        covered.add(pick['key'])
        assert pick['tier']!='cheap'
        assert sum(pick['distribution'].values())==pytest.approx(1)
        assert pick['propensity']==pytest.approx(pick['distribution'][pick['key']])
    assert len(covered)==9
    forced=choose(f,'codex',mode='collect',override='gpt-6-luna',rows=[],weights={},baseline={})
    assert forced['override'] and forced['id']=='gpt-6-luna' and forced['propensity']==1
    capped=choose(f,'codex',mode='collect',cfg={**DEFAULTS,'daily_spend_cap':0},rows=[],weights={},baseline={})
    assert capped['paused'] and not capped['collection'] and capped['propensity']==1


@pytest.mark.parametrize('engine',['codex','claude'])
def test_shadow_commands_and_pair_storage(home,tmp_path,monkeypatch,engine):
    c=command(engine,options(engine)[0]['id'],'low','some-session',executable='fake',readonly=True)
    assert 'some-session' not in c
    if engine=='codex': assert c[c.index('-s')+1]=='read-only' and '--ignore-user-config' in c
    else:
        assert c[c.index('--tools')+1]=='' and c[c.index('--permission-mode')+1]=='plan'
        assert '--strict-mcp-config' in c and 'disableAllHooks' in ' '.join(c)
    ledger=tmp_path/'args.jsonl';monkeypatch.setenv('FAKE_ENGINE_ARGS',str(ledger))
    lines=iter(['hello','/b','b','/quit']);out=[]
    chat(engine,mode="collect",shadow=True,cwd=tmp_path,input_fn=lambda _:next(lines),output=out.append,executable=[sys.executable,str(Path(__file__).with_name('fake_engine.py'))])
    rows=events();assert len(rows)==2 and rows[1]['indicators']['shadow']
    assert rows[0]['model'] != rows[1]['model']
    with connect() as db: pair=pairs(db)[0]
    assert pair['preference']==0 and pair['propensity']==pytest.approx(rows[0]['propensity']*rows[1]['propensity'])
    assert sum(rows[0]['indicators']['selection_probabilities'].values())==pytest.approx(1.)
    assert any('B [collapsed' in line for line in out)
    argv=[json.loads(x) for x in ledger.read_text().splitlines()]
    assert len(argv)==2
    assert ('-s' if engine=='codex' else '--tools') in argv[1]


def test_high_shadow_guard_and_auto_target(home,tmp_path):
    lines=iter(['/stakes high','hello','+','/quit']);out=[]
    chat('codex',shadow=True,cwd=tmp_path,input_fn=lambda _:next(lines),output=out.append,executable=[sys.executable,str(Path(__file__).with_name('fake_engine.py'))])
    assert len(events())==1 and any('Shadow skipped' in x for x in out)
    lines=iter(['hello','+','/quit']);out=[]
    chat('codex',collect_target=1,cwd=tmp_path,input_fn=lambda _:next(lines),output=out.append,executable=[sys.executable,str(Path(__file__).with_name('fake_engine.py'))])
    assert any('collected 1/1; route mode' in x for x in out)


def test_rollout_account_and_usage(tmp_path):
    identity='12345678-1234-1234-1234-123456789abc'
    fixture=Path(__file__).with_name('fixtures')/'rollout-quota.jsonl'
    (tmp_path/(identity+'.jsonl')).write_bytes(fixture.read_bytes())
    out=rollout_usage(identity,tmp_path)
    a=out['account']
    assert a['plan_type']=='pro' and a['windows']['primary']['quota_used_pct']==42
    assert a['credit_balance']==5.5 and a['has_credits']
    assert out['reasoning_tokens']==20 and out['cached_tokens']==30
    assert rollout_usage('../../auth',tmp_path)=={}
    _,_,parsed=parse_output(fixture.read_text(), 'codex',0,1,options('codex')[0])
    assert parsed['account']['windows']['secondary']['window_minutes']==10080


def test_quota_monotonic_ev_and_slopes(home):
    assert scarcity(90,100,300)>scarcity(50,100,300)>scarcity(10,100,300)
    assert scarcity(90,1,300)==scarcity(90,100,300)
    assert scarcity(100,0,300)==10000 and math.isfinite(scarcity(100,1,300))
    f=featurize('hello','medium'); f['base_predictions']={'cheap':.7,'mid':.8,'frontier':.99}
    cfg={**DEFAULTS,'billing':'api','lambda_lat':0}
    pick=choose(f,'codex',exploration=0,rows=[],weights={},baseline={},cfg=cfg,objective='ev')
    assert pick['tier']=='frontier'
    cfg={**cfg,'stakes':{**cfg['stakes'],'medium':{'value':0,'failure':0}}}
    assert choose(f,'codex',exploration=0,rows=[],weights={},baseline={},cfg=cfg,objective='ev')['id']=='gpt-6-luna'
    rows=[sample(),sample()]
    for i,r in enumerate(rows):
        r['indicators'].update(input_tokens=100,output_tokens=100,account=rate_limits({'primary':{'used_percent':20+i*2,'window_minutes':300,'resets_at':2000000000}}))
    assert slopes(rows)['codex:gpt-6-luna:primary']==pytest.approx(.01)
    rows[1]['indicators']['account']['windows']['primary']['resets_at']+=1
    assert slopes(rows)=={}


def test_ips_and_bradley_terry(home):
    positive=sample(rating=1,propensity=.9); negative=sample(rating=0,propensity=.1)
    fitted=fit([positive,negative],epochs=20)
    uniform=fit([{**positive,'propensity':.5},{**negative,'propensity':.5}],epochs=20)
    option=options('codex')[0];f=positive['features']
    assert probability(option,f,fitted['weights'])<probability(option,f,uniform['weights'])
    b=sample('gpt-6.1-terra',rating=None)
    pair={'a':positive['id'],'b':b['id'],'preference':0.,'propensity':.1}
    result=fit([{**positive,'rating':None},b],[pair])
    assert result['pair_examples']==1 and result['weights']['codex:gpt-6-luna:low'][0]<0


def test_baseline_holdout_verification_migration_privacy(home,tmp_path):
    canary='PRIVATE_XY123_PAYLOAD_49_SECRET'
    f=featurize(f'Write exactly 3 items only in JSON? `{canary}`\n- wrong again https://secret.example/a\nC:/secret/file.py',local=True,cwd=tmp_path)
    assert f['constraints']==2 and f['question_marks']==1 and 0<=f['cluster_id']<64
    with pytest.raises(ValueError): validate({**f,'top_extensions':[canary]})
    with pytest.raises(ValueError): validate({**f,'previous_model':canary})
    for i in range(10):
        r=sample(rating=i%2);r['features']=f
        r=add(r)
        revise(r['id'],rating=parse_rating('+' if i%2 else '-v'))
    path=train_baseline()
    from advisor.priors import evaluate
    later=evaluate()['heldout']
    assert later['heldout_turns']==2 and later['user_baseline']['rated_turns']==2
    assert later['user_baseline']['brier'] is not None
    head=fit_verification(events()*3)
    o=choose(f,'codex',weights={},rows=[],baseline={},exploration=0)
    assert verification(o,f,head)[0] is not None
    # An actual v2 database migration preserves ratings while adding unknown fields.
    with connect() as db:
        r=events(db)[0];r['features']={k:v for k,v in r['features'].items() if k not in EXTRA_KEYS};r['schema']=2
        db.execute('UPDATE events SET row=? WHERE id=?',(json.dumps(r),r['id']));db.execute('PRAGMA user_version=2')
    assert events()[0]['schema']==3 and events()[0]['features']['chars'] is None
    for p in home.rglob('*'):
        if p.is_file():
            data=p.read_bytes();assert canary.encode() not in data and b'secret.example' not in data and b'prompt_hash' not in data


def test_claude_model_usage_fields():
    raw={'type':'result','result':'not stored','modelUsage':{'private-model':{'inputTokens':12,'outputTokens':4,'cacheReadInputTokens':3}},'total_cost_usd':.05}
    _,_,i=parse_output(json.dumps(raw),'claude',0,1,options('claude')[0])
    assert i['input_tokens']==12 and i['cached_tokens']==3 and i['cost_usd']==.05
    assert 'private-model' not in json.dumps(i)


def test_unknown_windows_preserve_reached_and_distribution_validation(home):
    from advisor.quota import account_state
    from advisor.storage import clean_indicators
    row=sample();row['indicators']['account']=rate_limits({'rate_limit_reached_type':'limit','plan_type':'pro'})
    assert account_state([row],'codex')['limit_reached']
    assert budget([row],DEFAULTS,'codex')['paused']
    with pytest.raises(ValueError):clean_indicators({'selection_probabilities':{'private-text':1}})
    with pytest.raises(ValueError):clean_indicators({'selection_probabilities':{options('codex')[0]['key']:.2}})


def test_rollout_resumed_cumulative_delta(tmp_path):
    from datetime import datetime,timezone
    identity='12345678-1234-1234-1234-123456789abc'
    rows=[]
    for timestamp,n in ((1000,100),(2000,175),(2100,220)):
        rows.append({'timestamp':datetime.fromtimestamp(timestamp,timezone.utc).isoformat(),'payload':{'info':{'total_token_usage':{'input_tokens':n,'output_tokens':n//2},'last_token_usage':{'input_tokens':45}}}})
    (tmp_path/(identity+'.jsonl')).write_text('\n'.join(json.dumps(r) for r in rows))
    out=rollout_usage(identity,tmp_path,since=1500)
    assert out['input_tokens']==120 and out['output_tokens']==60
