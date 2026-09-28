"""Локальный поиск по маршрутам Fleet: relocate, swap, 2-opt, or-opt.

Каждый ход оценивается только по двум изменённым маршрутам (вставка — за O(1) через RouteEvaluator),
а принимается, если строго улучшает (число бригад, стоимость маршрутов). Новые бригады не выводятся.
Ограничение — число проходов, а не время: результат детерминирован на любой машине.
"""

from app.domain.models import Order
from app.solver.evaluator import RouteState
from app.solver.fleet import Fleet

EPS = 1e-6


def _improves(crews_delta: int, cost_delta: float) -> bool:
    return crews_delta < 0 or (crews_delta == 0 and cost_delta < -EPS)


def _targets(fleet: Fleet, src: str, orders: tuple[Order, ...]) -> list[str]:
    """Бригады, в маршрут которых можно перенести заявки (без вывода новых бригад)."""
    ev = fleet.evaluator
    out = []
    for e in fleet.engineers:
        eid = e.id
        if eid == src:
            out.append(eid)
            continue
        if not fleet.available.get(eid, True) or not fleet.is_active(eid):
            continue
        if all(ev.pair_reason(e, o) is None for o in orders):
            out.append(eid)
    return out


def _relocate_pass(fleet: Fleet) -> int:
    """Перенос одной заявки в лучшую позицию любого маршрута (включая свой)."""
    ev = fleet.evaluator
    moves = 0
    for src_eng in fleet.engineers:
        src = src_eng.id
        i = 0
        while i < len(fleet.states[src]):
            st = fleet.states[src]
            order = st.stops[i].order
            rem = ev.without(st, i)
            if rem is None:
                i += 1
                continue
            crew_gain = 1 if len(rem) == 0 and not fleet.pinned.get(src, False) else 0
            gain = st.cost - rem.cost
            best: tuple[int, float, str, int] | None = None
            for dst in _targets(fleet, src, (order,)):
                target = rem if dst == src else fleet.states[dst]
                ins = ev.best_insertion(target, order)
                if ins is None:
                    continue
                cand = (0 if dst == src else -crew_gain, ins.cost - gain)
                if _improves(*cand) and (best is None or cand < best[:2]):
                    best = (cand[0], cand[1], dst, ins.pos)
            if best is None:
                i += 1
                continue
            _, _, dst, pos = best
            if dst == src:
                fleet.states[src] = ev.insert(rem, order, pos)
            else:
                fleet.states[src] = rem
                fleet.states[dst] = ev.insert(fleet.states[dst], order, pos)
            moves += 1
    return moves


def _or_opt_pass(fleet: Fleet, lengths: tuple[int, ...] = (2, 3)) -> int:
    """Перенос цепочки из 2–3 подряд идущих заявок в другое место (свой или чужой маршрут)."""
    ev = fleet.evaluator
    moves = 0
    for src_eng in fleet.engineers:
        src = src_eng.id
        for length in lengths:
            i = 0
            while i + length <= len(fleet.states[src]):
                st = fleet.states[src]
                seg = st.orders[i : i + length]
                rem = ev.without(st, i, length)
                if rem is None:
                    i += 1
                    continue
                crew_gain = 1 if len(rem) == 0 and not fleet.pinned.get(src, False) else 0
                gain = st.cost - rem.cost
                best: tuple[int, float, str, RouteState] | None = None
                for dst in _targets(fleet, src, seg):
                    target = rem if dst == src else fleet.states[dst]
                    t_orders = target.orders
                    for pos in range(len(t_orders) + 1):
                        if dst == src and pos == i:
                            continue
                        new = ev.with_orders(target, t_orders[:pos] + seg + t_orders[pos:])
                        if new is None:
                            continue
                        cand = (0 if dst == src else -crew_gain, new.cost - target.cost - gain)
                        if _improves(*cand) and (best is None or cand < best[:2]):
                            best = (cand[0], cand[1], dst, new)
                if best is None:
                    i += 1
                    continue
                _, _, dst, new = best
                if dst != src:
                    fleet.states[src] = rem
                fleet.states[dst] = new
                moves += 1
    return moves


def _swap_pass(fleet: Fleet) -> int:
    """Обмен двумя заявками между маршрутами (каждая встаёт на место другой)."""
    ev = fleet.evaluator
    moves = 0
    eids = [e.id for e in fleet.engineers if fleet.is_active(e.id) and fleet.available.get(e.id, True)]
    for a_idx, a in enumerate(eids):
        for b in eids[a_idx + 1 :]:
            improved = True
            while improved:
                improved = False
                sa, sb = fleet.states[a], fleet.states[b]
                ao, bo = sa.orders, sb.orders
                for i, oi in enumerate(ao):
                    if ev.pair_reason(sb.engineer, oi) is not None:
                        continue
                    for j, oj in enumerate(bo):
                        if ev.pair_reason(sa.engineer, oj) is not None:
                            continue
                        new_a = ev.with_orders(sa, ao[:i] + (oj,) + ao[i + 1 :])
                        if new_a is None:
                            continue
                        new_b = ev.with_orders(sb, bo[:j] + (oi,) + bo[j + 1 :])
                        if new_b is None:
                            continue
                        if new_a.cost + new_b.cost < sa.cost + sb.cost - EPS:
                            fleet.states[a], fleet.states[b] = new_a, new_b
                            moves += 1
                            improved = True
                            break
                    if improved:
                        break
    return moves


def _two_opt_pass(fleet: Fleet) -> int:
    """Разворот участка внутри маршрута."""
    ev = fleet.evaluator
    moves = 0
    for eng in fleet.engineers:
        eid = eng.id
        improved = True
        while improved:
            improved = False
            st = fleet.states[eid]
            orders = st.orders
            n = len(orders)
            for i in range(n - 1):
                for j in range(i + 1, n):
                    new = ev.with_orders(st, orders[:i] + orders[i : j + 1][::-1] + orders[j + 1 :])
                    if new is not None and new.cost < st.cost - EPS:
                        fleet.states[eid] = new
                        moves += 1
                        improved = True
                        break
                if improved:
                    break
    return moves


def improve(fleet: Fleet, *, max_rounds: int = 60) -> int:
    """Применяет ходы, пока они улучшают план. Возвращает число принятых ходов."""
    total = 0
    for _ in range(max_rounds):
        moves = _relocate_pass(fleet)
        moves += _swap_pass(fleet)
        moves += _or_opt_pass(fleet)
        moves += _two_opt_pass(fleet)
        total += moves
        if moves == 0:
            break
    return total
