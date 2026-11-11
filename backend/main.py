import asyncio

from fastapi import FastAPI, Header, HTTPException
from fastapi.staticfiles import StaticFiles

from .sim.agent_models import Setup, TickRequest
from .sim.config import DEMO_ROOT, JEV_MODEL, LLM_MODEL, LOG_ROOT, PRESETS
from .sim.population import create_state
from .sim.tick_engine import execute_tick
from .utils.logger import list_runs, read_run, save_snapshot
from .farmer_routes import attach_farmer_routes


def create_app(log_root=LOG_ROOT):
    app = FastAPI(title="多主体节水决策AI仿真沙盒", docs_url="/api/docs", redoc_url=None)
    app.state.simulation = None
    app.state.lock = asyncio.Lock()
    app.state.progress = {"running": False, "phase": "尚未开始", "tick": 0, "total": 0, "completed": 0, "active_entity_ids": []}
    log_root.mkdir(parents=True, exist_ok=True)

    @app.get("/api/config")
    async def config():
        return {"presets": PRESETS, "models": {"jev": JEV_MODEL, "deepseek": LLM_MODEL}, "defaults": Setup(farmers_min=12, farmers_max=18).model_dump()}

    @app.get("/api/state")
    async def state():
        if app.state.simulation is None:
            app.state.simulation = create_state(Setup(farmers_min=12, farmers_max=18))
            save_snapshot(app.state.simulation, log_root)
        return app.state.simulation

    @app.post("/api/reset")
    async def reset(setup: Setup):
        if app.state.lock.locked():
            raise HTTPException(409, "本轮仍在执行，请完成后新建运行。")
        async with app.state.lock:
            result = create_state(setup)
            save_snapshot(result, log_root)
            app.state.simulation = result
            return result

    @app.post("/api/tick")
    async def tick(request: TickRequest, authorization: str = Header(default="")):
        if app.state.lock.locked():
            raise HTTPException(409, "已有一轮正在执行。")
        current = app.state.simulation
        if current is None or request.run_id != current["run_id"] or request.expected_tick != current["tick"]:
            raise HTTPException(409, "页面运行状态已变化，请返回当前状态后重试。")
        key = authorization.removeprefix("Bearer ").strip()
        if request.mode == "online" and not key:
            raise HTTPException(400, "在线模式需要在设置中输入 OpenRouter Key。")
        async with app.state.lock:
            progress = app.state.progress
            progress.update(running=True, error=None)
            try:
                result = await execute_tick(current, request, progress, key)
                save_snapshot(result, log_root)
                app.state.simulation = result
                progress["phase"] = "本轮完成"
                return result
            finally:
                progress.update(running=False, active_entity_ids=[])

    @app.get("/api/progress")
    async def progress():
        return app.state.progress

    @app.get("/api/runs")
    async def runs():
        return list_runs(log_root)

    @app.get("/api/runs/{run_id}")
    async def run(run_id: str):
        try:
            return read_run(run_id, log_root)
        except (ValueError, FileNotFoundError):
            raise HTTPException(404, "没有找到该运行日志。")

    attach_farmer_routes(app, log_root)
    app.mount("/", StaticFiles(directory=DEMO_ROOT / "frontend", html=True), name="frontend")
    return app


app = create_app()
