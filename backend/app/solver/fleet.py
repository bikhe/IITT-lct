from dataclasses import dataclass, field

from app.domain.models import Engineer, Order
from app.solver.evaluator import RouteEvaluator, RouteStart, RouteState
from app.solver.objective import PlanScore


@dataclass
class Fleet:
    """Изменяемая часть плана: допустимые маршруты всех бригад от их текущих стартовых точек.

    pinned — бригада уже на линии (есть замороженные работы), поэтому считается задействованной
    даже с пустым хвостом. available=False — бригада сошла с линии и новых заявок не берёт.
    """

    evaluator: RouteEvaluator
    engineers: list[Engineer]
    states: dict[str, RouteState]
    pinned: dict[str, bool] = field(default_factory=dict)
    available: dict[str, bool] = field(default_factory=dict)

    @classmethod
    def empty(
        cls,
        evaluator: RouteEvaluator,
        engineers: list[Engineer],
        starts: dict[str, RouteStart] | None = None,
        pinned: dict[str, bool] | None = None,
        available: dict[str, bool] | None = None,
    ) -> "Fleet":
        starts = starts or {}
        return cls(
            evaluator=evaluator,
            engineers=list(engineers),
            states={e.id: evaluator.empty(e, starts.get(e.id)) for e in engineers},
            pinned={e.id: bool((pinned or {}).get(e.id, False)) for e in engineers},
            available={e.id: (available or {}).get(e.id, True) for e in engineers},
        )

    @property
    def engineer_map(self) -> dict[str, Engineer]:
        return {e.id: e for e in self.engineers}

    def is_active(self, eid: str) -> bool:
        return self.pinned.get(eid, False) or len(self.states[eid]) > 0

    def crews(self) -> int:
        return sum(1 for e in self.engineers if self.is_active(e.id))

    def cost(self) -> float:
        return sum(st.cost for st in self.states.values())

    def km(self) -> float:
        return sum(st.km for st in self.states.values())

    def breaches(self) -> int:
        """Аварии, которые начнутся позже SLA (2 часа от поступления)."""
        return sum(st.breaches for st in self.states.values())

    def score(self, unassigned: list[Order] | None = None) -> PlanScore:
        lost = sum(o.kind.weight for o in unassigned or [])
        return PlanScore(lost, self.breaches(), self.crews(), round(self.cost(), 6))

    def assigned_ids(self) -> set[str]:
        return {oid for st in self.states.values() for oid in st.order_ids}

    def owner_of(self, order_id: str) -> str | None:
        for eid, st in self.states.items():
            if order_id in st.order_ids:
                return eid
        return None

    def snapshot(self) -> dict[str, RouteState]:
        return dict(self.states)

    def restore(self, snap: dict[str, RouteState]) -> None:
        self.states = dict(snap)
