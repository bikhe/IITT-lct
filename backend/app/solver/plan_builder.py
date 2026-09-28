"""Сборка итогового Plan из Fleet: замороженные работы + расписание хвоста, метрики, причины."""

from app.domain.enums import JobStatus
from app.domain.models import AssignedJob, Order, Plan, Route, UnassignedInfo
from app.solver.evaluator import Schedule
from app.solver.fleet import Fleet
from app.solver.metrics import calculate_plan_metrics


def materialize(schedule: Schedule) -> list[AssignedJob]:
    return [
        AssignedJob(
            order_id=s.order_id,
            departure_time_min=s.departure,
            arrival_time_min=s.arrival,
            start_time_min=s.start,
            end_time_min=s.end,
            travel_time_min=s.travel_min,
            travel_dist_km=s.dist_km,
            waiting_time_min=s.idle_min,
        )
        for s in schedule.stops
    ]


def route_from_jobs(
    engineer_id: str, jobs: list[AssignedJob], unavailable_from_min: int | None = None
) -> Route:
    return Route(
        engineer_id=engineer_id,
        jobs=jobs,
        total_distance_km=round(sum(j.travel_dist_km for j in jobs), 2),
        total_travel_time_min=sum(j.travel_time_min for j in jobs),
        total_work_time_min=sum(
            j.end_time_min - j.start_time_min for j in jobs if j.status != JobStatus.CANCELLED
        ),
        total_waiting_time_min=sum(j.waiting_time_min for j in jobs),
        unavailable_from_min=unavailable_from_min,
    )


def build_plan(
    fleet: Fleet,
    orders: list[Order],
    unassigned: dict[str, UnassignedInfo],
    *,
    prefixes: dict[str, list[AssignedJob]] | None = None,
    cancelled: dict[str, str] | None = None,
    unavailable_from: dict[str, int] | None = None,
    as_of_min: int | None = None,
    extra_crews_needed: int = 0,
) -> Plan:
    ev = fleet.evaluator
    prefixes = prefixes or {}
    unavailable_from = unavailable_from or {}
    routes: list[Route] = []
    for eng in fleet.engineers:
        st = fleet.states[eng.id]
        schedule = ev.schedule(eng, st.orders, st.start)
        if not schedule.feasible:
            raise ValueError(
                f"Недопустимый маршрут {eng.id}: {schedule.violation}"  # защита инварианта
            )
        jobs = list(prefixes.get(eng.id, [])) + materialize(schedule)
        routes.append(route_from_jobs(eng.id, jobs, unavailable_from.get(eng.id)))

    ordered_unassigned = {oid: unassigned[oid] for oid in sorted(unassigned)}
    metrics = calculate_plan_metrics(
        orders,
        fleet.engineers,
        routes,
        ordered_unassigned,
        cancelled,
        day_start_min=ev.day_start_min,
        extra_crews_needed=extra_crews_needed,
    )
    return Plan(
        routes=routes,
        unassigned_orders={oid: info.text for oid, info in ordered_unassigned.items()},
        unassigned_details=ordered_unassigned,
        cancelled_orders=dict(cancelled or {}),
        metrics=metrics,
        as_of_min=as_of_min,
    )
