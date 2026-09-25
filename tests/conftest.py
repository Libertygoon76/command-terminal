"""Shared test setup.

Phase 7 added two sources of friction that the older, focused tests were not written for: the weekly
weather (winter frostbite, mud), the CLASSIFIED DILEMMA event deck (which pauses the game until
answered) and, since Phase 8, random epidemics and natural disasters. Unless a test is marked `live`, the weather is held clear and no event cards are drawn, so
those tests keep measuring exactly what they were written to measure. Tests marked `live` (the fuzzed
campaigns and the Phase 7 tests) run with the world exactly as the player gets it.
"""

import pytest

from src.engine import court, crisis_engine, dilemmas, neural_engine, weather_engine


def pytest_configure(config):
    config.addinivalue_line("markers", "live: run with weather and the event deck active (no calm-world patch)")


def _clear_weather(state):
    return {"condition": "clear", "season": weather_engine.season_of(state), "storm": False,
            "turn": state.clock.turn, "previous_season": state.weather.get("season")}


@pytest.fixture(autouse=True)
def no_local_llm(monkeypatch):
    """No test ever talks to a real Ollama server (Expansion 2.0): the Neural Court is off unless a test mocks it."""
    monkeypatch.setenv("CT_NEURAL", "off")
    neural_engine.reset_cache()


@pytest.fixture(autouse=True)
def calm_world(request, monkeypatch):
    if request.node.get_closest_marker("live"):
        return
    monkeypatch.setattr(dilemmas.DilemmaSystem, "on_tick", lambda self, state, report: None)
    monkeypatch.setattr(weather_engine, "roll_weather", _clear_weather)
    # No random epidemics or disasters either (relief duty still counts down).
    monkeypatch.setattr(crisis_engine.CrisisSystem, "on_tick",
                        lambda self, state, report: crisis_engine.run_relief(state, report))
    # ... and no court intrigue (Expansion 1.2): no audiences, deaths, plots or loyalty drift.
    monkeypatch.setattr(court.CourtSystem, "on_tick", lambda self, state, report: None)


def settle_dilemma(state, rng=None):
    """Answer every pending dilemma or emergency (first option, or a random one) so a live campaign can go on."""
    while state.pending_dilemma:
        choices = dilemmas.card(state, state.pending_dilemma)["choices"]
        dilemmas.resolve(state, (rng.choice(choices) if rng else choices[0])["id"])
