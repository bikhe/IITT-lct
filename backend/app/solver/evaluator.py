"""RouteEvaluator — единственный источник правды о допустимости маршрута.

Конструктор, локальный поиск, LNS, baseline, перепланирование, диагностика неназначений и
объяснения проверяют ограничения только через этот класс и одну TravelMatrix.

Модель времени. Бригада стартует из точки RouteStart (депо в начале смены или место последней
замороженной работы) и выезжает к следующему клиенту так, чтобы прибыть к началу окна
(just-in-time): ожидание проходит в предыдущей точке. Начало работ = max(готовность + дорога,
начало окна) и должно быть не позже конца окна; работа должна закончиться до конца смены.
"""

from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass

from app.domain.enums import ReasonCode, Transport
from app.domain.models import Engineer, Location, Order, minutes_to_time
from app.geo.base import DistanceProvider
from app.geo.matrix import as_matrix
from app.solver import objective as obj
from app.solver.constraints import DEFAULT_PAIR_CONSTRAINTS, PairConstraint

BEST_CACHE_SIZE = 50_000  # запомненных «маршрут × заявка»; при переполнении кэш очищается
NODE_BITS = 24  # узлов матрицы меньше 2**24: пара узлов упаковывается в одно целое


@dataclass(frozen=True, slots=True)
class RouteStart:
    """Откуда и с какого момента бригада может продолжать маршрут."""

    location: Location
    time_min: int


@dataclass(slots=True)
class _Stop:
    order: Order
    node: int
    ws: int
    we: int
    dur: int
    deadline: int | None
    zone: str


@dataclass(slots=True)
class _Crew:
    engineer: Engineer
    ss: int
    se: int
    transport: Transport
    legs: dict[int, tuple[float, int]]  # отрезки для этого транспорта: (a << NODE_BITS) | b


@dataclass(frozen=True, slots=True)
class Insertion:
    pos: int
    cost: float  # прирост стоимости маршрута (км + штрафы)
    delta_km: float
    delta_travel_min: int
    start_min: int  # начало работ по вставляемой заявке
    shift_min: int  # суммарный сдвиг начала последующих заявок
    breach_delta: int = 0  # сколько аварий маршрута станут начинаться позже SLA


@dataclass(frozen=True, slots=True)
class Violation:
    code: ReasonCode
    order_id: str
    message: str


@dataclass(frozen=True, slots=True)
class ScheduledStop:
    order_id: str
    departure: int
    arrival: int
    start: int
    end: int
    travel_min: int
    dist_km: float
    idle_min: int


@dataclass(slots=True)
class Schedule:
    stops: list[ScheduledStop]
    violation: Violation | None

    @property
    def feasible(self) -> bool:
        return self.violation is None

    @property
    def km(self) -> float:
        return sum(s.dist_km for s in self.stops)


@dataclass(frozen=True, slots=True)
class RouteState:
    """Допустимый маршрут бригады от RouteStart с данными для быстрых проверок вставки."""

    engineer: Engineer
    start: RouteStart
    start_node: int
    stops: tuple[_Stop, ...]
    starts: tuple[int, ...]
    ends: tuple[int, ...]
    leg_km: tuple[float, ...]  # км на отрезке до i-й заявки
    leg_min: tuple[int, ...]
    latest: tuple[int, ...]  # самое позднее начало i-й заявки, при котором хвост остаётся допустимым
    em_from: tuple[bool, ...]  # есть ли авария на позициях >= i
    km: float
    travel_min: int
    late_min: int  # опоздание аварий сверх SLA
    breaches: int  # аварии, начатые позже SLA
    zone_switches: int
    cost: float

    @property
    def orders(self) -> tuple[Order, ...]:
        return tuple(s.order for s in self.stops)

    @property
    def order_ids(self) -> tuple[str, ...]:
        return tuple(s.order.id for s in self.stops)

    def __len__(self) -> int:
        return len(self.stops)


class RouteEvaluator:
    def __init__(
        self,
        distance_provider: DistanceProvider | None = None,
        *,
        day_start_min: int = 600,
        constraints: Iterable[PairConstraint] = DEFAULT_PAIR_CONSTRAINTS,
    ):
        self.matrix = as_matrix(distance_provider)
        self.day_start_min = day_start_min
        self.constraints = tuple(constraints)
        self._stops: dict[str, _Stop] = {}
        self._crews: dict[str, _Crew] = {}
        self._pair: dict[tuple[str, str], tuple[Engineer, Order, ReasonCode | None]] = {}
        self._best: dict[tuple[str, int, bool], tuple[RouteState, Order, Insertion | None]] = {}
        self._legs: dict[Transport, dict[int, tuple[float, int]]] = {}

    # ------------------------------------------------------------------ компиляция

    def _stop(self, order: Order) -> _Stop:
        cached = self._stops.get(order.id)
        if cached is not None and cached.order is order:
            return cached
        st = _Stop(
            order=order,
            node=self.matrix.node(order.location),
            ws=order.window.start_min,
            we=order.window.end_min,
            dur=order.duration_min,
            deadline=obj.sla_deadline(order, self.day_start_min),
            zone=obj.zone_of(order.location.district),
        )
        self._stops[order.id] = st
        return st

    def _crew(self, engineer: Engineer) -> _Crew:
        cached = self._crews.get(engineer.id)
        if cached is not None and cached.engineer is engineer:
            return cached
        crew = _Crew(
            engineer=engineer,
            ss=engineer.shift.start_min,
            se=engineer.shift.end_min,
            transport=engineer.transport,
            legs=self._legs.setdefault(engineer.transport, {}),
        )
        self._crews[engineer.id] = crew
        return crew

    def _leg(self, crew: _Crew, a: int, b: int) -> tuple[float, int]:
        """Отрезок a → b для транспорта бригады: матрица + таблица с целочисленным ключом
        (в горячих циклах ключ (a, b, транспорт) заметно дороже)."""
        key = (a << NODE_BITS) | b
        v = crew.legs.get(key)
        if v is None:
            v = crew.legs[key] = self.matrix.leg(a, b, crew.transport)
        return v

    def leg_fn(self, engineer: Engineer) -> Callable[[int, int], tuple[float, int]]:
        """Функция «отрезок a → b (км, мин)» для транспорта бригады — для циклов поиска."""
        crew = self._crew(engineer)
        legs = crew.legs

        def leg(a: int, b: int) -> tuple[float, int]:
            v = legs.get((a << NODE_BITS) | b)
            return v if v is not None else self._leg(crew, a, b)

        return leg

    # ------------------------------------------------------------------ совместимость

    def start_of(self, engineer: Engineer) -> RouteStart:
        return RouteStart(engineer.depot, engineer.shift.start_min)

    def pair_reason(self, engineer: Engineer, order: Order) -> ReasonCode | None:
        """Парные ограничения (навык, транспорт): не зависят от маршрута и времени."""
        key = (engineer.id, order.id)
        cached = self._pair.get(key)
        if cached is not None and cached[0] is engineer and cached[1] is order:
            return cached[2]
        reason = None
        for c in self.constraints:
            if not c.allows(engineer, order):
                reason = c.code
                break
        self._pair[key] = (engineer, order, reason)
        return reason

    def solo_reason(
        self, engineer: Engineer, order: Order, start: RouteStart | None = None
    ) -> ReasonCode | None:
        """Успевает ли бригада выполнить заявку, если возьмёт только её (из точки start)."""
        crew = self._crew(engineer)
        o = self._stop(order)
        start = start or self.start_of(engineer)
        _, mins = self.matrix.leg(self.matrix.node(start.location), o.node, crew.transport)
        s = max(max(start.time_min, crew.ss) + mins, o.ws)
        if s > o.we or s + o.dur > crew.se:
            return ReasonCode.SHIFT_WINDOW
        return None

    def compat(
        self,
        engineer: Engineer,
        order: Order,
        start: RouteStart | None = None,
        *,
        available: bool = True,
    ) -> ReasonCode | None:
        if not available:
            return ReasonCode.UNAVAILABLE
        return self.pair_reason(engineer, order) or self.solo_reason(engineer, order, start)

    # ------------------------------------------------------------------ маршрут

    def empty(self, engineer: Engineer, start: RouteStart | None = None) -> RouteState:
        state = self.state(engineer, (), start)
        assert state is not None
        return state

    def state(
        self,
        engineer: Engineer,
        orders: Sequence[Order],
        start: RouteStart | None = None,
    ) -> RouteState | None:
        """Строит допустимый маршрут или возвращает None при любом нарушении."""
        crew = self._crew(engineer)
        legs = crew.legs
        start = start or self.start_of(engineer)
        start_node = self.matrix.node(start.location)

        stops: list[_Stop] = []
        starts: list[int] = []
        ends: list[int] = []
        leg_km: list[float] = []
        leg_min: list[int] = []
        t = max(start.time_min, crew.ss)
        node = start_node
        km = 0.0
        travel = 0
        late = 0
        breaches = 0
        switches = 0
        prev_zone: str | None = None

        for order in orders:
            o = self._stop(order)
            v = legs.get((node << NODE_BITS) | o.node)
            dk, dm = v if v is not None else self._leg(crew, node, o.node)
            s = t + dm
            s = max(s, o.ws)
            if s > o.we:
                return None
            e = s + o.dur
            if e > crew.se:
                return None
            if o.deadline is not None and s > o.deadline:
                late += s - o.deadline
                breaches += 1
            if prev_zone is not None and prev_zone != o.zone:
                switches += 1
            prev_zone = o.zone
            stops.append(o)
            starts.append(s)
            ends.append(e)
            leg_km.append(dk)
            leg_min.append(dm)
            km += dk
            travel += dm
            t = e
            node = o.node

        n = len(stops)
        latest = [0] * n
        em_from = [False] * (n + 1)
        for i in range(n - 1, -1, -1):
            o = stops[i]
            if i == n - 1:
                lim = crew.se - o.dur
            else:
                lim = latest[i + 1] - leg_min[i + 1] - o.dur
            latest[i] = min(o.we, lim)
            em_from[i] = em_from[i + 1] or o.deadline is not None

        cost = (
            km
            + obj.LATE_KM_PER_MIN * late
            + obj.SLA_BREACH_KM * breaches
            + obj.ZONE_SWITCH_KM * switches
        )
        return RouteState(
            engineer=engineer,
            start=start,
            start_node=start_node,
            stops=tuple(stops),
            starts=tuple(starts),
            ends=tuple(ends),
            leg_km=tuple(leg_km),
            leg_min=tuple(leg_min),
            latest=tuple(latest),
            em_from=tuple(em_from),
            km=km,
            travel_min=travel,
            late_min=late,
            breaches=breaches,
            zone_switches=switches,
            cost=cost,
        )

    def with_orders(self, state: RouteState, orders: Sequence[Order]) -> RouteState | None:
        return self.state(state.engineer, orders, state.start)

    def without(self, state: RouteState, idx: int, length: int = 1) -> RouteState | None:
        orders = state.orders
        return self.state(state.engineer, orders[:idx] + orders[idx + length :], state.start)

    def insert(self, state: RouteState, order: Order, pos: int) -> RouteState:
        orders = state.orders
        new = self.state(state.engineer, orders[:pos] + (order,) + orders[pos:], state.start)
        if new is None:
            raise ValueError(f"Вставка заявки {order.id} на позицию {pos} недопустима")
        return new

    # ------------------------------------------------------------------ вставка

    def insertions(
        self,
        state: RouteState,
        order: Order,
        *,
        positions: Iterable[int] | None = None,
        with_shift: bool = False,
    ) -> list[Insertion]:
        """Все допустимые позиции вставки заявки в маршрут; проверка каждой — O(1) по latest[]."""
        crew = self._crew(state.engineer)
        tr = crew.transport
        o = self._stop(order)
        leg = self.matrix.leg
        n = len(state.stops)
        out: list[Insertion] = []
        base_t = max(state.start.time_min, crew.ss)

        for p in positions if positions is not None else range(n + 1):
            if p == 0:
                prev_end, prev_node, prev_zone = base_t, state.start_node, None
            else:
                prev_end = state.ends[p - 1]
                prev_node = state.stops[p - 1].node
                prev_zone = state.stops[p - 1].zone
            if prev_end > o.we:
                break
            km1, m1 = leg(prev_node, o.node, tr)
            s = prev_end + m1
            s = max(s, o.ws)
            if s > o.we:
                continue
            e = s + o.dur
            own_late = max(0, s - o.deadline) if o.deadline is not None else 0
            dbreach = 1 if own_late > 0 else 0
            if p == n:
                if e > crew.se:
                    continue
                dkm, dmin = km1, m1
                dsw = 1 if prev_zone is not None and prev_zone != o.zone else 0
                dlate, shift = own_late, 0
            else:
                nxt = state.stops[p]
                km2, m2 = leg(o.node, nxt.node, tr)
                s_next = e + m2
                s_next = max(s_next, nxt.ws)
                if s_next > state.latest[p]:
                    continue
                dkm = km1 + km2 - state.leg_km[p]
                dmin = m1 + m2 - state.leg_min[p]
                old_sw = 1 if prev_zone is not None and prev_zone != nxt.zone else 0
                new_sw = (1 if prev_zone is not None and prev_zone != o.zone else 0) + (
                    1 if o.zone != nxt.zone else 0
                )
                dsw = new_sw - old_sw
                dlate, shift = own_late, 0
                if state.em_from[p] or with_shift:
                    suffix_late, shift, suffix_breach = self._propagate(state, p, s_next)
                    dlate += suffix_late
                    dbreach += suffix_breach
            cost = (
                dkm
                + obj.LATE_KM_PER_MIN * dlate
                + obj.SLA_BREACH_KM * dbreach
                + obj.ZONE_SWITCH_KM * dsw
            )
            out.append(Insertion(p, cost, dkm, dmin, s, shift, dbreach))
        return out

    def best_insertion(
        self, state: RouteState, order: Order, *, with_shift: bool = False
    ) -> Insertion | None:
        """Самая дешёвая позиция вставки (при равной цене — самая ранняя), как минимум по
        insertions(). RouteState неизменяем, поэтому ответ для пары «маршрут × заявка» запоминается:
        LNS многократно проверяет одни и те же нетронутые маршруты."""
        key = (order.id, id(state), with_shift)
        hit = self._best.get(key)
        if hit is not None and hit[0] is state and hit[1] is order:
            return hit[2]
        best = self._scan_best(state, order, with_shift)
        if len(self._best) >= BEST_CACHE_SIZE:
            self._best.clear()
        self._best[key] = (state, order, best)
        return best

    def _scan_best(self, state: RouteState, order: Order, with_shift: bool) -> Insertion | None:
        """Тот же перебор, что в insertions(), но без списка кандидатов."""
        crew = self._crew(state.engineer)
        legs = crew.legs
        o = self._stop(order)
        stops = state.stops
        n = len(stops)
        we, ws, dur, deadline, zone, node = o.we, o.ws, o.dur, o.deadline, o.zone, o.node
        late_k, zone_k, breach_k = obj.LATE_KM_PER_MIN, obj.ZONE_SWITCH_KM, obj.SLA_BREACH_KM
        best: tuple[int, float, float, int, int, int, int] | None = None
        best_cost = 0.0
        base_t = max(state.start.time_min, crew.ss)

        for p in range(n + 1):
            if p == 0:
                prev_end, prev_node, prev_zone = base_t, state.start_node, None
            else:
                prev = stops[p - 1]
                prev_end, prev_node, prev_zone = state.ends[p - 1], prev.node, prev.zone
            if prev_end > we:
                break
            v = legs.get((prev_node << NODE_BITS) | node)
            km1, m1 = v if v is not None else self._leg(crew, prev_node, node)
            s = prev_end + m1
            s = max(s, ws)
            if s > we:
                continue
            e = s + dur
            dlate = s - deadline if deadline is not None and s > deadline else 0
            dbreach = 1 if dlate > 0 else 0
            shift = 0
            if p == n:
                if e > crew.se:
                    continue
                dkm, dmin = km1, m1
                dsw = 1 if prev_zone is not None and prev_zone != zone else 0
            else:
                nxt = stops[p]
                v = legs.get((node << NODE_BITS) | nxt.node)
                km2, m2 = v if v is not None else self._leg(crew, node, nxt.node)
                s_next = e + m2
                s_next = max(s_next, nxt.ws)
                if s_next > state.latest[p]:
                    continue
                dkm = km1 + km2 - state.leg_km[p]
                dmin = m1 + m2 - state.leg_min[p]
                old_sw = 1 if prev_zone is not None and prev_zone != nxt.zone else 0
                new_sw = (1 if prev_zone is not None and prev_zone != zone else 0) + (
                    1 if zone != nxt.zone else 0
                )
                dsw = new_sw - old_sw
                if state.em_from[p] or with_shift:
                    suffix_late, shift, suffix_breach = self._propagate(state, p, s_next)
                    dlate += suffix_late
                    dbreach += suffix_breach
            cost = dkm + late_k * dlate + breach_k * dbreach + zone_k * dsw
            if best is None or cost < best_cost - 1e-9:
                best, best_cost = (p, cost, dkm, dmin, s, shift, dbreach), cost
        if best is None:
            return None
        return Insertion(*best)

    def _propagate(self, state: RouteState, p: int, new_start: int) -> tuple[int, int, int]:
        """Сдвиг хвоста маршрута, если p-я заявка начнётся в new_start:
        (Δопоздания аварий, Σсдвига, Δчисла аварий позже SLA)."""
        dlate = 0
        shift = 0
        dbreach = 0
        t = new_start
        n = len(state.stops)
        k = p
        while k < n:
            old = state.starts[k]
            if t <= old:
                break
            shift += t - old
            dl = state.stops[k].deadline
            if dl is not None:
                dlate += max(0, t - dl) - max(0, old - dl)
                dbreach += (t > dl) - (old > dl)
            if k + 1 < n:
                nxt = state.stops[k + 1]
                t2 = t + state.stops[k].dur + state.leg_min[k + 1]
                t = max(nxt.ws, t2)
            k += 1
        return dlate, shift, dbreach

    # ------------------------------------------------------------------ расписание

    def schedule(
        self,
        engineer: Engineer,
        orders: Sequence[Order],
        start: RouteStart | None = None,
    ) -> Schedule:
        """Полное расписание с первой причиной недопустимости (для плана, тестов и объяснений)."""
        crew = self._crew(engineer)
        tr = crew.transport
        start = start or self.start_of(engineer)
        node = self.matrix.node(start.location)
        t = max(start.time_min, crew.ss)
        stops: list[ScheduledStop] = []
        for order in orders:
            o = self._stop(order)
            dk, dm = self.matrix.leg(node, o.node, tr)
            s = t + dm
            s = max(s, o.ws)
            if s > o.we:
                return Schedule(
                    stops,
                    Violation(
                        ReasonCode.WINDOW_LATE,
                        order.id,
                        f"начало работ в {minutes_to_time(s)} позже окна "
                        f"{order.window.start}–{order.window.end}",
                    ),
                )
            e = s + o.dur
            if e > crew.se:
                return Schedule(
                    stops,
                    Violation(
                        ReasonCode.SHIFT_END,
                        order.id,
                        f"работа закончится в {minutes_to_time(e)}, после конца смены "
                        f"{engineer.shift.end}",
                    ),
                )
            departure = s - dm
            stops.append(
                ScheduledStop(
                    order_id=order.id,
                    departure=departure,
                    arrival=s,
                    start=s,
                    end=e,
                    travel_min=dm,
                    dist_km=dk,
                    idle_min=departure - t,
                )
            )
            t = e
            node = o.node
        return Schedule(stops, None)
