"""Почему заявка не назначена: разбор по каждой бригаде через RouteEvaluator и одна фраза для диспетчера."""

from collections import Counter

from app.domain.enums import ReasonCode, Skill, Transport
from app.domain.models import Engineer, Order, TimeWindow, UnassignedInfo, minutes_to_time
from app.solver.evaluator import RouteEvaluator
from app.solver.fleet import Fleet
from app.solver.search.insertion import insert_pool


def plural(n: int, one: str, few: str, many: str) -> str:
    n = abs(n)
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def crew_name(name: str) -> str:
    """«Бригада Иванов» — чтобы глаголы согласовывались в женском роде при любом формате имени."""
    return name if name.lower().startswith("бригада") else f"Бригада {name}"


def km_ru(value: float) -> str:
    """«2,4 км» — десятичная запятая, как в интерфейсе."""
    return f"{value:.1f}".replace(".", ",") + " км"


def crews_word(n: int) -> str:
    return f"{n} {plural(n, 'бригада', 'бригады', 'бригад')}"


def engineer_reasons(
    fleet: Fleet, order: Order, *, append_only: bool = False
) -> dict[str, ReasonCode | None]:
    """Код причины для каждой бригады; None — бригада могла бы взять заявку в текущем плане."""
    ev = fleet.evaluator
    out: dict[str, ReasonCode | None] = {}
    for e in fleet.engineers:
        st = fleet.states[e.id]
        code = ev.compat(e, order, st.start, available=fleet.available.get(e.id, True))
        if code is None:
            positions = [len(st)] if append_only else None
            code = None if ev.insertions(st, order, positions=positions) else ReasonCode.BUSY
        out[e.id] = code
    return out


def _skills_ru(order: Order) -> str:
    return ", ".join(f"«{s.label_ru}»" for s in order.skills)


def describe(
    order: Order,
    reasons: dict[str, ReasonCode | None],
    engineers: list[Engineer],
    *,
    event_time_min: int | None = None,
    context: str | None = None,
) -> UnassignedInfo:
    """Сводит причины по бригадам в одну фразу и подсказку."""
    window = f"{order.window.start}–{order.window.end}"
    counts = Counter(code.value if code else "ok" for code in reasons.values())
    names = {e.id: e.name for e in engineers}

    def remaining(*excluded: ReasonCode) -> list[str]:
        return [eid for eid, code in reasons.items() if code not in excluded]

    need = f"навыком {_skills_ru(order)}"
    if order.required_transport is not None:
        need += f" и транспортом «{order.required_transport.label_ru.lower()}»"

    if event_time_min is not None and order.window.end_min < event_time_min:
        code = ReasonCode.WINDOW_PASSED
        text = f"Окно {window} уже прошло к моменту события ({minutes_to_time(event_time_min)})."
        hint = "Согласуйте с клиентом новое время."
    elif not remaining(ReasonCode.SKILL):
        code = ReasonCode.SKILL
        text = f"Ни у одной бригады нет навыка {_skills_ru(order)}."
        hint = f"Нужна бригада с {need}."
    elif not remaining(ReasonCode.SKILL, ReasonCode.TRANSPORT):
        code = ReasonCode.TRANSPORT
        transport = order.required_transport.label_ru.lower() if order.required_transport else "—"
        text = (
            f"Заявка требует транспорт «{transport}», а у бригад с навыком "
            f"{_skills_ru(order)} его нет."
        )
        hint = f"Нужна бригада с {need}."
    elif not remaining(ReasonCode.SKILL, ReasonCode.TRANSPORT, ReasonCode.UNAVAILABLE):
        code = ReasonCode.UNAVAILABLE
        text = "Все подходящие по навыку и транспорту бригады сошли с линии."
        hint = f"Нужна замена: бригада с {need}."
    else:
        fit = remaining(ReasonCode.SKILL, ReasonCode.TRANSPORT, ReasonCode.UNAVAILABLE)
        timely = [eid for eid in fit if reasons[eid] != ReasonCode.SHIFT_WINDOW]
        free = [eid for eid in timely if reasons[eid] is None]
        if not timely:
            code = ReasonCode.SHIFT_WINDOW
            if event_time_min is None:
                text = (
                    f"Смены подходящих бригад ({len(fit)}) не покрывают окно {window} "
                    f"с учётом дороги."
                )
            else:
                text = f"Подходящие бригады ({len(fit)}) уже не успевают доехать в окно {window}."
            hint = f"Нужна бригада с {need}, свободная в {window}."
        elif free:
            code = ReasonCode.BUSY
            listed = ", ".join(names.get(eid, eid) for eid in free[:3])
            text = (
                f"Взять может только бригада не на линии: {listed}. "
                f"Автоматически не назначено, чтобы не выводить дополнительную бригаду."
            )
            hint = "Назначьте вручную или вызовите бригаду."
        else:
            code = ReasonCode.BUSY
            k = len(timely)
            if k == 1:
                text = f"Единственная подходящая бригада занята в окно {window}."
            elif k == 2:
                text = f"Обе подходящие бригады заняты в окно {window}."
            else:
                adj = plural(k, "подходящая", "подходящие", "подходящих")
                verb = plural(k, "занята", "заняты", "заняты")
                text = f"Все {k} {adj} {plural(k, 'бригада', 'бригады', 'бригад')} {verb} в окно {window}."
            hint = f"Нужна ещё 1 бригада с {need}, свободная в {window}."

    if context:
        text = f"{context} {text}"
    return UnassignedInfo(
        code=code,
        text=text,
        hint=hint,
        engineer_codes=dict(sorted(counts.items())),
        context=context,
    )


def diagnose(
    fleet: Fleet,
    order: Order,
    *,
    append_only: bool = False,
    event_time_min: int | None = None,
    context: str | None = None,
) -> UnassignedInfo:
    reasons = engineer_reasons(fleet, order, append_only=append_only)
    return describe(
        order, reasons, fleet.engineers, event_time_min=event_time_min, context=context
    )


def estimate_extra_crews(
    evaluator: RouteEvaluator, engineers: list[Engineer], unassigned: list[Order]
) -> int:
    """Сколько ещё бригад (все навыки, смена на весь рабочий день) закрыли бы неназначенные заявки."""
    if not unassigned or not engineers:
        return 0
    depots = Counter((e.depot.lat, e.depot.lon) for e in engineers)
    (lat, lon), _ = depots.most_common(1)[0]
    depot = next(e.depot for e in engineers if (e.depot.lat, e.depot.lon) == (lat, lon))
    shift = TimeWindow(
        start=minutes_to_time(min(e.shift.start_min for e in engineers)),
        end=minutes_to_time(max(e.shift.end_min for e in engineers)),
    )
    transports = sorted({o.required_transport or Transport.CAR for o in unassigned}, key=str)
    virtual = [
        Engineer(
            id=f"__extra_{tr.value}_{k}",
            name=f"Доп. бригада {k + 1}",
            skills=[Skill.LOCAL, Skill.CONNECTION, Skill.EMERGENCY],
            transport=tr,
            shift=shift,
            depot=depot,
        )
        for tr in transports
        for k in range(len(unassigned))
    ]
    fleet = Fleet.empty(evaluator, virtual)
    insert_pool(fleet, list(unassigned))
    return fleet.crews()
