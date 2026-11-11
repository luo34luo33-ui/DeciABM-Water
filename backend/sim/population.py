"""Adapted from Level3 spatial_abm.geometry and mini_abm_v3 population.

The packaged profiles are the 48 synthetic farmers from seed 20261022.
Spatially scaled base gains are normalized before assigning a new parcel.
"""
import json
import uuid
from datetime import datetime, timezone
from itertools import combinations
from pathlib import Path

import numpy as np
from shapely import affinity
from shapely.geometry import MultiPoint, Point, Polygon, mapping
from shapely.ops import voronoi_diagram

from .agent_models import Bank, Bureau, Environment, Farmer, Setup, Village
from .config import NORMAL_SURPLUS_PER_MU_MIN, NORMAL_SURPLUS_PER_MU_MAX


def parcels(seed, count):
    rng = np.random.default_rng(np.random.SeedSequence([seed, 80]))
    angles = np.linspace(0, 2 * np.pi, int(rng.integers(7, 11)), endpoint=False)
    radii = rng.uniform(.78, 1.2, len(angles))
    stretch = rng.uniform(.75, 1.4)
    boundary = Polygon([(np.cos(a) * r * stretch, np.sin(a) * r) for a, r in zip(angles, radii)])
    scale = np.sqrt(count * 30 * (2000 / 3) / boundary.area)
    boundary = affinity.scale(boundary, xfact=scale, yfact=scale, origin=(0, 0))
    rng = np.random.default_rng(np.random.SeedSequence([seed, 81]))
    points = []
    lo = boundary.bounds[:2]
    hi = boundary.bounds[2:]
    while len(points) < count:
        point = rng.uniform(lo, hi)
        if boundary.contains(Point(point)):
            points.append(point)

    def partition(pts):
        cells = list(voronoi_diagram(MultiPoint(pts), envelope=boundary.envelope).geoms)
        return [next(c for c in cells if c.covers(Point(p))).intersection(boundary) for p in pts]

    cells = partition(points)
    cells = partition([list(c.centroid.coords)[0] for c in cells])
    network = [[] for _ in cells]
    for i, j in combinations(range(count), 2):
        if cells[i].boundary.intersection(cells[j].boundary).length > 1e-7:
            network[i].append(j)
            network[j].append(i)
    return boundary, cells, network


def create_state(setup: Setup):
    profiles = json.loads((Path(__file__).parent / "data/profiles.json").read_text(encoding="utf-8"))
    grouped = {}
    for p in profiles:
        grouped.setdefault(p["archetype_id"], []).append(p)
    state = {"run_id": datetime.now().strftime("run-%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6],
             "created_at": datetime.now(timezone.utc).isoformat(), "tick": 0,
             "setup": setup.model_dump(), "environment": Environment().model_dump(), "mode": "offline",
             "basin": {"id": "basin", "name": "流域管理局"}, "bureaus": {}, "villages": {},
             "banks": {}, "farmers": {}, "quota_transfers": [], "metrics": {}, "history": [], "traces": []}
    region_names = ["上原", "清禾", "平川", "河畔", "丰田", "润泽"]
    for bi in range(setup.bureaus):
        bid = f"B{bi+1:02}"
        bureau = Bureau(id=bid, name=f"{region_names[bi]}水利局").model_dump()
        state["bureaus"][bid] = bureau
        for vi in range(setup.villages_per_bureau):
            vid = f"{bid}-V{vi+1:02}"
            bank_id = vid + "-bank"
            local_seed = int(np.random.SeedSequence([setup.seed, bi, vi]).generate_state(1)[0])
            rng = np.random.default_rng(np.random.SeedSequence([local_seed, 82]))
            count = int(rng.integers(setup.farmers_min, setup.farmers_max + 1)) if setup.farmers_min is not None else setup.farmers_per_village
            boundary, cells, neighbors = parcels(local_seed, count)
            names = [f"{vid}-F{i+1:02}" for i in range(len(cells))]
            initial = set(rng.choice(len(cells), 2, replace=False).tolist())
            group_names = list(grouped)
            rng.shuffle(group_names)
            features = []
            for fi, cell in enumerate(cells):
                p = grouped[group_names[fi % len(group_names)]][int(rng.integers(4))]
                area = cell.area / (2000 / 3)
                installed = fi in initial
                savings_position = max(0, min(1, (p['savings_cny'] - 500) / 1300))
                earning_factor = .6 * p['technical_skill_level'] / 4 + .4 * savings_position
                surplus_per_mu = NORMAL_SURPLUS_PER_MU_MIN + (NORMAL_SURPLUS_PER_MU_MAX - NORMAL_SURPLUS_PER_MU_MIN) * earning_factor
                farmer = Farmer(id=names[fi], name=f"农户 {fi+1:02}", village_id=vid, bureau_id=bid,
                    bank_id=bank_id, archetype=p["archetype_id"], area_mu=area,
                    cash_cny=round(p["liquid_capital_cny"] * setup.initial_cash_factor, 2), normal_surplus_cny=round(area * surplus_per_mu, 2),
                    normal_surplus_per_mu=round(surplus_per_mu, 4), reference_savings_cny=p['savings_cny'],
                    base_gain_cny=round(p["base_gain_cny"] / p["farm_area_mu"] * area, 2),
                    equipment_cny=round(1200 * area, 2), risk=p["risk_aversion_level"],
                    skill=p["technical_skill_level"], trust=p["neighbor_trust_level"],
                    confidence=p["benefit_confidence_level"], support=p["technical_support_level"], age=p["age"],
                    neighbors=[names[j] for j in neighbors[fi]], initial_adopter=installed,
                    status="operating" if installed else "uncommitted",
                    commitment_tick=0 if installed else None, installation_tick=0 if installed else None)
                state["farmers"][farmer.id] = farmer.model_dump()
                features.append({"type": "Feature", "geometry": mapping(cell),
                    "properties": {"farmer_id": farmer.id, "area_mu": area}})
            village = Village(id=vid, name=f"{region_names[bi]}{['一','二','三','四'][vi]}村", bureau_id=bid,
                bank_id=bank_id, farmers=names, boundary=mapping(boundary), parcels=features).model_dump()
            state["villages"][vid] = village
            capital = round(sum(state["farmers"][f]["equipment_cny"] for f in names) * .4, 2)
            state["banks"][bank_id] = Bank(id=bank_id, name=village["name"] + "农商行", village_id=vid,
                bureau_id=bid, cash_cny=capital, initial_cash_cny=capital).model_dump()
            bureau["villages"].append(vid)
        quote = sum(f["equipment_cny"] for f in state["farmers"].values() if f["bureau_id"] == bid)
        bureau["budget_base_cny"] = bureau["budget_cny"] = round(quote * .25, 2)
    from .economics import aggregate
    aggregate(state)
    state["history"] = [{"tick": 0, "scenario": "initial", "metrics": state["metrics"]}]
    return state

