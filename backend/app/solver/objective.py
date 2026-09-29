"""Цель оптимизации и её параметры. Все компоненты поиска сравнивают планы только через этот модуль.

Лексикографический порядок (разъяснения организаторов, Q&A 16.09 и ответы про аварии):
  1) больше выполненных заявок с учётом приоритета (авария 10 · подключение 5 · ремонт/дозаказ 3);
  2) меньше аварий, начатых позже SLA (2 часа от поступления): авария — форс-мажор,
     ради неё допустимо вывести ещё одну бригаду;
  3) меньше задействованных бригад;
  4) стоимость маршрутов: км + штраф за опоздание аварии сверх SLA + штраф за смену зоны.
"""

from typing import NamedTuple

from app.domain.enums import Transport
from app.domain.models import Engineer, Order

SLA_REACTION_MIN = 120  # ориентир реакции на аварию — 1–2 часа (22.09)
LATE_KM_PER_MIN = 0.5  # час опоздания аварии сверх SLA весит как 30 км пробега
ZONE_SWITCH_KM = 20.0  # переезд Москва ↔ пригород внутри дня («кочевание», 22.09)
CREW_PENALTY = 1000.0  # вывести ещё одну бригаду дороже любого разумного пробега
# Нарушение SLA аварии хуже вывода ещё одной бригады: штраф входит в стоимость маршрута и вставки,
# поэтому regret-вставка скорее выведет бригаду, чем задержит аварию сверх 2 часов.
SLA_BREACH_KM = 2 * CREW_PENALTY
STABILITY_KM_PER_MIN = 0.05  # при перепланировании сдвиг чужой заявки на час ≈ 3 км
# Если вывести можно любую из нескольких бригад, выводим ту, что возьмёт больше заявок: длинная
# смена, быстрый транспорт, больше навыков. Надбавка к штрафу за бригаду — до 3 × 30 км.
ACTIVATION_BIAS_KM = 30.0
TRANSPORT_SLOWNESS = {
    Transport.CAR: 0.0,
    Transport.TRANSIT: 0.4,
    Transport.BIKE: 0.6,
    Transport.FOOT: 1.0,
}

MOSCOW_ZONE = "Москва"
SUBURBAN_ZONES = frozenset({"Кашира", "Ступино", "Домодедово"})


def zone_of(district: str) -> str:
    """Зона для штрафа за переезды: Москва целиком или конкретный город Подмосковья."""
    name = district.strip()
    return name if name in SUBURBAN_ZONES else MOSCOW_ZONE


def activation_bias(engineer: Engineer) -> float:
    """Надбавка к штрафу за вывод бригады: 0 у бригады со сменой 12 ч, авто и тремя навыками."""
    shift = engineer.shift.end_min - engineer.shift.start_min
    short = min(1.0, max(0.0, (720 - shift) / 180))
    slow = TRANSPORT_SLOWNESS.get(engineer.transport, 1.0)
    few = (3 - min(3, len(engineer.skills))) / 2
    return ACTIVATION_BIAS_KM * (short + slow + few)


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
    breaches: int  # аварии, начатые позже SLA
    crews: int
    cost: float

    @property
    def rank(self) -> tuple[int, int, int]:
        """Уровни цели до стоимости: их ухудшение не компенсируется никаким выигрышем в км."""
        return self.lost_weight, self.breaches, self.crews

    def better_than(self, other: "PlanScore", eps: float = 1e-6) -> bool:
        if self.rank != other.rank:
            return self.rank < other.rank
        return self.cost < other.cost - eps
