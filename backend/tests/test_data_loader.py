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

