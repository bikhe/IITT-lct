"""Состояние бригад на момент события: что заморожено, откуда и когда можно продолжать маршрут."""

from dataclasses import dataclass, field

from app.domain.enums import JobStatus
from app.domain.models import AssignedJob, Engineer, Order, Plan
from app.solver.evaluator import RouteEvaluator, RouteStart
from app.solver.fleet import Fleet


def job_status_at(job: AssignedJob, t: int) -> JobStatus:
    if job.status == JobStatus.CANCELLED:
        return JobStatus.CANCELLED
    if job.end_time_min <= t:
        return JobStatus.DONE
    if job.start_time_min <= t:
        return JobStatus.IN_PROGRESS
    return JobStatus.EN_ROUTE


@dataclass
class DayState:
    """Разрез плана в момент t.

    prefixes — замороженные работы: выполнены, идут или бригада уже выехала к клиенту
    (выезд <= t). Их не переносим: начатую работу не прерываем, бригаду в пути не разворачиваем.
    tails — заявки, к которым бригада ещё не выехала; их можно перестраивать.
    """

    t: int
    prefixes: dict[str, list[AssignedJob]]
    tails: dict[str, list[Order]]
    starts: dict[str, RouteStart]
    available: dict[str, bool]
    unavailable_from: dict[str, int] = field(default_factory=dict)

    def start_after(self, engineer: Engineer, orders_map: dict[str, Order]) -> RouteStart:
        """Точка и время, с которых бригада свободна после своих замороженных работ."""
        prefix = self.prefixes.get(engineer.id, [])
        if prefix:
            last = prefix[-1]
            return RouteStart(orders_map[last.order_id].location, max(self.t, last.end_time_min))
        return RouteStart(engineer.depot, max(self.t, engineer.shift.start_min))

    def frozen_count(self) -> int:
        return sum(len(p) for p in self.prefixes.values())


def state_at(
    plan: Plan, t: int, orders_map: dict[str, Order], engineers: list[Engineer]
) -> DayState:
    prefixes: dict[str, list[AssignedJob]] = {}
    tails: dict[str, list[Order]] = {}
    available: dict[str, bool] = {}
    unavailable_from: dict[str, int] = {}
    routes = {r.engineer_id: r for r in plan.routes}

    for eng in engineers:
        route = routes.get(eng.id)
        prefix: list[AssignedJob] = []
        tail: list[Order] = []
        for job in route.jobs if route else []:
            departure = job.departure_time_min
            if departure is None:
                departure = job.arrival_time_min - job.travel_time_min
            if not tail and (job.is_frozen or departure <= t):
                prefix.append(
                    job.model_copy(update={"is_frozen": True, "status": job_status_at(job, t)})
                )
            else:
                tail.append(orders_map[job.order_id])
        prefixes[eng.id] = prefix
        tails[eng.id] = tail
        is_available = route is None or route.unavailable_from_min is None
        available[eng.id] = is_available
        if not is_available and route is not None and route.unavailable_from_min is not None:
            unavailable_from[eng.id] = route.unavailable_from_min

    ds = DayState(t, prefixes, tails, {}, available, unavailable_from)
    ds.starts = {e.id: ds.start_after(e, orders_map) for e in engineers}
    return ds


def fleet_from_state(
    ev: RouteEvaluator, engineers: list[Engineer], ds: DayState
) -> tuple[Fleet, list[Order]]:
    """Строит Fleet по хвостам. Если хвост стал недопустим, сохраняет допустимую часть,
    а остальное возвращает в пул — маршрут никогда не обнуляется молча."""
    fleet = Fleet.empty(
        ev,
        engineers,
        starts=ds.starts,
        pinned={eid: bool(p) for eid, p in ds.prefixes.items()},
        available=ds.available,
    )
    dropped: list[Order] = []
    for eng in engineers:
        tail = ds.tails.get(eng.id, [])
        if not tail:
            continue
        start = ds.starts[eng.id]
        state = ev.state(eng, tail, start)
        if state is None:
            kept: list[Order] = []
            for order in tail:
                if ev.state(eng, kept + [order], start) is None:
                    dropped.append(order)
                else:
                    kept.append(order)
            state = ev.state(eng, kept, start)
            assert state is not None
        fleet.states[eng.id] = state
    return fleet, dropped
