from enum import Enum


class Skill(str, Enum):
    """Справочник требуемых навыков согласно ТЗ §2.4.1."""

    LOCAL = "local"  # Локальные работы
    CONNECTION = "connection"  # Работы на подключение и дозаказы
    EMERGENCY = "emergency"  # Аварийные работы

    @property
    def label_ru(self) -> str:
        mapping = {
            Skill.LOCAL: "Локальные работы",
            Skill.CONNECTION: "Подключение и дозаказы",
            Skill.EMERGENCY: "Аварийные работы",
        }
        return mapping[self]


class WorkType(str, Enum):
    """Вид работ по классификатору BK. Определяет норматив и приоритет при нехватке ресурсов."""

    EMERGENCY = "emergency"  # Глобальная проблема (авария)
    CONNECTION = "connection"  # Подключение
    LOCAL = "local"  # Локальная проблема (ремонт)
    ADDON = "addon"  # Дозаказ

    @property
    def label_ru(self) -> str:
        mapping = {
            WorkType.EMERGENCY: "Авария",
            WorkType.CONNECTION: "Подключение",
            WorkType.LOCAL: "Ремонт",
            WorkType.ADDON: "Дозаказ",
        }
        return mapping[self]

    @property
    def skill(self) -> Skill:
        mapping = {
            WorkType.EMERGENCY: Skill.EMERGENCY,
            WorkType.CONNECTION: Skill.CONNECTION,
            WorkType.LOCAL: Skill.LOCAL,
            WorkType.ADDON: Skill.CONNECTION,
        }
        return mapping[self]

    @property
    def work_min(self) -> int:
        """Чистое время работ: норматив минус 20 мин брони на дорогу (её заменяет расчётное время в пути)."""
        mapping = {
            WorkType.EMERGENCY: 80,
            WorkType.CONNECTION: 70,
            WorkType.LOCAL: 30,
            WorkType.ADDON: 20,
        }
        return mapping[self]

    @property
    def priority_tier(self) -> int:
        """Порядок распределения при нехватке ресурсов: авария → подключение → ремонт/дозаказ."""
        mapping = {
            WorkType.EMERGENCY: 0,
            WorkType.CONNECTION: 1,
            WorkType.LOCAL: 2,
            WorkType.ADDON: 2,
        }
        return mapping[self]

    @property
    def weight(self) -> int:
        """Вес заявки в цели «больше выполненных заявок» с учётом приоритета."""
        mapping = {
            WorkType.EMERGENCY: 10,
            WorkType.CONNECTION: 5,
            WorkType.LOCAL: 3,
            WorkType.ADDON: 3,
        }
        return mapping[self]


class Transport(str, Enum):
    """Справочник типов транспортных средств согласно ТЗ §2.4.1."""

    CAR = "car"  # Автомобиль
    FOOT = "foot"  # Пешеход
    BIKE = "bike"  # Велосипед
    TRANSIT = "transit"  # Общественный транспорт

    @property
    def label_ru(self) -> str:
        mapping = {
            Transport.CAR: "Автомобиль",
            Transport.FOOT: "Пешеход",
            Transport.BIKE: "Велосипед",
            Transport.TRANSIT: "Общественный транспорт",
        }
        return mapping[self]

    @property
    def speed_kmh(self) -> float:
        """Расчетная скорость в городских условиях."""
        mapping = {
            Transport.CAR: 30.0,
            Transport.TRANSIT: 18.0,
            Transport.BIKE: 12.0,
            Transport.FOOT: 4.5,
        }
        return mapping[self]

    @property
    def boarding_delay_min(self) -> int:
        """Добавочное время ожидания/посадки для общественного транспорта."""
        return 5 if self == Transport.TRANSIT else 0


class Priority(str, Enum):
    """Приоритет заявки согласно ТЗ §2.4.1."""

    NORMAL = "normal"  # Обычная
    URGENT = "urgent"  # Срочная

    @property
    def label_ru(self) -> str:
        return "Срочная" if self == Priority.URGENT else "Обычная"


class OrderStatus(str, Enum):
    """Статус заявки в системе."""

    UNASSIGNED = "unassigned"
    ASSIGNED = "assigned"
    EN_ROUTE = "en_route"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    CANCELLED = "cancelled"


class JobStatus(str, Enum):
    """Состояние работы в маршруте на момент последнего события."""

    PLANNED = "planned"  # ещё не выехали — можно переносить
    EN_ROUTE = "en_route"  # бригада выехала или ждёт у клиента — заморожено
    IN_PROGRESS = "in_progress"  # работа идёт — заморожено
    DONE = "done"  # выполнено
    CANCELLED = "cancelled"  # клиент отменил, когда бригада уже выехала

    @property
    def label_ru(self) -> str:
        mapping = {
            JobStatus.PLANNED: "Запланирована",
            JobStatus.EN_ROUTE: "В пути",
            JobStatus.IN_PROGRESS: "В работе",
            JobStatus.DONE: "Выполнена",
            JobStatus.CANCELLED: "Отменена на месте",
        }
        return mapping[self]


class ReasonCode(str, Enum):
    """Почему конкретная бригада не может взять заявку. Единый справочник для поиска и объяснений."""

    SKILL = "skill"  # нет навыка
    TRANSPORT = "transport"  # нет требуемого транспорта
    SHIFT_WINDOW = "shift_window"  # окно заявки несовместимо со сменой даже при пустом маршруте
    UNAVAILABLE = "unavailable"  # бригада сошла с линии
    BUSY = "busy"  # по отдельности успевает, но маршрут уже занят
    WINDOW_LATE = "window_late"  # в маршруте начало работ позже окна
    SHIFT_END = "shift_end"  # в маршруте работа заканчивается после смены
    WINDOW_PASSED = "window_passed"  # окно заявки уже прошло к моменту события

    @property
    def label_ru(self) -> str:
        mapping = {
            ReasonCode.SKILL: "нет навыка",
            ReasonCode.TRANSPORT: "другой транспорт",
            ReasonCode.SHIFT_WINDOW: "смена не покрывает окно",
            ReasonCode.UNAVAILABLE: "сошла с линии",
            ReasonCode.BUSY: "занята",
            ReasonCode.WINDOW_LATE: "не успевает в окно",
            ReasonCode.SHIFT_END: "не успевает до конца смены",
            ReasonCode.WINDOW_PASSED: "окно прошло",
        }
        return mapping[self]
