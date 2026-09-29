from app.domain.models import Engineer, Order, Plan
from app.geo.base import DistanceProvider
from app.geo.matrix import as_matrix
from app.solver.diagnose import diagnose, estimate_extra_crews
from app.solver.evaluator import RouteEvaluator
from app.solver.fleet import Fleet
from app.solver.objective import CREW_PENALTY
from app.solver.plan_builder import build_plan
from app.solver.search.insertion import insert_pool
from app.solver.search.lns import remove_routes
from app.solver.search.local_search import improve
from app.solver.search.ruin import RuinRecreate

MAX_LNS_ROUNDS = 6
MAX_FINISH_ROUNDS = 5

# Бюджет ruin & recreate — число итераций, а не время: план одинаков на любой машине.
# Итерация дорожает с числом заявок, поэтому бюджет — «итерации × заявки»: до 100 заявок
# это ~1 с, на 300 заявках — несколько секунд. На маленьких задачах — не больше 20 на заявку.
RR_WORK = 90_000
RR_MAX_ITERATIONS = 1500
RR_PER_ORDER = 20
RR_ELIMINATE_SHARE = 0.4  # доля бюджета на снятие маршрутов (меньше бригад)
RR_PER_ATTEMPT = 150  # итераций на попытку снять один маршрут
RR_SEED = 2026
# Несколько независимых запусков LNS с разными зёрнами, лучший план по цели. Разброс между
# зёрнами заметен (± бригада), поэтому запуски окупаются; на больших задачах — один запуск.
RR_STARTS = 3
RR_MULTISTART_MAX_ORDERS = 150


def day_start_of(engineers: list[Engineer]) -> int:
    return min((e.shift.start_min for e in engineers), default=600)


def rr_iterations(n_orders: int) -> int:
    return min(RR_MAX_ITERATIONS, RR_WORK // max(1, n_orders), RR_PER_ORDER * n_orders)


class Solver:
    """Фасад солвера: regret-вставка → локальный поиск → снятие лёгких маршрутов →
    ruin & recreate (снижение стоимости и снятие маршрутов) → доназначение и локальный поиск."""

    def __init__(
        self,
        distance_provider: DistanceProvider | None = None,
        use_local_search: bool = True,
        crew_penalty: float = CREW_PENALTY,
    ):
        self.matrix = as_matrix(distance_provider)
        self.distance_provider = self.matrix
        self.use_local_search = use_local_search
        self.crew_penalty = crew_penalty

    def evaluator(self, engineers: list[Engineer]) -> RouteEvaluator:
        return RouteEvaluator(self.matrix, day_start_min=day_start_of(engineers))

    def optimize(self, orders: list[Order], engineers: list[Engineer]) -> tuple[Fleet, list[Order]]:
        """Возвращает маршруты и неназначенные заявки (без сборки Plan)."""
        fleet = Fleet.empty(self.evaluator(engineers), engineers)
        left = insert_pool(fleet, list(orders), crew_penalty=self.crew_penalty)
        if not self.use_local_search:
            return fleet, left
        improve(fleet)
        left = insert_pool(fleet, left, crew_penalty=self.crew_penalty)
        for _ in range(MAX_LNS_ROUNDS):
            if not remove_routes(fleet, crew_penalty=self.crew_penalty):
                break
            improve(fleet)
        left = self._finish(fleet, left)
        starts = RR_STARTS if len(orders) <= RR_MULTISTART_MAX_ORDERS else 1
        base, base_left = fleet.snapshot(), list(left)
        best: tuple | None = None
        for k in range(starts):
            fleet.restore(base)
            left = self._ruin_recreate(fleet, orders, list(base_left), seed=RR_SEED + k)
            left = self._finish(fleet, left)
            score = fleet.score(left)
            if best is None or score.better_than(best[0]):
                best = (score, fleet.snapshot(), left)
        assert best is not None
        fleet.restore(best[1])
        return fleet, best[2]

    def _ruin_recreate(
        self, fleet: Fleet, orders: list[Order], left: list[Order], *, seed: int = RR_SEED
    ) -> list[Order]:
        """LNS: сначала стоимость и неназначенные, затем снятие маршрутов, затем снова стоимость."""
        total = rr_iterations(len(orders))
        if total <= 0 or not orders:
            return left
        rr = RuinRecreate(fleet, orders, seed=seed, crew_penalty=self.crew_penalty)
        eliminate = int(total * RR_ELIMINATE_SHARE)
        first = (total - eliminate) // 2
        left = rr.improve(left, first)
        left = rr.eliminate_routes(left, iterations=eliminate, per_attempt=RR_PER_ATTEMPT)
        return rr.improve(left, total - eliminate - first)

    def _finish(self, fleet: Fleet, left: list[Order]) -> list[Order]:
        """Доназначение и локальный поиск по очереди, пока что-то меняется: после улучшения
        маршрутов неназначенная заявка может найти место."""
        for _ in range(MAX_FINISH_ROUNDS):
            before = len(left)
            left = insert_pool(fleet, left, crew_penalty=self.crew_penalty)
            moves = improve(fleet)
            if moves == 0 and len(left) == before:
                break
        return insert_pool(fleet, left, crew_penalty=self.crew_penalty)

    def solve(self, orders: list[Order], engineers: list[Engineer]) -> Plan:
        """Решает задачу распределения заявок и построения маршрутов."""
        fleet, left = self.optimize(orders, engineers)
        unassigned = {o.id: diagnose(fleet, o) for o in left}
        extra = estimate_extra_crews(fleet.evaluator, engineers, left)
        return build_plan(fleet, orders, unassigned, extra_crews_needed=extra)
