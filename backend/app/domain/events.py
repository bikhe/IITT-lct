from enum import Enum
from typing import Any

from pydantic import BaseModel, Field

from app.domain.models import Order


class EventType(str, Enum):
    """Тип события оперативного дня (ТЗ §2.1.6, §2.4)."""

    NEW_ORDER = "new_order"  # Новая обычная заявка: только в свободный интервал
    URGENT_ORDER = "urgent_order"  # Авария: как можно раньше, может перестроить хвост дня
    CANCEL_ORDER = "cancel_order"  # Отмена заявки клиентом
    ENGINEER_UNAVAILABLE = "engineer_unavailable"  # Поломка/сход инженера с линии
    MANUAL_ASSIGN = "manual_assign"  # Диспетчер вручную назначил заявку бригаде

    @property
    def label_ru(self) -> str:
        mapping = {
            EventType.NEW_ORDER: "Новая заявка",
            EventType.URGENT_ORDER: "Авария",
            EventType.CANCEL_ORDER: "Отмена заявки",
            EventType.ENGINEER_UNAVAILABLE: "Сход бригады",
            EventType.MANUAL_ASSIGN: "Ручное назначение",
        }
        return mapping[self]


class ReplanEvent(BaseModel):
    """Событие, инициирующее динамический пересчет плана."""

    event_type: EventType
    event_time: str = Field(..., description="Время наступления события (HH:MM), например 14:00")
    order_id: str | None = Field(
        default=None, description="ID заявки: отменяемой (CANCEL_ORDER) или назначаемой (MANUAL_ASSIGN)"
    )
    engineer_id: str | None = Field(
        default=None,
        description="ID выбывшего инженера (ENGINEER_UNAVAILABLE) или бригады-получателя (MANUAL_ASSIGN)",
    )
    new_order: Order | None = Field(
        default=None, description="Новая заявка (NEW_ORDER или URGENT_ORDER)"
    )
    description: str | None = Field(default=None, description="Описание или комментарий диспетчера")


class ChangeStatus(str, Enum):
    ADDED = "added"  # новая заявка поставлена в маршрут
    ASSIGNED = "assigned"  # ранее неназначенная заявка поставлена в маршрут
    REASSIGNED = "reassigned"  # перенесена к другой бригаде
    SHIFTED = "shifted"  # та же бригада, другое время
    UNASSIGNED = "unassigned"  # снята с маршрута или новая заявка не поставлена
    CANCELLED = "cancelled"  # отменена клиентом

    @property
    def label_ru(self) -> str:
        mapping = {
            ChangeStatus.ADDED: "новая",
            ChangeStatus.ASSIGNED: "назначена",
            ChangeStatus.REASSIGNED: "перенесена",
            ChangeStatus.SHIFTED: "сдвинута",
            ChangeStatus.UNASSIGNED: "снята",
            ChangeStatus.CANCELLED: "отменена",
        }
        return mapping[self]


class ReassignedJob(BaseModel):
    """Изменение по одной заявке после события."""

    order_id: str
    old_engineer_id: str | None = None
    new_engineer_id: str | None = None
    old_engineer_name: str | None = None
    new_engineer_name: str | None = None
    old_start_time: str | None = None
    new_start_time: str | None = None
    shift_min: int | None = None
    status: ChangeStatus = ChangeStatus.REASSIGNED
    reason: str | None = None


class EngineerKm(BaseModel):
    engineer_id: str
    engineer_name: str
    km_before: float
    km_after: float
    orders_before: int
    orders_after: int


class PlanDiff(BaseModel):
    """Что изменилось в плане после события."""

    event: ReplanEvent
    added_order_ids: list[str] = Field(default_factory=list)
    cancelled_order_ids: list[str] = Field(default_factory=list)
    changes: list[ReassignedJob] = Field(default_factory=list, description="Все изменения по заявкам")
    reassigned_orders: list[ReassignedJob] = Field(
        default_factory=list, description="Перенесённые и сдвинутые (подмножество changes)"
    )
    unassigned_after_event: list[dict[str, str]] = Field(
        default_factory=list,
        description="Заявки, которые после события остались без исполнителя (order_id, reason)",
    )
    called_in_engineer_ids: list[str] = Field(
        default_factory=list, description="Бригады, выведенные на линию из резерва"
    )
    engineer_km: list[EngineerKm] = Field(default_factory=list)
    frozen_jobs_count: int = 0
    metrics_delta: dict[str, Any] = Field(
        default_factory=dict,
        description="Дельта ключевых показателей: пробег, задействованные бригады и т.д.",
    )
    summary_ru: str = ""
