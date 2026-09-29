"""Поиск: быстрая вставка, цель с нарушениями SLA аварий, ruin & recreate."""

import random

import pytest

from app.domain.enums import Skill
from app.solver import Solver
from app.solver.evaluator import RouteEvaluator
from app.solver.fleet import Fleet
from app.solver.search.insertion import insert_pool
from app.solver.search.ruin import RuinRecreate
from tests.helpers import REGIONS, assert_plan_valid, make_engineer, make_order


def _random_route(rng: random.Random, ev: RouteEvaluator, trial: int):
    eng = make_engineer(shift_start="10:00", shift_end=rng.choice(["17:00", "19:00", "22:00"]))
    orders = []
    for i in range(rng.randint(0, 7)):
        emergency = rng.random() < 0.3
        ws = rng.choice(range(10, 21, 2)) * 60
        orders.append(
            make_order(
                f"{trial}-{i}",
                skill=Skill.EMERGENCY if emergency else Skill.LOCAL,
                start="00:01" if emergency and rng.random() < 0.5 else f"{ws // 60:02d}:00",
                end="23:59" if emergency else f"{ws // 60 + 2:02d}:00",
                duration=rng.choice([20, 30, 70, 80]),
                lat=55.6 + rng.random() * 0.2,
                lon=37.5 + rng.random() * 0.3,
                district=rng.choice(["Кузьминки", "Кузьминки", "Кашира"]),
            )
        )
    orders.sort(key=lambda o: o.window.start_min)
    return eng, orders, ev.state(eng, orders)


def test_best_insertion_matches_full_scan_and_counts_sla_breaches() -> None:
    """Запомненная лучшая вставка = минимум по insertions(); прирост нарушений SLA и стоимость
    совпадают с полным пересчётом маршрута."""
    rng = random.Random(11)
    ev = RouteEvaluator()
    checked = 0
    for trial in range(400):
        _eng, _orders, state = _random_route(rng, ev, trial)
        if state is None:
            continue
        emergency = rng.random() < 0.4
        new = make_order(
            f"{trial}-new",
            skill=Skill.EMERGENCY if emergency else Skill.LOCAL,
            start="00:01" if emergency else "12:00",
            end="23:59" if emergency else "14:00",
            duration=rng.choice([20, 30, 70]),
            lat=55.6 + rng.random() * 0.2,
            lon=37.5 + rng.random() * 0.3,
        )
        for with_shift in (False, True):
            all_ins = ev.insertions(state, new, with_shift=with_shift)
            best = ev.best_insertion(state, new, with_shift=with_shift)
            assert ev.best_insertion(state, new, with_shift=with_shift) is best  # из кэша
            if not all_ins:
                assert best is None
                continue
            expected = all_ins[0]
            for ins in all_ins:
                if ins.cost < expected.cost - 1e-9:
                    expected = ins
            assert best == expected
        for ins in ev.insertions(state, new):
            full = ev.insert(state, new, ins.pos)
            assert ins.breach_delta == full.breaches - state.breaches
            assert abs(ins.cost - (full.cost - state.cost)) < 1e-6
            checked += 1
    assert checked > 100


def test_sla_breach_outranks_one_more_crew() -> None:
    """Аварию лучше начать вовремя второй бригадой, чем опоздать одной (ответ организаторов)."""
    engineers = [
        make_engineer("e1", "Бригада Первая", [Skill.LOCAL, Skill.EMERGENCY]),
        make_engineer("e2", "Бригада Вторая", [Skill.LOCAL, Skill.EMERGENCY]),
    ]
    # одной бригаде пришлось бы начать аварию в 12:30 (SLA — до 12:00) или потерять заявку
    orders = [
        make_order("sos", skill=Skill.EMERGENCY, start="00:01", end="23:59", duration=80),
        make_order("long", skill=Skill.LOCAL, start="10:00", end="10:30", duration=150),
    ]
    plan = Solver().solve(orders, engineers)
    assert_plan_valid(plan, orders, engineers)
    assert plan.metrics.assigned_orders == 2
    assert plan.metrics.emergency_within_sla == 1
    assert plan.metrics.active_engineers_count == 2


@pytest.mark.parametrize("region", REGIONS)
def test_ruin_recreate_never_worsens_plan_rank(datasets, region: str) -> None:
    """LNS и снятие маршрутов не теряют назначенный вес, не добавляют нарушений SLA и бригад."""
    orders, engineers = datasets[region]
    solver = Solver()
    fleet = Fleet.empty(solver.evaluator(engineers), engineers)
    left = insert_pool(fleet, list(orders))
    start = fleet.score(left)
    rr = RuinRecreate(fleet, orders, seed=5)
    left = rr.improve(left, 60)
    after_improve = fleet.score(left)
    assert after_improve.rank <= start.rank
    left = rr.eliminate_routes(left, iterations=60, per_attempt=30)
    after_elim = fleet.score(left)
    assert after_elim.lost_weight <= after_improve.lost_weight
    assert after_elim.breaches <= after_improve.breaches
    assert after_elim.crews <= after_improve.crews
    assigned = fleet.assigned_ids()
    assert len(assigned) + len(left) == len(orders)
    assert assigned.isdisjoint(o.id for o in left)
