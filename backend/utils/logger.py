import json
import re
from pathlib import Path

from ..sim.config import LOG_ROOT


def run_folder(run_id, root=LOG_ROOT):
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run_id):
        raise ValueError("无效运行编号")
    return Path(root) / run_id


def save_snapshot(state, root=LOG_ROOT):
    folder = run_folder(state["run_id"], root)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / f"tick-{state['tick']:04}.json").write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def list_runs(root=LOG_ROOT):
    runs = []
    for folder in Path(root).iterdir():
        if not folder.is_dir():
            continue
        files = sorted(folder.glob("tick-*.json"))
        if files:
            first = json.loads(files[0].read_text(encoding="utf-8"))
            runs.append({"run_id": folder.name, "created_at": first["created_at"],
                         "last_tick": int(files[-1].stem.split("-")[-1]), "setup": first["setup"]})
    return sorted(runs, key=lambda r: r["created_at"], reverse=True)


def read_run(run_id, root=LOG_ROOT):
    files = sorted(run_folder(run_id, root).glob("tick-*.json"))
    if not files:
        raise FileNotFoundError(run_id)
    return {"run_id": run_id, "snapshots": [json.loads(p.read_text(encoding="utf-8")) for p in files]}

