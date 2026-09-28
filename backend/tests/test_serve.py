"""Запуск сервера: занятый порт не выбирается."""

import socket

from app.serve import HOST, pick_port, port_is_free


def test_busy_port_is_not_picked():
    with socket.socket() as busy:
        busy.bind((HOST, 0))
        busy.listen()
        port = busy.getsockname()[1]
        assert not port_is_free(port)
        assert pick_port(port) is None
