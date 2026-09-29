"""Детерминированный генератор сценариев дня для проверки планировщика на разных данных.

Вид сценария (kind) задаёт, какую сторону алгоритма проверяем: пики окон, кластеры адресов,
пригород, нехватку навыков, требования к транспорту, перегрузку, большой объём, крайние случаи,
а также возмущения реальных участков. Один и тот же (kind, seed) всегда даёт одинаковые заявки
и бригады: используется только random.Random с фиксированным зерном, коллекции перебираются
в отсортированном порядке.

Правила данных — из разъяснений организаторов: окно — интервал начала работ, 2 ч с чётного часа
10:00–20:00; доли видов работ 40 % подключения, 40 % ремонты, 10 % аварии, 10 % дозаказы; авария —
окно на весь день, известна утром, ориентир реакции 2 ч; смены 2/2 (10:00–22:00, преобладают)
и 5/2 (9 ч); у бригады 1–3 навыка; в Москве больше пешеходов и общественного транспорта,
в пригороде — автомобили, бригада живёт в своём городе.
"""

import math
import random
from collections import Counter
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import TypeVar

from app.domain.enums import Priority, Skill, Transport, WorkType
from app.domain.models import Engineer, Location, Order, TimeWindow, minutes_to_time
from app.geo.districts import DISTRICT_CENTROIDS
from app.geo.haversine import haversine_distance

T = TypeVar("T")

# ---------------------------------------------------------------------------- справочники

TOWNS: dict[str, tuple[float, float]] = {
    "Домодедово": (55.4431, 37.7478),
    "Кашира": (54.8384, 38.1601),
    "Ступино": (54.8875, 38.0772),
}
MOSCOW_DISTRICTS: tuple[str, ...] = tuple(
    sorted(d for d in DISTRICT_CENTROIDS if d not in TOWNS and not d.startswith("GPON"))
)
# районы юго-востока, рядом с которыми «пригородный» участок держит офис
SOUTH_EAST_ANCHORS = (
    "Бирюлево Восточное",
    "Бирюлево Западное",
    "Братеево",
    "Зябликово",
    "Орехово Борисово Южное",
    "Царицыно",
)
STREETS = (
    "Центральная",
    "Садовая",
    "Школьная",
    "Лесная",
    "Молодёжная",
    "Новая",
    "Парковая",
    "Заводская",
    "Советская",
    "Мира",
)
SURNAMES = (
    "Иванов", "Смирнов", "Кузнецов", "Попов", "Васильев", "Петров", "Соколов", "Михайлов",
    "Новиков", "Фёдоров", "Морозов", "Волков", "Алексеев", "Лебедев", "Семёнов", "Егоров",
    "Павлов", "Козлов", "Степанов", "Николаев", "Орлов", "Андреев", "Макаров", "Никитин",
    "Захаров", "Зайцев", "Соловьёв", "Борисов", "Яковлев", "Григорьев", "Романов", "Воробьёв",
    "Сергеев", "Кузьмин", "Фролов", "Александров", "Дмитриев", "Королёв", "Гусев", "Киселёв",
    "Ильин", "Максимов", "Поляков", "Сорокин", "Виноградов", "Ковалёв", "Белов", "Медведев",
    "Антонов", "Тарасов",
)  # fmt: skip
BK_TYPES = {
    WorkType.EMERGENCY: "Глобальная проблема",
    WorkType.CONNECTION: "Подключение",
    WorkType.LOCAL: "Локальная проблема",
    WorkType.ADDON: "Дозаказ",
}

L, C, E = Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY
SKILL_ORDER = (L, C, E)

# окна начала работ: 2 ч с чётного часа 10:00–20:00
TWO_HOUR_STARTS = (600, 720, 840, 960, 1080, 1200)
REAL_PEAKS = (14, 15, 10, 7, 12, 8)  # Восток по окнам 10…20: пики 10–14 и 18–20
PEAK_HEAVY = (35, 10, 7, 7, 35, 6)  # ~70 % в 10–12 и 18–20
WIDE_WINDOWS = (((600, 840), 40), ((840, 1080), 30), ((1080, 1320), 30))  # 10–14, 14–18, 18–22

STANDARD_MIX = (
    (WorkType.CONNECTION, 0.4),
    (WorkType.LOCAL, 0.4),
    (WorkType.EMERGENCY, 0.1),
    (WorkType.ADDON, 0.1),
)
EMERGENCY_MIX = (
    (WorkType.EMERGENCY, 0.3),
    (WorkType.CONNECTION, 0.3),
    (WorkType.LOCAL, 0.3),
    (WorkType.ADDON, 0.1),
)

SKILL_PROFILES: tuple[tuple[tuple[Skill, ...], int], ...] = (
    ((L,), 15),
    ((C,), 15),
    ((L, C), 30),
    ((L, E), 12),
    ((C, E), 8),
    ((L, C, E), 20),
)
BROAD_PROFILES: tuple[tuple[tuple[Skill, ...], int], ...] = (
    ((L, C), 40),
    ((L, E), 15),
    ((C, E), 10),
    ((L, C, E), 35),
)
MOSCOW_TRANSPORT = (
    (Transport.TRANSIT, 40),
    (Transport.FOOT, 20),
    (Transport.CAR, 30),
    (Transport.BIKE, 10),
)
REQUIRED_TRANSPORT = (
    (Transport.CAR, 40),
    (Transport.TRANSIT, 20),
    (Transport.FOOT, 20),
    (Transport.BIKE, 20),
)
SHIFT_2_2 = ("10:00", "22:00")
SHIFT_MIX = ((SHIFT_2_2, 70), (("10:00", "19:00"), 15), (("13:00", "22:00"), 15))

DISTRICT_RADIUS_KM = 1.5
TRAVEL_ESTIMATE_MIN = 15  # оценка дороги на заявку для расчёта перегрузки

REAL_REGIONS = ("east", "southeast", "southcenter")
REGION_NAMES = {"east": "Восток", "southeast": "Юго-восток", "southcenter": "Югоцентр"}
REAL_PERTURBATIONS = ("real_shuffled", "real_fewer_crews", "real_jitter", "real_long_shifts")


@dataclass
class Scenario:
    """Сценарий дня: заявки и бригады одного участка плюс пояснения генератора."""

    name: str
    kind: str
    seed: int
    orders: list[Order]
    engineers: list[Engineer]
    notes: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------- геометрия

KM_PER_DEG_LAT = 111.2


def disk_point(
    rng: random.Random, center: tuple[float, float], radius_km: float
) -> tuple[float, float]:
    """Равномерная точка в круге радиуса radius_km; координаты округлены до 6 знаков."""
    r = radius_km * math.sqrt(rng.random())
    a = rng.uniform(0.0, 2.0 * math.pi)
    lat = center[0] + r * math.sin(a) / KM_PER_DEG_LAT
    lon = center[1] + r * math.cos(a) / (KM_PER_DEG_LAT * math.cos(math.radians(center[0])))
    return round(lat, 6), round(lon, 6)


def _km(a: tuple[float, float], b: tuple[float, float]) -> float:
    return haversine_distance(a[0], a[1], b[0], b[1])


def _nearest_districts(anchor: str, k: int) -> list[str]:
    a = DISTRICT_CENTROIDS[anchor]
    ranked = sorted(MOSCOW_DISTRICTS, key=lambda d: (_km(a, DISTRICT_CENTROIDS[d]), d))
    return ranked[:k]


def _office(rng: random.Random, district: str, label: str = "Офис участка") -> Location:
    lat, lon = disk_point(rng, DISTRICT_CENTROIDS[district], 0.4)
    return Location(lat=lat, lon=lon, address=f"{label} ({district})", district=district)


def _moscow_point(rng: random.Random, districts: Sequence[str]) -> tuple[str, tuple[float, float]]:
    district = rng.choice(list(districts))
    return district, disk_point(rng, DISTRICT_CENTROIDS[district], DISTRICT_RADIUS_KM)


# ---------------------------------------------------------------------------- случайные выборы


def _weighted(rng: random.Random, pairs: Sequence[tuple[T, float]]) -> T:
    return rng.choices([p for p, _ in pairs], weights=[w for _, w in pairs])[0]


def _two_hour_window(rng: random.Random, weights: Sequence[float]) -> tuple[int, int]:
    start = rng.choices(TWO_HOUR_STARTS, weights=weights)[0]
    return start, start + 120


def _wide_window(rng: random.Random) -> tuple[int, int]:
    return _weighted(rng, WIDE_WINDOWS)


def _type_sequence(
    rng: random.Random, n: int, mix: Sequence[tuple[WorkType, float]]
) -> list[WorkType]:
    """Ровно по долям (метод наибольшего остатка), затем перемешать."""
    raw = [(kind, n * share) for kind, share in mix]
    counts = {kind: math.floor(v) for kind, v in raw}
    rest = n - sum(counts.values())
    by_remainder = sorted(range(len(raw)), key=lambda i: (-(raw[i][1] - math.floor(raw[i][1])), i))
    for i in by_remainder[:rest]:
        counts[raw[i][0]] += 1
    seq = [kind for kind, _ in mix for _ in range(counts[kind])]
    rng.shuffle(seq)
    return seq


def _default_crews(rng: random.Random, n_orders: int) -> int:
    return max(10, min(15, round(n_orders / 6.5) + rng.randint(-1, 1)))


# ---------------------------------------------------------------------------- бригады


@dataclass
class _CrewSpec:
    skills: set[Skill]
    transport: Transport
    shift: tuple[str, str]
    depot: Location


def _crew_specs(
    rng: random.Random,
    n: int,
    depots: Sequence[Location],
    *,
    profiles: Sequence[tuple[tuple[Skill, ...], int]] = SKILL_PROFILES,
    transports: Sequence[tuple[Transport, int]] = MOSCOW_TRANSPORT,
    need: dict[Skill, int] | None = None,
) -> list[_CrewSpec]:
    specs: list[_CrewSpec] = []
    for i in range(n):
        skills = set(_weighted(rng, profiles))
        transport = _weighted(rng, transports)
        if E in skills and rng.random() < 0.5:
            transport = Transport.CAR  # аварийщику нужен сварочный аппарат — чаще на машине
        shift = _weighted(rng, SHIFT_MIX)
        specs.append(_CrewSpec(skills, transport, shift, depots[i % len(depots)]))
    if need is None:
        need = {E: min(2, n), C: max(1, round(0.4 * n)), L: max(1, round(0.4 * n))}
    _ensure_skills(specs, need)
    return specs


def _ensure_skills(specs: list[_CrewSpec], need: dict[Skill, int]) -> None:
    """Добавляет навык бригадам с наименьшим числом навыков, пока навыка не хватает."""
    for skill in (E, C, L):
        have = sum(1 for s in specs if skill in s.skills)
        lacking = sorted(
            (i for i, s in enumerate(specs) if skill not in s.skills),
            key=lambda i: (len(specs[i].skills), i),
        )
        for i in lacking[: max(0, need.get(skill, 0) - have)]:
            specs[i].skills.add(skill)


def _names(rng: random.Random, n: int) -> list[str]:
    if n <= len(SURNAMES):
        return rng.sample(SURNAMES, n)
    base = list(SURNAMES)
    rng.shuffle(base)
    return [
        base[i % len(base)] + ("" if i < len(base) else f"-{i // len(base) + 1}") for i in range(n)
    ]


def _engineers(rng: random.Random, specs: Sequence[_CrewSpec]) -> list[Engineer]:
    names = _names(rng, len(specs))
    width = 2 if len(specs) < 100 else 3
    return [
        Engineer(
            id=f"e{i + 1:0{width}d}",
            name=f"Бригада {names[i]}",
            skills=[s for s in SKILL_ORDER if s in spec.skills],
            transport=spec.transport,
            shift=TimeWindow(start=spec.shift[0], end=spec.shift[1]),
            depot=spec.depot,
        )
        for i, spec in enumerate(specs)
    ]


# ---------------------------------------------------------------------------- заявки


class _Orders:
    """Накопитель заявок сценария: сквозные ID, окна, требования к транспорту."""

    def __init__(self, rng: random.Random, engineers: Sequence[Engineer]):
        self.rng = rng
        self.engineers = list(engineers)
        self.orders: list[Order] = []

    def satisfiable(self, kind: WorkType, transport: Transport | None) -> bool:
        return any(
            kind.skill in e.skills and (transport is None or e.transport == transport)
            for e in self.engineers
        )

    def car_if_possible(self, kind: WorkType, p_emergency: float = 0.3, p_other: float = 0.03):
        """Иногда заявка требует автомобиль (авария — сварка); только если такая бригада есть."""
        p = p_emergency if kind == WorkType.EMERGENCY else p_other
        if self.rng.random() < p and self.satisfiable(kind, Transport.CAR):
            return Transport.CAR
        return None

    def add(
        self,
        kind: WorkType,
        district: str,
        point: tuple[float, float],
        *,
        window: tuple[int, int] | None = None,
        transport: Transport | None = None,
        address: str | None = None,
    ) -> Order:
        oid = f"S{len(self.orders) + 1:03d}"
        if address is None:
            street = self.rng.choice(STREETS)
            address = f"{district}, ул. {street}, д. {self.rng.randint(1, 120)}"
        if kind == WorkType.EMERGENCY:
            tw = TimeWindow(start="00:01", end="23:59")  # авария известна утром, окно на весь день
        else:
            if window is None:
                raise ValueError("Для обычной заявки нужно окно")
            tw = TimeWindow(start=minutes_to_time(window[0]), end=minutes_to_time(window[1]))
        order = Order(
            id=oid,
            skills=[kind.skill],
            priority=Priority.URGENT if kind == WorkType.EMERGENCY else Priority.NORMAL,
            work_type=kind,
            window=tw,
            duration_min=kind.work_min,
            required_transport=transport,
            location=Location(lat=point[0], lon=point[1], address=address, district=district),
            bk_type=BK_TYPES[kind],
        )
        self.orders.append(order)
        return order


Generated = tuple[list[Order], list[Engineer], list[str]]


def _moscow_region(rng: random.Random, k: int, anchors: Sequence[str] | None = None):
    anchor = rng.choice(list(anchors or MOSCOW_DISTRICTS))
    districts = _nearest_districts(anchor, k)
    return anchor, districts, _office(rng, anchor)


def _fill_moscow(
    f: _Orders,
    types: Sequence[WorkType],
    districts: Sequence[str],
    window_fn: Callable[[], tuple[int, int]],
) -> None:
    for kind in types:
        district, point = _moscow_point(f.rng, districts)
        window = None if kind == WorkType.EMERGENCY else window_fn()
        f.add(kind, district, point, window=window, transport=f.car_if_possible(kind))


# ---------------------------------------------------------------------------- виды сценариев


def _city(
    rng: random.Random,
    n_orders: int | None,
    n_crews: int | None,
    *,
    weights: Sequence[float] = REAL_PEAKS,
    wide: bool = False,
) -> Generated:
    n = n_orders or rng.randint(60, 100)
    m = n_crews or _default_crews(rng, n)
    anchor, districts, office = _moscow_region(rng, rng.randint(4, 6))
    engineers = _engineers(rng, _crew_specs(rng, m, [office]))
    f = _Orders(rng, engineers)
    window_fn = (lambda: _wide_window(rng)) if wide else (lambda: _two_hour_window(rng, weights))
    _fill_moscow(f, _type_sequence(rng, n, STANDARD_MIX), districts, window_fn)
    notes = [f"Соседние районы: {', '.join(districts)}; офис — {anchor}."]
    return f.orders, engineers, notes


def _gen_city(rng, n_orders, n_crews) -> Generated:
    return _city(rng, n_orders, n_crews)


def _gen_peak(rng, n_orders, n_crews) -> Generated:
    orders, engineers, notes = _city(rng, n_orders, n_crews, weights=PEAK_HEAVY)
    regular = [o for o in orders if not o.is_emergency]
    peak = sum(1 for o in regular if o.window.start in ("10:00", "18:00"))
    notes.append(f"Окна 10–12 и 18–20: {peak} из {len(regular)} обычных заявок.")
    return orders, engineers, notes


def _gen_wide(rng, n_orders, n_crews) -> Generated:
    orders, engineers, notes = _city(rng, n_orders, n_crews, wide=True)
    notes.append("Окна по 4 ч: 10–14, 14–18, 18–22; у аварий — весь день.")
    return orders, engineers, notes


def _gen_clustered(rng, n_orders, n_crews) -> Generated:
    n = n_orders or rng.randint(60, 100)
    m = n_crews or _default_crews(rng, n)
    anchor, districts, office = _moscow_region(rng, rng.randint(5, 8))
    engineers = _engineers(rng, _crew_specs(rng, m, [office]))
    f = _Orders(rng, engineers)
    k = rng.randint(4, 8)
    homes = rng.sample(districts, min(k, len(districts)))
    homes += [rng.choice(districts) for _ in range(k - len(homes))]
    clusters = [
        (
            d,
            disk_point(rng, DISTRICT_CENTROIDS[d], 1.2),
            rng.uniform(0.2, 0.5),
            rng.uniform(0.5, 1.5),
        )
        for d in homes
    ]
    for kind in _type_sequence(rng, n, STANDARD_MIX):
        district, center, radius, _ = rng.choices(clusters, weights=[c[3] for c in clusters])[0]
        point = disk_point(rng, center, radius)
        window = None if kind == WorkType.EMERGENCY else _two_hour_window(rng, REAL_PEAKS)
        f.add(kind, district, point, window=window, transport=f.car_if_possible(kind))
    sizes = Counter(o.location.district for o in f.orders)
    notes = [
        f"Плотных кластеров радиусом 0,2–0,5 км: {k}; заявок по районам: "
        + ", ".join(f"{d} ({sizes[d]})" for d in sorted(sizes))
        + f"; офис — {anchor}."
    ]
    return f.orders, engineers, notes


def _gen_suburban(rng, n_orders, n_crews) -> Generated:
    n = n_orders or rng.randint(60, 100)
    share = rng.uniform(0.35, 0.40)
    towns = sorted(rng.sample(sorted(TOWNS), rng.randint(2, 3)))
    n_town = round(n * share)
    n_msk = n - n_town
    town_weights = [rng.uniform(0.7, 1.3) for _ in towns]
    town_counts = Counter(rng.choices(towns, weights=town_weights, k=n_town))

    anchor, districts, office = _moscow_region(rng, rng.randint(3, 5), SOUTH_EAST_ANCHORS)
    local_per_town = {t: max(1, math.ceil(town_counts[t] / 8)) for t in towns}
    if n_crews is not None:
        local_total = sum(local_per_town.values())
        while local_total > max(len(towns), n_crews - 1) and any(
            v > 1 for v in local_per_town.values()
        ):
            biggest = max(sorted(local_per_town), key=lambda t: local_per_town[t])
            local_per_town[biggest] -= 1
            local_total -= 1
        m_msk = max(1, n_crews - local_total)
    else:
        m_msk = max(4, min(10, round(n_msk / 6.5)))

    specs = _crew_specs(rng, m_msk, [office])
    for town in towns:
        for j in range(local_per_town[town]):
            lat, lon = disk_point(rng, TOWNS[town], 1.5)
            home = Location(lat=lat, lon=lon, address=f"г. {town}, дом исполнителя", district=town)
            skills = {L, C, E} if j == 0 else set(_weighted(rng, BROAD_PROFILES))
            shift = SHIFT_2_2 if rng.random() < 0.8 else _weighted(rng, SHIFT_MIX)
            specs.append(_CrewSpec(skills, Transport.CAR, shift, home))
    engineers = _engineers(rng, specs)
    f = _Orders(rng, engineers)

    places = ["Москва"] * n_msk + [t for t in towns for _ in range(town_counts[t])]
    rng.shuffle(places)
    for kind, place in zip(_type_sequence(rng, n, STANDARD_MIX), places):
        window = None if kind == WorkType.EMERGENCY else _two_hour_window(rng, REAL_PEAKS)
        if place == "Москва":
            district, point = _moscow_point(rng, districts)
        else:
            district, point = place, disk_point(rng, TOWNS[place], 2.5)
        f.add(kind, district, point, window=window, transport=f.car_if_possible(kind, 0.5))
    notes = [
        f"Пригород: {n_town} из {n} заявок ("
        + ", ".join(f"{t} {town_counts[t]}" for t in towns)
        + "); местные бригады на авто: "
        + ", ".join(f"{t} {local_per_town[t]}" for t in towns)
        + f"; московский офис — {anchor}."
    ]
    return f.orders, engineers, notes


def _gen_emergency_heavy(rng, n_orders, n_crews) -> Generated:
    n = n_orders or rng.randint(60, 100)
    m = n_crews or _default_crews(rng, n)
    _anchor, districts, office = _moscow_region(rng, rng.randint(4, 6))
    need = {E: max(2, round(0.5 * m)), C: max(1, round(0.4 * m)), L: max(1, round(0.4 * m))}
    engineers = _engineers(rng, _crew_specs(rng, m, [office], need=need))
    f = _Orders(rng, engineers)
    types = _type_sequence(rng, n, EMERGENCY_MIX)
    left = types.count(WorkType.EMERGENCY)
    slots: list[tuple[str, tuple[float, float], str]] = []
    sizes: list[int] = []
    for _ in range(rng.randint(2, 3)):
        size = min(left, rng.randint(3, 6))
        if size < 2:
            break
        district, center = _moscow_point(rng, districts)
        street, house = rng.choice(STREETS), rng.randint(1, 60)
        for j in range(size):
            address = f"{district}, ул. {street}, д. {house + j // 2}, корп. {j % 2 + 1}"
            slots.append((district, disk_point(rng, center, 0.15), address))
        sizes.append(size)
        left -= size
    for kind in types:
        if kind == WorkType.EMERGENCY and slots:
            district, point, address = slots.pop(0)
            f.add(kind, district, point, transport=f.car_if_possible(kind), address=address)
            continue
        district, point = _moscow_point(rng, districts)
        window = None if kind == WorkType.EMERGENCY else _two_hour_window(rng, REAL_PEAKS)
        f.add(kind, district, point, window=window, transport=f.car_if_possible(kind))
    n_em_crews = sum(1 for e in engineers if E in e.skills)
    clustered = ", ".join(map(str, sizes)) or "нет"
    notes = [
        (
            f"Аварий {types.count(WorkType.EMERGENCY)} из {n}, все известны утром; кластеры "
            f"соседних домов: {clustered}; аварийный навык у {n_em_crews} из {m} бригад."
        )
    ]
    return f.orders, engineers, notes


def _gen_skill_scarce(rng, n_orders, n_crews) -> Generated:
    n = n_orders or rng.randint(60, 100)
    m = n_crews or _default_crews(rng, n)
    anchor, districts, office = _moscow_region(rng, rng.randint(4, 6))
    n_em = min(m, rng.randint(1, 2))
    n_conn = min(max(1, round(0.25 * m)), m - n_em)
    specs: list[_CrewSpec] = []
    for i in range(m):
        if i < n_em:
            skills = {E} | ({L} if rng.random() < 0.5 else set())
        elif i < n_em + n_conn:
            skills = {C} | ({L} if rng.random() < 0.5 else set())
        else:
            skills = {L}
        transport = Transport.CAR if E in skills else _weighted(rng, MOSCOW_TRANSPORT)
        specs.append(_CrewSpec(skills, transport, _weighted(rng, SHIFT_MIX), office))
    rng.shuffle(specs)
    engineers = _engineers(rng, specs)
    f = _Orders(rng, engineers)
    _fill_moscow(
        f,
        _type_sequence(rng, n, STANDARD_MIX),
        districts,
        lambda: _two_hour_window(rng, REAL_PEAKS),
    )
    notes = [f"Аварии умеют {n_em} из {m} бригад, подключения — {n_conn}; офис — {anchor}."]
    return f.orders, engineers, notes


def _gen_transport_mix(rng, n_orders, n_crews) -> Generated:
    n = n_orders or rng.randint(60, 100)
    m = n_crews or _default_crews(rng, n)
    _anchor, districts, office = _moscow_region(rng, rng.randint(4, 6))
    specs = _crew_specs(rng, m, [office])
    for spec, tr in zip(specs, (Transport.CAR, Transport.TRANSIT, Transport.FOOT, Transport.BIKE)):
        spec.transport = tr  # все четыре вида транспорта (если бригад хватает)
    rng.shuffle(specs)
    engineers = _engineers(rng, specs)
    f = _Orders(rng, engineers)
    for kind in _type_sequence(rng, n, STANDARD_MIX):
        district, point = _moscow_point(rng, districts)
        window = None if kind == WorkType.EMERGENCY else _two_hour_window(rng, REAL_PEAKS)
        transport = _weighted(rng, REQUIRED_TRANSPORT) if rng.random() < 0.2 else None
        f.add(kind, district, point, window=window, transport=transport)
    required = [o for o in f.orders if o.required_transport is not None]
    unsat = [o for o in required if not f.satisfiable(o.kind, o.required_transport)]
    listed = ", ".join(
        f"#{o.id} ({o.kind.label_ru.lower()}, {o.required_transport.label_ru.lower()})"
        for o in unsat
        if o.required_transport is not None
    )
    kinds = Counter(e.transport.label_ru.lower() for e in engineers)
    notes = [
        f"Требуют транспорт {len(required)} из {n} заявок; бригады: "
        + ", ".join(f"{k} {v}" for k, v in sorted(kinds.items()))
        + ".",
        f"Невыполнимы по навыку и транспорту: {listed or 'нет'}.",
    ]
    return f.orders, engineers, notes


def _workload(types: Sequence[WorkType]) -> int:
    return sum(k.work_min + TRAVEL_ESTIMATE_MIN for k in types)


def _gen_overload(rng, n_orders, n_crews) -> Generated:
    avg_order = sum(share * (k.work_min + TRAVEL_ESTIMATE_MIN) for k, share in STANDARD_MIX)
    avg_shift = 0.7 * 720 + 0.3 * 540
    if n_crews is not None:
        m = n_crews
    elif n_orders is not None:
        m = max(1, round(n_orders * avg_order / (1.5 * avg_shift)))
    else:
        m = rng.randint(6, 8)
    anchor, districts, office = _moscow_region(rng, rng.randint(4, 6))
    engineers = _engineers(rng, _crew_specs(rng, m, [office]))
    capacity = sum(e.shift.end_min - e.shift.start_min for e in engineers)
    if n_orders is not None:
        types = _type_sequence(rng, n_orders, STANDARD_MIX)
    else:
        n = math.ceil(1.5 * capacity / avg_order)
        types = _type_sequence(rng, n, STANDARD_MIX)
        while _workload(types) < 1.5 * capacity and n < 200:
            n += 1
            types = _type_sequence(rng, n, STANDARD_MIX)
    f = _Orders(rng, engineers)
    _fill_moscow(f, types, districts, lambda: _two_hour_window(rng, REAL_PEAKS))
    ratio = _workload(types) / capacity
    notes = [
        (
            f"Нагрузка ≈ {ratio:.2f} ёмкости смен (работа + ~{TRAVEL_ESTIMATE_MIN} мин дороги "
            f"на заявку, {len(types)} заявок на {m} бригад); офис — {anchor}."
        )
    ]
    return f.orders, engineers, notes


def _gen_large(rng, n_orders, n_crews) -> Generated:
    n = n_orders or rng.randint(280, 320)
    m = n_crews or rng.randint(36, 44)
    anchor = rng.choice(MOSCOW_DISTRICTS)
    districts = _nearest_districts(anchor, rng.randint(12, 14))
    hubs = [anchor]
    while len(hubs) < 3:  # офисы подальше друг от друга
        far = max(
            sorted(d for d in districts if d not in hubs),
            key=lambda d: min(_km(DISTRICT_CENTROIDS[d], DISTRICT_CENTROIDS[h]) for h in hubs),
        )
        hubs.append(far)
    offices = [_office(rng, h) for h in hubs]
    engineers = _engineers(rng, _crew_specs(rng, m, offices))
    f = _Orders(rng, engineers)
    _fill_moscow(
        f,
        _type_sequence(rng, n, STANDARD_MIX),
        districts,
        lambda: _two_hour_window(rng, REAL_PEAKS),
    )
    notes = [f"{len(districts)} районов, офисы: {', '.join(hubs)}."]
    return f.orders, engineers, notes


def _gen_tiny(rng, n_orders, n_crews) -> Generated:
    m = n_crews or rng.randint(1, 3)
    n = n_orders or rng.randint(3, 8)
    mode = ("skill", "transport", "window")[rng.randrange(3)]
    _anchor, districts, office = _moscow_region(rng, rng.randint(1, 2))
    specs: list[_CrewSpec] = []
    for _ in range(m):
        profiles = (
            [(p, w) for p, w in SKILL_PROFILES if E not in p] if mode == "skill" else SKILL_PROFILES
        )
        skills = set(_weighted(rng, profiles)) | {
            L
        }  # ремонт умеют все: невыполнимость — только в одном
        if mode == "transport":
            transport = rng.choice((Transport.CAR, Transport.TRANSIT, Transport.BIKE))
        else:
            transport = _weighted(rng, MOSCOW_TRANSPORT)
        shift = ("10:00", "19:00") if mode == "window" else _weighted(rng, SHIFT_MIX)
        specs.append(_CrewSpec(skills, transport, shift, office))
    engineers = _engineers(rng, specs)
    f = _Orders(rng, engineers)
    regular_mix = [
        (k, s) for k, s in STANDARD_MIX if not (mode == "skill" and k == WorkType.EMERGENCY)
    ]
    for kind in [_weighted(rng, regular_mix) for _ in range(max(0, n - 1))]:
        district, point = _moscow_point(rng, districts)
        window = None if kind == WorkType.EMERGENCY else _two_hour_window(rng, REAL_PEAKS)
        if mode == "window" and window is not None and window[0] >= 1080:
            window = (
                600,
                720,
            )  # смены до 19:00: вечерние окна оставляем только невыполнимой заявке
        f.add(kind, district, point, window=window)
    district, point = _moscow_point(rng, districts)
    if mode == "skill":
        bad = f.add(WorkType.EMERGENCY, district, point)
        expected = "skill"
    elif mode == "transport":
        bad = f.add(WorkType.LOCAL, district, point, window=(720, 840), transport=Transport.FOOT)
        expected = "transport"
    else:
        bad = f.add(WorkType.LOCAL, district, point, window=(1200, 1320))
        expected = "shift_window"
    notes = [f"Невыполнимая заявка #{bad.id}: ожидаемая причина — {expected}."]
    return f.orders, engineers, notes


SYNTHETIC_GENERATORS: dict[str, Callable[[random.Random, int | None, int | None], Generated]] = {
    "city": _gen_city,
    "clustered": _gen_clustered,
    "peak": _gen_peak,
    "suburban": _gen_suburban,
    "emergency_heavy": _gen_emergency_heavy,
    "skill_scarce": _gen_skill_scarce,
    "transport_mix": _gen_transport_mix,
    "overload": _gen_overload,
    "wide_windows": _gen_wide,
    "large": _gen_large,
    "tiny": _gen_tiny,
}
SYNTHETIC_KINDS: tuple[str, ...] = tuple(SYNTHETIC_GENERATORS)
REAL_KINDS: tuple[str, ...] = tuple(f"{p}:{r}" for p in REAL_PERTURBATIONS for r in REAL_REGIONS)
KINDS: list[str] = [*SYNTHETIC_KINDS, *REAL_KINDS]
# виды, где зерно ни на что не влияет: достаточно одного прогона
SEED_INDEPENDENT_KINDS: frozenset[str] = frozenset(f"real_long_shifts:{r}" for r in REAL_REGIONS)


# ---------------------------------------------------------------------------- реальные участки

_REAL_CACHE: dict[str, tuple[list[Order], list[Engineer]]] = {}


def _load_real(region: str) -> tuple[list[Order], list[Engineer]]:
    if region not in _REAL_CACHE:
        from app.cli import load_normalized_dataset  # тяжёлый импорт — только для реальных видов

        _REAL_CACHE[region] = load_normalized_dataset(region)
    orders, engineers = _REAL_CACHE[region]
    return [o.model_copy(deep=True) for o in orders], [e.model_copy(deep=True) for e in engineers]


def _gen_real(kind: str, rng: random.Random) -> Generated:
    base, region = kind.split(":", 1)
    orders, engineers = _load_real(region)
    notes = [f"Реальный участок «{REGION_NAMES[region]}»."]
    if base == "real_shuffled":
        rng.shuffle(orders)
        notes.append("Порядок заявок в файле перемешан (базовый вариант от него зависит).")
    elif base == "real_fewer_crews":
        k = max(1, round(0.3 * len(engineers)))
        removed = sorted(rng.sample([e.id for e in engineers], k))
        engineers = [e for e in engineers if e.id not in set(removed)]
        notes.append(f"Убраны 30 % бригад ({k}): {', '.join(removed)}.")
    elif base == "real_jitter":
        moved = []
        for o in orders:
            lat, lon = disk_point(rng, (o.location.lat, o.location.lon), 0.3)
            moved.append(
                o.model_copy(
                    update={"location": o.location.model_copy(update={"lat": lat, "lon": lon})}
                )
            )
        orders = moved
        notes.append("Координаты заявок сдвинуты случайно в пределах 300 м.")
    elif base == "real_long_shifts":
        engineers = [
            e.model_copy(update={"shift": TimeWindow(start="10:00", end="22:00")})
            for e in engineers
        ]
        notes.append("Все смены заменены на 2/2: 10:00–22:00.")
    else:
        raise ValueError(f"Неизвестное возмущение: {base}")
    return orders, engineers, notes


# ---------------------------------------------------------------------------- публичный API


def summary_note(orders: Sequence[Order], engineers: Sequence[Engineer]) -> str:
    kinds = Counter(o.kind for o in orders)
    shifts = Counter(f"{e.shift.start}–{e.shift.end}" for e in engineers)
    transports = Counter(e.transport.label_ru.lower() for e in engineers)
    required = sum(1 for o in orders if o.required_transport is not None)
    return (
        f"Заявок {len(orders)}: "
        + ", ".join(f"{k.label_ru.lower()} {kinds[k]}" for k in WorkType if kinds[k])
        + f"; требуют транспорт {required}. Бригад {len(engineers)}; смены: "
        + ", ".join(f"{s} ×{c}" for s, c in sorted(shifts.items()))
        + "; транспорт: "
        + ", ".join(f"{t} {c}" for t, c in sorted(transports.items()))
        + "."
    )


def generate(
    kind: str, seed: int, *, n_orders: int | None = None, n_crews: int | None = None
) -> Scenario:
    """Сценарий заданного вида. Для реальных участков n_orders и n_crews не используются."""
    rng = random.Random(f"{kind}|{seed}")
    if kind in SYNTHETIC_GENERATORS:
        orders, engineers, notes = SYNTHETIC_GENERATORS[kind](rng, n_orders, n_crews)
    elif kind in REAL_KINDS:
        orders, engineers, notes = _gen_real(kind, rng)
    else:
        raise ValueError(f"Неизвестный вид сценария «{kind}». Доступны: {', '.join(KINDS)}")
    if len({o.id for o in orders}) != len(orders):
        raise AssertionError(f"{kind}#{seed}: повторяющиеся ID заявок")
    return Scenario(
        name=f"{kind}#{seed}",
        kind=kind,
        seed=seed,
        orders=orders,
        engineers=engineers,
        notes=[summary_note(orders, engineers), *notes],
    )
