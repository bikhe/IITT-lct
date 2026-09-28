from app.domain.models import Engineer, Order, Plan
from app.geo.base import DistanceProvider
from app.geo.matrix import as_matrix
from app.solver.diagnose import diagnose, estimate_extra_crews
from app.solver.evaluator import RouteEvaluator
from app.solver.fleet import Fleet
from app.solver.plan_builder import build_plan


class BaselineSolver:
    """Базовый вариант распределения заявок согласно ТЗ §2.3.

    Заявки обрабатываются строго по порядку поступления и назначаются первому по порядку во
    входных данных инженеру, который удовлетворяет обязательным ограничениям; порядок посещения
    соответствует порядку назначения (заявка добавляется в конец маршрута).
    Допустимость проверяет тот же RouteEvaluator, что и у оптимизатора.
    """

    def __init__(self, distance_provider: DistanceProvider | None = None):
        self.matrix = as_matrix(distance_provider)
        self.distance_provider = self.matrix

    def solve(self, orders: list[Order], engineers: list[Engineer]) -> Plan:
        ev = RouteEvaluator(
            self.matrix, day_start_min=min((e.shift.start_min for e in engineers), default=600)
        )
        fleet = Fleet.empty(ev, engineers)
        left: list[Order] = []
        for order in orders:
            for eng in engineers:
                st = fleet.states[eng.id]
                if ev.pair_reason(eng, order) is not None:
                    continue
                if not ev.insertions(st, order, positions=[len(st)]):
                    continue
                fleet.states[eng.id] = ev.insert(st, order, len(st))
                break
            else:
                left.append(order)

        unassigned = {o.id: diagnose(fleet, o, append_only=True) for o in left}
        extra = estimate_extra_crews(ev, engineers, left)
        return build_plan(fleet, orders, unassigned, extra_crews_needed=extra)
