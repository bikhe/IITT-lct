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

MAX_LNS_ROUNDS = 6


def day_start_of(engineers: list[Engineer]) -> int:
    return min((e.shift.start_min for e in engineers), default=600)


class Solver:
    """Фасад солвера: regret-вставка → локальный поиск → доназначение → LNS «удаление маршрута»."""

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
        if self.use_local_search:
            improve(fleet)
            left = insert_pool(fleet, left, crew_penalty=self.crew_penalty)
            for _ in range(MAX_LNS_ROUNDS):
                if not remove_routes(fleet, crew_penalty=self.crew_penalty):
                    break
                improve(fleet)
            left = insert_pool(fleet, left, crew_penalty=self.crew_penalty)
            improve(fleet)
        return fleet, left

    def solve(self, orders: list[Order], engineers: list[Engineer]) -> Plan:
        """Решает задачу распределения заявок и построения маршрутов."""
        fleet, left = self.optimize(orders, engineers)
        unassigned = {o.id: diagnose(fleet, o) for o in left}
        extra = estimate_extra_crews(fleet.evaluator, engineers, left)
        return build_plan(fleet, orders, unassigned, extra_crews_needed=extra)
