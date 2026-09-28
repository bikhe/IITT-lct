"""LNS «удаление маршрута»: снять все заявки самой лёгкой бригады и раздать их остальным."""

from app.solver.fleet import Fleet
from app.solver.objective import CREW_PENALTY
from app.solver.search.insertion import insert_pool


def remove_routes(fleet: Fleet, *, crew_penalty: float = CREW_PENALTY) -> int:
    """Пробует освободить бригады по одной (от самых лёгких маршрутов). Возвращает, сколько освобождено.

    Ход принимается, только если все заявки маршрута удалось вставить в маршруты других уже
    задействованных бригад — так число бригад строго уменьшается, а назначенные не теряются.
    """
    ev = fleet.evaluator
    removed = 0
    candidates = sorted(
        (e.id for e in fleet.engineers if len(fleet.states[e.id]) > 0 and not fleet.pinned.get(e.id)),
        key=lambda eid: (len(fleet.states[eid]), fleet.states[eid].cost, eid),
    )
    for eid in candidates:
        st = fleet.states[eid]
        if len(st) == 0:
            continue
        snap = fleet.snapshot()
        pool = list(st.orders)
        fleet.states[eid] = ev.empty(st.engineer, st.start)
        left = insert_pool(
            fleet, pool, crew_penalty=crew_penalty, allow_activation=False, exclude={eid}
        )
        if left:
            fleet.restore(snap)
        else:
            removed += 1
    return removed
