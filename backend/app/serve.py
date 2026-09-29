"""Запуск сервера для демонстрации: свободный порт, адрес в консоли, открытие браузера.

python -m app.serve              # порт 8000, если занят — следующий свободный
python -m app.serve --port 8080  # только этот порт
python -m app.serve --no-browser # не открывать браузер (или переменная окружения NO_BROWSER=1)
"""

import argparse
import os
import socket
import sys
import threading
import time
import webbrowser

import uvicorn

HOST = "127.0.0.1"
DEFAULT_PORT = 8000


def port_is_free(port: int) -> bool:
    """Порт свободен, если на нём никто не слушает и его можно занять."""
    with socket.socket() as probe:
        probe.settimeout(0.3)
        if probe.connect_ex((HOST, port)) == 0:
            return False
    with socket.socket() as sock:
        try:
            sock.bind((HOST, port))
        except OSError:
            return False
    return True


def pick_port(requested: int | None) -> int | None:
    if requested is not None:
        return requested if port_is_free(requested) else None
    return next((p for p in range(DEFAULT_PORT, DEFAULT_PORT + 20) if port_is_free(p)), None)


def can_open_browser() -> bool:
    if os.environ.get("NO_BROWSER"):  # проверки без графической сессии
        return False
    # В Linux без графической сессии webbrowser запустил бы текстовый браузер в этой же консоли
    if sys.platform.startswith("linux"):
        return bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))
    return True


def open_when_ready(url: str, port: int, timeout_s: float = 60) -> None:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with socket.create_connection((HOST, port), timeout=0.5):
                webbrowser.open(url)
                return
        except OSError:
            time.sleep(0.3)


def main() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")

    parser = argparse.ArgumentParser(description="Запуск помощника диспетчера")
    parser.add_argument(
        "--port", type=int, default=None, help="порт (по умолчанию 8000 или следующий свободный)"
    )
    parser.add_argument("--no-browser", action="store_true", help="не открывать браузер")
    args = parser.parse_args()

    port = pick_port(args.port)
    if port is None:
        busy = args.port if args.port is not None else f"{DEFAULT_PORT}–{DEFAULT_PORT + 19}"
        print(
            f"Порт {busy} занят. Укажите другой, например: .\\run.bat 8088 или ./run.sh 8088",
            file=sys.stderr,
        )
        sys.exit(1)

    url = f"http://localhost:{port}"
    if args.port is None and port != DEFAULT_PORT:
        print(f"Порт {DEFAULT_PORT} занят, выбран {port}.")
    print(f"\n  Помощник диспетчера: {url}\n  Остановить — Ctrl+C\n", flush=True)

    if not args.no_browser and can_open_browser():
        threading.Thread(target=open_when_ready, args=(url, port), daemon=True).start()
    uvicorn.run("app.api.main:app", host=HOST, port=port, access_log=False)


if __name__ == "__main__":
    main()
