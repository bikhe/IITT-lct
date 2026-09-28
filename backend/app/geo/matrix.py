from app.domain.enums import Transport
from app.domain.models import Location
from app.geo.base import DistanceProvider
from app.geo.haversine import HaversineDistanceProvider


class TravelMatrix(DistanceProvider):
    """Матрица «точка → точка → транспорт» с ленивым заполнением.

    Каждая пара точек считается провайдером один раз, дальше берётся из кэша. Точки
    нумеруются по координатам, поэтому заявки по одному адресу делят один узел.
    Провайдер можно заменить (OSRM, 2ГИС) — поиск и оценщик маршрутов работают только с матрицей.
    """

    def __init__(self, provider: DistanceProvider | None = None):
        self.provider = provider or HaversineDistanceProvider()
        self._node_of: dict[tuple[float, float], int] = {}
        self._locations: list[Location] = []
        self._legs: dict[tuple[int, int, Transport], tuple[float, int]] = {}

    def node(self, loc: Location) -> int:
        key = (round(loc.lat, 6), round(loc.lon, 6))
        idx = self._node_of.get(key)
        if idx is None:
            idx = len(self._locations)
            self._node_of[key] = idx
            self._locations.append(loc)
        return idx

    def location(self, node: int) -> Location:
        return self._locations[node]

    def leg(self, a: int, b: int, transport: Transport) -> tuple[float, int]:
        """Возвращает (км, минуты) между узлами a и b для вида транспорта."""
        key = (a, b, transport)
        cached = self._legs.get(key)
        if cached is None:
            if a == b:
                cached = (0.0, 0)
            else:
                cached = self.provider.get_distance_and_time(
                    self._locations[a], self._locations[b], transport
                )
            self._legs[key] = cached
        return cached

    def get_distance_and_time(
        self, loc1: Location, loc2: Location, transport: Transport
    ) -> tuple[float, int]:
        return self.leg(self.node(loc1), self.node(loc2), transport)


def as_matrix(provider: DistanceProvider | None) -> TravelMatrix:
    """Оборачивает произвольный провайдер в матрицу (или возвращает матрицу как есть)."""
    if isinstance(provider, TravelMatrix):
        return provider
    return TravelMatrix(provider)
