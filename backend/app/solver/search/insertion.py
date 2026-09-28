"""Regret-2 вставка пула заявок в маршруты. Используется конструктором, доназначением, LNS и replan."""

import math

from app.domain.models import Order
from app.solver.evaluator import Insertion
from app.solver.fleet import Fleet
from app.solver.objective import CREW_PENALTY

SINGLE_OPTION_REGRET = 1e5  # заявку с единственным вариантом вставляем раньше прочих того же приоритета


def _option(
    fleet: Fleet,
    order: Order,
    eid: str,
    *,
    crew_penalty: float,
    allow_activation: bool,
    stability: float,
    exclude: frozenset[str],
) -> tuple[float, Insertion] | None:
    if eid in exclude or not fleet.available.get(eid, True):
        return None
    ev = fleet.evaluator
    state = fleet.states[eid]
    if ev.pair_reason(state.engineer, order) is not None:
        return None
    activation = 0.0
    if not fleet.is_active(eid):
        if not allow_activation:
            return None
        activation = crew_penalty
    best = ev.best_insertion(state, order, with_shift=stability > 0)
    if best is None:
        return None
    return best.cost + activation + stability * best.shift_min, best


def insert_pool(
    fleet: Fleet,
    pool: list[Order],
    *,
    crew_penalty: float = CREW_PENALTY,
    allow_activation: bool = True,
    stability: float = 0.0,
    exclude: frozenset[str] | set[str] = frozenset(),
) -> list[Order]:
    """Вставляет заявки пула по одной: сначала высший приоритет (авария → подключение → прочие),
    внутри приоритета — заявка с наибольшим regret (цена второго лучшего варианта минус лучшего).

    Меняет fleet на месте. Возвращает заявки, которые вставить не удалось (в исходном порядке).
    stability > 0 — штраф за сдвиг уже запланированных заявок (перепланирование).
    """
    exclude = frozenset(exclude)
    remaining: dict[str, Order] = {o.id: o for o in pool}
    eids = [e.id for e in fleet.engineers]
    kw = {
        "crew_penalty": crew_penalty,
        "allow_activation": allow_activation,
        "stability": stability,
        "exclude": exclude,
    }
    options: dict[str, dict[str, tuple[float, Insertion] | None]] = {
        oid: {eid: _option(fleet, o, eid, **kw) for eid in eids} for oid, o in remaining.items()
    }

    while remaining:
        pick: tuple[tuple, Order, str] | None = None
        for oid in sorted(remaining):
            order = remaining[oid]
            costs = sorted((v[0], eid) for eid, v in options[oid].items() if v is not None)
            if not costs:
                continue
            c1 = costs[0][0]
            regret = costs[1][0] - c1 if len(costs) > 1 else SINGLE_OPTION_REGRET
            key = (order.kind.priority_tier, -round(regret, 6), order.window.start_min, oid)
            if pick is None or key < pick[0]:
                pick = (key, order, costs[0][1])
        if pick is None:
            break

        _, order, eid = pick
        opt = options[order.id][eid]
        assert opt is not None and math.isfinite(opt[0])
        fleet.states[eid] = fleet.evaluator.insert(fleet.states[eid], order, opt[1].pos)
        del remaining[order.id]
        del options[order.id]
        for oid, o in remaining.items():
            options[oid][eid] = _option(fleet, o, eid, **kw)

    return [o for o in pool if o.id in remaining]
