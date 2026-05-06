import time

import pytest

from feed.stream import CircuitBreaker


class TestCircuitBreaker:
    def test_initial_state(self):
        cb = CircuitBreaker()
        assert cb.is_open is False

    def test_opens_after_max_failures(self, monkeypatch):
        cb = CircuitBreaker()
        for _ in range(5):
            cb.next_delay()
        assert cb.is_open is True

    def test_exponential_backoff_increases(self):
        cb = CircuitBreaker()
        delays = [cb.next_delay() for _ in range(4)]
        assert delays[0] < delays[1] < delays[2] < delays[3]

    def test_success_resets_failures(self):
        cb = CircuitBreaker()
        for _ in range(3):
            cb.next_delay()
        cb.record_success()
        assert cb.is_open is False
        assert cb.next_delay() > 0  # First delay after reset

    def test_auto_reset_after_park(self, monkeypatch):
        cb = CircuitBreaker()
        # Force circuit open
        for _ in range(5):
            cb.next_delay()
        assert cb.is_open is True

        # Advance time past reset
        monkeypatch.setattr(time, "monotonic", lambda: cb._open_until + 1.0)
        assert cb.is_open is False
