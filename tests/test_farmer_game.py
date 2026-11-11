import copy
import json

import pytest
import httpx
from fastapi.testclient import TestClient

from backend.main import create_app
from backend.sim.farmer_game import PROFILE_FIELDS


@pytest.fixture
def game_client(tmp_path, monkeypatch):
    monkeypatch.setattr('backend.sim.farmer_game.secrets.randbelow', lambda n: n-1)
    app = create_app(tmp_path)
    with TestClient(app) as client:
        client.get('/api/state')
        yield client, app


def start(client, index=0, custom=None):
    preview=client.post('/api/farmer/preview').json()
    candidate=preview['farmers'][index]
    profile={k:candidate[k] for k in PROFILE_FIELDS}
    profile.update(custom or {})
    body={'draft_id':preview['draft_id'],'farmer_id':candidate['id'],'profile':profile}
    response=client.post('/api/farmer/start',json=body)
    assert response.status_code==200,response.text
    return response.json(),body,preview


def prepare(client,state):
    response=client.post('/api/farmer/prepare',json={'run_id':state['run_id'],'expected_tick':state['committed_tick']})
    assert response.status_code==200,response.text
    return response.json()


def decide(client,state,action):
    body={'run_id':state['run_id'],'expected_tick':state['committed_tick'],'action':action,'reason':'测试选择'}
    response=client.post('/api/farmer/decide',json=body)
    assert response.status_code==200,response.text
    return response.json(),body


def test_cash_draw_is_server_only_once_and_other_farmers_unchanged(game_client,monkeypatch):
    client,app=game_client
    draws=[]
    monkeypatch.setattr('backend.sim.farmer_game.secrets.randbelow',lambda n:draws.append(n) or 5)
    original=copy.deepcopy(app.state.simulation)
    state,body,preview=start(client,custom={'skill':4,'age':25})
    assert state['initial_cash_cny']==13000
    assert draws==[29]
    assert client.post('/api/farmer/start',json=body).json()==state
    assert client.get('/api/farmer/state').json()==state
    assert draws==[29]
    assert client.post('/api/farmer/start',json={**body,'cash_cny':999999}).status_code==422
    actual=app.state.farmer_game['state']
    fid=actual['experience']['player_id']
    before=app.state.farmer_draft['world']
    assert all(f==before['farmers'][id] for id,f in actual['farmers'].items() if id!=fid)
    assert actual['banks']==before['banks']
    assert actual['villages']==before['villages']
    assert app.state.simulation==original
    assert 'events' not in json.dumps(preview)
    assert 'cash_cny' not in json.dumps(preview)
    assert len(preview['recommendations'])==3
    changed={**body,'profile':{**body['profile'],'age':50}}
    assert client.post('/api/farmer/start',json=changed).status_code==409


def test_prepare_freezes_state_hides_reference_and_repeat_requests_do_not_settle(game_client):
    client,app=game_client
    state,_,_=start(client)
    original=copy.deepcopy(app.state.farmer_game['state'])
    pending=prepare(client,state)
    assert pending['stage']=='choice' and pending['tick']==1 and pending['committed_tick']==0
    assert 'p_adopt' not in json.dumps(pending)
    assert 'jev_reference' not in json.dumps(pending)
    assert 'events' not in json.dumps(pending)
    assert client.get('/api/farmer/state').json()==pending
    assert prepare(client,state)==pending
    assert app.state.farmer_game['state']==original
    assert len(client.get('/api/farmer/history').json()['snapshots'])==1
    result,body=decide(client,pending,'wait')
    assert result['tick']==1 and result['player']['status']=='uncommitted'
    assert result['last_turn']['comparison']['result']['p_adopt']>=0
    assert client.post('/api/farmer/decide',json=body).json()==result
    assert len(client.get('/api/farmer/history').json()['snapshots'])==2
    assert app.state.farmer_game['state']['farmers'][result['player']['id']]['decision']['source']=='human'


def test_human_choice_overrides_opposite_probability_and_financing_is_still_required(game_client,monkeypatch):
    client,app=game_client
    monkeypatch.setattr('backend.sim.tick_engine.farmer_rule',lambda f,c:{'p_adopt':0.01})
    state,_,_=start(client)
    pending=prepare(client,state)
    assert pending['context']['financing_feasible']
    result,_=decide(client,pending,'adopt')
    assert result['player']['status']=='pending'
    assert result['last_turn']['comparison']['result']['p_adopt']==.01
    assert result['last_turn']['economics']['operating_gain_cny']==0
    pending=prepare(client,result)
    assert pending['player']['status']=='operating'
    result,_=decide(client,pending,'continue')
    assert result['last_turn']['comparison'] is None
    assert result['last_turn']['saved_m3']>0
    # A different new game with a genuine cash deficit must still be refused.
    state,_,_=start(client)
    game=app.state.farmer_game
    game['state']['farmers'][state['player']['id']]['cash_cny']=-500
    pending=prepare(client,state)
    assert not pending['context']['financing_feasible']
    body={'run_id':state['run_id'],'expected_tick':0,'action':'adopt'}
    assert client.post('/api/farmer/decide',json=body).status_code==422
    assert game['state']['tick']==0 and game['pending']
    result,_=decide(client,pending,'wait')
    assert result['tick']==1


@pytest.mark.parametrize('timing',[1,10,None])
def test_complete_ten_season_paths_and_truthful_ending(game_client,timing):
    client,app=game_client
    state,_,_=start(client)
    for season in range(1,11):
        pending=prepare(client,state)
        action='continue' if pending['player']['status']!='uncommitted' else 'adopt' if timing==season else 'wait'
        state,_=decide(client,pending,action)
    assert state['stage']=='finished'
    if timing==1:
        assert state['player']['status']=='operating' and state['summary']['cumulative_saved_m3']>0
    elif timing==10:
        assert state['player']['status']=='pending' and state['summary']['cumulative_saved_m3']==0
    else:
        assert state['player']['status']=='uncommitted' and state['summary']['cumulative_saved_m3']==0
    assert state['summary']['cash_change_cny']==pytest.approx(state['player']['cash_cny']-state['initial_cash_cny'])
    history=client.get('/api/farmer/history').json()
    assert len(history['snapshots'])==11
    assert 'events' not in json.dumps(history)
    assert 'setup' not in json.dumps(client.get('/api/farmer/runs').json())
    assert client.post('/api/farmer/prepare',json={'run_id':state['run_id'],'expected_tick':10}).status_code==409


def test_player_loan_starts_at_installation_and_repaid_in_five_seasons(game_client,monkeypatch):
    client,app=game_client
    monkeypatch.setattr('backend.sim.farmer_game.secrets.randbelow',lambda n:0)
    preview=client.post('/api/farmer/preview').json()
    index=next(i for i,f in enumerate(preview['farmers']) if 15000<f['equipment_cny']<30000)
    state,_,_=start(client,index,{'age':25,'skill':4})
    pending=prepare(client,state)
    assert pending['context']['required_loan_cny']>0 and pending['context']['financing_feasible']
    state,_=decide(client,pending,'adopt')
    original=state['player']['loan']['original_cny']
    assert state['last_turn']['economics']['principal_paid_cny']==0
    for _ in range(5):
        state,_=decide(client,prepare(client,state),'continue')
    loan=state['player']['loan']
    assert loan['principal_cny']==0
    assert loan['repaid_principal_cny']==original


def test_online_comparison_is_hidden_then_revealed_without_storing_key(game_client,monkeypatch):
    client,app=game_client
    from backend.sim.config import JEV_MODEL
    from backend.sim.fallback_rules import bank_rule,bureau_rule
    from backend.sim import tick_engine
    original_client=httpx.AsyncClient
    requests=[]
    key='test-only-player-secret'
    def handler(request):
        assert request.headers['authorization']=='Bearer '+key
        body=json.loads(request.content);requests.append(body)
        if 'questions' in body:
            return httpx.Response(200,json={'model':JEV_MODEL,'answers':{'adopt_drip':{'type':'noul','noul':.01}}})
        facts=json.loads(body['messages'][1]['content'])
        value=bank_rule(facts) if 'bank_id' in facts else bureau_rule(facts)
        return httpx.Response(200,json={'choices':[{'finish_reason':'stop','message':{'content':json.dumps(value)}}]})
    monkeypatch.setattr(tick_engine.httpx,'AsyncClient',lambda **kwargs:original_client(transport=httpx.MockTransport(handler),**kwargs))
    app.state.simulation['mode']='online'
    state,_,_=start(client)
    body={'run_id':state['run_id'],'expected_tick':0}
    assert client.post('/api/farmer/prepare',json=body).status_code==400
    headers={'Authorization':'Bearer '+key}
    pending=client.post('/api/farmer/prepare',json=body,headers=headers).json()
    count=len(requests)
    assert 'p_adopt' not in json.dumps(pending)
    assert key not in json.dumps(app.state.farmer_game)
    assert client.post('/api/farmer/prepare',json=body,headers=headers).json()==pending
    assert len(requests)==count
    done=client.post('/api/farmer/decide',json={**body,'action':'adopt'},headers=headers).json()
    assert done['player']['status']=='pending'
    assert done['last_turn']['comparison']['source']=='jev'
    assert done['last_turn']['comparison']['result']['p_adopt']==.01
    assert key not in json.dumps(client.get('/api/farmer/history').json())
