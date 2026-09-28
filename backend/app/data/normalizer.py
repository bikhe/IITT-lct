from typing import Any

from app.domain.enums import Priority, Skill, Transport, WorkType
from app.domain.models import Location, Order, TechInfo, TimeWindow
from app.geo.geocoder import HybridGeocoder


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
        """Вид работ по классификатору BK (HD — запасной признак для аварий)."""
        bk_lower = bk_type.lower()
        if "глобальная проблема" in bk_lower or "авария" in hd_type.lower():
            return WorkType.EMERGENCY
        if "подключение" in bk_lower:
            return WorkType.CONNECTION
        if "дозаказ" in bk_lower:
            return WorkType.ADDON
        return WorkType.LOCAL

    def _determine_skill_and_duration(self, bk_type: str, hd_type: str) -> tuple[Skill, int]:
        """Навык и чистое время работ по нормативу (90/100/50/40 мин минус 20 мин брони на дорогу)."""
        kind = self._determine_work_type(bk_type, hd_type)
        return kind.skill, kind.work_min

    def _determine_priority(self, bk_type: str, hd_type: str) -> Priority:
        """Аварийные работы и глобальные проблемы имеют наивысший приоритет."""
        if self._determine_work_type(bk_type, hd_type) == WorkType.EMERGENCY:
            return Priority.URGENT
        return Priority.NORMAL

    def _determine_transport_requirement(
        self, district: str, skill: Skill, priority: Priority, order_id: str
    ) -> Transport | None:
        """Синтезирует ограничение на транспорт согласно ТЗ §3.2 для демонстрации констрейнта H3."""
        distant_districts = {
            "Бирюлево Восточное",
            "Бирюлево Западное",
            "Домодедово",
            "Кашира",
            "Ступино",
            "Братеево",
        }
        central_districts = {"Таганский", "Басманный", "Замоскворечье", "Хамовники"}

        # Аварии в удаленные районы строго требуют авто
        if district in distant_districts and priority == Priority.URGENT:
            return Transport.CAR

        # Часть заявок в центре города детерминированно ограничиваем пешеходом/велосипедом (демонстрация H3)
        hash_id = int(order_id) if order_id.isdigit() else 0
        if district in central_districts:
            if hash_id % 17 == 0:
                return Transport.FOOT
            elif hash_id % 9 == 0:
                return Transport.BIKE

        # Часть заявок с тяжелым подключением требует авто
        if skill == Skill.CONNECTION and hash_id % 6 == 0:
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

        skill, duration_min = self._determine_skill_and_duration(bk_type, hd_type)
        priority = self._determine_priority(bk_type, hd_type)
        required_transport = self._determine_transport_requirement(
            district, skill, priority, order_id
        )

        lat, lon, _ = self.geocoder.geocode(district, address)

        # Техническая информация
        conn_type = raw.get("Подключение", "").strip() or None
        gbit_str = raw.get("Гигабитное подключение", "").strip().lower()
        gbit = gbit_str in ["да", "true", "1", "yes"]

        return Order(
            id=order_id,
            skills=[skill],
            priority=priority,
            work_type=self._determine_work_type(bk_type, hd_type),
            bk_type=bk_type or None,
            hd_type=hd_type or None,
            window=TimeWindow(start=start_time, end=end_time),
            duration_min=duration_min,
            required_transport=required_transport,
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

