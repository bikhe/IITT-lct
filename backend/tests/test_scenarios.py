"""Сценарные тесты планировщика на синтетических данных (app.bench).

Допустимость обоих планов по независимой проверке, «не хуже базового варианта» по
лексикографической цели (потерянный вес → бригады), детерминизм генератора и солвера,
инварианты перепланирования на дне событий.
"""

import pytest

from app.bench.run import plan_stats, run_replan
from app.bench.scenarios import SYNTHETIC_KINDS, generate
from app.bench.validate import check_plan, check_reasons, check_replan
from app.domain.enums import ReasonCode
from app.solver import BaselineSolver, Solver

SMALL = {"n_orders": 30, "n_crews": 5}
CHECKED_KINDS = (
    "city",
    "clustered",
    "peak",
    "suburban",
    "emergency_heavy",
    "skill_scarce",
    "transport_mix",
    "overload",
    "wide_windows",
    "tiny",
)
SEEDS = (1, 2)


def _small(kind: str, seed: int):
    return generate(kind, seed) if kind == "tiny" else generate(kind, seed, **SMALL)


@pytest.mark.parametrize("seed", SEEDS)
@pytest.mark.parametrize("kind", CHECKED_KINDS)
def test_plans_are_valid_and_not_worse_than_baseline(kind: str, seed: int) -> None:
    scn = _small(kind, seed)
    ours = Solver().solve(scn.orders, scn.engineers)
    base = BaselineSolver().solve(scn.orders, scn.engineers)
    for plan in (ours, base):
        assert check_plan(plan, scn.orders, scn.engineers) == []
        assert check_reasons(plan, scn.orders, scn.engineers) == []
    o = plan_stats(ours, scn.orders, scn.engineers, 0.0)
    b = plan_stats(base, scn.orders, scn.engineers, 0.0)
    assert o.lost_weight <= b.lost_weight, (o.lost_weight, b.lost_weight)
    if o.lost_weight == b.lost_weight:
        assert o.crews <= b.crews, (o.crews, b.crews)


def test_generator_is_deterministic() -> None:
    for kind in SYNTHETIC_KINDS:
        if kind == "large":
            continue
        a, b = generate(kind, 7, **SMALL), generate(kind, 7, **SMALL)
        assert [o.model_dump() for o in a.orders] == [o.model_dump() for o in b.orders]
        assert [e.model_dump() for e in a.engineers] == [e.model_dump() for e in b.engineers]
        assert a.notes == b.notes
        c = generate(kind, 8, **SMALL)
        assert [o.model_dump() for o in a.orders] != [o.model_dump() for o in c.orders]


@pytest.mark.parametrize("kind", ["suburban", "transport_mix"])
def test_solver_is_deterministic_on_scenarios(kind: str) -> None:
    first = _small(kind, 3)
    second = _small(kind, 3)
    plan_a = Solver().solve(first.orders, first.engineers)
    plan_b = Solver().solve(second.orders, second.engineers)
    assert plan_a.model_dump() == plan_b.model_dump()


def test_impossible_order_gets_the_expected_reason() -> None:
    """В tiny ровно одна заявка невыполнима; причина совпадает с заявленной генератором."""
    for seed in range(1, 7):
        scn = generate("tiny", seed)
        note = next(n for n in scn.notes if n.startswith("Невыполнимая заявка"))
        oid = note.split("#")[1].split(":")[0]
        expected = ReasonCode(note.rsplit("— ", 1)[1].rstrip("."))
        for plan in (
            Solver().solve(scn.orders, scn.engineers),
            BaselineSolver().solve(scn.orders, scn.engineers),
        ):
            assert plan.unassigned_details[oid].code == expected


def test_validator_detects_corrupted_plans() -> None:
    """Проверка не пустая: порча расписания, пробега и учёта заявок находится."""
    scn = generate("city", 3, **SMALL)
    plan = Solver().solve(scn.orders, scn.engineers)
    route_idx = next(i for i, r in enumerate(plan.routes) if len(r.jobs) >= 2)

    def broken(mutate) -> list[str]:
        copy = plan.model_copy(deep=True)
        mutate(copy.routes[route_idx])
        return check_plan(copy, scn.orders, scn.engineers)

    assert broken(lambda r: setattr(r.jobs[1], "start_time_min", r.jobs[1].start_time_min + 1))
    assert broken(lambda r: setattr(r.jobs[1], "travel_dist_km", r.jobs[1].travel_dist_km + 0.3))
    assert broken(lambda r: r.jobs.pop(0))
    assert broken(lambda r: r.jobs.append(r.jobs[0].model_copy()))
    frozen = plan.model_copy(deep=True)
    job = frozen.routes[route_idx].jobs[0]
    job.is_frozen = True
    job.start_time_min += 5
    assert check_replan(plan, frozen, job.departure_time_min + 1)


@pytest.mark.parametrize("kind", ["city", "suburban"])
def test_replan_day_keeps_invariants(kind: str) -> None:
    scn = _small(kind, 1)
    plan = Solver().solve(scn.orders, scn.engineers)
    stats = run_replan(scn, plan, 6)
    assert stats.applied >= 5
    assert stats.errors == []
    assert stats.crashes == []
    assert stats.violations == []
    assert stats.policy == []
