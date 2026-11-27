"""Offline by default: an accidental network call fails the local test suite."""

import socket
import sys

import pytest


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def denied(*args, **kwargs):
        raise AssertionError("Network is forbidden in the offline test suite")

    monkeypatch.setattr(socket, "getaddrinfo", denied)
    monkeypatch.setattr(socket.socket, "connect", denied)
    monkeypatch.setattr(socket.socket, "connect_ex", denied)


@pytest.hookimpl(wrapper=True, tryfirst=True)
def pytest_runtest_teardown(item, nextitem):
    """Deliver deleteLater events posted by finalizers in this test's Qt thread."""
    result = yield
    # CLI-only tests and environments without the desktop extra never import Qt.
    core = sys.modules.get("PySide6.QtCore")
    if core is not None and core.QCoreApplication.instance() is not None:
        core.QCoreApplication.sendPostedEvents(None, core.QEvent.Type.DeferredDelete)
    return result
