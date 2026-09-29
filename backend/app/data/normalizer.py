from typing import Any

from app.domain.enums import Priority, Transport, WorkType
from app.domain.models import Location, Order, TechInfo, TimeWindow
from app.geo.geocoder import HybridGeocoder
from app.solver.diagnose import plural
from app.solver.objective import MOSCOW_ZONE, zone_of


class OrderNormalizer:
    """Нормализатор сырых записей CSV в типизированные объекты доменной модели Order."""

    def __init__(self, geocoder: HybridGeocoder | None = None):
        self.geocoder = geocoder or HybridGeocoder()

    def _parse_time_from_datetime(self, dt_str: str) -> str:
        """Извлекает HH:MM из строки '17.08.2026 14:00' или '14:00'."""
        parts = dt_str.strip().split()
        time_part = parts[-1] if len(parts) > 1 else parts[0]
        subparts = time_part.split(":")
        if len(subparts) == 2:
            h, m = int(subparts[0]), int(subparts[1])
            return f"{h:02d}:{m:02d}"
        return "10:00"

    def _determine_work_type(self, bk_type: str, hd_type: str) -> WorkType:
        """Вид работ. Авария — только по полю HD «Авария» (ответ экспертов 29.09), остальное — по BK.

        «Глобальная проблема» с HD «Информация» — не авария: обычный визит в окно заявки
        с навыком «Локальные работы» и нормативом локальной заявки.
        """
        if "авария" in hd_type.lower():
            return WorkType.EMERGENCY
        bk_lower = bk_type.lower()
        if "подключение" in bk_lower:
            return WorkType.CONNECTION
        if "дозаказ" in bk_lower:
            return WorkType.ADDON
        return WorkType.LOCAL

    def _determine_transport_requirement(self, kind: WorkType, hd_type: str) -> Transport | None:
        """Требование транспорта задаётся правилом по виду работ (в CSV такого поля нет).

        Авария — сварочный аппарат для оптики и расходники, «Работа с кабелем» — бухта кабеля
        и лестница: такие выезды возможны только на автомобиле. Остальные заявки транспорт
        не ограничивают.
        """
        if kind == WorkType.EMERGENCY or "работа с кабелем" in hd_type.lower():
            return Transport.CAR
        return None

    def normalize(self, raw: dict[str, Any]) -> Order:
        """Конвертирует одну сырую запись в доменный объект Order."""
        order_id = raw.get("Заявка", "").strip()
        bk_type = raw.get("Тип заявки BK", "").strip()
        hd_type = raw.get("Тип заявки HD", "").strip()
        district = raw.get("Район", "").strip()
        address = raw.get("Адрес", "").strip()

        start_time = self._parse_time_from_datetime(raw.get("Начало", "10:00"))
        end_time = self._parse_time_from_datetime(raw.get("Окончание", "12:00"))

        kind = self._determine_work_type(bk_type, hd_type)
        priority = Priority.URGENT if kind == WorkType.EMERGENCY else Priority.NORMAL

        lat, lon, _ = self.geocoder.geocode(district, address)

        # Техническая информация
        conn_type = raw.get("Подключение", "").strip() or None
        gbit_str = raw.get("Гигабитное подключение", "").strip().lower()
        gbit = gbit_str in ["да", "true", "1", "yes"]

        return Order(
            id=order_id,
            skills=[kind.skill],
            priority=priority,
            work_type=kind,
            bk_type=bk_type or None,
            hd_type=hd_type or None,
            window=TimeWindow(start=start_time, end=end_time),
            duration_min=kind.work_min,
            required_transport=self._determine_transport_requirement(kind, hd_type),
            location=Location(lat=lat, lon=lon, address=address, district=district),
            tech=TechInfo(product=conn_type, gbit=gbit),
        )

    def normalize_list(self, raw_list: list[dict[str, Any]]) -> list[Order]:
        """Нормализует список записей с дедупликацией по ID."""
        seen_ids = set()
        orders: list[Order] = []
        for r in raw_list:
            order = self.normalize(r)
            if order.id not in seen_ids:
                seen_ids.add(order.id)
                orders.append(order)
        return orders


def _town_prefix(addresses: list[str], town: str) -> str:
    """Начало адреса до названия города включительно: «МО, г. Кашира»."""
    ends = [a.find(town) for a in addresses]
    if min(ends) < 0:
        return ""
    prefixes = {a[: i + len(town)] for a, i in zip(addresses, ends)}
    return prefixes.pop() if len(prefixes) == 1 else ""


def merge_node_accidents(orders: list[Order]) -> list[Order]:
    """Аварии в одном городе Подмосковья с одинаковым окном — одна авария на узле.

    Эксперты (29.09) разрешили оба варианта: отдельный выезд в каждый дом или одна авария на
    узле, из-за которой без связи остались несколько домов. В городе области дома подключены
    к одному узлу, поэтому такие аварии объединяем: один выезд с нормативом аварии, точка —
    центр домов. ID выезда — первой заявки группы по файлу, остальные ID — в поле covers.
    Аварии в Москве не объединяются.
    """
    groups: dict[tuple[str, int, int], list[Order]] = {}
    for o in orders:
        zone = zone_of(o.location.district)
        if not o.is_emergency or zone == MOSCOW_ZONE:
            continue
        groups.setdefault((zone, o.window.start_min, o.window.end_min), []).append(o)

    merged: dict[str, Order] = {}
    dropped: set[str] = set()
    for group in groups.values():
        if len(group) < 2:
            continue
        head = group[0]
        addresses = [o.location.address for o in group]
        prefix = _town_prefix(addresses, head.location.district)
        houses = [a[len(prefix):].strip(" ,") if prefix else a for a in addresses]
        n = len(group)
        title = f"{prefix} — " if prefix else ""
        address = (
            f"{title}авария на узле, {n} {plural(n, 'дом', 'дома', 'домов')}: " + "; ".join(houses)
        )
        location = Location(
            lat=round(sum(o.location.lat for o in group) / n, 5),
            lon=round(sum(o.location.lon for o in group) / n, 5),
            address=address,
            district=head.location.district,
        )
        merged[head.id] = head.model_copy(
            update={"location": location, "covers": [o.id for o in group[1:]]}
        )
        dropped.update(o.id for o in group[1:])

    return [merged.get(o.id, o) for o in orders if o.id not in dropped]
