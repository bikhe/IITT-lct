from app.data import EngineerSynthesizer, OrderNormalizer, RawDatasetLoader
from app.domain.enums import Priority, Skill, Transport, WorkType
from tests.helpers import RAW_DIR, needs_raw_csv


@needs_raw_csv
def test_raw_dataset_loader_east() -> None:
    loader = RawDatasetLoader()
    raw_file = RAW_DIR / "Восток Синтетические данные.csv"

    records, office = loader.load_csv(raw_file)
    assert len(records) == 66
    assert office is not None
    assert "адрес" in office.get("Заявка", "").lower() or "адрес" in office.get("Тип заявки BK", "").lower()


def test_order_normalizer() -> None:
    normalizer = OrderNormalizer()
    sample_raw = {
        "Заявка": "12345",
        "Тип заявки BK": "Подключение",
        "Тип заявки HD": "Заявка на подключение",
        "Начало": "17.08.2026 10:00",
        "Окончание": "17.08.2026 12:00",
        "Район": "Кузьминки",
        "Адрес": "Город Москва, пр-кт.Волгоградский, д. 128",
        "Подключение": "FMC",
        "Гигабитное подключение": "Да",
    }

    order = normalizer.normalize(sample_raw)
    assert order.id == "12345"
    assert order.primary_skill == Skill.CONNECTION
    assert order.work_type == WorkType.CONNECTION
    assert order.duration_min == 70
    assert order.priority == Priority.NORMAL
    assert order.window.start == "10:00"
    assert order.window.end == "12:00"
    assert order.window.start_min == 600
    assert order.window.end_min == 720
    assert order.tech is not None and order.tech.product == "FMC" and order.tech.gbit is True


def test_emergency_order_normalization() -> None:
    normalizer = OrderNormalizer()
    sample_raw = {
        "Заявка": "99999",
        "Тип заявки BK": "Глобальная проблема",
        "Тип заявки HD": "Авария",
        "Начало": "17.08.2026 20:00",
        "Окончание": "17.08.2026 22:00",
        "Район": "Бирюлево Восточное",
        "Адрес": "ул. Бирюлевская, 1",
        "Подключение": "",
        "Гигабитное подключение": "Нет",
    }

    order = normalizer.normalize(sample_raw)
    assert order.primary_skill == Skill.EMERGENCY
    assert order.work_type == WorkType.EMERGENCY
    assert order.priority == Priority.URGENT
    assert order.duration_min == 80
    assert order.required_transport == Transport.CAR


def test_engineer_synthesizer_determinism() -> None:
    synth1 = EngineerSynthesizer(seed=42)
    synth2 = EngineerSynthesizer(seed=42)

    names = ["Бригада А", "Бригада Б", "Бригада В"]
    engineers1 = synth1.synthesize_for_region("test", names)
    engineers2 = synth2.synthesize_for_region("test", names)

    assert len(engineers1) == len(engineers2) == 3
    for e1, e2 in zip(engineers1, engineers2, strict=False):
        assert e1.id == e2.id
        assert e1.name == e2.name
        assert e1.skills == e2.skills
        assert e1.transport == e2.transport
        assert e1.shift.start == e2.shift.start
        assert e1.depot.lat == e2.depot.lat



def _raw(order_id: str, bk: str, hd: str, district: str, address: str, start="10:00", end="12:00"):
    return {
        "Заявка": order_id,
        "Тип заявки BK": bk,
        "Тип заявки HD": hd,
        "Начало": f"17.08.2026 {start}",
        "Окончание": f"17.08.2026 {end}",
        "Район": district,
        "Адрес": address,
    }


def test_accident_is_defined_by_hd_type() -> None:
    """Эксперты 29.09: авария — по полю HD. «Глобальная проблема» с HD «Информация» — обычный визит."""
    normalizer = OrderNormalizer()
    info = normalizer.normalize(
        _raw("1", "Глобальная проблема", "Информация", "Выхино", "ул. Ташкентская, 16", "14:00", "16:00")
    )
    assert info.work_type == WorkType.LOCAL and info.priority == Priority.NORMAL
    assert info.skills == [Skill.LOCAL] and info.duration_min == 30
    assert info.required_transport is None
    cable = normalizer.normalize(_raw("2", "Локальная заявка", "Работа с кабелем", "Выхино", "д. 1"))
    assert cable.required_transport == Transport.CAR  # бухта кабеля и лестница — на автомобиле


def test_node_accident_merges_houses_of_one_town() -> None:
    """Аварии одного города области с одинаковым окном — одна авария на узле; Москва — по домам."""
    from app.data.normalizer import merge_node_accidents

    normalizer = OrderNormalizer()
    rows = [
        _raw("10", "Подключение", "Конвергенция абонента", "Кашира", "МО, г. Кашира Ленина ул. д. 1"),
        _raw("11", "Глобальная проблема", "Авария", "Кашира", "МО, г. Кашира Центральная ул. д. 21", "0:01", "23:59"),
        _raw("12", "Глобальная проблема", "Авария", "Кашира", "МО, г. Кашира Центральная ул. д. 19", "0:01", "23:59"),
        _raw("13", "Глобальная проблема", "Авария", "Кашира", "МО, г. Кашира Кржижановского ул. д. 5/1", "0:01", "23:59"),
        _raw("14", "Глобальная проблема", "Авария", "Ступино", "МО, г. Ступино Андропова ул. д. 37", "0:01", "23:59"),
        _raw("15", "Глобальная проблема", "Авария", "Царицыно", "Москва, ул. Луганская, д. 1", "0:01", "23:59"),
        _raw("16", "Глобальная проблема", "Авария", "Царицыно", "Москва, ул. Луганская, д. 3", "0:01", "23:59"),
    ]
    orders = merge_node_accidents(normalizer.normalize_list(rows))
    assert [o.id for o in orders] == ["10", "11", "14", "15", "16"]  # порядок файла сохранён
    node = orders[1]
    assert node.covers == ["12", "13"] and node.is_emergency and node.duration_min == 80
    assert node.location.address.startswith("МО, г. Кашира — авария на узле, 3 дома:")
    assert all(not o.covers for o in orders if o.id != "11")


def test_crews_are_built_from_the_control_day() -> None:
    from app.data.synth_engineers import crew_histories

    def row(crew: str, bk: str, district: str, start: str, end: str, hd: str = "") -> dict:
        return {"Бригада": crew, "Тип заявки BK": bk, "Тип заявки HD": hd, "Район": district,
                "Начало": f"17.08.2026 {start}", "Окончание": f"17.08.2026 {end}"}

    rows = [
        # весь день в Кашире, аварии на весь день о смене не говорят
        row("Бригада Кашира", "Подключение", "Кашира", "10:00", "12:00"),
        row("Бригада Кашира", "Глобальная проблема", "Кашира", "0:01", "23:59", "Авария"),
        row("Бригада Кашира", "Локальная заявка", "Кашира", "20:00", "22:00"),
        # один район, только до 18:00 — график 5/2 днём
        row("Бригада Дневная", "Подключение", "Зябликово", "10:00", "12:00"),
        row("Бригада Дневная", "Подключение", "Зябликово", "16:00", "18:00"),
        # с 12:00 до 22:00 — вечерняя смена 5/2
        row("Бригада Вечерняя", "Локальная заявка", "Царицыно", "12:00", "14:00"),
        row("Бригада Вечерняя", "Дозаказ", "Братеево", "20:00", "22:00"),
        # широкий участок — автомобиль; добирает аварийный навык до двух бригад
        row("Бригада Широкая", "Подключение", "Царицыно", "10:00", "12:00"),
        row("Бригада Широкая", "Локальная заявка", "Братеево", "14:00", "16:00"),
        row("Бригада Широкая", "Подключение", "Зябликово", "18:00", "20:00"),
        row("", "Подключение", "Зябликово", "18:00", "20:00"),  # без бригады — пропускаем
    ]
    office = {"Тип заявки BK": "г. Москва, ул Бирюлёвская, д 1с1"}
    crews = EngineerSynthesizer().synthesize_for_region("t", crew_histories(rows), office)
    by = {e.name: e for e in crews}
    assert set(by) == {"Бригада Кашира", "Бригада Дневная", "Бригада Вечерняя", "Бригада Широкая"}

    kashira = by["Бригада Кашира"]
    assert kashira.depot.district == "Кашира" and kashira.transport == Transport.CAR
    assert set(kashira.skills) == {Skill.CONNECTION, Skill.EMERGENCY, Skill.LOCAL}
    assert (kashira.shift.start, kashira.shift.end) == ("10:00", "22:00")

    day = by["Бригада Дневная"]
    assert (day.shift.start, day.shift.end) == ("10:00", "19:00")
    assert day.skills == [Skill.CONNECTION] and day.transport in (Transport.FOOT, Transport.BIKE)
    assert day.depot.address == office["Тип заявки BK"]

    evening = by["Бригада Вечерняя"]
    assert (evening.shift.start, evening.shift.end) == ("13:00", "22:00")
    assert evening.transport == Transport.TRANSIT

    wide = by["Бригада Широкая"]
    assert Skill.EMERGENCY in wide.skills and wide.transport == Transport.CAR
