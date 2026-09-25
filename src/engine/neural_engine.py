"""Expansion 2.0: The Neural Court.

Courtiers speak through a LOCAL language model served by Ollama (http://localhost:11434) on the player's own machine:
no cloud service, no API key. data/neural.json holds the settings.

AVAILABILITY. `check_available()` pings Ollama (`GET /api/tags`) with a sub-second timeout and checks that the
configured model is installed; the answer is cached for a few seconds. If Ollama is not running, the model is
missing, `requests` is not installed, CT_NEURAL=off is set, or the host is not a loopback address, the feature is
simply off: the court keeps its scripted text (court.json petitions, the event deck in dilemmas.py) and conversations
get a scripted reply. Nothing ever raises into the UI and nothing waits longer than `chat_timeout`.

THE MEMORY. `build_system_prompt()` compiles the courtier (relation, office, traits, loyalty, influence, where they
are) and the state of the nation (treasury, morale, stability, regency, epidemics, battles, blockades, weather) into a
strict system prompt. The last few exchanges with that courtier are replayed as chat history (and saved with the
game in `Dynasty.conversations`).

GAME MECHANICS. The model must answer with JSON: {"dialogue": "...", "loyalty_change": -5..+5}. The engine parses it
(tolerating code fences and chatter around the object), clamps the change, caps what any one courtier can gain or
lose by conversation in a week (`weekly_loyalty_cap`), and applies it to the courtier's loyalty.

THE HOOKS. `converse_with_character(state, character_id, text)` is the free-text conversation (Royal Court: T).
`narrate_audience(state, card)` gives a petitioner's audience card (Hold Court) an opening speech in their own words.
For a UI that must not freeze, each is split in three: `prepare_*` (reads the state), `try_fetch()` (the HTTP call,
no state, never raises: safe in a worker thread) and `complete_*` (applies the result to the state).
"""

from __future__ import annotations

import json
import os
import random
import re
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

try:  # the Neural Court is optional: without `requests` the game runs on scripted text
    import requests
except ImportError:  # pragma: no cover - exercised only on machines without requests
    requests = None

from src.engine.data_loader import load_json
from src.models import GameState
from src.models.character import RULER, Character

LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
NEURAL = "neural"
FALLBACK = "fallback"

# host -> (checked at, online, reason). Module-level: availability is a fact about the machine, not the campaign.
_PING_CACHE: dict[str, tuple[float, bool, str]] = {}


class NeuralError(RuntimeError):
    """The model answered, but not with something the game can use."""


class ConverseError(ValueError):
    """An invalid conversation (the dead, the ruler talking to himself, an empty line)."""


@dataclass
class Reply:
    """The outcome of one exchange with a courtier."""

    character_id: str
    player_text: str
    dialogue: str
    loyalty_change: int = 0  # the model's verdict, clamped to [-5, +5]
    applied: int = 0  # what actually reached the courtier's loyalty (after the weekly cap)
    source: str = FALLBACK  # "neural" | "fallback"
    reason: str = ""  # why the scripted fallback was used


@dataclass
class Exchange:
    """A prepared request: everything fetch() needs, and nothing that touches the game state."""

    character_id: str
    player_text: str
    messages: list[dict[str, str]]
    settings: dict[str, Any]
    kind: str = "conversation"  # "conversation" | "audience"
    card_id: str = ""
    extra: dict[str, Any] = field(default_factory=dict)


# --- settings and availability ------------------------------------------------------------------------


def settings(state: GameState | None = None) -> dict[str, Any]:
    if state is not None and state.catalog.get("neural"):
        return state.catalog["neural"]
    return load_json("neural.json")


def ollama_host(cfg: dict[str, Any]) -> str:
    return (os.environ.get("CT_OLLAMA_HOST") or cfg.get("host") or "http://localhost:11434").rstrip("/")


def _host_problem(host: str) -> str:
    parsed = urlparse(host)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return f"the Ollama host {host!r} is not an http(s) URL"
    if parsed.hostname not in LOOPBACK_HOSTS:
        return f"the Ollama host {parsed.hostname!r} is not on this machine (only localhost is allowed)"
    return ""


def _model_installed(model: str, tags: dict[str, Any]) -> bool:
    names = {m.get("name", "") for m in tags.get("models", [])} | {m.get("model", "") for m in tags.get("models", [])}
    return any(n == model or n.split(":")[0] == model or n == f"{model}:latest" for n in names if n)


def check_available(cfg: dict[str, Any], force: bool = False) -> tuple[bool, str]:
    """(online, reason). Fast: one GET with a sub-second timeout, cached for `ping_cache_seconds`."""
    if requests is None:
        return False, "the Python 'requests' library is not installed"
    if os.environ.get("CT_NEURAL", "").lower() in ("0", "off", "false", "no") or not cfg.get("enabled", True):
        return False, "the Neural Court is switched off"
    host = ollama_host(cfg)
    problem = _host_problem(host)
    if problem:
        return False, problem
    now = time.monotonic()
    cached = _PING_CACHE.get(host)
    if cached and not force and now - cached[0] < float(cfg.get("ping_cache_seconds", 30)):
        return cached[1], cached[2]
    model = cfg.get("model", "llama3.2")
    try:
        response = requests.get(f"{host}/api/tags", timeout=float(cfg.get("ping_timeout", 0.5)))
        response.raise_for_status()
        tags = response.json()
    except (requests.RequestException, ValueError):
        result = (False, f"Ollama is not running at {host} (install it from ollama.com, then run: ollama serve)")
    else:
        result = (True, "") if _model_installed(model, tags) else \
            (False, f"the model {model!r} is not installed (run: ollama pull {model})")
    _PING_CACHE[host] = (now, *result)
    return result


def mark_offline(cfg: dict[str, Any], reason: str) -> None:
    """A chat call failed: treat Ollama as offline until the cache expires, so the next line does not wait again."""
    _PING_CACHE[ollama_host(cfg)] = (time.monotonic(), False, reason)


def reset_cache() -> None:
    _PING_CACHE.clear()


def is_available(state: GameState | None = None, force: bool = False) -> bool:
    return check_available(settings(state), force)[0]


def cached_status(state: GameState | None = None) -> dict[str, Any]:
    """Like status(), but never touches the network: online is None until a ping has been made (for redraws)."""
    cfg = settings(state)
    host = ollama_host(cfg)
    off = requests is None or os.environ.get("CT_NEURAL", "").lower() in ("0", "off", "false", "no") \
        or not cfg.get("enabled", True) or _host_problem(host)
    cached = _PING_CACHE.get(host)
    if off:
        online, reason = check_available(cfg)  # no network: an early exit
    else:
        online, reason = (cached[1], cached[2]) if cached else (None, "not checked yet")
    return {"online": online, "reason": reason, "model": cfg.get("model", ""), "host": host}


def status(state: GameState | None = None) -> dict[str, Any]:
    cfg = settings(state)
    online, reason = check_available(cfg)
    return {"online": online, "reason": reason, "model": cfg.get("model", ""), "host": ollama_host(cfg)}


# --- the context builder (the memory) ---------------------------------------------------------------------


def _court():
    from src.engine import court

    return court


def _where(state: GameState, char: Character) -> str:
    if char.imprisoned:
        return "You are a prisoner in the Aldmark citadel."
    if char.married_to:
        return f"You live abroad in {char.married_to.title()} since your political marriage."
    if char.unit_id and state.unit(char.unit_id):
        unit = state.unit(char.unit_id)
        return f"You command the {unit.name} ({unit.designation}) at the front; you speak over the field telephone."
    if char.ill_weeks:
        return "You are gravely ill with the fever."
    return "You are at court in the palace at Aldmark."


def character_facts(state: GameState, char: Character) -> list[str]:
    court = _court()
    house = state.player.dynasty
    ruler = house.ruler
    threshold = float(court.cfg(state).get("treason", {}).get("threshold", 20))
    post = court.office_name(state, char.office) if char.office else char.role
    facts = [f"You are {char.name}, {post} of the Commonwealth of Kestria ({char.relation} of the ruling "
             f"{house.name}), aged {char.age}."]
    if house.regent_id == char.id:
        facts.append(f"You govern the Commonwealth as REGENT for the under-age Lord Protector {ruler.name}.")
    for trait in char.traits:
        info = court.trait_info(state, trait)
        facts.append(f"Trait — {info['name']}: {info.get('description', '')}.".replace("..", "."))
    facts.append(f"Administration {char.administration}/10, Military {char.military}/10, Intrigue "
                 f"{char.intrigue}/10. Influence {char.influence:.0f}/100.")
    if char.loyalty < threshold:
        mood = "You despise the Lord Protector and are secretly plotting against the House. Never admit it openly."
    elif char.loyalty < 40:
        mood = "You resent the Lord Protector and trust them little."
    elif char.loyalty < 70:
        mood = "You serve the Lord Protector dutifully, but your loyalty has limits."
    else:
        mood = "You are devoted to the Lord Protector."
    facts.append(f"Your loyalty to the Lord Protector is {char.loyalty:.0f}/100. {mood}")
    facts.append(_where(state, char))
    facts.append(f"The Lord Protector is {ruler.name}, aged {ruler.age} ({court.trait_names(state, ruler)}).")
    return facts


def nation_facts(state: GameState) -> list[str]:
    court = _court()
    nation = state.player
    house = nation.dynasty
    cur = state.currency
    facts = [f"It is week {state.clock.turn} of the war ({state.clock.current_date:%d %B %Y}). Kestria is at war "
             f"with the Vosk Hegemony."]
    ledger = state.last_ledger
    net = f", {ledger.net:+,} {cur} a week" if ledger else ""
    facts.append(f"The treasury holds {nation.treasury:,} {cur}{net}"
                 + (" — THE STATE IS BANKRUPT." if nation.treasury <= 0 else ".")
                 + f" Taxes are {nation.tax_policy.upper()} ({nation.tax_rate:.0%}).")
    facts.append(f"Civil morale {nation.morale:.0f}/100, military morale {nation.military_morale:.0f}/100, "
                 f"dynastic stability {house.stability:.0f}/100.")
    if court.regency_active(state):
        regent = house.regent
        facts.append(f"A REGENCY rules: {regent.name if regent else 'nobody'} governs for the child ruler; every "
                     "Treasury cost is 10% higher.")
    weather = state.weather.get("condition")
    if weather:
        facts.append(f"The weather is {str(weather).replace('_', ' ')}, season {state.weather.get('season', '')}.")
    sites = sorted(k.split(":", 1)[1] for k in state.infections if k.startswith("city:"))
    if sites:
        diseases = sorted({str(v.get("disease", "an epidemic")).replace("_", " ") for v in state.infections.values()})
        facts.append(f"Epidemic ({', '.join(diseases)}) in {', '.join(sites[:6])}"
                     + (f" and {len(sites) - 6} more towns." if len(sites) > 6 else "."))
    active = [b for b in state.battles.values() if b.ended_turn is None]
    recent = sorted((b for b in state.battles.values() if b.ended_turn is not None
                     and state.clock.turn - b.ended_turn <= 4), key=lambda b: -b.ended_turn)
    for battle in active[:3]:
        facts.append(f"The {battle.name} is raging (week {battle.weeks}); Kestria has lost "
                     f"{battle.casualties.get(nation.id, 0):,} men there.")
    for battle in recent[:2]:
        outcome = ("a Kestrian victory" if battle.victor == nation.id else
                   "inconclusive" if battle.victor is None else "a Kestrian defeat")
        facts.append(f"The {battle.name} ended in week {battle.ended_turn}: {outcome}, "
                     f"{battle.casualties.get(nation.id, 0):,} Kestrian dead and wounded.")
    ours = sorted(port for port, holder in state.blockades.items() if holder != nation.id)
    if ours:
        facts.append(f"The Vosk navy blockades {', '.join(ours)}.")
    if state.ceasefire_weeks:
        facts.append(f"A ceasefire with the Vosk holds for {state.ceasefire_weeks} more weeks.")
    return facts


def build_system_prompt(state: GameState, char: Character, situation: str = "") -> str:
    """The strict system prompt: who the courtier is, what they know, how they must answer."""
    cfg = settings(state)
    lo, hi = _bounds(cfg)
    lines = ["ROLE:"] + character_facts(state, char) + ["", "THE STATE OF THE NATION (what you know):"]
    lines += nation_facts(state)
    lines += ["", "RULES:",
              "This is a fictional 1984 world. Stay in character; never mention being an AI, a model or a game.",
              cfg.get("tone", "Speak in a gritty, clipped 1980s Cold-War bureaucratic tone."),
              situation or "The Lord Protector (the player) is speaking to you.",
              "Keep your spoken reply under 3 sentences. Do not invent battles, numbers or events beyond what you know.",
              "Judge how the Lord Protector's words land with someone of your traits and loyalty: respect, reward and "
              "honest promises please you; threats, insults, broken promises and demands against your interests "
              "anger you.",
              "OUTPUT FORMAT: answer ONLY with one JSON object, no other text:",
              '{"dialogue": "<what you say aloud>", "loyalty_change": <integer from '
              f'{lo} to {hi}>}}',
              f"loyalty_change is how this exchange moves your loyalty: {lo} furious, 0 unmoved, {hi} deeply won over."]
    return "\n".join(lines)


def _bounds(cfg: dict[str, Any]) -> tuple[int, int]:
    spec = cfg.get("loyalty_change", {})
    return int(spec.get("min", -5)), int(spec.get("max", 5))


def build_messages(state: GameState, char: Character, player_text: str, situation: str = "") -> list[dict[str, str]]:
    cfg = settings(state)
    messages = [{"role": "system", "content": build_system_prompt(state, char, situation)}]
    history = _conversations(state).get(char.id, [])[-int(cfg.get("memory_exchanges", 6)):]
    for entry in history:
        messages.append({"role": "user", "content": entry["player"]})
        messages.append({"role": "assistant", "content": json.dumps(
            {"dialogue": entry["reply"], "loyalty_change": entry.get("loyalty", 0)})})
    messages.append({"role": "user", "content": player_text})
    return messages


# --- the model's answer --------------------------------------------------------------------------------


_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")


def clean_player_text(text: str, cfg: dict[str, Any]) -> str:
    text = re.sub(r"\s+", " ", _CONTROL.sub(" ", text or "")).strip()
    if not text:
        raise ConverseError("Say something first.")
    return text[: int(cfg.get("max_player_chars", 400))]


def parse_reply(raw: str, cfg: dict[str, Any]) -> tuple[str, int]:
    """(dialogue, loyalty_change) from the model's JSON. Tolerates code fences and chatter around the object."""
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    data = None
    try:
        data = json.loads(text)
    except ValueError:
        match = re.search(r"\{.*\}", text, re.DOTALL)
        if match:
            try:
                data = json.loads(match.group(0))
            except ValueError:
                data = None
    if not isinstance(data, dict):
        raise NeuralError("the model did not answer with a JSON object")
    dialogue = data.get("dialogue")
    if not isinstance(dialogue, str) or not dialogue.strip():
        raise NeuralError("the model's answer has no dialogue")
    dialogue = _CONTROL.sub(" ", dialogue).strip()[: int(cfg.get("max_dialogue_chars", 600))]
    try:
        change = int(round(float(data.get("loyalty_change", 0))))
    except (TypeError, ValueError):
        change = 0
    lo, hi = _bounds(cfg)
    return dialogue, max(lo, min(hi, change))


def fetch(exchange: Exchange) -> tuple[str, int]:
    """The HTTP call to Ollama. Touches no game state, so it may run in a worker thread. Raises on any failure."""
    if requests is None:
        raise NeuralError("the Python 'requests' library is not installed")
    cfg = exchange.settings
    payload = {"model": cfg.get("model", "llama3.2"), "messages": exchange.messages, "stream": False,
               "format": "json", "options": {"temperature": float(cfg.get("temperature", 0.8))}}
    response = requests.post(f"{ollama_host(cfg)}/api/chat", json=payload, timeout=float(cfg.get("chat_timeout", 45)))
    response.raise_for_status()
    try:
        content = response.json()["message"]["content"]
    except (ValueError, KeyError, TypeError) as error:
        raise NeuralError("Ollama's answer was not a chat message") from error
    return parse_reply(content, cfg)


def _failure_reason(error: Exception) -> str:
    if requests is not None and isinstance(error, requests.Timeout):
        return "the model took too long to answer"
    if requests is not None and isinstance(error, requests.RequestException):
        return "Ollama stopped answering"
    return str(error) or error.__class__.__name__


def try_fetch(exchange: Exchange) -> tuple[tuple[str, int] | None, str]:
    """(result, reason): the model's answer, or None and why not. Never raises; safe in a worker thread.

    A dead or slow server is marked offline (the next line does not wait for it again); a garbled answer is not."""
    online, reason = check_available(exchange.settings)
    if not online:
        return None, reason
    try:
        return fetch(exchange), ""
    except Exception as error:  # noqa: BLE001 - whatever goes wrong, the court falls back to its script
        reason = _failure_reason(error)
        if not isinstance(error, NeuralError):
            mark_offline(exchange.settings, reason)
        return None, reason


# --- conversation -------------------------------------------------------------------------------------


def _conversations(state: GameState) -> dict[str, list[dict[str, Any]]]:
    house = state.player.dynasty
    return house.conversations if house is not None else {}


def speaker(state: GameState, character_id: str) -> Character:
    house = state.player.dynasty
    if state.game_over:
        raise ConverseError("The government has fallen. The terminal is locked.")
    if house is None or character_id not in house.characters:
        raise ConverseError(f"No such courtier: {character_id}")
    char = house.characters[character_id]
    if not char.alive:
        raise ConverseError(f"{char.name} is dead.")
    if char.relation == RULER:
        raise ConverseError("Choose a courtier: the Lord Protector cannot converse with the Lord Protector.")
    if char.married_to:
        raise ConverseError(f"{char.name} lives abroad since the marriage.")
    return char


def fallback_line(state: GameState, char: Character) -> str:
    """A scripted reply by loyalty band (neural.json → fallback), chosen without touching the campaign RNG."""
    cfg = settings(state)
    band = "loyal" if char.loyalty >= 60 else "wary" if char.loyalty >= 20 else "hostile"
    lines = cfg.get("fallback", {}).get(band) or ["{name} listens in silence."]
    rng = random.Random(f"{state.intel_seed}:talk:{state.clock.turn}:{char.id}:"
                        f"{len(_conversations(state).get(char.id, []))}")
    text = rng.choice(lines)
    court = _court()
    return text.format(name=char.name, role=court.office_name(state, char.office) if char.office else char.role,
                       ruler=f"Lord Protector {state.player.dynasty.ruler.name}")


def week_total(state: GameState, character_id: str) -> int:
    """Loyalty already gained or lost by conversation with this courtier this week."""
    return sum(int(e.get("applied", 0)) for e in _conversations(state).get(character_id, [])
               if e.get("turn") == state.clock.turn)


def prepare_conversation(state: GameState, character_id: str, player_text: str) -> Exchange:
    char = speaker(state, character_id)
    cfg = settings(state)
    text = clean_player_text(player_text, cfg)
    return Exchange(char.id, text, build_messages(state, char, text), cfg)


def complete_conversation(state: GameState, exchange: Exchange, result: tuple[str, int] | None,
                          reason: str = "") -> Reply:
    """Apply an answer (or, with result None, the scripted fallback) to the game state."""
    court = _court()
    char = speaker(state, exchange.character_id)
    cfg = settings(state)
    if result is None:
        reply = Reply(char.id, exchange.player_text, fallback_line(state, char), source=FALLBACK, reason=reason)
    else:
        dialogue, change = result
        cap = int(cfg.get("weekly_loyalty_cap", 5))
        before = week_total(state, char.id)
        applied = max(-cap, min(cap, before + change)) - before
        if applied:
            court.adjust_loyalty(char, applied)
        reply = Reply(char.id, exchange.player_text, dialogue, change, applied, NEURAL)
    log = _conversations(state).setdefault(char.id, [])
    log.append({"turn": state.clock.turn, "player": reply.player_text, "reply": reply.dialogue,
                "loyalty": reply.loyalty_change, "applied": reply.applied, "source": reply.source})
    keep = max(int(cfg.get("memory_exchanges", 6)), 1) * 2
    del log[:-keep]
    return reply


def converse_with_character(state: GameState, character_id: str, player_text: str) -> Reply:
    """Speak to a courtier. Uses the local model when Ollama is running, the scripted fallback otherwise.

    Blocks for up to `chat_timeout` seconds while the model thinks: a UI should call prepare_conversation(), run
    try_fetch() in a worker, then complete_conversation() (see src/ui/screens/converse.py)."""
    exchange = prepare_conversation(state, character_id, player_text)
    result, reason = try_fetch(exchange)
    return complete_conversation(state, exchange, result, reason)


# --- the Hold Court hook --------------------------------------------------------------------------------


def prepare_audience(state: GameState, card: dict[str, Any]) -> Exchange | None:
    """An audience card's petitioner is to speak the petition in their own words (None: not an audience)."""
    char_id = card.get("audience")
    house = state.player.dynasty
    if not char_id or house is None or char_id not in house.characters:
        return None
    char = house.characters[char_id]
    cfg = settings(state)
    petition = card["text"].split("\n\nPETITIONER:")[0]
    situation = (f"You have demanded an audience with the Lord Protector to make this petition: "
                 f"\"{card['title']}: {petition}\" Make your case to the Lord Protector in your own words. "
                 "Use loyalty_change 0.")
    messages = [{"role": "system", "content": build_system_prompt(state, char, situation)},
                {"role": "user", "content": "The Lord Protector nods. 'Speak.'"}]
    return Exchange(char.id, "", messages, cfg, kind="audience", card_id=card["id"])


def complete_audience(state: GameState, exchange: Exchange, result: tuple[str, int] | None) -> bool:
    """Put the petitioner's own words on the card. With no result the card keeps its scripted text."""
    if result is None:
        return False
    card = state.crisis_cards.get(exchange.card_id)
    if card is None:
        return False
    house = state.player.dynasty
    name = house.characters[exchange.character_id].name if house else "The petitioner"
    card["text"] = f"{name.upper()} SPEAKS: “{result[0]}”\n\n{card['text']}"
    card["neural"] = True
    return True


def narrate_audience(state: GameState, card: dict[str, Any] | None) -> bool:
    """Synchronous Hold Court hook: the petitioner speaks (if Ollama is running). Returns True if it did."""
    if not card:
        return False
    exchange = prepare_audience(state, card)
    if exchange is None:
        return False
    return complete_audience(state, exchange, try_fetch(exchange)[0])
