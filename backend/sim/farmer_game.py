"""Single-player views built from the same world and economics as the sandbox."""
import copy
import secrets
import uuid

from .agent_models import Environment, Setup
from .economics import aggregate
from .population import create_state
from .config import NORMAL_SURPLUS_PER_MU_MIN, NORMAL_SURPLUS_PER_MU_MAX

PROFILE_FIELDS = ('age', 'skill', 'risk', 'confidence', 'support', 'trust')


def make_preview(source):
    world = create_state(Setup.model_validate(source['setup']))
    candidates = [f for f in world['farmers'].values() if not f['initial_adopter']]
    picked = []
    for label, ordered in [
        ('小田经营型', sorted(candidates, key=lambda f: (f['area_mu'], f['id']))),
        ('技术熟练型', sorted(candidates, key=lambda f: (-f['skill'], -f['normal_surplus_per_mu'], f['id']))),
        ('规模经营型', sorted(candidates, key=lambda f: (-f['area_mu'], f['id'])))]:
        farmer = next(f for f in ordered if f['id'] not in [p['farmer_id'] for p in picked])
        picked.append({'label': label, 'farmer_id': farmer['id']})
    return {'draft_id': uuid.uuid4().hex, 'world': world, 'recommendations': picked,
            'source_run_id': source['run_id'], 'mode': source.get('mode', 'offline'),
            'base_environment': Environment.model_validate(source.get('base_environment', source['environment'])).model_dump()}


def preview_view(draft):
    state = draft['world']
    farmers = []
    for f in state['farmers'].values():
        if f['initial_adopter']:
            continue
        farmers.append({k: f[k] for k in ('id', 'name', 'village_id', 'area_mu', 'equipment_cny', 'reference_savings_cny', *PROFILE_FIELDS)})
    return {'draft_id': draft['draft_id'], 'mode': draft['mode'], 'recommendations': draft['recommendations'],
            'farmers': farmers, 'villages': {vid: {'name': v['name'], 'parcels': v['parcels']} for vid, v in state['villages'].items()},
            'summary': {'bureaus': len(state['bureaus']), 'villages': len(state['villages']),
                        'farmers': len(state['farmers']), 'seed': state['setup']['seed']}}


def start_game(draft, farmer_id, profile):
    state = copy.deepcopy(draft['world'])
    f = state['farmers'].get(farmer_id)
    if not f or f['initial_adopter']:
        raise ValueError('请选择一位未安装设备的农户')
    original = {k: f[k] for k in PROFILE_FIELDS}
    f.update(profile)
    cash = 8000 + secrets.randbelow(29) * 1000
    f['cash_cny'] = cash
    position = max(0, min(1, (f['reference_savings_cny'] - 500) / 1300))
    per_mu = NORMAL_SURPLUS_PER_MU_MIN + (NORMAL_SURPLUS_PER_MU_MAX - NORMAL_SURPLUS_PER_MU_MIN) * (.6 * f['skill'] / 4 + .4 * position)
    f['normal_surplus_per_mu'] = round(per_mu, 4)
    f['normal_surplus_cny'] = round(f['area_mu'] * per_mu, 2)
    state['mode'] = draft['mode']
    state['base_environment'] = copy.deepcopy(draft['base_environment'])
    state['experience'] = {'type': 'farmer', 'player_id': farmer_id, 'draft_id': draft['draft_id'],
                           'source_run_id': draft['source_run_id'], 'original_profile': original, 'profile': profile,
                           'cash_draw': {'amount_cny': cash, 'min_cny': 8000, 'max_cny': 36000, 'step_cny': 1000}}
    state['player_turns'] = []
    aggregate(state)
    state['history'] = [{'tick': 0, 'scenario': 'initial', 'metrics': copy.deepcopy(state['metrics'])}]
    return state


def public_view(state, pending=None):
    base = state
    showing = pending['state'] if pending else state
    fid = state['experience']['player_id']
    f = showing['farmers'][fid]
    v = showing['villages'][f['village_id']]
    b = showing['bureaus'][f['bureau_id']]
    # Construct the response explicitly: no draft decisions or event schedule escape.
    own_keys = ('id', 'name', 'area_mu', 'cash_cny', 'normal_surplus_cny', 'normal_surplus_per_mu', 'equipment_cny',
                'status', 'commitment_tick', 'installation_tick', 'loan', 'relief_cny', 'relief_received_cny', *PROFILE_FIELDS)
    player = {k: f[k] for k in own_keys}
    player['financing'] = copy.deepcopy(f['financing'])
    neighbors = [base['farmers'][nid] for nid in f['neighbors']]
    last = state.get('player_turns', [])[-1:]
    if pending:
        ctx = copy.deepcopy(pending['contexts'][fid])
    else:
        ctx = copy.deepcopy(f.get('economics', {}))
    turns = state.get('player_turns', [])
    return {'active': True, 'run_id': state['run_id'], 'tick': showing['tick'], 'committed_tick': state['tick'],
            'stage': 'choice' if pending else 'finished' if state['tick'] >= 10 else 'ready',
            'mode': state['mode'], 'player': player, 'context': ctx,
            'initial_cash_cny': state['experience']['cash_draw']['amount_cny'],
            'original_profile': state['experience']['original_profile'],
            'village': {'id': v['id'], 'name': v['name'], 'parcels': v['parcels']},
            'neighbors': [{'id': n['id'], 'name': n['name'], 'status': n['status']} for n in neighbors],
            'bureau': {'name': b['name'], 'water_price': b['water_price'], 'subsidy_rate': b['subsidy_rate'],
                       'next_policy': copy.deepcopy(b.get('next_policy')) if not pending else None},
            'environment': {k: val for k, val in showing['environment'].items() if k != 'instruction'},
            'last_turn': copy.deepcopy(last[0]) if last else None,
            'summary': {'cumulative_saved_m3': round(sum(t['saved_m3'] for t in turns), 2),
                        'cash_change_cny': round(f['cash_cny'] - state['experience']['cash_draw']['amount_cny'], 2),
                        'loan_principal_cny': f['loan']['principal_cny'] if f['loan'] else 0,
                        'overdue_cny': round(f['loan']['principal_arrears_cny'] + f['loan']['interest_arrears_cny'], 2) if f['loan'] else 0},
            'turns': [{'tick': t['tick'], 'action': t['action'], 'cash_cny': t['cash_cny'], 'status': t['status']} for t in turns]}
