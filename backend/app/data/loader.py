import csv
from pathlib import Path
from typing import Any


class RawDatasetLoader:
    """Загрузчик сырых данных из CSV файлов организаторов (CP1251, разделитель ';')."""

    @staticmethod
    def load_csv(file_path: Path | str) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        """Читает CSV файл, фильтрует пустые строки, извлекает адрес офиса/депо и возвращает (заявки, офис)."""
        path = Path(file_path)
        if not path.exists():
            raise FileNotFoundError(f"Файл не найден: {path}")

        records: list[dict[str, Any]] = []
        office_record: dict[str, Any] | None = None

        with open(path, mode="r", encoding="cp1251", errors="replace") as f:
            reader = csv.DictReader(f, delimiter=";")
            for row in reader:
                # Очистка ключей и значений от лишних пробелов
                clean_row = {
                    (k.strip() if k else ""): (v.strip() if v else "")
                    for k, v in row.items()
                    if k is not None
                }

                order_id = clean_row.get("Заявка", "")
                bk_type = clean_row.get("Тип заявки BK", "")

                # Пропуск полностью пустых строк
                if not any(clean_row.values()):
                    continue

                # Выделение строки адреса офиса региона
                if "адрес офиса" in order_id.lower() or "адрес офиса" in bk_type.lower():
                    office_record = clean_row
                    continue

                # Проверка валидности записи заявки
                if order_id and order_id.isdigit():
                    records.append(clean_row)

        return records, office_record

