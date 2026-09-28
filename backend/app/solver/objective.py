"""Цель оптимизации и её параметры. Все компоненты поиска сравнивают планы только через этот модуль.

Лексикографический порядок (разъяснения организаторов, Q&A 16.09):
  1) больше выполненных заявок с учётом приоритета (авария 10 · подключение 5 · ремонт/дозаказ 3);
  2) меньше задействованных бригад;
  3) стоимость маршрутов: км + штраф за опоздание аварии сверх SLA + штраф за смену зоны.
"""

from typing import NamedTuple

from app.domain.models import Order

SLA_REACTION_MIN = 120  # ориентир реакции на аварию — 1–2 часа (22.09)
LATE_KM_PER_MIN = 0.5  # час опоздания аварии сверх SLA весит как 30 км пробега
ZONE_SWITCH_KM = 20.0  # переезд Москва ↔ пригород внутри дня («кочевание», 22.09)
CREW_PENALTY = 1000.0  # вывести ещё одну бригаду дороже любого разумного пробега
STABILITY_KM_PER_MIN = 0.05  # при перепланировании сдвиг чужой заявки на час ≈ 3 км

MOSCOW_ZONE = "Москва"
SUBURBAN_ZONES = frozenset({"Кашира", "Ступино", "Домодедово"})


def zone_of(district: str) -> str:
    """Зона для штрафа за переезды: Москва целиком или конкретный город Подмосковья."""
    name = district.strip()
    return name if name in SUBURBAN_ZONES else MOSCOW_ZONE


def release_min(order: Order, day_start_min: int) -> int:
    """Момент, от которого считается реакция: поступление заявки или начало её окна в рабочем дне."""
    if order.released_min is not None:
        return order.released_min
    return max(order.window.start_min, day_start_min)


def sla_deadline(order: Order, day_start_min: int) -> int | None:
    """Крайний срок начала работ по аварии (None для остальных заявок)."""
    if not order.is_emergency:
        return None
    return release_min(order, day_start_min) + SLA_REACTION_MIN


class PlanScore(NamedTuple):
    """Ключ сравнения планов: меньше — лучше."""

    lost_weight: int
    crews: int
    cost: float

    def better_than(self, other: "PlanScore", eps: float = 1e-6) -> bool:
        if self.lost_weight != other.lost_weight:
            return self.lost_weight < other.lost_weight
        if self.crews != other.crews:
            return self.crews < other.crews
        return self.cost < other.cost - eps
