"""Ruin & recreate (LNS): снять часть заявок и вставить их заново жадной вставкой SISR.

Операторы разрушения (выбор — генератором с фиксированным seed):
  - случайный — любые назначенные заявки;
  - связанный (Shaw) — близкие к затравке по расстоянию и времени начала;
  - строки (SISR) — отрезки подряд идущих заявок в маршрутах рядом с затравкой;
  - худший — заявки с наибольшим вкладом в стоимость своего маршрута;
  - маршрут — все заявки одной лёгкой бригады.
Приёмка — по PlanScore: назначенный вес не теряется, аварий позже SLA и бригад не становится
больше, стоимость — отжиг с убывающей температурой. Бюджет — число итераций: результат одинаков
на любой машине.

Снятие маршрута (eliminate_routes): заявки лёгкой бригады уходят в пул, бригада исключается,
и LNS без вывода новых бригад ищет план, в котором вес пула не больше исходного веса неназначенных,
а аварий позже SLA не больше, чем было. Приёмка как в SISR (Christiaens, Vanden Berghe, 2020):
пул стал легче или в нём заявки, которые реже оставались без места.
"""

import heapq
import math
import random
from collections.abc import Callable, Sequence

from app.domain.models import Order
from app.solver.evaluator import RouteState
from app.solver.fleet import Fleet
from app.solver.objective import CREW_PENALTY, activation_bias
from app.solver.search.insertion import insert_pool

KM_PER_DEG = 111.2
SHAW_POWER = 6  # чем больше, тем сильнее выбор тяготеет к самым связанным заявкам
WORST_POWER = 3
TIME_NORM_MIN = 120.0  # 2 часа разницы во времени начала «весят» как DIST_NORM_KM
DIST_NORM_KM = 5.0
STRING_MAX = 8  # самая длинная снимаемая строка
NEAR_K = 40  # соседей у заявки для снятия строк
BLINK = 0.05  # вероятность пропустить маршрут при жадной вставке (разнообразие поиска)

Destroy = Callable[[int, list[Order]], list[str]]


def weight(orders: Sequence[Order]) -> int:
    return sum(o.kind.weight for o in orders)


class RuinRecreate:
    """LNS над Fleet. Генератор случайных чисел создаётся на каждый запуск с фиксированным seed."""

    def __init__(
        self,
        fleet: Fleet,
        orders: Sequence[Order],
        *,
        seed: int = 2026,
        crew_penalty: float = CREW_PENALTY,
        temp_start: float = 5.0,
        temp_end: float = 0.1,
        remove_min: int = 3,
        remove_max: int = 15,
    ):
        self.fleet = fleet
        self.ev = fleet.evaluator
        self.rng = random.Random(seed)
        self.crew_penalty = crew_penalty
        self.temp_start = temp_start
        self.temp_end = temp_end
        self.remove_min = remove_min
        self.remove_max = remove_max
        self.orders = {o.id: o for o in orders}
        lat0 = sum(o.location.lat for o in orders) / len(orders) if orders else 55.75
        kx = KM_PER_DEG * math.cos(math.radians(lat0))
        self.xy = {o.id: (o.location.lon * kx, o.location.lat * KM_PER_DEG) for o in orders}
        ids = sorted(self.orders)
        k = min(len(ids), NEAR_K)
        self.near = {
            i: heapq.nsmallest(k, ids, key=lambda j, i=i: (self.dist(i, j), j)) for i in ids
        }
        self.pos: dict[str, tuple[str, int]] = {}
        self._gain_cache: dict[int, tuple[RouteState, list[float]]] = {}

    # ------------------------------------------------------------------ служебное

    def dist(self, a: str, b: str) -> float:
        ax, ay = self.xy[a]
        bx, by = self.xy[b]
        return math.hypot(ax - bx, ay - by)

    def _index(self) -> list[str]:
        """Где стоит каждая назначенная заявка; список назначенных в детерминированном порядке."""
        self.pos = {}
        assigned: list[str] = []
        for e in self.fleet.engineers:
            for i, s in enumerate(self.fleet.states[e.id].stops):
                self.pos[s.order.id] = (e.id, i)
                assigned.append(s.order.id)
        return assigned

    def _start_of(self, oid: str) -> float:
        where = self.pos.get(oid)
        if where is None:
            o = self.orders[oid]
            return (o.window.start_min + min(o.window.end_min, o.window.start_min + 120)) / 2
        eid, i = where
        return self.fleet.states[eid].starts[i]

    def _pick(self, ranked: list[str], k: int, power: float) -> list[str]:
        """Выбор k элементов, тяготеющий к началу списка (рандомизация Ропке — Писинджера)."""
        ranked = list(ranked)
        out: list[str] = []
        while ranked and len(out) < k:
            idx = int(self.rng.random() ** power * len(ranked))
            out.append(ranked.pop(idx))
        return out

    def _remove(self, ids: list[str]) -> list[Order]:
        """Снимает заявки с маршрутов. Снятие не нарушает допустимость, но проверяем всё равно."""
        by_route: dict[str, list[str]] = {}
        for oid in ids:
            where = self.pos.get(oid)
            if where is not None:
                by_route.setdefault(where[0], []).append(oid)
        removed: list[Order] = []
        for eid in sorted(by_route):
            st = self.fleet.states[eid]
            drop = set(by_route[eid])
            keep = [o for o in st.orders if o.id not in drop]
            new = self.ev.state(st.engineer, keep, st.start)
            if new is None:
                continue
            self.fleet.states[eid] = new
            removed.extend(o for o in st.orders if o.id in drop)
        return removed

    def _free_routes(self) -> list[str]:
        return [
            e.id
            for e in self.fleet.engineers
            if len(self.fleet.states[e.id]) > 0 and not self.fleet.pinned.get(e.id, False)
        ]

    # ------------------------------------------------------------------ разрушение

    def _seed(self, assigned: list[str], pool: list[Order]) -> str:
        """Затравка: при снятии маршрута чаще заявка из пула (освобождаем место рядом с ней)."""
        if pool and (not assigned or self.rng.random() < 0.7):
            return self.rng.choice(pool).id
        return self.rng.choice(assigned)

    def random_removal(self, q: int, pool: list[Order]) -> list[str]:
        assigned = self._index()
        return self.rng.sample(assigned, min(q, len(assigned)))

    def shaw_removal(self, q: int, pool: list[Order]) -> list[str]:
        assigned = self._index()
        if not assigned:
            return []
        seed = self._seed(assigned, pool)
        t0 = self._start_of(seed)
        ranked = sorted(
            (oid for oid in assigned if oid != seed),
            key=lambda j: (
                self.dist(seed, j) / DIST_NORM_KM + abs(self._start_of(j) - t0) / TIME_NORM_MIN,
                j,
            ),
        )
        picked = [seed] if seed in self.pos else []
        return picked + self._pick(ranked, q - len(picked), SHAW_POWER)

    def string_removal(self, q: int, pool: list[Order]) -> list[str]:
        assigned = self._index()
        if not assigned:
            return []
        seed = self._seed(assigned, pool)
        routes = self._free_routes()
        avg_len = sum(len(self.fleet.states[r]) for r in routes) / max(1, len(routes))
        ls_max = max(1.0, min(float(STRING_MAX), avg_len))
        ks_max = max(1.0, 4.0 * q / (1.0 + ls_max) - 1.0)
        ks = int(self.rng.uniform(1.0, ks_max + 1.0))
        ruined: list[str] = []
        out: list[str] = []
        for c in self.near[seed]:
            if len(ruined) >= ks:
                break
            where = self.pos.get(c)
            if where is None or where[0] in ruined or self.fleet.pinned.get(where[0], False):
                continue
            eid, i = where
            st = self.fleet.states[eid]
            n = len(st)
            length = int(self.rng.uniform(1.0, min(float(n), ls_max) + 1.0))
            length = max(1, min(length, n))
            lo, hi = max(0, i - length + 1), min(i, n - length)
            s = self.rng.randint(lo, hi)
            out.extend(st.order_ids[s : s + length])
            ruined.append(eid)
        return out

    def _gains(self, st: RouteState) -> list[float]:
        cached = self._gain_cache.get(id(st))
        if cached is not None and cached[0] is st:
            return cached[1]
        gains = []
        for i in range(len(st)):
            rem = self.ev.without(st, i)
            gains.append(st.cost - rem.cost if rem is not None else -math.inf)
        if len(self._gain_cache) > 4096:
            self._gain_cache.clear()
        self._gain_cache[id(st)] = (st, gains)
        return gains

    def worst_removal(self, q: int, pool: list[Order]) -> list[str]:
        self._index()
        scored: list[tuple[float, str]] = []
        for e in self.fleet.engineers:
            st = self.fleet.states[e.id]
            for i, g in enumerate(self._gains(st)):
                scored.append((-g, st.stops[i].order.id))
        scored.sort()
        return self._pick([oid for _, oid in scored], q, WORST_POWER)

    def route_removal(self, q: int, pool: list[Order]) -> list[str]:
        self._index()
        routes = sorted(
            self._free_routes(), key=lambda eid: (len(self.fleet.states[eid]), eid)
        )
        if not routes:
            return []
        eid = routes[int(self.rng.random() ** 2 * len(routes))]
        return list(self.fleet.states[eid].order_ids)

    # ------------------------------------------------------------------ поиск

    def _temperature(self, it: int, total: int) -> float:
        if total <= 1:
            return self.temp_end
        frac = it / (total - 1)
        return self.temp_start * (self.temp_end / self.temp_start) ** frac

    def _sa_accept(self, delta: float, temp: float) -> bool:
        if delta <= 1e-9:
            return True
        return self.rng.random() < math.exp(-delta / temp)

    def _count(self) -> int:
        n = len(self.orders)
        hi = max(self.remove_min, min(self.remove_max, n // 4))
        return self.rng.randint(self.remove_min, hi)

    # ------------------------------------------------------------------ восстановление

    def _recreate(
        self,
        pool: list[Order],
        *,
        allow_activation: bool = True,
        exclude: frozenset[str] = frozenset(),
    ) -> list[Order]:
        """Жадная вставка SISR: сначала высший приоритет, внутри — случайный порядок, раннее
        или узкое окно; каждая заявка — в самую дешёвую позицию; «моргание» иногда пропускает
        маршрут. Разнообразнее и быстрее regret-вставки, поэтому LNS за тот же бюджет находит
        больше. Возвращает заявки, которые не встали никуда (в порядке пула)."""
        fleet, ev, rng = self.fleet, self.ev, self.rng
        mode = rng.randrange(3)
        noise = {o.id: rng.random() for o in pool}

        def key(o: Order) -> tuple:
            if mode == 0:
                k: float = noise[o.id]
            elif mode == 1:
                k = o.window.start_min
            else:
                k = o.window.end_min - o.window.start_min
            return o.kind.priority_tier, k, o.id

        left: list[Order] = []
        engineers = [
            e for e in fleet.engineers if e.id not in exclude and fleet.available.get(e.id, True)
        ]
        # кто на линии — множеством: вставка меняет только маршрут, куда встала заявка
        on_line_ids = {e.id for e in engineers if fleet.is_active(e.id)}
        for o in sorted(pool, key=key):
            best: tuple[float, str, int] | None = None
            # сначала бригады на линии: вставка в пустой маршрут не дешевле штрафа за вывод
            # бригады, поэтому при дешёвом варианте на линии резерв можно не проверять
            active = [e for e in engineers if e.id in on_line_ids]
            reserve = [e for e in engineers if e.id not in on_line_ids] if allow_activation else []
            for e in active + reserve:
                eid = e.id
                on_line = eid in on_line_ids
                if not on_line and best is not None and best[0] < self.crew_penalty:
                    break
                if ev.pair_reason(e, o) is not None:
                    continue
                if rng.random() < BLINK:
                    continue
                ins = ev.best_insertion(fleet.states[eid], o)
                if ins is None:
                    continue
                c = ins.cost + (0.0 if on_line else self.crew_penalty + activation_bias(e))
                if best is None or c < best[0] - 1e-9:
                    best = (c, eid, ins.pos)
            if best is None:
                left.append(o)
                continue
            _, eid, pos = best
            fleet.states[eid] = ev.insert(fleet.states[eid], o, pos)
            on_line_ids.add(eid)
        left_ids = {o.id for o in left}
        return [o for o in pool if o.id in left_ids]

    def improve(
        self,
        left: list[Order],
        iterations: int,
        operators: Sequence[tuple[Destroy, float]] | None = None,
    ) -> list[Order]:
        """Основная фаза: вставляет неназначенные и снижает стоимость. План принимается, если его
        ранг (потерянный вес, аварии позже SLA, бригады) не хуже, а при равном ранге — по отжигу.
        Возвращает неназначенные заявки лучшего найденного плана."""
        fleet = self.fleet
        ops = list(
            operators
            or (
                (self.string_removal, 4.0),
                (self.shaw_removal, 3.0),
                (self.random_removal, 1.0),
                (self.worst_removal, 1.0),
                (self.route_removal, 0.5),
            )
        )
        funcs = [f for f, _ in ops]
        weights = [w for _, w in ops]
        cur = fleet.score(left)
        best, best_snap, best_left = cur, fleet.snapshot(), list(left)
        for it in range(iterations):
            temp = self._temperature(it, iterations)
            snap = fleet.snapshot()
            destroy = self.rng.choices(funcs, weights)[0]
            removed = self._remove(destroy(self._count(), left))
            if not removed:
                continue
            new_left = self._recreate(removed + left)
            new = fleet.score(new_left)
            if new.rank != cur.rank:
                ok = new.rank < cur.rank
            else:
                ok = self._sa_accept(new.cost - cur.cost, temp)
            if not ok:
                fleet.restore(snap)
                continue
            cur, left = new, new_left
            if cur.better_than(best):
                best, best_snap, best_left = cur, fleet.snapshot(), list(left)
        fleet.restore(best_snap)
        return best_left

    def eliminate_routes(
        self, left: list[Order], *, iterations: int, per_attempt: int
    ) -> list[Order]:
        """Пробует освободить бригады по одной, от самых лёгких, пока не кончится бюджет итераций.
        Возвращает новые неназначенные заявки (их вес не больше исходного)."""
        failed: list[str] = []
        budget = iterations
        while budget > 0:
            cands = [eid for eid in self._free_routes() if eid not in failed]
            if not cands:
                break
            target = min(cands, key=lambda eid: (self._load(eid), eid))
            ok, left, used = self._eliminate(target, left, min(per_attempt, budget))
            budget -= max(1, used)
            if not ok:
                failed.append(target)
        return left

    def _load(self, eid: str) -> int:
        st = self.fleet.states[eid]
        return sum(s.dur for s in st.stops) + st.travel_min

    def _eliminate(
        self, target: str, left: list[Order], iterations: int
    ) -> tuple[bool, list[Order], int]:
        """Снятие одного маршрута. Приёмка как в SISR: пул стал легче или в нём заявки, которые
        реже оставались без места (счётчики «отсутствия»), — так трудные заявки по очереди
        находят место, а в пуле остаются лёгкие. Возвращает (успех, неназначенные, итераций)."""
        fleet = self.fleet
        base_lost, base_breaches = weight(left), fleet.breaches()
        snap = fleet.snapshot()
        st = fleet.states[target]
        exclude = frozenset({target})
        fleet.states[target] = self.ev.empty(st.engineer, st.start)
        pool = insert_pool(
            fleet,
            list(st.orders) + list(left),
            crew_penalty=self.crew_penalty,
            allow_activation=False,
            exclude=exclude,
        )
        absence: dict[str, int] = {}

        def absent(orders: list[Order]) -> int:
            return sum(absence.get(o.id, 0) for o in orders)

        def excess() -> int:
            """Сколько аварий сверх исходного числа начнутся позже SLA."""
            return max(0, fleet.breaches() - base_breaches)

        cur_w, cur_x = weight(pool), excess()
        funcs = [self.string_removal, self.shaw_removal, self.random_removal]
        weights = [4.0, 4.0, 1.0]
        used = 0
        while (cur_x > 0 or cur_w > base_lost) and used < iterations:
            used += 1
            s2 = fleet.snapshot()
            destroy = self.rng.choices(funcs, weights)[0]
            removed = self._remove(destroy(self._count(), pool))
            new_pool = self._recreate(removed + pool, allow_activation=False, exclude=exclude)
            w, x = weight(new_pool), excess()
            if x != cur_x:
                ok = x < cur_x
            else:
                ok = w < cur_w or absent(new_pool) < absent(pool)
            if ok:
                pool, cur_w, cur_x = new_pool, w, x
            else:
                fleet.restore(s2)
            for o in pool:
                absence[o.id] = absence.get(o.id, 0) + 1
        if cur_x == 0 and cur_w <= base_lost:
            return True, pool, used
        fleet.restore(snap)
        return False, left, used
