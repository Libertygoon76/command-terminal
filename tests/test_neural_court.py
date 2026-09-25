"""Expansion 2.0: the Neural Court (src/engine/neural_engine.py).

Ollama is never contacted for real: `requests.get` (the ping) and `requests.post` (the chat) are mocked in every test
that switches the feature on. conftest's `no_local_llm` keeps it off everywhere else.
"""

import asyncio
import json

import pytest
import requests

from src.engine import court, crisis_engine, neural_engine
from src.engine.data_loader import new_game
from src.engine.savegame import load_game, save_game


class FakeResponse:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")

    def json(self):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class FakeOllama:
    """Stands in for http://localhost:11434: records every request, answers with queued chat contents."""

    def __init__(self, models=("llama3.2:latest",), running=True):
        self.models = models
        self.running = running
        self.pings = []
        self.chats = []
        self.answers = []

    def get(self, url, timeout=None):
        self.pings.append((url, timeout))
        if not self.running:
            raise requests.ConnectionError("connection refused")
        return FakeResponse({"models": [{"name": m} for m in self.models]})

    def post(self, url, json=None, timeout=None):
        self.chats.append({"url": url, "json": json, "timeout": timeout})
        answer = self.answers.pop(0) if self.answers else '{"dialogue": "Noted.", "loyalty_change": 0}'
        if isinstance(answer, Exception):
            raise answer
        return FakeResponse({"message": {"role": "assistant", "content": answer}})


@pytest.fixture
def state():
    return new_game(seed=12)


@pytest.fixture
def ollama(monkeypatch):
    monkeypatch.delenv("CT_NEURAL", raising=False)
    monkeypatch.delenv("CT_OLLAMA_HOST", raising=False)
    neural_engine.reset_cache()
    fake = FakeOllama()
    monkeypatch.setattr(neural_engine.requests, "get", fake.get)
    monkeypatch.setattr(neural_engine.requests, "post", fake.post)
    return fake


def answer(dialogue, change):
    return json.dumps({"dialogue": dialogue, "loyalty_change": change})


def vargus(state):
    return state.player.dynasty.characters["char_03"]


# --- availability and the fallback --------------------------------------------------------------------------


def test_the_court_falls_back_to_its_script_when_ollama_is_not_running(state, ollama):
    ollama.running = False
    online, reason = neural_engine.check_available(neural_engine.settings(state))
    assert not online and "not running" in reason
    assert ollama.pings[0] == ("http://localhost:11434/api/tags", 0.5)  # a fast ping, never a hang
    loyalty = vargus(state).loyalty
    reply = neural_engine.converse_with_character(state, "char_03", "Where is the money, Uncle?")
    assert reply.source == "fallback" and reply.dialogue and reply.applied == 0
    assert vargus(state).loyalty == loyalty and not ollama.chats
    assert state.player.dynasty.conversations["char_03"][0]["source"] == "fallback"


def test_the_ping_is_cached(state, ollama):
    cfg = neural_engine.settings(state)
    assert neural_engine.check_available(cfg) == (True, "")
    neural_engine.check_available(cfg)
    neural_engine.check_available(cfg)
    assert len(ollama.pings) == 1
    neural_engine.check_available(cfg, force=True)
    assert len(ollama.pings) == 2


def test_a_missing_model_is_reported(state, ollama):
    ollama.models = ("mistral:latest",)
    online, reason = neural_engine.check_available(neural_engine.settings(state))
    assert not online and "ollama pull llama3.2" in reason


def test_switched_off_means_no_network_at_all(state, ollama, monkeypatch):
    monkeypatch.setenv("CT_NEURAL", "off")
    reply = neural_engine.converse_with_character(state, "char_03", "Good morning.")
    assert reply.source == "fallback" and "switched off" in reply.reason
    assert not ollama.pings and not ollama.chats


def test_only_a_local_ollama_is_ever_contacted(state, ollama, monkeypatch):
    monkeypatch.setenv("CT_OLLAMA_HOST", "http://example.com:11434")
    online, reason = neural_engine.check_available(neural_engine.settings(state))
    assert not online and "not on this machine" in reason and not ollama.pings
    monkeypatch.setenv("CT_OLLAMA_HOST", "http://127.0.0.1:11434")
    assert neural_engine.check_available(neural_engine.settings(state), force=True)[0]


def test_the_game_runs_without_the_requests_library(state, ollama, monkeypatch):
    monkeypatch.setattr(neural_engine, "requests", None)
    reply = neural_engine.converse_with_character(state, "char_03", "Hello.")
    assert reply.source == "fallback" and "requests" in reply.reason


# --- the context builder -----------------------------------------------------------------------------------


def test_the_system_prompt_knows_the_courtier_and_the_nation(state):
    crisis_engine.infect(state, "city:Aldmark", "trench_typhus", "OB-t")
    state.player.treasury = -10000
    prompt = neural_engine.build_system_prompt(state, vargus(state))
    for fact in ("You are Duke Vargus, Minister of Finance", "Corrupt", "Financial Genius",
                 "Your loyalty to the Lord Protector is 35/100", "-10,000 CR", "BANKRUPT", "Epidemic",
                 "Aldmark", "under 3 sentences", '"dialogue"', '"loyalty_change"', "-5 to 5", "1980s"):
        assert fact in prompt, fact


def test_the_prompt_knows_about_the_regency_and_a_plotter(state):
    h = state.player.dynasty
    h.characters["char_02"].age = 12
    court.die(state, h.ruler, "assassinated")
    duke = vargus(state)
    duke.loyalty = 10
    prompt = neural_engine.build_system_prompt(state, duke)
    assert "REGENT for the under-age Lord Protector Julian" in prompt
    assert "secretly plotting" in prompt and "A REGENCY rules" in prompt


# --- structured output and the game math ----------------------------------------------------------------


def test_the_model_speaks_and_moves_loyalty(state, ollama):
    ollama.answers.append(answer("The ledgers are in order, Lord Protector. Mostly.", 3))
    loyalty = vargus(state).loyalty
    reply = neural_engine.converse_with_character(state, "char_03", "You have served the Treasury well, Uncle.")
    assert reply.source == "neural" and reply.dialogue.startswith("The ledgers are in order")
    assert reply.loyalty_change == 3 and reply.applied == 3 and vargus(state).loyalty == loyalty + 3
    request = ollama.chats[0]
    assert request["url"] == "http://localhost:11434/api/chat" and request["timeout"] == 45
    body = request["json"]
    assert body["model"] == "llama3.2" and body["format"] == "json" and body["stream"] is False
    assert body["messages"][0]["role"] == "system" and "Duke Vargus" in body["messages"][0]["content"]
    assert body["messages"][-1] == {"role": "user", "content": "You have served the Treasury well, Uncle."}


def test_loyalty_changes_are_clamped_and_capped_each_week(state, ollama):
    ollama.answers += [answer("Magnificent!", 40), answer("Again!", 5), answer("Yes?", -3)]
    loyalty = vargus(state).loyalty
    first = neural_engine.converse_with_character(state, "char_03", "A dukedom for you.")
    assert first.loyalty_change == 5 and first.applied == 5  # 40 clamped to +5
    second = neural_engine.converse_with_character(state, "char_03", "And another.")
    assert second.applied == 0 and vargus(state).loyalty == loyalty + 5  # the weekly cap: no farming
    third = neural_engine.converse_with_character(state, "char_03", "Actually, no.")
    assert third.applied == -3 and vargus(state).loyalty == loyalty + 2
    state.clock.turn += 1
    ollama.answers.append(answer("A new week.", 4))
    assert neural_engine.converse_with_character(state, "char_03", "Hello again.").applied == 4


@pytest.mark.parametrize("raw", [
    '```json\n{"dialogue": "Fenced.", "loyalty_change": 2}\n```',
    'Certainly! {"dialogue": "Chatty.", "loyalty_change": "2"} Hope that helps.',
    '{"dialogue": "Float.", "loyalty_change": 1.6}',
])
def test_the_parser_tolerates_a_sloppy_model(raw):
    dialogue, change = neural_engine.parse_reply(raw, {"loyalty_change": {"min": -5, "max": 5}})
    assert dialogue in ("Fenced.", "Chatty.", "Float.") and change == 2


@pytest.mark.parametrize("raw", ["I refuse to answer in JSON.", '{"loyalty_change": 3}', '{"dialogue": ""}', "[1, 2]"])
def test_the_parser_rejects_garbage(raw):
    with pytest.raises(neural_engine.NeuralError):
        neural_engine.parse_reply(raw, {})


def test_a_garbled_answer_falls_back_but_keeps_the_court_online(state, ollama):
    ollama.answers += ["The Duke mumbles.", answer("Better.", 1)]
    first = neural_engine.converse_with_character(state, "char_03", "Speak up.")
    assert first.source == "fallback" and "JSON" in first.reason
    assert neural_engine.converse_with_character(state, "char_03", "Again.").source == "neural"


def test_a_slow_model_times_out_and_the_court_goes_offline(state, ollama):
    ollama.answers.append(requests.Timeout("read timed out"))
    reply = neural_engine.converse_with_character(state, "char_03", "Well?")
    assert reply.source == "fallback" and "too long" in reply.reason
    neural_engine.converse_with_character(state, "char_03", "Well??")
    assert len(ollama.chats) == 1  # marked offline: the next line does not wait again


def test_the_courtier_remembers_the_conversation(state, ollama):
    ollama.answers += [answer("I remember everything.", 0), answer("As I said.", 0)]
    neural_engine.converse_with_character(state, "char_03", "Do you recall the Iren accounts?")
    neural_engine.converse_with_character(state, "char_03", "And now?")
    messages = ollama.chats[1]["json"]["messages"]
    assert [m["role"] for m in messages] == ["system", "user", "assistant", "user"]
    assert messages[1]["content"] == "Do you recall the Iren accounts?"
    assert json.loads(messages[2]["content"])["dialogue"] == "I remember everything."


def test_player_input_is_cleaned_and_bounded(state, ollama):
    neural_engine.converse_with_character(state, "char_03", "  Hello\x00\x07 \n\n  Uncle " + "x" * 1000)
    text = ollama.chats[0]["json"]["messages"][-1]["content"]
    assert text.startswith("Hello Uncle ") and len(text) == 400 and "\x00" not in text
    with pytest.raises(neural_engine.ConverseError):
        neural_engine.converse_with_character(state, "char_03", "   ")


def test_who_can_be_spoken_to(state, ollama):
    h = state.player.dynasty
    with pytest.raises(neural_engine.ConverseError):
        neural_engine.converse_with_character(state, h.ruler_id, "Hello, me.")
    court.die(state, vargus(state), "died of the fever")
    with pytest.raises(neural_engine.ConverseError, match="dead"):
        neural_engine.converse_with_character(state, "char_03", "Hello?")
    with pytest.raises(neural_engine.ConverseError):
        neural_engine.converse_with_character(state, "nobody", "Hello?")


# --- the Hold Court hook --------------------------------------------------------------------------------------


def test_hold_court_lets_the_petitioner_speak(state, ollama):
    ollama.answers.append(answer("My Lord, the estates are rightfully mine.", 4))
    card = court.hold_court(state, narrate=True)
    shown = state.crisis_cards[card["id"]]
    name = state.player.dynasty.characters[card["audience"]].name.upper()
    assert shown["text"].startswith(f"{name} SPEAKS: “My Lord, the estates are rightfully mine.”")
    assert "PETITIONER:" in shown["text"]  # the scripted petition and its mechanics stay
    assert [c["id"] for c in shown["choices"]] == ["grant", "deny", "ignore"]
    system = ollama.chats[0]["json"]["messages"][0]["content"]
    assert "demanded an audience" in system


def test_hold_court_keeps_the_scripted_petition_when_offline(state, ollama):
    ollama.running = False
    card = court.hold_court(state, narrate=True)
    assert "SPEAKS:" not in state.crisis_cards[card["id"]]["text"] and not ollama.chats


# --- persistence and the screen --------------------------------------------------------------------------------


def test_conversations_survive_save_and_load(state, ollama, tmp_path):
    ollama.answers.append(answer("Saved for posterity.", 2))
    neural_engine.converse_with_character(state, "char_03", "Remember this.")
    loaded = load_game(save_game(state, tmp_path / "save.json"))
    log = loaded.player.dynasty.conversations["char_03"]
    assert log[0]["reply"] == "Saved for posterity." and log[0]["applied"] == 2
    assert loaded.player.dynasty == state.player.dynasty


def _talk_to_the_duke(typed):
    from src.ui.app import CommandTerminalApp
    from src.ui.screens.converse import ConverseScreen
    from src.ui.views.court import CourtView

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=1984)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("0")
            await pilot.pause()
            view = app.screen.query_one(CourtView)
            view.char_id = "char_03"
            await pilot.press("t")
            await pilot.pause()
            assert isinstance(app.screen, ConverseScreen)
            await pilot.press(*typed, "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            transcript = str(app.screen.query_one("#converse-transcript").render())
            await pilot.press("escape")
            await pilot.pause()
            assert not isinstance(app.screen, ConverseScreen)
            return app.game, transcript

    return asyncio.run(run())


def test_the_conversation_screen_uses_the_script_offline():
    game, transcript = _talk_to_the_duke("hello")
    log = game.player.dynasty.conversations["char_03"]
    assert log[0]["player"] == "hello" and log[0]["source"] == "fallback"
    assert "LORD PROTECTOR: hello" in transcript and "DUKE VARGUS:" in transcript


def test_the_conversation_screen_speaks_through_the_model(ollama):
    ollama.answers.append(answer("The Treasury thanks you.", 2))
    game, transcript = _talk_to_the_duke("thanks")
    assert "The Treasury thanks you." in transcript and "[LOYALTY +2]" in transcript
    assert game.player.dynasty.conversations["char_03"][0]["source"] == "neural"


def test_hold_court_on_screen_waits_for_the_petitioner_to_speak(ollama):
    from src.ui.app import CommandTerminalApp
    from src.ui.screens.dilemma import DilemmaScreen

    ollama.answers.append(answer("Hear me, Lord Protector.", 0))

    async def run():
        app = CommandTerminalApp(skip_boot=True, seed=1984)
        async with app.run_test(size=(200, 60)) as pilot:
            await pilot.press("0")
            await pilot.pause()
            await pilot.press("a")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert isinstance(app.screen, DilemmaScreen)
            card = app.game.crisis_cards[app.game.pending_dilemma]
            assert "SPEAKS: “Hear me, Lord Protector.”" in card["text"]

    asyncio.run(run())
