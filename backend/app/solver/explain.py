"""Объяснения для диспетчера строятся по итоговому плану (после поиска и событий), а не по трейсу."""

from app.domain.enums import JobStatus, Transport
from app.domain.models import AssignedJob, Engineer, Location, Order, Plan, minutes_to_time
from app.geo.base import DistanceProvider
from app.geo.matrix import as_matrix
from app.solver.diagnose import crews_word, km_ru, plural
from app.solver.objective import SLA_REACTION_MIN, release_min, zone_of


def _leg_km(matrix, a: Location, b: Location, transport: Transport) -> float:
    return matrix.leg(matrix.node(a), matrix.node(b), transport)[0]


class ExplanationGenerator:
    """Короткие объяснения назначений, неназначений и маршрутов (ТЗ §2.1.7, §2.4.2)."""

    @staticmethod
    def explain_assignment(
        order: Order,
        engineer: Engineer,
        job: AssignedJob,
        position: int,
        route_len: int,
        added_km: float,
        prev_label: str,
        next_label: str | None,
        fitting_crews: int,
        day_start_min: int,
        as_of_min: int | None = None,
    ) -> str:
        skills = ", ".join(f"«{s.label_ru}»" for s in order.skills)
        if order.required_transport is not None:
            transport = f"{engineer.transport.label_ru.lower()} — как требует заявка"
        else:
            transport = f"{engineer.transport.label_ru.lower()} (заявка транспорт не ограничивает)"
        path = f"{prev_label} → #{order.id}" + (f" → {next_label}" if next_label else "")
        lines = [
            f"{order.kind.label_ru} #{order.id} → {engineer.name}, визит {position} из {route_len}.",
            f"Квалификация: нужен навык {skills} — у бригады есть.",
            f"Транспорт: {transport}.",
            (
                f"Время: выезд {job.departure_time}, начало {job.start_time} "
                f"(окно {order.window.start}–{order.window.end}), окончание {job.end_time}; "
                f"смена {engineer.shift.start}–{engineer.shift.end}."
            ),
            f"Маршрут: {path}; заявка добавляет {km_ru(added_km)}.",
        ]
        if order.is_emergency and job.status != JobStatus.CANCELLED:
            reaction = job.start_time_min - release_min(order, day_start_min)
            verdict = "в пределах ориентира 2 ч" if reaction <= SLA_REACTION_MIN else "сверх ориентира 2 ч"
            lines.append(f"Реакция на аварию: {reaction} мин от поступления — {verdict}.")
        if fitting_crews > 1:
            lines.append(f"Подходят по навыку и транспорту: {crews_word(fitting_crews)}.")
        if job.is_frozen and as_of_min is not None:
            lines.append(f"На {minutes_to_time(as_of_min)}: {job.status.label_ru.lower()}, не переносится.")
        return "\n".join(lines)

    @staticmethod
    def explain_unassigned(order: Order, reason: str, hint: str | None = None) -> str:
        lines = [
            (
                f"{order.kind.label_ru} #{order.id} (окно {order.window.start}–{order.window.end}, "
                f"{order.location.district}) не назначена."
            ),
            f"Причина: {reason}",
        ]
        if hint:
            lines.append(f"Что можно сделать: {hint}")
        return "\n".join(lines)

    @classmethod
    def enrich_plan_explanations(
        cls,
        plan: Plan,
        orders: list[Order],
        engineers: list[Engineer],
        distance_provider: DistanceProvider | None = None,
    ) -> dict[str, str]:
        """order_id -> объяснение для каждой заявки плана."""
        matrix = as_matrix(distance_provider)
        orders_map = {o.id: o for o in orders}
        eng_map = {e.id: e for e in engineers}
        day_start = min((e.shift.start_min for e in engineers), default=600)
        explanations: dict[str, str] = {}

        for route in plan.routes:
            eng = eng_map.get(route.engineer_id)
            if eng is None:
                continue
            jobs = route.jobs
            route_len = sum(1 for j in jobs if j.status != JobStatus.CANCELLED)
            visit = 0
            for idx, job in enumerate(jobs):
                order = orders_map.get(job.order_id)
                if order is None:
                    continue
                if job.status == JobStatus.CANCELLED:
                    when = (
                        "во время работ"
                        if job.end_time_min > job.start_time_min
                        else "когда бригада уже была в пути"
                    )
                    explanations[order.id] = (
                        f"Заявка #{order.id} ({order.kind.label_ru.lower()}) отменена клиентом "
                        f"{when} ({eng.name}). Бригада свободна с {job.end_time} в этой точке."
                    )
                    continue
                visit += 1
                prev_loc = eng.depot if idx == 0 else orders_map[jobs[idx - 1].order_id].location
                prev_label = "старт" if idx == 0 else f"#{jobs[idx - 1].order_id}"
                next_order = orders_map.get(jobs[idx + 1].order_id) if idx + 1 < len(jobs) else None
                added = _leg_km(matrix, prev_loc, order.location, eng.transport)
                if next_order is not None:
                    added += _leg_km(matrix, order.location, next_order.location, eng.transport)
                    added -= _leg_km(matrix, prev_loc, next_order.location, eng.transport)
                fitting = sum(
                    1
                    for e in engineers
                    if all(s in e.skills for s in order.skills)
                    and (order.required_transport is None or order.required_transport == e.transport)
                )
                explanations[order.id] = cls.explain_assignment(
                    order,
                    eng,
                    job,
                    visit,
                    route_len,
                    max(0.0, added),
                    prev_label,
                    f"#{next_order.id}" if next_order else None,
                    fitting,
                    day_start,
                    plan.as_of_min,
                )

        for order_id, reason in plan.unassigned_orders.items():
            order = orders_map.get(order_id)
            details = plan.unassigned_details.get(order_id)
            hint = details.hint if details else None
            if order is not None:
                explanations[order_id] = cls.explain_unassigned(order, reason, hint)
            else:
                explanations[order_id] = f"Заявка #{order_id} не назначена: {reason}"

        for order_id, text in plan.cancelled_orders.items():
            explanations.setdefault(order_id, f"Заявка #{order_id}: {text}")

        return explanations

    @staticmethod
    def route_explanations(
        plan: Plan, orders: list[Order], engineers: list[Engineer]
    ) -> dict[str, str]:
        """engineer_id -> одна-две строки о маршруте бригады."""
        orders_map = {o.id: o for o in orders}
        out: dict[str, str] = {}
        for route in plan.routes:
            eng = next((e for e in engineers if e.id == route.engineer_id), None)
            if eng is None:
                continue
            if route.unavailable_from_min is not None:
                prefix = f"Сошла с линии в {minutes_to_time(route.unavailable_from_min)}. "
            else:
                prefix = ""
            done = [j for j in route.jobs if j.status != JobStatus.CANCELLED]
            if not done:
                out[eng.id] = prefix + "Не задействована: заявок нет, на линию не выходит."
                continue
            shift_len = max(1, eng.shift.end_min - eng.shift.start_min)
            load = round(100 * (route.total_work_time_min + route.total_travel_time_min) / shift_len)
            districts: list[str] = []
            zones: list[str] = []
            for j in done:
                d = orders_map[j.order_id].location.district
                if d not in districts:
                    districts.append(d)
                z = zone_of(d)
                if not zones or zones[-1] != z:
                    zones.append(z)
            text = (
                f"{prefix}{len(done)} {plural(len(done), 'заявка', 'заявки', 'заявок')}, "
                f"{km_ru(route.total_distance_km)}, в пути {route.total_travel_time_min} мин; "
                f"смена {eng.shift.start}–{eng.shift.end}, загрузка {load} %. "
                f"Районы: {', '.join(districts[:4])}{' и др.' if len(districts) > 4 else ''}."
            )
            if len(zones) > 1:
                text += f" Переезды между зонами: {' → '.join(zones)}."
            out[eng.id] = text
        return out
