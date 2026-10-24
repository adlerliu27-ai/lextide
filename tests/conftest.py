"""Offline by default: an accidental network call fails the local test suite."""

import socket

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in the offline test suite")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)
