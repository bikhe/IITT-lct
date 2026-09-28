"""Что изменилось в плане после события: по заявкам и по бригадам."""

from app.domain.enums import JobStatus
from app.domain.events import ChangeStatus, EngineerKm, PlanDiff, ReassignedJob, ReplanEvent
from app.domain.models import Engineer, Plan

_Where = tuple[str, str, int] | str  # (engineer_id, "HH:MM", start_min) | "unassigned" | "cancelled"


def _locate(plan: Plan) -> dict[str, _Where]:
    where: dict[str, _Where] = {}
    for route in plan.routes:
        for job in route.jobs:
            if job.status == JobStatus.CANCELLED:
                where[job.order_id] = "cancelled"
            else:
                where[job.order_id] = (route.engineer_id, job.start_time, job.start_time_min)
    for oid in plan.unassigned_orders:
        where[oid] = "unassigned"
    for oid in plan.cancelled_orders:
        where[oid] = "cancelled"
    return where


def compute_diff(
    old: Plan,
    new: Plan,
    event: ReplanEvent,
    engineers: list[Engineer],
    *,
    frozen_count: int,
    summary: str,
) -> PlanDiff:
    names = {e.id: e.name for e in engineers}
    before, after = _locate(old), _locate(new)
    changes: list[ReassignedJob] = []

    for oid in sorted(after):
        now = after[oid]
        was = before.get(oid)
        if was == now:
            continue
        change = ReassignedJob(order_id=oid)
        if isinstance(was, tuple):
            change.old_engineer_id, change.old_start_time = was[0], was[1]
            change.old_engineer_name = names.get(was[0])
        if isinstance(now, tuple):
            change.new_engineer_id, change.new_start_time = now[0], now[1]
            change.new_engineer_name = names.get(now[0])

        if now == "cancelled":
            change.status = ChangeStatus.CANCELLED
        elif now == "unassigned":
            if was == "unassigned":
                continue
            change.status = ChangeStatus.UNASSIGNED
            change.reason = new.unassigned_orders.get(oid)
        elif was is None:
            change.status = ChangeStatus.ADDED
        elif was == "unassigned":
            change.status = ChangeStatus.ASSIGNED
        elif isinstance(was, tuple) and isinstance(now, tuple):
            if was[0] != now[0]:
                change.status = ChangeStatus.REASSIGNED
            else:
                change.status = ChangeStatus.SHIFTED
            change.shift_min = now[2] - was[2]
        changes.append(change)

    order_rank = {
        ChangeStatus.ADDED: 0,
        ChangeStatus.CANCELLED: 1,
        ChangeStatus.UNASSIGNED: 2,
        ChangeStatus.REASSIGNED: 3,
        ChangeStatus.ASSIGNED: 4,
        ChangeStatus.SHIFTED: 5,
    }
    changes.sort(key=lambda c: (order_rank[c.status], c.order_id))

    old_routes = {r.engineer_id: r for r in old.routes}
    km_rows: list[EngineerKm] = []
    called_in: list[str] = []
    for r in new.routes:
        o = old_routes.get(r.engineer_id)
        km_before = o.total_distance_km if o else 0.0
        n_before = sum(1 for j in o.jobs if j.status != JobStatus.CANCELLED) if o else 0
        n_after = sum(1 for j in r.jobs if j.status != JobStatus.CANCELLED)
        if abs(km_before - r.total_distance_km) > 0.005 or n_before != n_after:
            km_rows.append(
                EngineerKm(
                    engineer_id=r.engineer_id,
                    engineer_name=names.get(r.engineer_id, r.engineer_id),
                    km_before=km_before,
                    km_after=r.total_distance_km,
                    orders_before=n_before,
                    orders_after=n_after,
                )
            )
        if (o is None or not o.is_active) and r.is_active:
            called_in.append(r.engineer_id)

    om, nm = old.metrics, new.metrics
    return PlanDiff(
        event=event,
        added_order_ids=[c.order_id for c in changes if c.status == ChangeStatus.ADDED],
        cancelled_order_ids=[c.order_id for c in changes if c.status == ChangeStatus.CANCELLED],
        changes=changes,
        reassigned_orders=[
            c for c in changes if c.status in (ChangeStatus.REASSIGNED, ChangeStatus.SHIFTED)
        ],
        unassigned_after_event=[
            {"order_id": c.order_id, "reason": c.reason or ""}
            for c in changes
            if c.status == ChangeStatus.UNASSIGNED
        ],
        called_in_engineer_ids=called_in,
        engineer_km=km_rows,
        frozen_jobs_count=frozen_count,
        metrics_delta={
            "assigned_delta": nm.assigned_orders - om.assigned_orders,
            "unassigned_delta": nm.unassigned_orders - om.unassigned_orders,
            "cancelled_delta": nm.cancelled_orders - om.cancelled_orders,
            "distance_km_delta": round(nm.total_distance_km - om.total_distance_km, 2),
            "travel_time_delta_min": nm.total_travel_time_min - om.total_travel_time_min,
            "active_crews_delta": nm.active_engineers_count - om.active_engineers_count,
        },
        summary_ru=summary,
    )
