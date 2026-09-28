"""Ограничения ТЗ §2.2 через единый RouteEvaluator: H1 навык, H2 окна и смена, H3 транспорт, H4 связность."""

import random

from app.domain.enums import ReasonCode, Skill, Transport
from app.solver.evaluator import RouteEvaluator, RouteStart
from tests.helpers import make_engineer, make_order


def test_h1_qualification() -> None:
    ev = RouteEvaluator()
    eng = make_engineer(skills=[Skill.LOCAL, Skill.CONNECTION])
    assert ev.compat(eng, make_order(skill=Skill.LOCAL)) is None
    assert ev.compat(eng, make_order("2", skill=Skill.EMERGENCY)) == ReasonCode.SKILL


def test_h3_transport() -> None:
    ev = RouteEvaluator()
    car = make_engineer(transport=Transport.CAR)
    assert ev.compat(car, make_order(transport=Transport.CAR)) is None
    assert ev.compat(car, make_order("2", transport=None)) is None
    assert ev.compat(car, make_order("3", transport=Transport.FOOT)) == ReasonCode.TRANSPORT


def test_h2_window_and_shift() -> None:
    ev = RouteEvaluator()
    eng = make_engineer(shift_start="10:00", shift_end="19:00")
    assert ev.compat(eng, make_order(start="12:00", end="14:00")) is None
    # окно после конца смены
    assert ev.compat(eng, make_order("2", start="20:00", end="22:00")) == ReasonCode.SHIFT_WINDOW
    # начать можно в окне, но работа не закончится до конца смены
    late = make_order("3", start="18:30", end="20:30", duration=60)
    assert ev.compat(eng, late) == ReasonCode.SHIFT_WINDOW


def test_work_starts_not_before_window_and_departure_is_just_in_time() -> None:
    ev = RouteEvaluator()
    eng = make_engineer()
    order = make_order(start="14:00", end="16:00", lat=55.75, lon=37.60)
    sched = ev.schedule(eng, [order])
    assert sched.feasible
    stop = sched.stops[0]
    assert stop.start == 14 * 60  # начать раньше окна нельзя
    assert stop.arrival == stop.start  # выезжает так, чтобы прибыть к началу окна
    assert stop.departure == stop.start - stop.travel_min
    assert stop.idle_min == stop.departure - 10 * 60  # ждёт в депо, а не у клиента


def test_h4_route_accounts_for_travel_between_orders() -> None:
    ev = RouteEvaluator()
    eng = make_engineer(transport=Transport.FOOT)
    near = make_order("a", start="10:00", end="10:30", duration=60, lat=55.70, lon=37.70)
    far = make_order("b", start="11:00", end="11:10", duration=30, lat=55.80, lon=37.80)
    sched = ev.schedule(eng, [near, far])
    assert not sched.feasible
    assert sched.violation is not None and sched.violation.code == ReasonCode.WINDOW_LATE
    assert ev.state(eng, [near, far]) is None
    assert ev.state(eng, [near]) is not None


def test_route_start_after_frozen_work() -> None:
    ev = RouteEvaluator()
    eng = make_engineer()
    order = make_order(start="10:00", end="12:00")
    start = RouteStart(eng.depot, 11 * 60 + 55)
    assert ev.compat(eng, order, start) is None
    assert ev.compat(eng, order, RouteStart(eng.depot, 12 * 60 + 5)) == ReasonCode.SHIFT_WINDOW
    assert ev.compat(eng, order, available=False) == ReasonCode.UNAVAILABLE


def test_fast_insertion_matches_full_evaluation() -> None:
    """Проверка вставки за O(1) (latest[]) и её стоимость (км, опоздание аварий, смена зон)
    обязаны совпадать с полным пересчётом маршрута."""
    rng = random.Random(7)
    ev = RouteEvaluator()
    for trial in range(300):
        eng = make_engineer(shift_start="10:00", shift_end=rng.choice(["17:00", "19:00", "22:00"]))
        orders = []
        for i in range(rng.randint(0, 6)):
            ws = rng.choice(range(10, 21, 2)) * 60
            orders.append(
                make_order(
                    f"{trial}-{i}",
                    skill=rng.choice([Skill.LOCAL, Skill.LOCAL, Skill.EMERGENCY]),
                    start=f"{ws // 60:02d}:00",
                    end=f"{ws // 60 + 2:02d}:00",
                    duration=rng.choice([20, 30, 70, 80]),
                    lat=55.6 + rng.random() * 0.2,
                    lon=37.5 + rng.random() * 0.3,
                    district=rng.choice(["Кузьминки", "Кузьминки", "Кашира"]),
                )
            )
        orders.sort(key=lambda o: o.window.start_min)
        state = ev.state(eng, orders)
        if state is None:
            continue
        ws = rng.choice(range(10, 21, 2)) * 60
        new = make_order(
            f"{trial}-new",
            skill=rng.choice([Skill.LOCAL, Skill.EMERGENCY]),
            district=rng.choice(["Кузьминки", "Кашира"]),
            start=f"{ws // 60:02d}:00",
            end=f"{ws // 60 + 2:02d}:00",
            duration=rng.choice([20, 30, 70]),
            lat=55.6 + rng.random() * 0.2,
            lon=37.5 + rng.random() * 0.3,
        )
        fast = {ins.pos: ins for ins in ev.insertions(state, new)}
        for pos in range(len(orders) + 1):
            full = ev.state(eng, orders[:pos] + [new] + orders[pos:])
            assert (pos in fast) == (full is not None), (trial, pos)
            if full is not None:
                assert abs(fast[pos].cost - (full.cost - state.cost)) < 1e-6
