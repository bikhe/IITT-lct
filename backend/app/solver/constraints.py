"""Парные ограничения «бригада × заявка». Добавить ограничение = добавить класс в DEFAULT_PAIR_CONSTRAINTS.

Ограничения, зависящие от маршрута (окно заявки, смена, время в пути, отсутствие наложений),
проверяет RouteEvaluator при построении расписания. Уникальность назначения обеспечивается
структурой плана: заявка хранится ровно в одном маршруте или в списке неназначенных.
"""

from typing import Protocol

from app.domain.enums import ReasonCode
from app.domain.models import Engineer, Order


class PairConstraint(Protocol):
    code: ReasonCode
    name: str

    def allows(self, engineer: Engineer, order: Order) -> bool: ...


class SkillConstraint:
    """H1. Квалификация: все навыки заявки есть у бригады."""

    code = ReasonCode.SKILL
    name = "Квалификация"

    def allows(self, engineer: Engineer, order: Order) -> bool:
        return all(skill in engineer.skills for skill in order.skills)


class TransportConstraint:
    """H3. Транспорт: если заявка требует вид транспорта, он совпадает с транспортом бригады."""

    code = ReasonCode.TRANSPORT
    name = "Транспорт"

    def allows(self, engineer: Engineer, order: Order) -> bool:
        return order.required_transport is None or order.required_transport == engineer.transport


DEFAULT_PAIR_CONSTRAINTS: tuple[PairConstraint, ...] = (SkillConstraint(), TransportConstraint())
