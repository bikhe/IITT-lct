"""Профили бригад по контрольному распределению (реальный день диспетчеров на тех же участках).

Эксперты (29.09): состав и параметры бригад команда задаёт сама, бригада — виртуальный сотрудник.
Мы берём бригады, работавшие в контрольный день, и выводим профиль из их истории:
- навыки — виды работ, которые бригада выполняла: подключение и дозаказ → «Подключение и
  дозаказы», локальная заявка → «Локальные работы», глобальная проблема → «Аварийные работы»;
  на участке должно быть не меньше двух аварийных бригад на смене 2/2;
- смена — по окнам её заявок: график 2/2 (10:00–22:00, в Москве преобладает) или 5/2 (9 ч:
  10:00–19:00, если заявки заканчивались к 18:00, или 13:00–22:00, если начинались с 12:00);
- старт — офис участка; бригада, работавшая только в городе области, живёт в этом городе;
- транспорт — пригородные и аварийные бригады на автомобиле (расстояния, сварочный аппарат),
  остальные по размеру участка: один район — пешком или велосипед, два — общественный
  транспорт, три и больше — автомобиль.
"""

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from app.domain.enums import Skill, Transport
from app.domain.models import Engineer, Location, TimeWindow, time_to_minutes
from app.geo.districts import get_district_centroid
from app.solver.objective import MOSCOW_ZONE, zone_of

SHIFT_2_2 = ("10:00", "22:00")
SHIFT_5_2_DAY = ("10:00", "19:00")
SHIFT_5_2_EVENING = ("13:00", "22:00")
MIN_EMERGENCY_CREWS = 2

# Офисы участков (строка «Адрес офиса» в конце CSV)
OFFICES: dict[str, tuple[float, float]] = {
    "юных ленинцев": (55.7056, 37.7667),
    "бирюлёвская": (55.5976, 37.6749),
    "бирюлевская": (55.5976, 37.6749),
    "симферопольский": (55.6703, 37.6186),
}


@dataclass
class CrewHistory:
    """Что бригада делала в контрольный день."""

    name: str
    districts: Counter = field(default_factory=Counter)
    skills: set[Skill] = field(default_factory=set)
    first_start: int | None = None  # самое раннее начало 2-часового окна
    last_end: int | None = None  # самое позднее окончание 2-часового окна

    @property
    def towns(self) -> list[str]:
        return [d for d in self.districts if zone_of(d) != MOSCOW_ZONE]

    @property
    def is_suburban(self) -> bool:
        return bool(self.districts) and len(self.towns) == len(self.districts)


def _minutes(value: str) -> int:
    return time_to_minutes(value.strip().split()[-1])


def crew_histories(control_rows: list[dict[str, Any]]) -> list[CrewHistory]:
    """История бригад из строк контрольного CSV (строки без бригады пропускаются)."""
    out: dict[str, CrewHistory] = {}
    for row in control_rows:
        name = (row.get("Бригада") or "").strip()
        if not name:
            continue
        h = out.setdefault(name, CrewHistory(name))
        h.districts[(row.get("Район") or "").strip()] += 1
        bk = (row.get("Тип заявки BK") or "").lower()
        if "подключение" in bk or "дозаказ" in bk:
            h.skills.add(Skill.CONNECTION)
        elif "локальная" in bk:
            h.skills.add(Skill.LOCAL)
        elif "глобальная" in bk:
            h.skills.add(Skill.EMERGENCY)
        start, end = _minutes(row.get("Начало", "10:00")), _minutes(row.get("Окончание", "12:00"))
        if end - start <= 4 * 60:  # окна аварий «на весь день» о смене ничего не говорят
            h.first_start = start if h.first_start is None else min(h.first_start, start)
            h.last_end = end if h.last_end is None else max(h.last_end, end)
    return [out[k] for k in sorted(out)]


def shift_of(h: CrewHistory) -> tuple[str, str]:
    if h.first_start is None or h.last_end is None:
        return SHIFT_2_2
    if h.first_start >= 12 * 60 and h.last_end >= 20 * 60:
        return SHIFT_5_2_EVENING
    if h.last_end <= 18 * 60:
        return SHIFT_5_2_DAY
    return SHIFT_2_2


class EngineerSynthesizer:
    """Детерминированно строит бригады участка по их истории в контрольном распределении."""

    def __init__(self, seed: int = 42):
        self.seed = seed  # правила детерминированы; параметр оставлен для совместимости

    @staticmethod
    def office_location(region_name: str, office_info: dict[str, Any] | None) -> Location:
        address = ""
        if office_info:
            address = (office_info.get("Тип заявки BK") or "").strip()
        lower = address.lower()
        lat, lon = next(
            (xy for key, xy in OFFICES.items() if key in lower), get_district_centroid("Таганский")
        )
        return Location(
            lat=lat, lon=lon, address=address or "Офис участка", district=region_name
        )

    def synthesize_for_region(
        self,
        region_name: str,
        crews: list[CrewHistory] | list[str],
        office_info: dict[str, Any] | None = None,
    ) -> list[Engineer]:
        histories = [c if isinstance(c, CrewHistory) else CrewHistory(c) for c in crews]
        histories.sort(key=lambda h: h.name)
        office = self.office_location(region_name, office_info)

        shifts = {h.name: shift_of(h) for h in histories}
        skills = {
            h.name: set(h.skills) or {Skill.CONNECTION, Skill.LOCAL} for h in histories
        }
        # не меньше двух аварийных бригад на длинной смене: добавляем навык бригадам
        # с самым широким участком (так на участке всегда есть кому выехать на аварию)
        long_shift = [h for h in histories if shifts[h.name] == SHIFT_2_2]
        emergency = [h for h in long_shift if Skill.EMERGENCY in skills[h.name]]
        extra = sorted(
            (h for h in long_shift if Skill.EMERGENCY not in skills[h.name]),
            key=lambda h: (-len(h.districts), -sum(h.districts.values()), h.name),
        )
        for h in extra[: max(0, MIN_EMERGENCY_CREWS - len(emergency))]:
            skills[h.name].add(Skill.EMERGENCY)

        engineers: list[Engineer] = []
        walkers = 0
        for i, h in enumerate(histories):
            crew_skills = [
                s for s in (Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY) if s in skills[h.name]
            ]
            if h.is_suburban:
                town = max(h.towns, key=lambda d: (h.districts[d], d))
                lat, lon = get_district_centroid(town)
                depot = Location(
                    lat=lat, lon=lon, address=f"Дом исполнителя, г. {town}", district=town
                )
                transport = Transport.CAR
            else:
                depot = office
                if Skill.EMERGENCY in crew_skills or h.towns or len(h.districts) >= 3:
                    transport = Transport.CAR
                elif len(h.districts) == 2:
                    transport = Transport.TRANSIT
                else:
                    transport = Transport.FOOT if walkers % 2 == 0 else Transport.BIKE
                    walkers += 1
            start, end = shifts[h.name]
            engineers.append(
                Engineer(
                    id=f"eng_{region_name}_{i + 1:02d}",
                    name=h.name,
                    skills=crew_skills,
                    transport=transport,
                    shift=TimeWindow(start=start, end=end),
                    depot=depot,
                )
            )
        return engineers
