"""Фабрики тестовых данных и независимая проверка плана (не использует RouteEvaluator)."""

from pathlib import Path

import pytest

from app.domain.enums import JobStatus, Priority, Skill, Transport
from app.domain.models import Engineer, Location, Order, Plan, TimeWindow
from app.geo.haversine import HaversineDistanceProvider

REPO_ROOT = Path(__file__).resolve().parents[2]
RAW_DIR = REPO_ROOT / "docs" / "Датасет"
REGIONS = ("east", "southeast", "southcenter")

needs_raw_csv = pytest.mark.skipif(
    not RAW_DIR.exists(), reason="Исходные CSV не публикуются; тест запускается локально"
)


def make_order(
    order_id: str = "1",
    skill: Skill = Skill.LOCAL,
    start: str = "10:00",
    end: str = "12:00",
    duration: int = 60,
    transport: Transport | None = None,
    lat: float = 55.70,
    lon: float = 37.70,
    district: str = "Кузьминки",
    priority: Priority = Priority.NORMAL,
) -> Order:
    return Order(
        id=order_id,
        skills=[skill],
        priority=priority,
        window=TimeWindow(start=start, end=end),
        duration_min=duration,
        required_transport=transport,
        location=Location(lat=lat, lon=lon, address=f"адрес {order_id}", district=district),
    )


def make_engineer(
    eng_id: str = "e1",
    name: str = "Бригада Тест",
    skills: list[Skill] | None = None,
    transport: Transport = Transport.CAR,
    shift_start: str = "10:00",
    shift_end: str = "19:00",
    lat: float = 55.70,
    lon: float = 37.70,
) -> Engineer:
    return Engineer(
        id=eng_id,
        name=name,
        skills=skills or [Skill.LOCAL],
        transport=transport,
        shift=TimeWindow(start=shift_start, end=shift_end),
        depot=Location(lat=lat, lon=lon, address="депо", district="Кузьминки"),
    )


def assert_plan_valid(plan: Plan, orders: list[Order], engineers: list[Engineer]) -> None:
    """Все обязательные ограничения ТЗ §2.2 плюс учёт каждой заявки ровно один раз."""
    provider = HaversineDistanceProvider()
    orders_map = {o.id: o for o in orders}
    eng_map = {e.id: e for e in engineers}
    state: dict[str, str] = {}

    for route in plan.routes:
        eng = eng_map[route.engineer_id]
        ready = eng.shift.start_min
        loc = eng.depot
        for job in route.jobs:
            order = orders_map[job.order_id]
            assert job.order_id not in state, f"заявка {job.order_id} назначена дважды"
            state[job.order_id] = "cancelled" if job.status == JobStatus.CANCELLED else "assigned"
            km, minutes = provider.get_distance_and_time(loc, order.location, eng.transport)
            assert abs(km - job.travel_dist_km) < 1e-9 and minutes == job.travel_time_min
            assert job.departure_time_min >= ready, f"{job.order_id}: выезд раньше готовности"
            assert job.arrival_time_min == job.departure_time_min + minutes
            if job.status != JobStatus.CANCELLED:
                assert all(s in eng.skills for s in order.skills), f"{job.order_id}: навык"
                assert order.required_transport in (None, eng.transport), f"{job.order_id}: транспорт"
                assert job.start_time_min >= job.arrival_time_min
                assert order.window.start_min <= job.start_time_min <= order.window.end_min
                assert job.end_time_min == job.start_time_min + order.duration_min
                assert job.end_time_min <= eng.shift.end_min, f"{job.order_id}: после смены"
            ready = job.end_time_min
            loc = order.location

    for oid, reason in plan.unassigned_orders.items():
        assert oid not in state, f"заявка {oid} и назначена, и не назначена"
        assert reason, f"у неназначенной заявки {oid} нет причины"
        state[oid] = "unassigned"
    for oid in plan.cancelled_orders:
        assert state.get(oid, "cancelled") == "cancelled", f"отменённая {oid} в маршруте"
        state[oid] = "cancelled"

    assert set(state) == set(orders_map), "каждая заявка должна быть ровно в одном состоянии"
    m = plan.metrics
    assert m.assigned_orders + m.unassigned_orders + m.cancelled_orders == m.total_orders
