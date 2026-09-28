from typing import Any

from app.domain.enums import Skill, Transport
from app.domain.models import Engineer, Location, TimeWindow
from app.geo.districts import get_district_centroid


class EngineerSynthesizer:
    """Детерминированный генератор параметров инженеров/выездных бригад на основе контрольных файлов."""

    def __init__(self, seed: int = 42):
        self.seed = seed

    def synthesize_for_region(
        self,
        region_name: str,
        brigade_names: list[str],
        office_info: dict[str, Any] | None = None,
    ) -> list[Engineer]:
        """Генерирует список инженеров для региона со сбалансированными сменами, навыками и транспортом."""
        # Смены для полного покрытия рабочего дня (10:00-22:00) согласно ТЗ §2.4.1:
        # в данных нет заявок раньше 10:00, поэтому смены сбалансированы под дневной и вечерний поток
        shifts = [
            TimeWindow(start="10:00", end="19:00"),
            TimeWindow(start="11:00", end="20:00"),
            TimeWindow(start="13:00", end="22:00"),
        ]

        # Дополняем до целевой емкости 13-14 бригад (ТЗ §2.4: от 10 до 15 на регион) для основных регионов
        names = sorted(brigade_names)
        if region_name in ("east", "southeast", "southcenter"):
            reserve_names = ["Бригада Смирнов", "Бригада Васильев", "Бригада Попов", "Бригада Ковалев"]
            target_count = 14 if region_name == "southeast" else 13
            res_idx = 0
            while len(names) < target_count and res_idx < len(reserve_names):
                cand = reserve_names[res_idx]
                if cand not in names:
                    names.append(cand)
                res_idx += 1

        # Наборы навыков (от 1 до 3 навыков, ТЗ §2.4.1 и §6)
        skill_profiles = [
            [Skill.CONNECTION, Skill.LOCAL],
            [Skill.LOCAL],
            [Skill.CONNECTION],
            [Skill.EMERGENCY, Skill.LOCAL],
            [Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY],
            [Skill.CONNECTION, Skill.EMERGENCY],
            [Skill.LOCAL],
            [Skill.CONNECTION, Skill.LOCAL],
            [Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY],
            [Skill.LOCAL],
            [Skill.CONNECTION],
            [Skill.EMERGENCY, Skill.LOCAL],
        ]

        # Распределение транспорта
        # 65% авто, 15% общественный транспорт, 10% пешеход, 10% велосипед
        transport_options = [
            Transport.CAR,
            Transport.CAR,
            Transport.CAR,
            Transport.TRANSIT,
            Transport.CAR,
            Transport.FOOT,
            Transport.CAR,
            Transport.BIKE,
            Transport.CAR,
            Transport.CAR,
            Transport.TRANSIT,
            Transport.CAR,
        ]

        # Координаты депо по умолчанию (офис или дефолт)
        if office_info and office_info.get("Тип заявки BK"):
            office_addr = office_info.get("Тип заявки BK", "").strip()
            # Например 'г. Москва, ул Юных Ленинцев, д 83с 4' -> Кузьминки
            lat, lon = 55.7056, 37.7667
            if "бирюлёвская" in office_addr.lower():
                lat, lon = 55.5976, 37.6749
            elif "симферопольский" in office_addr.lower():
                lat, lon = 55.6703, 37.6186
            default_depot = Location(
                lat=lat,
                lon=lon,
                address=office_addr,
                district=region_name,
            )
        else:
            lat, lon = get_district_centroid("Таганский")
            default_depot = Location(
                lat=lat,
                lon=lon,
                address="Региональный диспетчерский пункт",
                district=region_name,
            )

        engineers: list[Engineer] = []
        for i, name in enumerate(names):
            eng_id = f"eng_{region_name}_{i+1:02d}"
            shift = shifts[i % len(shifts)]
            skills = skill_profiles[i % len(skill_profiles)]
            transport = transport_options[i % len(transport_options)]

            # Разъяснение организаторов: для бригад удаленного Подмосковья (Кашира, Ступино, Домодедово)
            # стартовая точка задается локально в соответствующем городе («дом» исполнителя) на автомобиле.
            if region_name == "southeast" and i in (9, 10):
                c_lat, c_lon = get_district_centroid("Кашира")
                depot = Location(
                    lat=c_lat,
                    lon=c_lon,
                    address="Домашняя база исполнителя (г. Кашира)",
                    district="Кашира",
                )
                transport = Transport.CAR
                skills = [Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY]
            elif region_name == "southeast" and i == 11:
                c_lat, c_lon = get_district_centroid("Ступино")
                depot = Location(
                    lat=c_lat,
                    lon=c_lon,
                    address="Домашняя база исполнителя (г. Ступино)",
                    district="Ступино",
                )
                transport = Transport.CAR
                skills = [Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY]
            elif region_name == "southeast" and i in (12, 13):
                c_lat, c_lon = get_district_centroid("Домодедово")
                depot = Location(
                    lat=c_lat,
                    lon=c_lon,
                    address="Домашняя база исполнителя (г. Домодедово)",
                    district="Домодедово",
                )
                transport = Transport.CAR
                skills = [Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY]
            else:
                # Небольшой индивидуальный джиттер депо возле офиса (стартовой точки бригады)
                depot_lat = round(default_depot.lat + ((i % 5) - 2) * 0.003, 5)
                depot_lon = round(default_depot.lon + (((i * 3) % 5) - 2) * 0.004, 5)

                depot = Location(
                    lat=depot_lat,
                    lon=depot_lon,
                    address=default_depot.address,
                    district=default_depot.district,
                )

            engineers.append(
                Engineer(
                    id=eng_id,
                    name=name,
                    skills=skills,
                    transport=transport,
                    shift=shift,
                    depot=depot,
                )
            )

        return engineers
