import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

from rich import box
from rich.console import Console
from rich.panel import Panel
from rich.table import Table

from app.data import EngineerSynthesizer, OrderNormalizer, RawDatasetLoader
from app.domain.models import Engineer, Order
from app.solver import BaselineSolver, Solver, compare_plans
from app.solver.explain import ExplanationGenerator

console = Console()

REGIONS = {
    "east": {"name_ru": "Восток", "csv_pattern": "Восток"},
    "southeast": {"name_ru": "Юго-восток", "csv_pattern": "Юго-восток"},
    "southcenter": {"name_ru": "Югоцентр", "csv_pattern": "Югоцентр"},
}


def get_repo_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


def resolve_out_file(base_dir: Path, filename: str, user_override: str | None = None) -> Path:
    """Возвращает безопасный путь для записи результата.

    Записи разрешены только внутри корня проекта: пользовательский путь
    не может быть абсолютным или содержать выход за пределы репозитория ('..').
    """
    repo_root = get_repo_root()

    if user_override is not None:
        candidate = Path(user_override)
        if candidate.is_absolute():
            console.print(
                f"[bold red]Отклонён абсолютный путь вывода: {user_override}. "
                f"Разрешены только относительные пути внутри проекта.[/bold red]"
            )
            raise SystemExit(2)
        target = (Path.cwd() / candidate).resolve()
    else:
        if "/" in filename or "\\" in filename or ".." in filename:
            raise ValueError(f"Недопустимое имя файла: {filename}")
        target = (base_dir / filename).resolve()

    if target != repo_root and repo_root not in target.parents:
        console.print(
            f"[bold red]Отклонён путь вне проекта: {target}. "
            f"Разрешена запись только внутри {repo_root}.[/bold red]"
        )
        raise SystemExit(2)
    target.parent.mkdir(parents=True, exist_ok=True)
    return target


def get_dataset_dir() -> Path:
    # Датасеты всегда лежат в корне репозитория; путь anchoring-ится от __file__,
    # пользовательский ввод в путь не попадает
    root_datasets = get_repo_root() / "datasets"
    root_datasets.mkdir(parents=True, exist_ok=True)
    return root_datasets


def get_raw_docs_dir() -> Path:
    root_docs = get_repo_root() / "docs" / "Датасет"
    if not root_docs.exists():
        raise FileNotFoundError(
            "Директория с исходными датасетами docs/Датасет не найдена в корне репозитория."
        )
    return root_docs


def build_datasets() -> None:
    """Нормализует сырые CSV и сохраняет структурированные JSON в datasets/."""
    raw_dir = get_raw_docs_dir()
    out_dir = get_dataset_dir()
    console.print(
        f"[bold cyan]Сборка датасетов из [yellow]{raw_dir}[/yellow] в [yellow]{out_dir}[/yellow]...[/bold cyan]"
    )

    loader = RawDatasetLoader()
    normalizer = OrderNormalizer()
    synthesizer = EngineerSynthesizer(seed=42)

    table = Table(
        title="Собранные нормализованные датасеты",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    table.add_column("Регион", style="cyan")
    table.add_column("Заявок", justify="right")
    table.add_column("Бригад", justify="right")
    table.add_column("Файл заявок", style="dim")
    table.add_column("Файл инженеров", style="dim")

    for reg_key, reg_info in REGIONS.items():
        pattern = reg_info["csv_pattern"]
        syn_files = list(raw_dir.glob(f"{pattern} Синтетические*.csv"))
        ctrl_files = list(raw_dir.glob(f"{pattern} Контрольное*.csv"))

        if not syn_files or not ctrl_files:
            console.print(
                f"[bold red]Ошибка: Не найдены файлы для региона {reg_info['name_ru']}[/bold red]"
            )
            continue

        raw_records, office_rec = loader.load_csv(syn_files[0])
        orders = normalizer.normalize_list(raw_records)

        ctrl_records, _ = loader.load_csv(ctrl_files[0])
        brigade_names = list({r["Бригада"] for r in ctrl_records if r.get("Бригада")})
        engineers = synthesizer.synthesize_for_region(reg_key, brigade_names, office_rec)

        # Сохранение orders JSON
        orders_file = out_dir / f"{reg_key}.orders.json"
        with open(orders_file, "w", encoding="utf-8") as f:
            json.dump([o.model_dump() for o in orders], f, ensure_ascii=False, indent=2)

        # Сохранение engineers JSON
        engineers_file = out_dir / f"{reg_key}.engineers.json"
        with open(engineers_file, "w", encoding="utf-8") as f:
            json.dump([e.model_dump() for e in engineers], f, ensure_ascii=False, indent=2)

        table.add_row(
            f"{reg_info['name_ru']} ({reg_key})",
            str(len(orders)),
            str(len(engineers)),
            orders_file.name,
            engineers_file.name,
        )

    console.print(table)
    console.print("[bold green]✓ Все датасеты успешно собраны и сохранены.[/bold green]\n")


def load_normalized_dataset(region_key: str) -> tuple[list[Order], list[Engineer]]:
    """Загружает нормализованный JSON датасета из datasets/."""
    dataset_dir = get_dataset_dir()
    orders_file = dataset_dir / f"{region_key}.orders.json"
    engineers_file = dataset_dir / f"{region_key}.engineers.json"

    if not orders_file.exists() or not engineers_file.exists():
        console.print(
            f"[yellow]Файлы для региона '{region_key}' отсутствуют. Запуск сборки...[/yellow]"
        )
        build_datasets()

    with open(orders_file, "r", encoding="utf-8") as f:
        orders_data = json.load(f)
        orders = [Order.model_validate(item) for item in orders_data]

    with open(engineers_file, "r", encoding="utf-8") as f:
        engineers_data = json.load(f)
        engineers = [Engineer.model_validate(item) for item in engineers_data]

    return orders, engineers


def get_out_dir() -> Path:
    repo_root = Path(__file__).resolve().parent.parent.parent
    out = repo_root / "out"
    out.mkdir(parents=True, exist_ok=True)
    return out


def update_readme_benchmark_table(benchmark_data: dict[str, Any]) -> None:
    """Автоматически обновляет таблицу бенчмарка в README.md из результатов benchmark_data."""
    repo_root = Path(__file__).resolve().parent.parent.parent
    readme_path = repo_root / "README.md"
    if not readme_path.exists():
        return

    content = readme_path.read_text(encoding="utf-8")
    start_tag = "<!-- BENCHMARK_TABLE_START -->"
    end_tag = "<!-- BENCHMARK_TABLE_END -->"

    table_lines = [
        start_tag,
        (
            "| Участок | Заявок | Назначено (наш / базовый) | Бригад (наш / базовый) "
            "| Пробег, км (наш / базовый) |"
        ),
        "|---|:---:|:---:|:---:|:---:|",
    ]

    def km(value: float) -> str:
        return f"{value:.1f}".replace(".", ",")

    for reg_key, reg_info in REGIONS.items():
        diff = benchmark_data.get(reg_key)
        if not diff:
            continue
        opt = diff["optimized"]
        base = diff["baseline"]

        line = (
            f"| {reg_info['name_ru']} | {opt['total_orders']} | "
            f"**{opt['assigned_orders']}** / {base['assigned_orders']} | "
            f"**{opt['active_crews']}** / {base['active_crews']} | "
            f"**{km(opt['total_distance_km'])}** / {km(base['total_distance_km'])} |"
        )
        table_lines.append(line)

    table_lines.append(end_tag)
    new_table_str = "\n".join(table_lines)

    if start_tag in content and end_tag in content:
        import re

        pattern = re.compile(f"{re.escape(start_tag)}.*?{re.escape(end_tag)}", re.DOTALL)
        content = pattern.sub(new_table_str, content)
        readme_path.write_text(content, encoding="utf-8")
        console.print("[bold green]✓ Таблица в README.md успешно обновлена из бенчмарка![/bold green]")


def cmd_solve(args: argparse.Namespace) -> None:
    """Решение задачи планирования для одного региона."""
    reg = args.region
    if reg not in REGIONS:
        console.print(f"[bold red]Неизвестный регион: {reg}. Доступны: {list(REGIONS.keys())}[/bold red]")
        sys.exit(1)

    orders, engineers = load_normalized_dataset(reg)
    reg_ru = REGIONS[reg]["name_ru"]

    console.print(
        Panel.fit(
            f"[bold green]MCT Solver[/bold green] · Регион: [bold cyan]{reg_ru}[/bold cyan] · "
            f"Заявок: [yellow]{len(orders)}[/yellow] · Бригад: [yellow]{len(engineers)}[/yellow]",
            box=box.DOUBLE,
        )
    )

    solver = Solver(use_local_search=not args.no_local_search)
    plan = solver.solve(orders, engineers)

    # Вывод метрик плана
    m = plan.metrics
    metrics_table = Table(title="Метрики оптимизированного плана", box=box.ROUNDED)
    metrics_table.add_column("Параметр", style="bold")
    metrics_table.add_column("Значение", justify="right", style="cyan")

    metrics_table.add_row("Всего заявок в регионе", str(m.total_orders))
    metrics_table.add_row(
        "Назначено заявок", f"{m.assigned_orders} ({m.assignment_rate_pct}%)"
    )
    metrics_table.add_row("Не назначено заявок", str(m.unassigned_orders))
    metrics_table.add_row(
        "Задействовано бригад", f"{m.active_engineers_count} из {m.total_engineers_count}"
    )
    metrics_table.add_row("Суммарный пробег (км)", f"{m.total_distance_km:.2f}")
    metrics_table.add_row("Суммарное время в пути", f"{m.total_travel_time_min} мин")
    metrics_table.add_row("Суммарное время работ", f"{m.total_work_time_min} мин")
    if m.emergency_orders:
        metrics_table.add_row(
            "Аварии: начаты в пределах 2 ч",
            f"{m.emergency_within_sla} из {m.emergency_orders} "
            f"(среднее {m.emergency_avg_reaction_min} мин, макс. {m.emergency_max_reaction_min} мин)",
        )
    if m.extra_crews_needed:
        metrics_table.add_row("Нужно ещё бригад для неназначенных", f"+{m.extra_crews_needed}")
    console.print(metrics_table)

    # Вывод маршрутов по бригадам
    routes_table = Table(title="Распределение по бригадам", box=box.SIMPLE_HEAVY)
    routes_table.add_column("Бригада", style="cyan")
    routes_table.add_column("Смена")
    routes_table.add_column("Транспорт")
    routes_table.add_column("Заявок", justify="right")
    routes_table.add_column("Пробег (км)", justify="right")
    routes_table.add_column("В пути (мин)", justify="right")

    eng_map = {e.id: e for e in engineers}
    for r in sorted(plan.routes, key=lambda x: len(x.jobs), reverse=True):
        eng = eng_map[r.engineer_id]
        status_style = "green" if r.is_active else "dim"
        routes_table.add_row(
            f"[{status_style}]{eng.name}[/{status_style}]",
            f"{eng.shift.start}-{eng.shift.end}",
            eng.transport.label_ru,
            str(len(r.jobs)),
            f"{r.total_distance_km:.1f}",
            str(r.total_travel_time_min),
        )
    console.print(routes_table)

    # Пример объяснения для назначенной заявки
    explanations = ExplanationGenerator.enrich_plan_explanations(
        plan, orders, engineers, solver.distance_provider
    )
    first_job = next((j for r in plan.routes for j in r.jobs), None)
    if first_job is not None:
        console.print(
            Panel(
                explanations[first_job.order_id],
                title="[bold green]Пример объяснения назначения (ТЗ §2.1.7)[/bold green]",
                box=box.ROUNDED,
            )
        )

    # Неназначенные заявки с причинами
    if plan.unassigned_orders:
        unassigned_table = Table(
            title=f"Неназначенные заявки ({len(plan.unassigned_orders)}) с причинами",
            box=box.ROUNDED,
            style="yellow",
        )
        unassigned_table.add_column("ID Заявки", style="bold")
        unassigned_table.add_column("Причина неназначения")
        for ord_id, reason in list(plan.unassigned_orders.items())[:5]:
            unassigned_table.add_row(ord_id, reason)
        if len(plan.unassigned_orders) > 5:
            unassigned_table.add_row("...", f"и еще {len(plan.unassigned_orders) - 5} заявок")
        console.print(unassigned_table)

    # Сохранение в файл
    if args.output:
        out_path = Path(args.output)
    else:
        out_path = get_out_dir() / f"plan_{reg}.json"

    with open(out_path, "w", encoding="utf-8") as f:
        json.dump(plan.model_dump(), f, ensure_ascii=False, indent=2)
    console.print(f"[bold green]✓ План сохранен в файл: [cyan]{out_path}[/cyan][/bold green]\n")


def cmd_benchmark(args: argparse.Namespace) -> None:
    """Запуск бенчмарка: Наш оптимизированный солвер vs Базовый вариант FIFO (ТЗ §2.3) по всем регионам."""
    console.print(
        Panel.fit(
            "[bold white]БЕНЧМАРК ЭФФЕКТИВНОСТИ ПЛАНИРОВАНИЯ (ТЗ §2.3, §4.7)[/bold white]\n"
            "[cyan]Сравнение: MCT Regret-2 + Local Search против Базового варианта (FIFO)[/cyan]",
            border_style="yellow",
        )
    )

    summary_table = Table(
        title="Сводные результаты бенчмарка по трем регионам",
        box=box.ROUNDED,
        header_style="bold magenta",
    )
    summary_table.add_column("Регион", style="cyan")
    summary_table.add_column("Заявок всего", justify="right")
    summary_table.add_column("Назначено (Наш)", justify="right", style="green")
    summary_table.add_column("Назначено (База)", justify="right")
    summary_table.add_column("Бригад (Наш)", justify="right", style="green")
    summary_table.add_column("Бригад (База)", justify="right")
    summary_table.add_column("Δ Бригад", justify="right", style="bold yellow")
    summary_table.add_column("Км/заявку (Наш)", justify="right", style="green")
    summary_table.add_column("Км/заявку (База)", justify="right")
    summary_table.add_column("Пробег Наш (км)", justify="right")
    summary_table.add_column("Пробег База (км)", justify="right")
    summary_table.add_column("Аварии ≤2 ч (Наш)", justify="right")
    summary_table.add_column("Время, с", justify="right")

    benchmark_data: dict[str, Any] = {}

    for reg_key, reg_info in REGIONS.items():
        orders, engineers = load_normalized_dataset(reg_key)

        solver = Solver(use_local_search=True)
        baseline_solver = BaselineSolver(solver.distance_provider)

        started = time.perf_counter()
        opt_plan = solver.solve(orders, engineers)
        elapsed = time.perf_counter() - started
        base_plan = baseline_solver.solve(orders, engineers)

        diff = compare_plans(opt_plan, base_plan)

        crew_diff = diff["delta"]["crew_count"]
        crew_delta_str = (
            f"[bold green]{crew_diff}[/bold green]" if crew_diff < 0 else "0"
        )

        opt_kpo = diff["optimized"]["km_per_order"]
        base_kpo = diff["baseline"]["km_per_order"]
        kpo_str = (
            f"[bold green]{opt_kpo:.1f}[/bold green]"
            if opt_kpo <= base_kpo
            else f"{opt_kpo:.1f}"
        )

        summary_table.add_row(
            reg_info["name_ru"],
            str(opt_plan.metrics.total_orders),
            f"{opt_plan.metrics.assigned_orders} ({opt_plan.metrics.assignment_rate_pct}%)",
            f"{base_plan.metrics.assigned_orders} ({base_plan.metrics.assignment_rate_pct}%)",
            str(opt_plan.metrics.active_engineers_count),
            str(base_plan.metrics.active_engineers_count),
            crew_delta_str,
            kpo_str,
            f"{base_kpo:.1f}",
            f"{opt_plan.metrics.total_distance_km:.1f}",
            f"{base_plan.metrics.total_distance_km:.1f}",
            f"{opt_plan.metrics.emergency_within_sla}/{opt_plan.metrics.emergency_orders}",
            f"{elapsed:.1f}",
        )

        benchmark_data[reg_key] = diff

    console.print(summary_table)

    report_path = get_out_dir() / "benchmark_report.json"
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(benchmark_data, f, ensure_ascii=False, indent=2)

    console.print(
        f"[bold green]✓ Отчет бенчмарка успешно сохранен в: [cyan]{report_path}[/cyan][/bold green]\n"
    )

    if getattr(args, "update_readme", False):
        update_readme_benchmark_table(benchmark_data)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="MCT: Сервис планирования рабочих маршрутов инженеров"
    )
    subparsers = parser.add_subparsers(dest="command", help="Команда для выполнения")

    # build-datasets
    subparsers.add_parser("build-datasets", help="Собрать нормализованные JSON датасеты из CSV")

    # solve
    solve_parser = subparsers.add_parser("solve", help="Решить задачу для выбранного региона")
    solve_parser.add_argument(
        "--region",
        "-r",
        default="east",
        choices=["east", "southeast", "southcenter"],
        help="Регион (east, southeast, southcenter)",
    )
    solve_parser.add_argument(
        "--output",
        "-o",
        type=str,
        default=None,
        help="Путь к файлу для сохранения плана (JSON)",
    )
    solve_parser.add_argument(
        "--no-local-search",
        action="store_true",
        help="Отключить фазу локального поиска (только regret-2)",
    )

    # benchmark
    benchmark_parser = subparsers.add_parser("benchmark", help="Запустить сравнительный бенчмарк (наш план vs базовый)")
    benchmark_parser.add_argument(
        "--update-readme",
        action="store_true",
        help="Автоматически обновить таблицу в README.md",
    )

    args = parser.parse_args()

    if args.command == "build-datasets":
        build_datasets()
    elif args.command == "solve":
        cmd_solve(args)
    elif args.command == "benchmark":
        cmd_benchmark(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
