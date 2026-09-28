"""«Почему не другая бригада»: контрфакт по каждой бригаде через RouteEvaluator.

Заявку мысленно снимают с текущего маршрута (если к ней ещё не выехали) и для каждой бригады
проверяют: навык → транспорт → успевает ли в окно при пустом хвосте → есть ли место в маршруте.
Для подходящих — лучшая позиция вставки: прирост км и время начала. Тот же разбор использует
ручное назначение, поэтому ответ «можно / нельзя» в интерфейсе и на сервере всегда совпадает.
"""

from dataclasses import dataclass

from app.domain.enums import JobStatus, ReasonCode
from app.domain.models import Engineer, Order, Plan, minutes_to_time
from app.solver.diagnose import crew_name
from app.solver.evaluator import Insertion, RouteEvaluator
from app.solver.fleet import Fleet
from app.solver.replan.state import DayState, fleet_from_state, state_at


@dataclass
class Candidate:
    engineer_id: str
    engineer_name: str
    is_current: bool
    is_active: bool  # бригада уже на линии (иначе назначение выведет её на смену)
    code: ReasonCode | None
    reason: str | None
    delta_km: float | None = None
    start_time: str | None = None
    shift_min: int | None = None


@dataclass
class Alternatives:
    order_id: str
    as_of_min: int | None
    locked: str | None  # почему заявку нельзя переназначить (уже выехали / выполнена / отменена)
    current_engineer_id: str | None
    candidates: list[Candidate]


def reason_text(code: ReasonCode, order: Order, engineer: Engineer) -> str:
    skills = ", ".join(f"«{s.label_ru}»" for s in order.skills)
    window = f"{order.window.start}–{order.window.end}"
    if code == ReasonCode.SKILL:
        return f"Нет навыка {skills}."
    if code == ReasonCode.TRANSPORT:
        need = order.required_transport.label_ru.lower() if order.required_transport else "—"
        return f"Заявка требует транспорт «{need}», у бригады — «{engineer.transport.label_ru.lower()}»."
    if code == ReasonCode.SHIFT_WINDOW:
        return (
            f"Смена {engineer.shift.start}–{engineer.shift.end} не позволяет начать работы "
            f"в окно {window} с учётом дороги."
        )
    if code == ReasonCode.UNAVAILABLE:
        return "Бригада сошла с линии."
    return (
        f"Маршрут занят: в окно {window} заявку не вставить без опоздания к другим клиентам "
        f"или выхода за смену."
    )


def _day_state(plan: Plan, orders_map: dict[str, Order], engineers: list[Engineer]) -> DayState:
    return state_at(plan, plan.as_of_min or 0, orders_map, engineers)


def locked_reason(ds: DayState, cancelled: dict[str, str], order_id: str) -> str | None:
    """Почему заявку нельзя переназначить на момент разреза; None — можно."""
    if order_id in cancelled:
        return "Заявка отменена клиентом."
    for prefix in ds.prefixes.values():
        for job in prefix:
            if job.order_id != order_id:
                continue
            if job.status == JobStatus.CANCELLED:
                return "Заявка отменена клиентом."
            if job.status == JobStatus.DONE:
                return "Заявка уже выполнена."
            if job.status == JobStatus.IN_PROGRESS:
                return "Работы уже идут — начатую работу не прерываем."
            return "Бригада уже выехала к клиенту — в пути не разворачиваем."
    return None


def detach(
    ev: RouteEvaluator,
    plan: Plan,
    order_id: str,
    orders_map: dict[str, Order],
    engineers: list[Engineer],
) -> tuple[DayState, Fleet, str | None]:
    """Разрез дня на момент последнего события с заявкой, снятой с текущего хвоста маршрута."""
    ds = _day_state(plan, orders_map, engineers)
    owner: str | None = None
    for eid, tail in ds.tails.items():
        if any(o.id == order_id for o in tail):
            ds.tails[eid] = [o for o in tail if o.id != order_id]
            owner = eid
            break
    fleet, _ = fleet_from_state(ev, engineers, ds)
    return ds, fleet, owner


def check(
    fleet: Fleet, engineer: Engineer, order: Order
) -> tuple[ReasonCode | None, Insertion | None]:
    ev = fleet.evaluator
    st = fleet.states[engineer.id]
    code = ev.compat(engineer, order, st.start, available=fleet.available.get(engineer.id, True))
    if code is not None:
        return code, None
    ins = ev.best_insertion(st, order, with_shift=True)
    if ins is None:
        return ReasonCode.BUSY, None
    return None, ins


def alternatives(
    ev: RouteEvaluator, plan: Plan, order: Order, orders: list[Order], engineers: list[Engineer]
) -> Alternatives:
    orders_map = {o.id: o for o in orders}
    ds, fleet, owner = detach(ev, plan, order.id, orders_map, engineers)
    locked = locked_reason(ds, plan.cancelled_orders, order.id)
    if owner is None and locked is not None:
        owner = next(
            (eid for eid, p in ds.prefixes.items() if any(j.order_id == order.id for j in p)), None
        )

    out: list[Candidate] = []
    for eng in engineers:
        code, ins = check(fleet, eng, order)
        cand = Candidate(
            engineer_id=eng.id,
            engineer_name=crew_name(eng.name),
            is_current=eng.id == owner,
            is_active=fleet.is_active(eng.id),
            code=code,
            reason=reason_text(code, order, eng) if code is not None else None,
        )
        if ins is not None:
            cand.delta_km = round(ins.delta_km, 2)
            cand.start_time = minutes_to_time(ins.start_min)
            cand.shift_min = ins.shift_min
        out.append(cand)

    reason_rank = {
        ReasonCode.BUSY: 0,
        ReasonCode.SHIFT_WINDOW: 1,
        ReasonCode.TRANSPORT: 2,
        ReasonCode.SKILL: 3,
        ReasonCode.UNAVAILABLE: 4,
    }
    out.sort(
        key=lambda c: (
            not c.is_current,
            c.code is not None,
            reason_rank.get(c.code, 9) if c.code else 0,
            not c.is_active,
            c.delta_km if c.delta_km is not None else 0.0,
            c.engineer_name,
        )
    )
    return Alternatives(
        order_id=order.id,
        as_of_min=plan.as_of_min,
        locked=locked,
        current_engineer_id=owner,
        candidates=out,
    )
