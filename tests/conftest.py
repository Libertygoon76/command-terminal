"""Shared test setup.

Phase 7 added two sources of friction that the older, focused tests were not written for: the weekly
weather (winter frostbite, mud) and the CLASSIFIED DILEMMA event deck (which pauses the game until
answered). Unless a test is marked `live`, the weather is held clear and no event cards are drawn, so
those tests keep measuring exactly what they were written to measure. Tests marked `live` (the fuzzed
campaigns and the Phase 7 tests) run with the world exactly as the player gets it.
"""

import pytest

from src.engine import dilemmas, weather_engine


def pytest_configure(config):
    config.addinivalue_line("markers", "live: run with weather and the event deck active (no calm-world patch)")


def _clear_weather(state):
    return {"condition": "clear", "season": weather_engine.season_of(state), "storm": False,
            "turn": state.clock.turn, "previous_season": state.weather.get("season")}


@pytest.fixture(autouse=True)
def calm_world(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setattr(dilemmas.DilemmaSystem, "on_tick", lambda self, state, report: None)
    monkeypatch.setattr(weather_engine, "roll_weather", _clear_weather)


def settle_dilemma(state, rng=None):
    """Answer a pending dilemma (first option, or a random one) so a live campaign can go on."""
    if state.pending_dilemma:
        choices = dilemmas.card(state, state.pending_dilemma)["choices"]
        dilemmas.resolve(state, (rng.choice(choices) if rng else choices[0])["id"])
