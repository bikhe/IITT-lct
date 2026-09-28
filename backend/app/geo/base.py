from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.domain.enums import Transport
    from app.domain.models import Location


class DistanceProvider(ABC):
    """Абстрактный интерфейс расчета расстояний и времени в пути."""

    @abstractmethod
    def get_distance_and_time(
        self, loc1: "Location", loc2: "Location", transport: "Transport"
    ) -> tuple[float, int]:
        """Возвращает кортеж (расстояние_в_км, время_в_минутах)."""

