"""Прогон стенда: базовый вариант (FIFO по ТЗ) и наш солвер на наборе сценариев.

Для каждого сценария × зерна строятся оба плана (с замером времени), каждый проходит независимую
проверку, считаются показатели: потерянный вес (авария 10, подключение 5, прочие 3), назначено,
бригады, км, км на заявку, аварии в пределах 2 ч и средняя реакция, а также простые нижние оценки.
«Слабое место» — случай, где наш план хуже базового по лексикографической цели (вес → бригады →
км), есть нарушения проверки или расчёт до 100 заявок дольше 10 с.

С флагом --replan по нашему плану прогоняется день событий (авария, отмена — в том числе когда
бригада в пути, новая обычная заявка, сход бригады, ручное назначение) с проверкой плана и правил
перепланирования после каждого события.

С флагом --variants штатный солвер сравнивается с тем же солвером при других параметрах
конструктора (штраф за бригаду, без локального поиска): если вариант лучше по той же
лексикографической цели, поиск на этом сценарии недооптимизирован.
"""

import argparse
import json
import math
import random
import sys
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.table import Table

from app.bench.scenarios import (
    KINDS,
    REAL_KINDS,
    SEED_INDEPENDENT_KINDS,
    SYNTHETIC_KINDS,
    Scenario,
    disk_point,
    generate,
)
from app.bench.validate import (
    check_diff,
    check_explanations,
    check_plan,
    check_reasons,
    check_replan,
    solo_reason,
)
from app.domain.enums import JobStatus, Priority, Skill, WorkType
from app.domain.events import EventType, ReplanEvent
from app.domain.models import (
    Engineer,
    Location,
    Order,
    Plan,
    TimeWindow,
    minutes_to_time,
    time_to_minutes,
)
from app.geo.haversine import HaversineDistanceProvider
from app.solver import BaselineSolver, Solver
from app.solver.evaluator import RouteEvaluator
from app.solver.objective import release_min
from app.solver.replan import ReplanEngine, ReplanError
from app.solver.replan.alternatives import alternatives

SLA_MIN = 120  # ориентир реакции на аварию (22.09)
SLOW_S = 10.0  # порог «долго» для сценариев до 100 заявок (план — за секунды, не минуты)
SLOW_ORDERS = 100
QUICK_SIZE = (30, 5)  # заявок, бригад в режиме --quick
QUICK_LARGE_SIZE = (80, 12)
QUICK_EVENTS = 6

# день событий: время 10:00, 10:45, … (шаг 45 мин), типы по кругу
EVENT_PLAN: tuple[tuple[EventType, str | None], ...] = (
    (EventType.URGENT_ORDER, None),
    (EventType.CANCEL_ORDER, "en_route"),
    (EventType.NEW_ORDER, None),
    (EventType.ENGINEER_UNAVAILABLE, None),
    (EventType.URGENT_ORDER, None),
    (EventType.CANCEL_ORDER, "in_progress"),
    (EventType.NEW_ORDER, None),
    (EventType.MANUAL_ASSIGN, None),
    (EventType.URGENT_ORDER, None),
    (EventType.CANCEL_ORDER, "planned"),
    (EventType.NEW_ORDER, None),
    (EventType.ENGINEER_UNAVAILABLE, None),
    (EventType.URGENT_ORDER, None),
    (EventType.CANCEL_ORDER, "unassigned"),
)
EVENT_START_MIN = 10 * 60
EVENT_STEP_MIN = 45
EN_ROUTE_LOOKAHEAD_MIN = 45  # насколько можно сдвинуть отмену, чтобы застать бригаду в пути
CANCEL_FALLBACK = {
    "en_route": ("en_route", "en_route_next", "in_progress", "planned", "unassigned"),
    "in_progress": ("in_progress", "en_route", "planned", "unassigned"),
    "planned": ("planned", "unassigned"),
    "unassigned": ("unassigned", "planned"),
}
CRASH_ERRORS = (ValueError, AssertionError, KeyError, IndexError, TypeError, AttributeError)
LOW_LOAD = 0.5  # загрузка бригады ниже 50 % — «лишний человек на линии» (ответ организаторов)

# --variants: тот же солвер с другими параметрами конструктора. Если вариант лексикографически
# лучше штатного (вес → бригады → км), штатный поиск на этом сценарии недооптимизирован.
VARIANTS: tuple[tuple[str, dict[str, Any]], ...] = (
    ("штраф за бригаду 100", {"crew_penalty": 100.0}),
    ("штраф за бригаду 300", {"crew_penalty": 300.0}),
    ("штраф за бригаду 3000", {"crew_penalty": 3000.0}),
    ("без локального поиска", {"use_local_search": False}),
)
VARIANTS_MAX_ORDERS = 150  # на больших сценариях варианты слишком долгие

console = Console() if sys.stdout.isatty() else Console(width=220)


# ---------------------------------------------------------------------------- результаты


@dataclass
class PlanStats:
    assigned: int
    unassigned: int
    lost_weight: int
    crews: int
    km: float
    km_per_order: float | None
    emergencies: int
    emergencies_within_sla: int
    reaction_avg_min: float | None
    runtime_s: float
    violations: list[str] = field(default_factory=list)
    reason_mismatches: list[str] = field(default_factory=list)


@dataclass
class EventRecord:
    index: int
    time: str
    event_type: str
    target: str
    ms: float | None = None
    outcome: str = ""
    error: str | None = None
    violations: list[str] = field(default_factory=list)
    policy: list[str] = field(default_factory=list)
    objective: list[str] = field(default_factory=list)
    moved: list[str] = field(default_factory=list)
    lost: list[str] = field(default_factory=list)
    called_in: list[str] = field(default_factory=list)
    reaction_min: int | None = None


@dataclass
class ReplanStats:
    events: int = 0
    applied: int = 0
    skipped: int = 0
    errors: list[str] = field(default_factory=list)  # ReplanError на корректном событии
    crashes: list[str] = field(default_factory=list)  # прочие исключения движка
    violations: list[str] = field(default_factory=list)  # проверка плана и правил replan
    policy: list[str] = field(default_factory=list)  # отклонения от правил организаторов
    objective: list[str] = field(default_factory=list)  # авария сняла другие аварии
    lost_orders: int = 0  # ранее назначенные заявки, оставшиеся без исполнителя
    moved_orders: int = 0  # перенесены к другой бригаде
    called_in: int = 0  # бригад выведено из резерва
    accidents: int = 0
    accidents_within_sla: int = 0
    accidents_unplaced: int = 0
    ms: list[float] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)  # спорные решения, не нарушения
    log: list[EventRecord] = field(default_factory=list)


@dataclass
class CaseResult:
    kind: str
    seed: int
    name: str
    n_orders: int
    n_crews: int
    notes: list[str]
    hard_unassignable: int
    lost_weight_lb: int
    crews_lb: int
    ours: PlanStats
    base: PlanStats
    weak: list[str] = field(default_factory=list)
    observations: list[str] = field(default_factory=list)
    variants_checked: bool = False
    variant_gaps: list[str] = field(default_factory=list)
    replan: ReplanStats | None = None


# ---------------------------------------------------------------------------- показатели


def day_start(engineers: list[Engineer]) -> int:
    return min((e.shift.start_min for e in engineers), default=600)


def plan_stats(
    plan: Plan, orders: list[Order], engineers: list[Engineer], runtime_s: float
) -> PlanStats:
    """Показатели плана, посчитанные по маршрутам (не по plan.metrics)."""
    by_id = {o.id: o for o in orders}
    starts = {
        j.order_id: j.start_time_min
        for r in plan.routes
        for j in r.jobs
        if j.status != JobStatus.CANCELLED
    }
    lost = sum(by_id[oid].kind.weight for oid in plan.unassigned_orders if oid in by_id)
    km = round(sum(j.travel_dist_km for r in plan.routes for j in r.jobs), 2)
    ds = day_start(engineers)
    emergencies = [o for o in orders if o.is_emergency and o.id not in plan.cancelled_orders]
    reactions = [starts[o.id] - release_min(o, ds) for o in emergencies if o.id in starts]
    return PlanStats(
        assigned=len(starts),
        unassigned=len(plan.unassigned_orders),
        lost_weight=lost,
        crews=sum(1 for r in plan.routes if r.jobs),
        km=km,
        km_per_order=round(km / len(starts), 3) if starts else None,
        emergencies=len(emergencies),
        emergencies_within_sla=sum(1 for x in reactions if x <= SLA_MIN),
        reaction_avg_min=round(sum(reactions) / len(reactions), 1) if reactions else None,
        runtime_s=round(runtime_s, 4),
    )


def lower_bounds(scn: Scenario, provider: HaversineDistanceProvider) -> tuple[int, int, int]:
    """(невыполнимых заявок, нижняя оценка потерянного веса, нижняя оценка числа бригад).

    Невыполнима — заявку не возьмёт ни одна бригада даже одну (навык, транспорт, окно и смена).
    Бригад не меньше ⌈Σ длительности выполнимых заявок / самая длинная смена⌉ — без учёта дороги,
    и только если назначены все выполнимые заявки.
    """
    hard = [o for o in scn.orders if solo_reason(o, scn.engineers, provider) is not None]
    hard_ids = {o.id for o in hard}
    work = sum(o.duration_min for o in scn.orders if o.id not in hard_ids)
    longest = max((e.shift.end_min - e.shift.start_min for e in scn.engineers), default=0)
    crews_lb = math.ceil(work / longest) if work and longest else 0
    return len(hard), sum(o.kind.weight for o in hard), crews_lb


def weak_spots(res: CaseResult) -> list[str]:
    o, b = res.ours, res.base
    out: list[str] = []
    if o.lost_weight > b.lost_weight:
        out.append(f"потерянный вес больше базового: {o.lost_weight} > {b.lost_weight}")
    elif o.lost_weight == b.lost_weight and o.crews > b.crews:
        out.append(f"при равном потерянном весе бригад больше: {o.crews} > {b.crews}")
    elif o.lost_weight == b.lost_weight and o.crews == b.crews and o.km > b.km + 0.01:
        out.append(f"при равных весе и бригадах пробег больше: {o.km:.2f} > {b.km:.2f} км")
    for label, stats in (("нашем", o), ("базовом", b)):
        if stats.violations:
            out.append(
                f"нарушения в {label} плане: {len(stats.violations)} "
                f"(первое: {stats.violations[0]})"
            )
        if stats.reason_mismatches:
            out.append(
                f"причины неназначения в {label} плане не сходятся с проверкой: "
                f"{len(stats.reason_mismatches)} (первая: {stats.reason_mismatches[0]})"
            )
    if res.n_orders <= SLOW_ORDERS and o.runtime_s > SLOW_S:
        out.append(f"долгий расчёт: {o.runtime_s:.1f} с на {res.n_orders} заявок")
    if o.lost_weight < res.lost_weight_lb:
        out.append(
            f"потерянный вес {o.lost_weight} ниже нижней оценки {res.lost_weight_lb} "
            "(ошибка проверки или плана)"
        )
    return out


def observations(res: CaseResult) -> list[str]:
    """Не нарушения цели, но заметные особенности (для разбора)."""
    o, b = res.ours, res.base
    out: list[str] = []
    if o.assigned < b.assigned:
        out.append(
            f"назначено меньше, чем у базового ({o.assigned} < {b.assigned}), "
            f"потерянный вес {o.lost_weight} против {b.lost_weight}"
        )
    if o.emergencies_within_sla < b.emergencies_within_sla:
        out.append(
            f"аварий в пределах 2 ч меньше, чем у базового "
            f"({o.emergencies_within_sla} < {b.emergencies_within_sla})"
        )
    if o.lost_weight == res.lost_weight_lb and res.crews_lb and o.crews > res.crews_lb + 2:
        out.append(f"бригад {o.crews} при нижней оценке {res.crews_lb}")
    return out


# ---------------------------------------------------------------------------- один сценарий


def solve_case(scn: Scenario) -> tuple[Plan, Plan, float, float]:
    started = time.perf_counter()
    base = BaselineSolver().solve(scn.orders, scn.engineers)
    t_base = time.perf_counter() - started
    started = time.perf_counter()
    ours = Solver().solve(scn.orders, scn.engineers)
    t_ours = time.perf_counter() - started
    return ours, base, t_ours, t_base


def _lex_better(a: PlanStats, b: PlanStats) -> bool:
    """a лучше b по цели: потерянный вес → бригады → км."""
    if a.lost_weight != b.lost_weight:
        return a.lost_weight < b.lost_weight
    if a.crews != b.crews:
        return a.crews < b.crews
    return a.km < b.km - 0.01


def variant_gaps(scn: Scenario, ours: PlanStats) -> list[str]:
    """Варианты того же солвера, которые на этом сценарии лучше штатного по его же цели."""
    out: list[str] = []
    for label, kwargs in VARIANTS:
        plan = Solver(**kwargs).solve(scn.orders, scn.engineers)
        s = plan_stats(plan, scn.orders, scn.engineers, 0.0)
        if _lex_better(s, ours):
            out.append(
                f"{label}: вес {s.lost_weight}, бригад {s.crews}, {s.km:.1f} км "
                f"(штатный: {ours.lost_weight}, {ours.crews}, {ours.km:.1f} км)"
            )
    return out


def run_case(
    kind: str,
    seed: int,
    *,
    n_orders: int | None = None,
    n_crews: int | None = None,
    replan_events: int = 0,
    variants: bool = False,
) -> CaseResult:
    scn = generate(kind, seed, n_orders=n_orders, n_crews=n_crews)
    provider = HaversineDistanceProvider()
    ours_plan, base_plan, t_ours, t_base = solve_case(scn)
    ours = plan_stats(ours_plan, scn.orders, scn.engineers, t_ours)
    base = plan_stats(base_plan, scn.orders, scn.engineers, t_base)
    for stats, plan in ((ours, ours_plan), (base, base_plan)):
        stats.violations = [
            *check_plan(plan, scn.orders, scn.engineers, provider=provider),
            *check_explanations(plan, scn.orders, scn.engineers),
        ]
        stats.reason_mismatches = check_reasons(plan, scn.orders, scn.engineers, provider=provider)
    hard, lost_lb, crews_lb = lower_bounds(scn, provider)
    res = CaseResult(
        kind=kind,
        seed=seed,
        name=scn.name,
        n_orders=len(scn.orders),
        n_crews=len(scn.engineers),
        notes=scn.notes,
        hard_unassignable=hard,
        lost_weight_lb=lost_lb,
        crews_lb=crews_lb,
        ours=ours,
        base=base,
    )
    res.weak = weak_spots(res)
    res.observations = observations(res)
    if variants and len(scn.orders) <= VARIANTS_MAX_ORDERS:
        res.variants_checked = True
        res.variant_gaps = variant_gaps(scn, ours)
    if replan_events:
        res.replan = run_replan(scn, ours_plan, replan_events)
    return res


# ---------------------------------------------------------------------------- день событий


def _pick(rng: random.Random, items: list[Any]) -> Any:
    return items[rng.randrange(len(items))] if items else None


def _live_jobs(plan: Plan) -> list[tuple[str, Any]]:
    return sorted(
        (
            (r.engineer_id, j)
            for r in plan.routes
            for j in r.jobs
            if j.status != JobStatus.CANCELLED and j.order_id not in plan.cancelled_orders
        ),
        key=lambda x: x[1].order_id,
    )


def _new_order_event(
    etype: EventType, k: int, t: int, orders: list[Order], rng: random.Random
) -> tuple[ReplanEvent, int, str]:
    ref = orders[rng.randrange(len(orders))].location
    lat, lon = disk_point(rng, (ref.lat, ref.lon), 0.4)
    loc = Location(
        lat=lat, lon=lon, address=f"{ref.district}, новый адрес {k + 1}", district=ref.district
    )
    oid = f"EV{k + 1:02d}"
    if etype == EventType.URGENT_ORDER:
        order = Order(
            id=oid,
            skills=[Skill.EMERGENCY],
            priority=Priority.URGENT,
            work_type=WorkType.EMERGENCY,
            window=TimeWindow(start=minutes_to_time(t), end="23:59"),
            duration_min=WorkType.EMERGENCY.work_min,
            location=loc,
        )
    else:
        kind = rng.choices(
            (WorkType.CONNECTION, WorkType.LOCAL, WorkType.ADDON), weights=(4, 4, 1)
        )[0]
        slot = min(((t + 60 + 119) // 120) * 120, 20 * 60)  # ближайшее окно не раньше t + 1 ч
        order = Order(
            id=oid,
            skills=[kind.skill],
            work_type=kind,
            window=TimeWindow(start=minutes_to_time(slot), end=minutes_to_time(slot + 120)),
            duration_min=kind.work_min,
            location=loc,
        )
    event = ReplanEvent(event_type=etype, event_time=minutes_to_time(t), new_order=order)
    return event, t, f"#{oid} ({order.kind.label_ru.lower()}, {ref.district})"


def _cancel_event(
    mode: str, t: int, plan: Plan, rng: random.Random
) -> tuple[ReplanEvent, int, str] | None:
    live = _live_jobs(plan)
    groups = {
        "en_route": [x for x in live if x[1].departure_time_min <= t < x[1].start_time_min],
        "in_progress": [x for x in live if x[1].start_time_min <= t < x[1].end_time_min],
        "planned": [x for x in live if x[1].departure_time_min > t],
    }
    for variant in CANCEL_FALLBACK[mode]:
        if variant == "unassigned":
            pool = sorted(o for o in plan.unassigned_orders if o not in plan.cancelled_orders)
            oid = _pick(rng, pool)
            if oid is not None:
                event = ReplanEvent(
                    event_type=EventType.CANCEL_ORDER, event_time=minutes_to_time(t), order_id=oid
                )
                return event, t, f"#{oid} (не назначена)"
            continue
        if variant == "en_route_next":
            # сдвигаем момент отмены на минуту после ближайшего выезда, чтобы застать бригаду в пути
            nxt = sorted(
                (
                    x
                    for x in groups["planned"]
                    if x[1].travel_time_min >= 2
                    and x[1].departure_time_min < t + EN_ROUTE_LOOKAHEAD_MIN
                ),
                key=lambda x: (x[1].departure_time_min, x[1].order_id),
            )
            if nxt:
                eid, job = nxt[0]
                t2 = job.departure_time_min + 1
                event = ReplanEvent(
                    event_type=EventType.CANCEL_ORDER,
                    event_time=minutes_to_time(t2),
                    order_id=job.order_id,
                )
                return event, t2, f"#{job.order_id} ({eid}, в пути)"
            continue
        picked = _pick(rng, groups[variant])
        if picked is not None:
            eid, job = picked
            label = {"en_route": "в пути", "in_progress": "в работе", "planned": "запланирована"}
            event = ReplanEvent(
                event_type=EventType.CANCEL_ORDER,
                event_time=minutes_to_time(t),
                order_id=job.order_id,
            )
            return event, t, f"#{job.order_id} ({eid}, {label[variant]})"
    return None


def _unavailable_event(
    t: int, plan: Plan, rng: random.Random
) -> tuple[ReplanEvent, int, str] | None:
    on_line = [r for r in plan.routes if r.unavailable_from_min is None]
    if len(on_line) <= 1:
        return None
    busy = sorted(
        r.engineer_id
        for r in on_line
        if any(j.end_time_min > t and j.status != JobStatus.CANCELLED for j in r.jobs)
    )
    eid = _pick(rng, busy or sorted(r.engineer_id for r in on_line))
    event = ReplanEvent(
        event_type=EventType.ENGINEER_UNAVAILABLE, event_time=minutes_to_time(t), engineer_id=eid
    )
    return event, t, eid


def _manual_event(
    plan: Plan,
    orders: list[Order],
    engineers: list[Engineer],
    engine: ReplanEngine,
    rng: random.Random,
) -> tuple[ReplanEvent, int, str] | None:
    """Ручное назначение, которое «Другие бригады» (alternatives) считают допустимым."""
    as_of = plan.as_of_min or 0
    ev = RouteEvaluator(engine.matrix, day_start_min=day_start(engineers))
    by_id = {o.id: o for o in orders}
    waiting = [
        oid
        for oid in sorted(plan.unassigned_orders)
        if oid not in plan.cancelled_orders and by_id[oid].window.end_min >= as_of
    ]
    tail = sorted(
        j.order_id
        for r in plan.routes
        for j in r.jobs
        if not j.is_frozen and j.status != JobStatus.CANCELLED
    )
    rng.shuffle(tail)
    for oid in waiting + tail[:8]:
        alt = alternatives(ev, plan, by_id[oid], orders, engineers)
        if alt.locked:
            continue
        ok = [c for c in alt.candidates if c.code is None and not c.is_current]
        if ok:
            event = ReplanEvent(
                event_type=EventType.MANUAL_ASSIGN,
                event_time=minutes_to_time(as_of),
                order_id=oid,
                engineer_id=ok[0].engineer_id,
            )
            was = alt.current_engineer_id or "без исполнителя"
            return event, as_of, f"#{oid}: {was} → {ok[0].engineer_id}"
    return None


def _make_event(
    etype: EventType,
    mode: str | None,
    k: int,
    t: int,
    plan: Plan,
    orders: list[Order],
    engineers: list[Engineer],
    engine: ReplanEngine,
    rng: random.Random,
) -> tuple[ReplanEvent, int, str] | None:
    if etype == EventType.ENGINEER_UNAVAILABLE:
        made = _unavailable_event(t, plan, rng)
        if made is not None:
            return made
        etype = EventType.NEW_ORDER  # сходить некому — заменяем новой заявкой
    if etype == EventType.MANUAL_ASSIGN:
        made = _manual_event(plan, orders, engineers, engine, rng)
        if made is not None:
            return made
        etype = EventType.NEW_ORDER
    if etype == EventType.CANCEL_ORDER:
        return _cancel_event(mode or "planned", t, plan, rng)
    return _new_order_event(etype, k, t, orders, rng)


def _assigned_map(plan: Plan) -> dict[str, str]:
    return {
        j.order_id: r.engineer_id
        for r in plan.routes
        for j in r.jobs
        if j.status != JobStatus.CANCELLED
    }


def _load(plan: Plan, engineers: list[Engineer], eid: str) -> float:
    """Загрузка бригады: (дорога + работа) / длительность смены."""
    eng = next(e for e in engineers if e.id == eid)
    route = next((r for r in plan.routes if r.engineer_id == eid), None)
    if route is None:
        return 0.0
    busy = route.total_work_time_min + route.total_travel_time_min
    return busy / max(1, eng.shift.end_min - eng.shift.start_min)


def _objective_issues(
    event: ReplanEvent, lost: list[str], by_id: dict[str, Order]
) -> tuple[list[str], list[str]]:
    """Решения по аварии: (против правил, спорные).

    Авария ставится всегда, если есть бригада с навыком, и ради неё можно снять обычные заявки,
    но не другие аварии. Снять другую аварию — против правил. Снять обычные заявки общим весом
    больше веса аварии — допустимо (авария важнее), но стоит показать в отчёте.
    """
    if event.event_type != EventType.URGENT_ORDER or not lost:
        return [], []
    issues: list[str] = []
    notes: list[str] = []
    lost_em = [oid for oid in lost if by_id[oid].is_emergency]
    weight = sum(by_id[oid].kind.weight for oid in lost)
    if lost_em:
        issues.append(f"авария сняла с маршрутов другие аварии: {lost_em}")
    if weight > WorkType.EMERGENCY.weight:
        notes.append(f"авария сняла обычные заявки общим весом {weight}: {lost}")
    return issues, notes


def _policy(
    event: ReplanEvent, before: Plan, after: Plan
) -> tuple[list[str], list[str], list[str]]:
    """Правила организаторов по типам событий: (отклонения, снятые заявки, перенесённые заявки)."""
    b, a = _assigned_map(before), _assigned_map(after)
    lost = sorted(oid for oid in b if oid not in a and oid not in after.cancelled_orders)
    moved = sorted(oid for oid in b if oid in a and a[oid] != b[oid])
    et = event.event_type
    issues: list[str] = []
    if et in (EventType.NEW_ORDER, EventType.CANCEL_ORDER):
        if moved:
            issues.append(f"чужие заявки перенесены к другим бригадам: {moved}")
        if lost:
            issues.append(f"назначенные заявки сняты с маршрутов: {lost}")
    if et == EventType.CANCEL_ORDER and event.order_id not in after.cancelled_orders:
        issues.append(f"#{event.order_id} не попала в список отменённых")
    if et == EventType.MANUAL_ASSIGN:
        others = [x for x in moved if x != event.order_id]
        if others:
            issues.append(f"ручное назначение перенесло и другие заявки: {others}")
        if lost:
            issues.append(f"ручное назначение сняло заявки: {lost}")
        if a.get(event.order_id or "") != event.engineer_id:
            issues.append(f"ручное назначение #{event.order_id} не применено")
    return issues, lost, moved


def run_replan(scn: Scenario, plan: Plan, n_events: int) -> ReplanStats:
    """День событий по нашему плану; после каждого — проверка плана и правил перепланирования."""
    engine = ReplanEngine()
    engineers = scn.engineers
    orders = list(scn.orders)
    rng = random.Random(f"replan|{scn.name}")
    stats = ReplanStats()
    t_prev = 0
    for k, (etype, mode) in enumerate(EVENT_PLAN[:n_events]):
        t = max(EVENT_START_MIN + EVENT_STEP_MIN * k, t_prev)
        made = _make_event(etype, mode, k, t, plan, orders, engineers, engine, rng)
        stats.events += 1
        if made is None:
            stats.skipped += 1
            stats.log.append(
                EventRecord(k, minutes_to_time(t), etype.value, "—", outcome="нет цели — пропущено")
            )
            continue
        event, t_event, target = made
        rec = EventRecord(k, event.event_time, event.event_type.value, target)
        stats.log.append(rec)
        before = plan
        started = time.perf_counter()
        try:
            new_plan, diff, new_orders = engine.apply_event(plan, event, orders, engineers)
        except ReplanError as exc:
            rec.error = f"ReplanError: {exc}"
            stats.errors.append(
                f"{scn.name} {event.event_time} {event.event_type.value} {target}: {exc}"
            )
            continue
        except CRASH_ERRORS as exc:
            rec.error = f"{type(exc).__name__}: {exc}"
            stats.crashes.append(
                f"{scn.name} {event.event_time} {event.event_type.value} {target}: "
                f"{type(exc).__name__}: {exc}"
            )
            continue
        rec.ms = round((time.perf_counter() - started) * 1000, 2)
        stats.ms.append(rec.ms)
        stats.applied += 1
        rec.outcome = diff.summary_ru

        manual = event.event_type == EventType.MANUAL_ASSIGN
        t_check = (before.as_of_min or 0) if manual else time_to_minutes(event.event_time)
        rec.violations = [
            *check_plan(new_plan, new_orders, engineers),
            *check_replan(before, new_plan, t_check),
            *check_diff(before, new_plan, diff),
            *check_explanations(new_plan, new_orders, engineers),
        ]
        if not manual and new_plan.as_of_min != t_check:
            rec.violations.append("время среза плана не равно времени события")
        by_id = {o.id: o for o in new_orders}
        rec.policy, rec.lost, rec.moved = _policy(event, before, new_plan)
        rec.objective, notes = _objective_issues(event, rec.lost, by_id)
        for x in rec.objective:
            stats.objective.append(f"{scn.name} {event.event_time} {event.event_type.value}: {x}")
        for x in notes:
            stats.observations.append(f"{scn.name} {event.event_time}: {x}")
        rec.called_in = list(diff.called_in_engineer_ids)
        if rec.called_in and event.event_type == EventType.NEW_ORDER:
            for eid in rec.called_in:
                load = _load(new_plan, engineers, eid)
                if load < LOW_LOAD:
                    stats.observations.append(
                        f"{scn.name} {event.event_time}: обычная заявка {target} вывела из резерва "
                        f"бригаду {eid} с загрузкой {load:.0%}"
                    )
        for v in rec.violations:
            stats.violations.append(f"{scn.name} {event.event_time} {event.event_type.value}: {v}")
        for p in rec.policy:
            stats.policy.append(f"{scn.name} {event.event_time} {event.event_type.value}: {p}")
        stats.lost_orders += len(rec.lost)
        stats.moved_orders += len(rec.moved)
        stats.called_in += len(rec.called_in)

        if event.event_type == EventType.URGENT_ORDER and event.new_order is not None:
            stats.accidents += 1
            start = next(
                (
                    j.start_time_min
                    for r in new_plan.routes
                    for j in r.jobs
                    if j.order_id == event.new_order.id
                ),
                None,
            )
            if start is None:
                stats.accidents_unplaced += 1
            else:
                rec.reaction_min = start - t_event
                stats.accidents_within_sla += int(rec.reaction_min <= SLA_MIN)

        plan, orders = new_plan, new_orders
        if not manual:
            t_prev = t_event
    return stats


# ---------------------------------------------------------------------------- сводка


def _mean(values: list[float | None]) -> float | None:
    xs = [v for v in values if v is not None]
    return sum(xs) / len(xs) if xs else None


def _num(value: float | None, digits: int = 1) -> str:
    if value is None:
        return "—"
    text = f"{value:.{digits}f}"
    return text.replace(".", ",")


def _pair(a: float | None, b: float | None, digits: int = 1) -> str:
    return f"{_num(a, digits)} / {_num(b, digits)}"


SUMMARY_COLUMNS = (
    "Вид",
    "Случаев",
    "Заявок",
    "Бригад в наборе",
    "Невыполнимо",
    "Потеря веса: наш / баз.",
    "Назначено: наш / баз.",
    "Бригад: наш / баз. (НО)",
    "Км: наш / баз.",
    "Км на заявку: наш / баз.",
    "Аварии ≤ 2 ч: наш / баз.",
    "Реакция, мин: наш / баз.",
    "Время, с: наш / баз. (макс.)",
    "Нарушений",
    "Слабых мест",
    "Вариант лучше",
)


def _summary_row(label: str, rs: list[CaseResult]) -> list[str]:
    def m(get) -> float | None:
        return _mean([get(r) for r in rs])

    sla_o = (
        f"{sum(r.ours.emergencies_within_sla for r in rs)}/{sum(r.ours.emergencies for r in rs)}"
    )
    sla_b = (
        f"{sum(r.base.emergencies_within_sla for r in rs)}/{sum(r.base.emergencies for r in rs)}"
    )
    violations = sum(
        len(s.violations) + len(s.reason_mismatches) for r in rs for s in (r.ours, r.base)
    )
    return [
        label,
        str(len(rs)),
        _num(m(lambda r: r.n_orders)),
        _num(m(lambda r: r.n_crews)),
        _num(m(lambda r: r.hard_unassignable)),
        _pair(m(lambda r: r.ours.lost_weight), m(lambda r: r.base.lost_weight)),
        _pair(m(lambda r: r.ours.assigned), m(lambda r: r.base.assigned)),
        _pair(m(lambda r: r.ours.crews), m(lambda r: r.base.crews))
        + f" ({_num(m(lambda r: r.crews_lb))})",
        _pair(m(lambda r: r.ours.km), m(lambda r: r.base.km)),
        _pair(m(lambda r: r.ours.km_per_order), m(lambda r: r.base.km_per_order), 2),
        f"{sla_o} / {sla_b}",
        _pair(m(lambda r: r.ours.reaction_avg_min), m(lambda r: r.base.reaction_avg_min), 0),
        _pair(m(lambda r: r.ours.runtime_s), m(lambda r: r.base.runtime_s), 2)
        + f" ({_num(max(r.ours.runtime_s for r in rs), 2)})",
        str(violations),
        str(sum(1 for r in rs if r.weak)),
        (
            f"{sum(1 for r in rs if r.variant_gaps)} из {sum(1 for r in rs if r.variants_checked)}"
            if any(r.variants_checked for r in rs)
            else "—"
        ),
    ]


def summary_rows(results: list[CaseResult]) -> list[list[str]]:
    groups: dict[str, list[CaseResult]] = {}
    for r in results:
        groups.setdefault(r.kind, []).append(r)
    rows = [_summary_row(kind, rs) for kind, rs in groups.items()]
    rows.append(_summary_row("Итого", results))
    return rows


REPLAN_COLUMNS = (
    "Вид",
    "Сценариев",
    "Событий (применено)",
    "Пропущено",
    "ReplanError",
    "Падений",
    "Нарушений",
    "Отклонений от правил",
    "Сняты аварии",
    "Снято / перенесено",
    "Вызвано из резерва",
    "Аварии ≤ 2 ч (не поставлено)",
    "мс на событие: ср. / макс.",
)


def _replan_row(label: str, rs: list[CaseResult]) -> list[str]:
    st = [r.replan for r in rs if r.replan is not None]
    ms = [x for s in st for x in s.ms]
    return [
        label,
        str(len(st)),
        f"{sum(s.events for s in st)} ({sum(s.applied for s in st)})",
        str(sum(s.skipped for s in st)),
        str(sum(len(s.errors) for s in st)),
        str(sum(len(s.crashes) for s in st)),
        str(sum(len(s.violations) for s in st)),
        str(sum(len(s.policy) for s in st)),
        str(sum(len(s.objective) for s in st)),
        f"{sum(s.lost_orders for s in st)} / {sum(s.moved_orders for s in st)}",
        str(sum(s.called_in for s in st)),
        (
            f"{sum(s.accidents_within_sla for s in st)}/{sum(s.accidents for s in st)} "
            f"({sum(s.accidents_unplaced for s in st)})"
        ),
        f"{_num(_mean(ms), 1)} / {_num(max(ms) if ms else None, 1)}",
    ]


def replan_rows(results: list[CaseResult]) -> list[list[str]]:
    groups: dict[str, list[CaseResult]] = {}
    for r in results:
        if r.replan is not None:
            groups.setdefault(r.kind, []).append(r)
    rows = [_replan_row(kind, rs) for kind, rs in groups.items()]
    if rows:
        rows.append(_replan_row("Итого", [r for r in results if r.replan is not None]))
    return rows


def _rich_table(title: str, columns: tuple[str, ...], rows: list[list[str]]) -> Table:
    table = Table(title=title, box=box.SIMPLE_HEAVY, header_style="bold", show_lines=False)
    for i, col in enumerate(columns):
        table.add_column(col, justify="left" if i == 0 else "right", overflow="fold")
    for row in rows:
        style = "bold" if row[0] == "Итого" else None
        table.add_row(*row, style=style)
    return table


def _md_table(columns: tuple[str, ...], rows: list[list[str]]) -> list[str]:
    lines = [
        "| " + " | ".join(columns) + " |",
        "|" + "|".join(["---"] + [":-:"] * (len(columns) - 1)) + "|",
    ]
    lines += ["| " + " | ".join(row) + " |" for row in rows]
    return lines


def collect_findings(results: list[CaseResult]) -> dict[str, list[str]]:
    weak = [f"{r.name}: {w}" for r in results for w in r.weak]
    obs = [f"{r.name}: {x}" for r in results for x in r.observations]
    violations = [
        f"{r.name} ({label}): {v}"
        for r in results
        for label, s in (("наш", r.ours), ("базовый", r.base))
        for v in s.violations + s.reason_mismatches
    ]
    replan_issues = [
        x
        for r in results
        if r.replan is not None
        for x in r.replan.errors + r.replan.crashes + r.replan.violations
    ]
    policy = [x for r in results if r.replan is not None for x in r.replan.policy]
    objective = [x for r in results if r.replan is not None for x in r.replan.objective]
    replan_obs = [x for r in results if r.replan is not None for x in r.replan.observations]
    variants = [f"{r.name}: {x}" for r in results for x in r.variant_gaps]
    return {
        "weak": weak,
        "variants": variants,
        "observations": obs,
        "violations": violations,
        "replan": replan_issues,
        "policy": policy,
        "objective": objective,
        "replan_observations": replan_obs,
    }


def write_markdown(
    path: Path, results: list[CaseResult], config: dict[str, Any], elapsed: float
) -> None:
    f = collect_findings(results)
    lines = [
        "# Стенд качества планировщика",
        "",
        (
            f"Команда: `python -m app.bench {config['argv']}` · сценариев: {len(results)} · "
            f"общее время: {_num(elapsed, 1)} с."
        ),
        "",
        (
            "Потерянный вес — сумма весов неназначенных заявок (авария 10, подключение 5, "
            "прочие 3). НО — нижняя оценка числа бригад: ⌈Σ длительности выполнимых заявок / "
            "самая длинная смена⌉. Аварии ≤ 2 ч — начаты не позже 2 ч от поступления "
            "(утренние — от начала рабочего дня)."
        ),
        "",
        "## Сводка по видам сценариев (средние по зёрнам)",
        "",
        *_md_table(SUMMARY_COLUMNS, summary_rows(results)),
        "",
        f"## Слабые места ({len(f['weak'])})",
        "",
        *([f"- {x}" for x in f["weak"]] or ["Нет."]),
        "",
    ]
    if config.get("variants"):
        lines += [
            f"## Недооптимизация: вариант того же солвера лучше штатного ({len(f['variants'])})",
            "",
            *([f"- {x}" for x in f["variants"]] or ["Нет."]),
            "",
        ]
    lines += [
        f"## Нарушения проверки планов ({len(f['violations'])})",
        "",
        *([f"- {x}" for x in f["violations"][:200]] or ["Нет."]),
        "",
        f"## Наблюдения ({len(f['observations'])})",
        "",
        *([f"- {x}" for x in f["observations"]] or ["Нет."]),
    ]
    rows = replan_rows(results)
    if rows:
        lines += [
            "",
            "## Перепланирование: день событий по нашему плану",
            "",
            *_md_table(REPLAN_COLUMNS, rows),
            "",
            f"### Ошибки, падения и нарушения ({len(f['replan'])})",
            "",
            *([f"- {x}" for x in f["replan"][:300]] or ["Нет."]),
            "",
            f"### Отклонения от правил организаторов ({len(f['policy'])})",
            "",
            *([f"- {x}" for x in f["policy"][:300]] or ["Нет."]),
            "",
            f"### Новая авария сняла другие аварии ({len(f['objective'])})",
            "",
            *([f"- {x}" for x in f["objective"][:300]] or ["Нет."]),
            "",
            f"### Спорные решения ({len(f['replan_observations'])})",
            "",
            *([f"- {x}" for x in f["replan_observations"][:300]] or ["Нет."]),
        ]
    lines += ["", "## Сценарии", ""]
    for r in results:
        lines.append(f"- **{r.name}** — " + " ".join(r.notes))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_json(
    path: Path, results: list[CaseResult], config: dict[str, Any], elapsed: float
) -> None:
    payload = {
        "config": config,
        "elapsed_s": round(elapsed, 2),
        "summary": [dict(zip(SUMMARY_COLUMNS, row)) for row in summary_rows(results)],
        "replan_summary": [dict(zip(REPLAN_COLUMNS, row)) for row in replan_rows(results)],
        "findings": collect_findings(results),
        "cases": [asdict(r) for r in results],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")


# ---------------------------------------------------------------------------- CLI


def resolve_kinds(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for tok in tokens:
        if tok == "all":
            cand = list(KINDS)
        elif tok == "synthetic":
            cand = list(SYNTHETIC_KINDS)
        elif tok == "real":
            cand = list(REAL_KINDS)
        elif tok in KINDS:
            cand = [tok]
        else:
            cand = [k for k in KINDS if k.split(":")[0] == tok]
        if not cand:
            raise SystemExit(f"Неизвестный вид сценария «{tok}». Доступны: {', '.join(KINDS)}")
        out += [k for k in cand if k not in out]
    return out


def seeds_for(kind: str, seeds: list[int], large_seeds: int) -> list[int]:
    if kind in SEED_INDEPENDENT_KINDS:
        return seeds[:1]
    if kind == "large":
        return seeds[:large_seeds]
    return seeds


def sizes_for(kind: str, quick: bool) -> tuple[int | None, int | None]:
    if not quick or kind in REAL_KINDS or kind == "tiny":
        return None, None
    return QUICK_LARGE_SIZE if kind == "large" else QUICK_SIZE


def _case_line(r: CaseResult) -> str:
    o, b = r.ours, r.base
    mark = "[red]СЛАБОЕ МЕСТО[/red]" if r.weak else "[green]ок[/green]"
    replan = ""
    if r.replan is not None:
        s = r.replan
        bad = len(s.errors) + len(s.crashes) + len(s.violations)
        replan = (
            f" · replan {s.applied}/{s.events}, проблем {bad}, правил {len(s.policy)}, "
            f"сняты аварии {len(s.objective)}"
        )
    variant = " · [yellow]вариант лучше[/yellow]" if r.variant_gaps else ""
    return (
        f"{r.name:<28} наш {o.assigned}/{r.n_orders} · {o.crews} бр · {o.km:.1f} км · "
        f"{o.runtime_s:.2f} с | баз {b.assigned} · {b.crews} бр · {b.km:.1f} км{replan}"
        f"{variant}  {mark}"
    )


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m app.bench",
        description="Стенд качества планировщика: сценарии, независимая проверка, сравнение "
        "с базовым вариантом и перепланирование.",
    )
    parser.add_argument(
        "--kinds",
        nargs="+",
        default=["all"],
        help="виды сценариев: имена, префиксы (real_shuffled) или группы all/synthetic/real",
    )
    parser.add_argument("--seeds", nargs="+", type=int, default=[1, 2, 3], help="зёрна генератора")
    parser.add_argument(
        "--large-seeds", type=int, default=2, help="сколько первых зёрен брать для large"
    )
    parser.add_argument(
        "--quick", action="store_true", help="маленькие сценарии (30 заявок, 5 бригад), 6 событий"
    )
    parser.add_argument(
        "--replan", action="store_true", help="прогнать день событий по нашему плану"
    )
    parser.add_argument(
        "--variants",
        action="store_true",
        help="сравнить штатный солвер с вариантами параметров (до 150 заявок, в ~5 раз дольше)",
    )
    parser.add_argument("--json", type=Path, default=None, help="сохранить результаты в JSON")
    parser.add_argument("--md", type=Path, default=None, help="сохранить отчёт в Markdown")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    kinds = resolve_kinds(args.kinds)
    n_events = (QUICK_EVENTS if args.quick else len(EVENT_PLAN)) if args.replan else 0
    config = {
        "argv": " ".join(sys.argv[1:] if argv is None else argv),
        "kinds": kinds,
        "seeds": args.seeds,
        "large_seeds": args.large_seeds,
        "quick": args.quick,
        "replan_events": n_events,
        "variants": args.variants,
    }
    started = time.perf_counter()
    results: list[CaseResult] = []
    for kind in kinds:
        n_orders, n_crews = sizes_for(kind, args.quick)
        for seed in seeds_for(kind, args.seeds, args.large_seeds):
            res = run_case(
                kind,
                seed,
                n_orders=n_orders,
                n_crews=n_crews,
                replan_events=n_events,
                variants=args.variants,
            )
            results.append(res)
            console.print(_case_line(res), highlight=False)
    elapsed = time.perf_counter() - started

    console.print()
    console.print(
        _rich_table("Сводка по видам (средние по зёрнам)", SUMMARY_COLUMNS, summary_rows(results))
    )
    rows = replan_rows(results)
    if rows:
        console.print(_rich_table("Перепланирование: день событий", REPLAN_COLUMNS, rows))
    findings = collect_findings(results)
    sections = [
        ("Слабые места", "weak"),
        ("Нарушения проверки", "violations"),
        ("Ошибки и нарушения перепланирования", "replan"),
        ("Отклонения от правил перепланирования", "policy"),
        ("Новая авария сняла другие аварии", "objective"),
        ("Спорные решения при перепланировании", "replan_observations"),
    ]
    if args.variants:
        sections.insert(1, ("Вариант того же солвера лучше штатного", "variants"))
    for title, key in sections:
        items = findings[key]
        console.print(f"[bold]{title}: {len(items)}[/bold]")
        for x in items[:40]:
            console.print(f"  • {x}", highlight=False)
        if len(items) > 40:
            console.print(f"  … и ещё {len(items) - 40}")
    console.print(f"Сценариев: {len(results)}, общее время {elapsed:.1f} с")

    if args.json:
        write_json(args.json, results, config, elapsed)
        console.print(f"JSON: {args.json}")
    if args.md:
        write_markdown(args.md, results, config, elapsed)
        console.print(f"Markdown: {args.md}")
    bad = findings["violations"] or findings["replan"]
    return 1 if bad else 0
