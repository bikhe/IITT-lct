import math

from app.domain.enums import Transport
from app.domain.models import Location
from app.geo.base import DistanceProvider

EARTH_RADIUS_KM = 6371.0
URBAN_DETOUR_FACTOR = 1.35


def haversine_distance(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    """Вычисление расстояния по ортодромии (великому кругу) в километрах."""
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    delta_phi = math.radians(lat2 - lat1)
    delta_lambda = math.radians(lon2 - lon1)

    a = (
        math.sin(delta_phi / 2.0) ** 2
        + math.cos(phi1) * math.cos(phi2) * math.sin(delta_lambda / 2.0) ** 2
    )
    c = 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
    return EARTH_RADIUS_KM * c


class HaversineDistanceProvider(DistanceProvider):
    """Провайдер расчета расстояний через формулу гаверсинусов с коэффициентом городской сети."""

    def __init__(self, detour_factor: float = URBAN_DETOUR_FACTOR):
        self.detour_factor = detour_factor

    def get_distance_and_time(
        self, loc1: Location, loc2: Location, transport: Transport
    ) -> tuple[float, int]:
        """Возвращает (расстояние_км, время_в_пути_мин)."""
        if abs(loc1.lat - loc2.lat) < 1e-6 and abs(loc1.lon - loc2.lon) < 1e-6:
            return 0.0, 0

        raw_dist = haversine_distance(loc1.lat, loc1.lon, loc2.lat, loc2.lon)
        dist_km = round(raw_dist * self.detour_factor, 2)

        speed = transport.speed_kmh
        travel_hours = dist_km / speed
        travel_min = round(travel_hours * 60) + transport.boarding_delay_min

        # Минимальное время на перемещение по городу при ненулевом расстоянии
        if dist_km > 0.05 and travel_min < 3:
            travel_min = 3

        return dist_km, int(travel_min)

