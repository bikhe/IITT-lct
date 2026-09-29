"""Эталонный солвер на OR-Tools — мерило качества нашей эвристики (в продукт не входит).

Модель повторяет RouteEvaluator, чтобы планы сравнивались честно:
- узлы: заявки + стартовый узел каждой бригады (её депо) + общий фиктивный финиш, до которого
  0 км и 0 мин из любой точки (возврат в депо не нужен);
- стоимость дуги = км × 1000 (метры); км берутся из той же TravelMatrix, что у нашего солвера;
- размерность «время»: транзит i → j = работа в i + дорога i → j для транспорта бригады
  (своя матрица на каждый вид транспорта); значение в заявке = начало работ, оно лежит в окне;
  старт бригады — внутри смены, финиш — не позже конца смены (финиш наступает сразу после
  последней работы, значит последняя работа заканчивается до конца смены);
- навык и транспорт: VehicleVar(index).SetValues([-1] + подходящие бригады); заодно отсекаются
  пары, в которых бригада не успевает к заявке даже с пустым маршрутом;
- заявку можно не назначить: дизъюнкция со штрафом вес × 10 000 000 (авария 10, подключение 5,
  ремонт и дозаказ 3), поэтому назначенные заявки важнее всего остального;
- фиксированная стоимость бригады — меньше бригад при том же числе назначенных заявок;
- авария: мягкая верхняя граница начала работ = срок SLA, 500 за минуту (≈ 0,5 км за минуту).

Штраф нашей цели за смену зоны (Москва ↔ пригород) по умолчанию не моделируется; его можно
включить параметром zone_switch_cost (20 000 ≈ ZONE_SWITCH_KM). Сам GLS плохо сокращает число
бригад (бригада освобождается, только когда из маршрута уходит последняя заявка), поэтому есть
жёсткий предел max_crews. Поиск: первое решение PARALLEL_CHEAPEST_INSERTION, затем
GUIDED_LOCAL_SEARCH до лимита времени; решатель маршрутов однопоточный, журнал выключен.
Результат зависит от скорости машины (лимит — по времени).

Пакет ortools не входит в зависимости проекта: pip install ortools.
Запуск (из backend/): python -m app.bench.ortools_ref --region southeast --time-limit 20
  [--crew-cost 1000000] [--zone-cost 20000] [--max-crews 9] [--shift 10:00-22:00]
"""

import time
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import Transport
from app.domain.models import Engineer, Order, Plan, TimeWindow
from app.geo.matrix import TravelMatrix
from app.solver import objective as obj
from app.solver.core import day_start_of
from app.solver.diagnose import crew_name, diagnose, estimate_extra_crews
from app.solver.evaluator import RouteEvaluator
from app.solver.fleet import Fleet
from app.solver.plan_builder import build_plan

HORIZON_MIN = 1440  # сутки: предел накопленного времени и ожидания
DROP_PENALTY_PER_WEIGHT = 10_000_000  # штраф за единицу веса неназначенной заявки
LATE_COST_PER_MIN = round(obj.LATE_KM_PER_MIN * 1000)  # опоздание аварии сверх SLA, метры за минуту


@dataclass
class OrtoolsResult:
    """Маршруты OR-Tools в терминах наших ID (до проверки нашим оценщиком)."""

    routes: dict[str, list[str]]  # ID бригады -> ID заявок по порядку (пустой список — не на линии)
    unassigned: list[str]
    objective: int
    wall_s: float
    status: str
    mismatches: list[str] = field(default_factory=list)  # заполняет to_plan

    @property
    def crews(self) -> int:
        return sum(1 for seq in self.routes.values() if seq)


def _import_ortools() -> tuple[Any, Any]:
    try:
        from ortools.constraint_solver import pywrapcp, routing_enums_pb2
    except ImportError as exc:
        raise ImportError(
            "Эталонному солверу нужен пакет ortools, в зависимости проекта он не входит. "
            "Установите его в отдельное окружение: pip install ortools"
        ) from exc
    return pywrapcp, routing_enums_pb2


def solve_ortools(
    orders: list[Order],
    engineers: list[Engineer],
    *,
    matrix: TravelMatrix | None = None,
    time_limit_s: float = 20.0,
    crew_fixed_cost: int = 200_000,
    zone_switch_cost: int = 0,
    max_crews: int | None = None,
) -> OrtoolsResult:
    """Строит план OR-Tools. Стоимости — в метрах: crew_fixed_cost 200 000 ≈ 200 км за бригаду.

    max_crews — жёсткий предел числа бригад на линии: проверить, хватит ли k бригад на все заявки.
    """
    pywrapcp, enums = _import_ortools()
    started = time.perf_counter()
    matrix = matrix or TravelMatrix()
    if not orders or not engineers:
        return OrtoolsResult(
            routes={e.id: [] for e in engineers},
            unassigned=[o.id for o in orders],
            objective=0,
            wall_s=time.perf_counter() - started,
            status="EMPTY",
        )

    ev = RouteEvaluator(matrix, day_start_min=day_start_of(engineers))
    n_orders, n_crews = len(orders), len(engineers)
    end = n_orders + n_crews  # общий финиш; n_orders + v — старт бригады v
    n_nodes = end + 1
    geo = [matrix.node(o.location) for o in orders] + [matrix.node(e.depot) for e in engineers]
    service = [o.duration_min for o in orders] + [0] * (n_crews + 1)
    zones = [obj.zone_of(o.location.district) for o in orders]

    manager = pywrapcp.RoutingIndexManager(
        n_nodes, n_crews, [n_orders + v for v in range(n_crews)], [end] * n_crews
    )
    routing = pywrapcp.RoutingModel(manager)

    # Стоимость дуг: км по транспорту бригады (при haversine км одинаковы для всех видов).
    transports = sorted({e.transport for e in engineers}, key=lambda t: t.value)
    cost_by_tr: dict[Transport, list[list[int]]] = {}
    time_by_tr: dict[Transport, list[list[int]]] = {}
    for tr in transports:
        cost = [[0] * n_nodes for _ in range(n_nodes)]
        transit = [[0] * n_nodes for _ in range(n_nodes)]
        for i in range(end):
            for j in range(end):
                km, mins = matrix.leg(geo[i], geo[j], tr)
                cost[i][j] = round(km * 1000)
                if zone_switch_cost and i < n_orders and j < n_orders and zones[i] != zones[j]:
                    cost[i][j] += zone_switch_cost
                transit[i][j] = service[i] + mins
            transit[i][end] = service[i]
        cost_by_tr[tr] = cost
        time_by_tr[tr] = transit

    cost_values = list(cost_by_tr.values())
    if all(c == cost_values[0] for c in cost_values):
        routing.SetArcCostEvaluatorOfAllVehicles(routing.RegisterTransitMatrix(cost_values[0]))
    else:
        cost_cb = {tr: routing.RegisterTransitMatrix(c) for tr, c in cost_by_tr.items()}
        for v, e in enumerate(engineers):
            routing.SetArcCostEvaluatorOfVehicle(cost_cb[e.transport], v)

    time_cb = {tr: routing.RegisterTransitMatrix(t) for tr, t in time_by_tr.items()}
    routing.AddDimensionWithVehicleTransits(
        [time_cb[e.transport] for e in engineers], HORIZON_MIN, HORIZON_MIN, False, "time"
    )
    time_dim = routing.GetDimensionOrDie("time")

    for v, e in enumerate(engineers):
        time_dim.CumulVar(routing.Start(v)).SetRange(e.shift.start_min, e.shift.end_min)
        time_dim.CumulVar(routing.End(v)).SetMax(e.shift.end_min)

    for node, order in enumerate(orders):
        index = manager.NodeToIndex(node)
        time_dim.CumulVar(index).SetRange(order.window.start_min, order.window.end_min)
        allowed = [v for v, e in enumerate(engineers) if ev.compat(e, order) is None]
        routing.VehicleVar(index).SetValues([-1, *allowed])
        routing.AddDisjunction([index], order.kind.weight * DROP_PENALTY_PER_WEIGHT)
        deadline = obj.sla_deadline(order, ev.day_start_min)
        if deadline is not None and deadline < order.window.end_min:
            time_dim.SetCumulVarSoftUpperBound(index, deadline, LATE_COST_PER_MIN)

    routing.SetFixedCostOfAllVehicles(crew_fixed_cost)
    if max_crews is not None:
        routing.SetMaximumNumberOfActiveVehicles(max_crews)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = enums.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    params.local_search_metaheuristic = enums.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    params.time_limit.FromMilliseconds(max(1, int(time_limit_s * 1000)))
    params.log_search = False

    solution = routing.SolveWithParameters(params)
    status = enums.RoutingSearchStatus.Value.Name(routing.status())
    routes: dict[str, list[str]] = {e.id: [] for e in engineers}
    objective = -1
    if solution is not None:
        objective = solution.ObjectiveValue()
        for v, e in enumerate(engineers):
            index = solution.Value(routing.NextVar(routing.Start(v)))
            while not routing.IsEnd(index):
                routes[e.id].append(orders[manager.IndexToNode(index)].id)
                index = solution.Value(routing.NextVar(index))
    assigned = {oid for seq in routes.values() for oid in seq}
    return OrtoolsResult(
        routes=routes,
        unassigned=[o.id for o in orders if o.id not in assigned],
        objective=objective,
        wall_s=time.perf_counter() - started,
        status=status,
    )


def to_plan(
    result: OrtoolsResult,
    orders: list[Order],
    engineers: list[Engineer],
    matrix: TravelMatrix | None = None,
) -> Plan:
    """Пересчитывает маршруты OR-Tools нашим оценщиком и собирает Plan тем же кодом метрик.

    Если наш оценщик не принимает маршрут (расхождение моделей), заявки снимаются по одной,
    пока маршрут не станет допустимым; расхождения записываются в result.mismatches.
    """
    ev = RouteEvaluator(matrix, day_start_min=day_start_of(engineers))
    fleet = Fleet.empty(ev, engineers)
    by_id = {o.id: o for o in orders}
    dropped: dict[str, str] = {}
    result.mismatches = []
    for e in engineers:
        seq = [by_id[oid] for oid in result.routes.get(e.id, [])]
        state = ev.state(e, seq)
        if state is None:
            kept: list[Order] = []
            for order in seq:
                if ev.state(e, [*kept, order]) is None:
                    violation = ev.schedule(e, [*kept, order]).violation
                    reason = violation.message if violation else "маршрут недопустим"
                    dropped[order.id] = crew_name(e.name)
                    result.mismatches.append(
                        f"{crew_name(e.name)}: заявка {order.id} снята — {reason}"
                    )
                else:
                    kept.append(order)
            state = ev.state(e, kept)
            assert state is not None
        fleet.states[e.id] = state

    assigned = fleet.assigned_ids()
    left = [o for o in orders if o.id not in assigned]
    unassigned = {
        o.id: diagnose(
            fleet,
            o,
            context=(
                f"OR-Tools ставил заявку в маршрут «{dropped[o.id]}», наш оценщик его не принял."
                if o.id in dropped
                else None
            ),
        )
        for o in left
    }
    extra = estimate_extra_crews(ev, engineers, left)
    return build_plan(fleet, orders, unassigned, extra_crews_needed=extra)


def main(argv: list[str] | None = None) -> None:
    """Эталонные планы по участкам: python -m app.bench.ortools_ref [--region ...]."""
    import argparse

    from app.cli import REGIONS, load_normalized_dataset

    parser = argparse.ArgumentParser(description="Эталонный план OR-Tools для участков")
    parser.add_argument("--region", "-r", choices=[*REGIONS, "all"], default="all")
    parser.add_argument("--time-limit", "-t", type=float, default=20.0, help="секунд на участок")
    parser.add_argument("--crew-cost", type=int, default=200_000, help="метров за бригаду")
    parser.add_argument("--zone-cost", type=int, default=0, help="метров за смену зоны")
    parser.add_argument("--max-crews", type=int, default=None, help="не больше k бригад")
    parser.add_argument("--shift", help="одна смена для всех бригад, например 10:00-22:00")
    args = parser.parse_args(argv)

    for region in REGIONS if args.region == "all" else [args.region]:
        orders, engineers = load_normalized_dataset(region)
        if args.shift:
            start, finish = args.shift.split("-")
            shift = TimeWindow(start=start, end=finish)
            engineers = [e.model_copy(update={"shift": shift}) for e in engineers]
        matrix = TravelMatrix()
        result = solve_ortools(
            orders,
            engineers,
            matrix=matrix,
            time_limit_s=args.time_limit,
            crew_fixed_cost=args.crew_cost,
            zone_switch_cost=args.zone_cost,
            max_crews=args.max_crews,
        )
        m = to_plan(result, orders, engineers, matrix).metrics
        print(
            f"{REGIONS[region]['name_ru']}: назначено {m.assigned_orders} из {m.total_orders}, "
            f"бригад {m.active_engineers_count}, пробег {m.total_distance_km:.1f} км, "
            f"аварии ≤ 2 ч {m.emergency_within_sla} из {m.emergency_orders}, "
            f"{result.wall_s:.1f} с, {result.status}"
        )
        for line in result.mismatches:
            print(f"  расхождение: {line}")


if __name__ == "__main__":
    main()
