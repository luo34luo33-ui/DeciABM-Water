import asyncio

from fastapi import Header, HTTPException
from pydantic import Field

from .sim.agent_models import Model, Setup, TickRequest
from .sim.farmer_game import make_preview, preview_view, public_view, start_game
from .sim.population import create_state
from .sim.tick_engine import finish_tick, prepare_tick
from .utils.logger import list_runs, read_run, save_snapshot
from typing import Literal


class PlayerProfile(Model):
    age: int = Field(ge=18, le=80)
    skill: int = Field(ge=0, le=4)
    risk: int = Field(ge=0, le=4)
    confidence: int = Field(ge=0, le=4)
    support: int = Field(ge=0, le=4)
    trust: int = Field(ge=0, le=4)


class StartGame(Model):
    draft_id: str
    farmer_id: str
    profile: PlayerProfile


class GameTick(Model):
    run_id: str
    expected_tick: int = Field(ge=0)


class PlayerChoice(GameTick):
    action: Literal['adopt', 'wait', 'continue']
    reason: str = Field(default='', max_length=500)


def attach_farmer_routes(app, log_root):
    app.state.farmer_game = None
    app.state.farmer_draft = None
    lock = asyncio.Lock()
    progress = {'running': False, 'phase': '准备选角', 'completed': 0, 'total': 0, 'active_entity_ids': []}

    def require_game():
        game = app.state.farmer_game
        if not game:
            raise HTTPException(409, '请先选择身份开始新局')
        return game

    def key_for(game, authorization):
        key = authorization.removeprefix('Bearer ').strip()
        if game['state']['mode'] == 'online' and not key:
            raise HTTPException(400, '本局沿用在线配置，请填写 OpenRouter Key')
        return key

    def check(game, req):
        if req.run_id != game['state']['run_id'] or req.expected_tick != game['state']['tick']:
            raise HTTPException(409, '运行状态已变化，请刷新角色状态')
        if game['state']['tick'] >= 10:
            raise HTTPException(409, '十季体验已结束，可查看结局或新建一局')

    @app.post('/api/farmer/preview')
    async def preview():
        if lock.locked():
            raise HTTPException(409, '当前季正在运行')
        source = app.state.simulation or create_state(Setup(farmers_min=12, farmers_max=18))
        app.state.farmer_draft = make_preview(source)
        return preview_view(app.state.farmer_draft)

    @app.post('/api/farmer/start')
    async def start(req: StartGame):
        if lock.locked():
            raise HTTPException(409, '当前季正在运行')
        async with lock:
            game = app.state.farmer_game
            if game and game['state']['experience']['draft_id'] == req.draft_id:
                experience = game['state']['experience']
                if req.farmer_id != experience['player_id'] or req.profile.model_dump() != experience['profile']:
                    raise HTTPException(409, '该局身份已锁定；更换身份请新建一局')
                return public_view(game['state'], game['pending'])
            draft = app.state.farmer_draft
            if not draft or draft['draft_id'] != req.draft_id:
                raise HTTPException(409, '选角资料已变化，请重新进入选角')
            try:
                state = start_game(draft, req.farmer_id, req.profile.model_dump())
            except ValueError as exc:
                raise HTTPException(422, str(exc))
            save_snapshot(state, log_root)
            app.state.farmer_game = {'state': state, 'pending': None, 'receipt': None}
            progress.update(running=False, phase='家底已抽取，等待揭晓', completed=0, total=0, active_entity_ids=[])
            return public_view(state)

    @app.get('/api/farmer/state')
    async def state():
        game = app.state.farmer_game
        return public_view(game['state'], game['pending']) if game else {'active': False}

    @app.get('/api/farmer/progress')
    async def get_progress():
        return progress

    @app.post('/api/farmer/prepare')
    async def prepare(req: GameTick, authorization: str = Header(default='')):
        if lock.locked():
            raise HTTPException(409, '当前季正在运行')
        async with lock:
            game = require_game()
            check(game, req)
            if game['pending']:
                return public_view(game['state'], game['pending'])
            key = key_for(game, authorization)
            s = game['state']
            request = TickRequest(run_id=s['run_id'], expected_tick=s['tick'], mode=s['mode'], environment=s['base_environment'])
            progress.update(running=True, error=None)
            try:
                game['pending'] = await prepare_tick(s, request, progress, key)
                progress['phase'] = '等待你的选择'
                return public_view(s, game['pending'])
            finally:
                progress.update(running=False, active_entity_ids=[])

    @app.post('/api/farmer/decide')
    async def decide(req: PlayerChoice, authorization: str = Header(default='')):
        if lock.locked():
            raise HTTPException(409, '当前季正在运行')
        async with lock:
            game = require_game()
            if game['receipt'] == req.model_dump():
                return public_view(game['state'])
            check(game, req)
            pending = game['pending']
            if not pending:
                raise HTTPException(409, '请先读取本季卷宗与授信条件')
            fid = game['state']['experience']['player_id']
            player = pending['state']['farmers'][fid]
            if player['status'] == 'uncommitted':
                if req.action == 'continue':
                    raise HTTPException(422, '本季请选择安装或等待')
                if req.action == 'adopt' and not pending['contexts'][fid]['financing_feasible']:
                    raise HTTPException(422, '现金不足且未获足额授信，本季不能执行安装')
            elif req.action != 'continue':
                raise HTTPException(422, '已经承诺投资，请继续推进经营')
            key = key_for(game, authorization)
            progress.update(running=True, phase='结算你的选择')
            try:
                result = await finish_tick(pending, progress, key, player_id=fid, action=req.action, reason=req.reason)
                save_snapshot(result, log_root)
                game.update(state=result, pending=None, receipt=req.model_dump())
                progress['phase'] = '本季卷宗已归档'
                return public_view(result)
            finally:
                progress.update(running=False, active_entity_ids=[])

    @app.get('/api/farmer/runs')
    async def runs():
        result = []
        for item in list_runs(log_root):
            first = read_run(item['run_id'], log_root)['snapshots'][0]
            if first.get('experience', {}).get('type') == 'farmer':
                result.append({'run_id': item['run_id'], 'last_tick': item['last_tick'],
                               'player_name': first['farmers'][first['experience']['player_id']]['name']})
        return result

    @app.get('/api/farmer/history')
    async def history(run_id: str | None = None):
        selected = run_id or require_game()['state']['run_id']
        try:
            bundle = read_run(selected, log_root)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, '没有找到角色卷宗')
        if bundle['snapshots'][0].get('experience', {}).get('type') != 'farmer':
            raise HTTPException(404, '该记录不是农户模式')
        return {'run_id': selected, 'snapshots': [public_view(s) for s in bundle['snapshots']]}
