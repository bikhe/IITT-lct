"""Regret-2 вставка пула заявок в маршруты. Используется конструктором, доназначением, LNS и replan."""

import math

from app.domain.models import Order
from app.solver.evaluator import Insertion
from app.solver.fleet import Fleet
from app.solver.objective import CREW_PENALTY, activation_bias

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
        activation = crew_penalty + activation_bias(state.engineer)
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
    top = {oid: _top2(opts) for oid, opts in options.items()}

    ids = sorted(remaining)
    while remaining:
        pick: tuple[tuple, Order, str] | None = None
        for oid in ids:
            order = remaining[oid]
            first, second = top[oid]
            if first is None:
                continue
            regret = second[0] - first[0] if second is not None else SINGLE_OPTION_REGRET
            key = (order.kind.priority_tier, -round(regret, 6), order.window.start_min, oid)
            if pick is None or key < pick[0]:
                pick = (key, order, first[1])
        if pick is None:
            break

        _, order, eid = pick
        opt = options[order.id][eid]
        assert opt is not None and math.isfinite(opt[0])
        fleet.states[eid] = fleet.evaluator.insert(fleet.states[eid], order, opt[1].pos)
        del remaining[order.id]
        del options[order.id]
        del top[order.id]
        ids.remove(order.id)
        for oid, o in remaining.items():
            v = _option(fleet, o, eid, **kw)
            options[oid][eid] = v
            first, second = top[oid]
            if (first is not None and first[1] == eid) or (second is not None and second[1] == eid):
                top[oid] = _top2(options[oid])  # вариант этой бригады был среди двух лучших
            elif v is not None:
                item = (v[0], eid)
                if first is None or item < first:
                    top[oid] = (item, first)
                elif second is None or item < second:
                    top[oid] = (first, item)

    return [o for o in pool if o.id in remaining]


Top2 = tuple[tuple[float, str] | None, tuple[float, str] | None]


def _top2(opts: dict[str, tuple[float, Insertion] | None]) -> Top2:
    """Два лучших варианта по (цена, бригада) — как первые два элемента sorted()."""
    first: tuple[float, str] | None = None
    second: tuple[float, str] | None = None
    for eid, v in opts.items():
        if v is None:
            continue
        item = (v[0], eid)
        if first is None or item < first:
            first, second = item, first
        elif second is None or item < second:
            second = item
    return first, second
