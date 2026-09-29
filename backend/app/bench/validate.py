"""Независимая проверка планов стенда: без RouteEvaluator и TravelMatrix.

Каждый отрезок пути пересчитывается напрямую через HaversineDistanceProvider, поэтому ошибка
в матрице, оценщике или сборке плана не может подтвердить сама себя. Все функции возвращают
список нарушений человеческим языком; пустой список — план корректен.
"""

from app.domain.enums import JobStatus, ReasonCode
from app.domain.events import ChangeStatus, PlanDiff
from app.domain.models import Engineer, Order, Plan, minutes_to_time
from app.geo.base import DistanceProvider
from app.geo.haversine import HaversineDistanceProvider
from app.solver.explain import ExplanationGenerator

EXPLAIN_ERRORS = (ValueError, KeyError, IndexError, TypeError, AttributeError, AssertionError)

KM_EPS = 1e-6  # пробег отрезка: точное совпадение с провайдером
TOTAL_KM_EPS = 0.011  # итоги округлены до 0,01 км


def _hhmm(minute: int | None) -> str:
    return "—" if minute is None else minutes_to_time(minute)


def solo_reason(
    order: Order, engineers: list[Engineer], provider: DistanceProvider | None = None
) -> ReasonCode | None:
    """Почему заявку не возьмёт ни одна бригада даже с пустым маршрутом; None — кто-то может.

    Порядок проверок как у причин для диспетчера: навык → транспорт → окно и смена с учётом дороги
    от стартовой точки бригады в начале смены.
    """
    provider = provider or HaversineDistanceProvider()
    skilled = [e for e in engineers if all(s in e.skills for s in order.skills)]
    if not skilled:
        return ReasonCode.SKILL
    carried = [e for e in skilled if order.required_transport in (None, e.transport)]
    if not carried:
        return ReasonCode.TRANSPORT
    for eng in carried:
        _, minutes = provider.get_distance_and_time(eng.depot, order.location, eng.transport)
        start = max(eng.shift.start_min + minutes, order.window.start_min)
        if start <= order.window.end_min and start + order.duration_min <= eng.shift.end_min:
            return None
    return ReasonCode.SHIFT_WINDOW


def check_plan(
    plan: Plan,
    orders: list[Order],
    engineers: list[Engineer],
    *,
    provider: DistanceProvider | None = None,
) -> list[str]:
    """Ограничения ТЗ §2.2, учёт каждой заявки ровно один раз и согласованность метрик."""
    provider = provider or HaversineDistanceProvider()
    out: list[str] = []
    by_id = {o.id: o for o in orders}
    if len(by_id) != len(orders):
        out.append("во входных данных повторяются ID заявок")
    eng_map = {e.id: e for e in engineers}
    seen_routes: set[str] = set()
    assigned: dict[str, str] = {}  # заявка -> бригада (выполняется или выполнена)
    cancelled_jobs: dict[str, str] = {}  # заявка -> бригада (отменена на месте или в пути)
    jobs_km = 0.0
    jobs_min = 0

    for route in plan.routes:
        eid = route.engineer_id
        eng = eng_map.get(eid)
        if eng is None:
            out.append(f"маршрут неизвестной бригады {eid}")
            continue
        if eid in seen_routes:
            out.append(f"у бригады {eid} больше одного маршрута")
        seen_routes.add(eid)
        ready, loc = eng.shift.start_min, eng.depot
        route_km, route_min = 0.0, 0
        for job in route.jobs:
            oid = job.order_id
            where = f"{eid}/#{oid}"
            order = by_id.get(oid)
            if order is None:
                out.append(f"{where}: заявки нет во входных данных")
                continue
            if oid in assigned or oid in cancelled_jobs:
                out.append(f"{where}: заявка стоит в маршрутах больше одного раза")
            is_cancel = job.status == JobStatus.CANCELLED
            (cancelled_jobs if is_cancel else assigned)[oid] = eid

            km, minutes = provider.get_distance_and_time(loc, order.location, eng.transport)
            if abs(km - job.travel_dist_km) > KM_EPS:
                out.append(f"{where}: пробег отрезка {job.travel_dist_km} км, по расчёту {km} км")
            if minutes != job.travel_time_min:
                out.append(f"{where}: в пути {job.travel_time_min} мин, по расчёту {minutes} мин")
            dep = job.departure_time_min
            if dep is None:
                out.append(f"{where}: нет времени выезда")
                dep = job.arrival_time_min - job.travel_time_min
            if dep < ready:
                out.append(f"{where}: выезд {_hhmm(dep)} раньше готовности {_hhmm(ready)}")
            if job.arrival_time_min != dep + job.travel_time_min:
                out.append(
                    f"{where}: прибытие {_hhmm(job.arrival_time_min)} ≠ выезд {_hhmm(dep)} "
                    f"+ {job.travel_time_min} мин"
                )
            if job.start_time_min < job.arrival_time_min:
                out.append(f"{where}: начало работ раньше прибытия")
            if not is_cancel:
                missing = [s.value for s in order.skills if s not in eng.skills]
                if missing:
                    out.append(f"{where}: у бригады нет навыка {', '.join(missing)}")
                if order.required_transport not in (None, eng.transport):
                    out.append(
                        f"{where}: заявка требует {order.required_transport.value}, "
                        f"у бригады {eng.transport.value}"
                    )
                if not order.window.start_min <= job.start_time_min <= order.window.end_min:
                    out.append(
                        f"{where}: начало {_hhmm(job.start_time_min)} вне окна "
                        f"{order.window.start}–{order.window.end}"
                    )
                if job.end_time_min != job.start_time_min + order.duration_min:
                    out.append(
                        f"{where}: длительность {job.end_time_min - job.start_time_min} мин "
                        f"вместо {order.duration_min}"
                    )
                if job.end_time_min > eng.shift.end_min:
                    out.append(
                        f"{where}: окончание {_hhmm(job.end_time_min)} после конца смены "
                        f"{eng.shift.end}"
                    )
                off = route.unavailable_from_min
                if off is not None and job.start_time_min > off:
                    out.append(f"{where}: работа начата после схода бригады в {_hhmm(off)}")
            else:
                if (
                    not job.start_time_min
                    <= job.end_time_min
                    <= job.start_time_min + order.duration_min
                ):
                    out.append(f"{where}: у отменённой работы некорректные начало и конец")
                if job.end_time_min > eng.shift.end_min:
                    out.append(f"{where}: отменённая работа заканчивается после смены")
                if oid not in plan.cancelled_orders:
                    out.append(f"{where}: работа отменена, но заявки нет в списке отменённых")
            route_km += job.travel_dist_km
            route_min += job.travel_time_min
            ready, loc = job.end_time_min, order.location
        if abs(route.total_distance_km - round(route_km, 2)) > TOTAL_KM_EPS:
            out.append(
                f"{eid}: пробег маршрута {route.total_distance_km} км, сумма отрезков "
                f"{round(route_km, 2)} км"
            )
        if route.total_travel_time_min != route_min:
            out.append(f"{eid}: время в пути маршрута не равно сумме отрезков")
        jobs_km += route_km
        jobs_min += route_min

    for oid, reason in plan.unassigned_orders.items():
        if oid not in by_id:
            out.append(f"#{oid}: неназначенной заявки нет во входных данных")
        if oid in assigned:
            out.append(f"#{oid}: одновременно назначена ({assigned[oid]}) и не назначена")
        if oid in plan.cancelled_orders:
            out.append(f"#{oid}: одновременно отменена и не назначена")
        if not (reason or "").strip():
            out.append(f"#{oid}: нет причины неназначения")
        info = plan.unassigned_details.get(oid)
        if info is None or not info.text.strip():
            out.append(f"#{oid}: нет подробной причины неназначения")
    extra_details = sorted(set(plan.unassigned_details) - set(plan.unassigned_orders))
    if extra_details:
        out.append(f"причины есть у заявок не из списка неназначенных: {extra_details[:5]}")
    for oid in plan.cancelled_orders:
        if oid not in by_id:
            out.append(f"#{oid}: отменённой заявки нет во входных данных")
        if oid in assigned:
            out.append(f"#{oid}: отменена, но стоит в маршруте как выполняемая")

    known = set(assigned) | set(plan.unassigned_orders) | set(plan.cancelled_orders)
    lost = sorted(set(by_id) - known)
    if lost:
        out.append(f"заявки без состояния ({len(lost)}): {lost[:10]}")

    m = plan.metrics
    active = sum(1 for r in plan.routes if r.jobs)
    expected = {
        "всего заявок": (m.total_orders, len(orders)),
        "назначено": (m.assigned_orders, len(assigned)),
        "не назначено": (m.unassigned_orders, len(plan.unassigned_orders)),
        "отменено": (m.cancelled_orders, len(plan.cancelled_orders)),
        "задействовано бригад": (m.active_engineers_count, active),
        "бригад всего": (m.total_engineers_count, len(engineers)),
        "время в пути": (m.total_travel_time_min, jobs_min),
    }
    for label, (got, want) in expected.items():
        if got != want:
            out.append(f"метрика «{label}»: {got}, по плану {want}")
    if m.assigned_orders + m.unassigned_orders + m.cancelled_orders != m.total_orders:
        out.append("метрики: назначено + не назначено + отменено ≠ всего заявок")
    routes_km = round(sum(r.total_distance_km for r in plan.routes), 2)
    if abs(m.total_distance_km - routes_km) > TOTAL_KM_EPS:
        out.append(f"метрика пробега {m.total_distance_km} км ≠ сумме маршрутов {routes_km} км")
    if abs(m.total_distance_km - round(jobs_km, 2)) > TOTAL_KM_EPS * max(1, len(plan.routes)):
        out.append(f"метрика пробега {m.total_distance_km} км ≠ сумме отрезков {jobs_km:.2f} км")
    return out


def check_reasons(
    plan: Plan,
    orders: list[Order],
    engineers: list[Engineer],
    *,
    provider: DistanceProvider | None = None,
) -> list[str]:
    """Утренний план: код причины неназначения совпадает с независимой классификацией.

    Навык / транспорт / окно и смена считаются по всем бригадам с пустым маршрутом; если хоть
    одна бригада могла бы взять заявку одна, причина должна быть «занята» (busy).
    """
    provider = provider or HaversineDistanceProvider()
    by_id = {o.id: o for o in orders}
    out: list[str] = []
    for oid in sorted(plan.unassigned_details):
        order = by_id.get(oid)
        if order is None:
            continue
        want = solo_reason(order, engineers, provider) or ReasonCode.BUSY
        got = plan.unassigned_details[oid].code
        if got != want:
            out.append(f"#{oid}: причина «{got.value}», по независимой проверке «{want.value}»")
    return out


def _frozen_at(job_departure: int | None, is_frozen: bool, t_min: int) -> bool:
    return is_frozen or (job_departure is not None and job_departure <= t_min)


def check_replan(before: Plan, after: Plan, t_min: int) -> list[str]:
    """Правила перепланирования на момент события t_min.

    - Работы, к которым бригада выехала до t_min (и замороженные ранее), остаются у той же бригады
      с тем же началом. Исключения: отмена этой заявки клиентом и сход бригады в момент события,
      когда она была в пути (такую заявку передают другим — это допущение движка).
    - Замороженные работы идут в начале маршрута и взяты из прежнего плана той же бригады.
    - Незамороженные работы начинаются с выезда не раньше t_min.
    - У сошедшей бригады нет незамороженных работ; сход не отменяется последующими событиями.
    """
    out: list[str] = []
    after_jobs = {j.order_id: (r.engineer_id, j) for r in after.routes for j in r.jobs}
    before_jobs = {j.order_id: (r.engineer_id, j) for r in before.routes for j in r.jobs}
    before_off = {r.engineer_id: r.unavailable_from_min for r in before.routes}
    after_routes = {r.engineer_id: r for r in after.routes}
    newly_off = {
        eid
        for eid, r in after_routes.items()
        if r.unavailable_from_min is not None and before_off.get(eid) is None
    }

    for route in before.routes:
        eid = route.engineer_id
        for job in route.jobs:
            if not _frozen_at(job.departure_time_min, job.is_frozen, t_min):
                continue
            oid = job.order_id
            where = f"{eid}/#{oid}"
            now = after_jobs.get(oid)
            handed_over = eid in newly_off and job.start_time_min > t_min
            if now is None:
                if not handed_over:
                    out.append(f"{where}: замороженная работа пропала из маршрутов")
                continue
            eid2, job2 = now
            if eid2 != eid:
                if not handed_over:
                    out.append(f"{where}: замороженная работа перенесена к бригаде {eid2}")
                continue
            if not job2.is_frozen:
                out.append(f"{where}: работа, к которой бригада выехала, не заморожена")
            if job.status == JobStatus.CANCELLED and job2.status != JobStatus.CANCELLED:
                out.append(f"{where}: отменённая работа снова активна")
            if job2.status != JobStatus.CANCELLED and job2.start_time_min != job.start_time_min:
                out.append(
                    f"{where}: начало замороженной работы сдвинуто "
                    f"{_hhmm(job.start_time_min)} → {_hhmm(job2.start_time_min)}"
                )

    for route in after.routes:
        eid = route.engineer_id
        seen_open = False
        for job in route.jobs:
            where = f"{eid}/#{job.order_id}"
            if job.is_frozen:
                if seen_open:
                    out.append(f"{where}: замороженная работа после незамороженной")
                prev = before_jobs.get(job.order_id)
                if prev is None or prev[0] != eid:
                    out.append(f"{where}: заморожена работа, которой не было у бригады")
                continue
            seen_open = True
            if job.departure_time_min is None or job.departure_time_min < t_min:
                out.append(
                    f"{where}: выезд {_hhmm(job.departure_time_min)} раньше момента события "
                    f"{_hhmm(t_min)}"
                )
            if route.unavailable_from_min is not None:
                out.append(f"{where}: сошедшая бригада получила работу")
        off_before = before_off.get(eid)
        if off_before is not None and route.unavailable_from_min != off_before:
            out.append(f"{eid}: изменилось время схода бригады")
    if after.as_of_min is not None and after.as_of_min < (before.as_of_min or 0):
        out.append("время среза плана ушло назад")
    return out


def check_explanations(plan: Plan, orders: list[Order], engineers: list[Engineer]) -> list[str]:
    """Выход для диспетчера полон: у каждой заявки плана есть объяснение, у каждой бригады —
    описание маршрута, генератор объяснений не падает (проверка полноты, не независимая)."""
    try:
        texts = ExplanationGenerator.enrich_plan_explanations(plan, orders, engineers)
        routes = ExplanationGenerator.route_explanations(plan, orders, engineers)
    except EXPLAIN_ERRORS as exc:
        return [f"объяснения: исключение {type(exc).__name__}: {exc}"]
    known = (
        {j.order_id for r in plan.routes for j in r.jobs}
        | set(plan.unassigned_orders)
        | set(plan.cancelled_orders)
    )
    out: list[str] = []
    missing = sorted(oid for oid in known if not texts.get(oid, "").strip())
    if missing:
        out.append(f"нет объяснения у заявок ({len(missing)}): {missing[:10]}")
    silent = sorted(r.engineer_id for r in plan.routes if not routes.get(r.engineer_id, "").strip())
    if silent:
        out.append(f"нет описания маршрута у бригад: {silent[:10]}")
    return out


def _where(plan: Plan) -> dict[str, tuple[str, int] | str]:
    where: dict[str, tuple[str, int] | str] = {}
    for route in plan.routes:
        for job in route.jobs:
            if job.status == JobStatus.CANCELLED:
                where[job.order_id] = "cancelled"
            else:
                where[job.order_id] = (route.engineer_id, job.start_time_min)
    for oid in plan.unassigned_orders:
        where[oid] = "unassigned"
    for oid in plan.cancelled_orders:
        where[oid] = "cancelled"
    return where


def check_diff(before: Plan, after: Plan, diff: PlanDiff) -> list[str]:
    """«Что изменилось»: в диффе ровно те заявки, чьё состояние поменялось, с верным статусом."""
    was_map, now_map = _where(before), _where(after)
    listed = {c.order_id: c for c in diff.changes}
    out: list[str] = []
    for oid, now in sorted(now_map.items()):
        was = was_map.get(oid)
        change = listed.get(oid)
        if was == now:
            if change is not None:
                out.append(f"#{oid}: в диффе ({change.status.value}), хотя не изменилась")
            continue
        if now == "cancelled":
            expected = ChangeStatus.CANCELLED
        elif now == "unassigned":
            expected = ChangeStatus.UNASSIGNED
        elif was is None:
            expected = ChangeStatus.ADDED
        elif was == "unassigned":
            expected = ChangeStatus.ASSIGNED
        elif isinstance(was, tuple) and isinstance(now, tuple) and was[0] != now[0]:
            expected = ChangeStatus.REASSIGNED
        else:
            expected = ChangeStatus.SHIFTED
        if change is None:
            out.append(f"#{oid}: изменилась ({was} → {now}), но в диффе её нет")
        elif change.status != expected:
            out.append(f"#{oid}: в диффе «{change.status.value}», ожидался «{expected.value}»")
    for oid in sorted(set(listed) - set(now_map)):
        out.append(f"#{oid}: в диффе заявка, которой нет в плане")
    return out
