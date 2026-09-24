"""Real SDL event tests plus simulation boundary and fog-of-war regression checks.

Optional: terminal-only installs skip this file when pygame is absent.
"""
import os
from dataclasses import asdict

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")
os.environ.setdefault("SDL_AUDIODRIVER", "dummy")
os.environ.setdefault("PYGAME_HIDE_SUPPORT_PROMPT", "1")

import pytest

pygame = pytest.importorskip("pygame")

from command_graphics.app import CommandApp, PAGES
from command_graphics.camera import Camera
from command_graphics.session import Session
from src.engine import dilemmas, movement, production, research
from src.engine.electronic_warfare import jam, log_signals
from src.engine.savegame import load_game, snapshot
from src.models.game_state import GameOver


@pytest.fixture
def session(tmp_path):
    return Session(seed=84, save_path=tmp_path / "graphics.json")


@pytest.fixture
def app(session):
    client = CommandApp(session)
    yield client
    client.close()


def click(app, point, button=1):
    app.handle(pygame.event.Event(pygame.MOUSEBUTTONDOWN, pos=point, button=button))


def test_camera_preserves_geography_and_zoom_anchor():
    camera = Camera(240, 80)
    rect = pygame.Rect(0, 0, 1000, 700)
    camera.fit(rect)
    for cell in [(0, 0), (120, 40), (239, 79)]:
        assert camera.cell(camera.screen(cell)) == cell
    anchor = (400, 300)
    before = ((anchor[0]-camera.x)/(camera.zoom*camera.column_scale), (anchor[1]-camera.y)/camera.zoom)
    camera.zoom_at(anchor, 2)
    after = ((anchor[0]-camera.x)/(camera.zoom*camera.column_scale), (anchor[1]-camera.y)/camera.zoom)
    assert after == pytest.approx(before)
    assert camera.screen((2, 0))[0]-camera.screen((0, 0))[0] == pytest.approx(camera.zoom, abs=1)


def test_hidden_enemies_and_exact_strength_never_enter_projection(session):
    game = session.game
    game.contacts.clear()
    assert all(c.side == 'friendly' for c in session.contacts())
    from src.engine.recon import update_contacts
    update_contacts(game)
    for view in session.contacts():
        if view.side == 'hostile':
            assert "CERTAINTY" in view.strength
            assert view.supply is None and view.morale is None and not view.can_order
            assert 'equipment_inventory' not in asdict(view)
    # Rendering intelligence cannot perturb simulation randomness.
    before = game.rng.getstate()
    session.contacts()
    assert game.rng.getstate() == before


def test_jammed_units_use_last_report_and_have_no_orders(session):
    game = session.game
    unit = game.unit('kes_1_inf')
    log_signals(game)
    original = unit.location
    jam(game, original, 10, 3)
    unit.location = original[0]+1, original[1]
    unit.strength = 1234
    view = next(c for c in session.contacts() if c.id == unit.id)
    assert view.location == original
    assert view.strength == 'Unknown'
    assert view.side == 'lost' and not view.can_order
    assert view.supply is None


@pytest.mark.parametrize('page', PAGES+['Help'])
def test_every_screen_renders_and_scrolls(app, page):
    app.change_page(page)
    app.draw()
    app.scroll = app.scroll_max
    app.draw()
    assert app.screen.get_width() == 1440
    assert app.p.buttons


def test_click_counter_and_right_click_issues_real_order(app):
    app.draw()
    unit = app.game.unit('kes_1_inf')
    rect = next(rect for rect, uid in app.atlas.hits if uid == unit.id)
    click(app, rect.center)
    assert app.selected == unit.id
    target = (unit.x-2, unit.y)
    app.draw()
    click(app, app.camera.screen(target), 3)
    assert unit.active_order.target == target


def test_air_page_only_presents_player_wings(app, monkeypatch):
    rendered=[]
    original=app.p.text
    def capture(text,*args,**kwargs):
        rendered.append(str(text))
        original(text,*args,**kwargs)
    monkeypatch.setattr(app.p,'text',capture)
    app.change_page('Air')
    app.draw()
    for wing in app.game.air_wings:
        if wing.nation_id != app.game.player.id:
            assert wing.name not in rendered


def test_emergency_captures_clicks_and_blocks_mutations(app):
    game=app.game
    entry=game.catalog['events_deck']['cards'][0]
    game.pending_dilemma=entry['id']
    app.draw()
    turn=game.clock.turn
    # Clicking the underlying advance button cannot advance through the modal.
    click(app,(app.screen.get_width()-120,50))
    assert game.clock.turn == turn
    with pytest.raises(ValueError,match='emergency'):
        app.session.execute(production.assign_factories,game.player.id,'rifles',1)
    app.modal_scroll=app.modal_max
    app.draw()
    assert app.p.buttons
    click(app,app.p.buttons[0][0].center)
    assert game.pending_dilemma != entry['id']


def test_dispatch_reply_click_changes_simulation(app):
    mail=next(m for m in app.game.inbox.newest_first() if m.awaiting_response)
    app.open_mail(mail.id)
    app.change_page('Dispatches')
    app.draw()
    app.scroll=app.scroll_max
    app.draw()
    # The last visible button is a real response, below page controls.
    click(app,app.p.buttons[-1][0].center)
    assert mail.response is not None


def test_save_can_be_loaded_by_terminal_engine_and_continues_identically(session):
    game=session.game
    session.execute(movement.issue_move_order,'kes_1_inf',(116,20))
    session.save()
    loaded=Session(load=str(session.save_path),save_path=session.save_path)
    session.advance()
    loaded.advance()
    first,second=snapshot(session.game),snapshot(loaded.game)
    first.pop('saved_at');second.pop('saved_at')
    assert first == second
    assert load_game(session.save_path).unit('kes_1_inf').active_order.target == (116,20)


def test_end_of_campaign_disallows_graphical_commands(session):
    session.game.game_over=GameOver('victory',1,'1984-01-02')
    with pytest.raises(ValueError,match='ended'):
        session.execute(production.assign_factories,session.game.player.id,'rifles',1)


def test_resize_and_long_dispatch_scroll(app):
    app.handle(pygame.event.Event(pygame.VIDEORESIZE,w=1100,h=720))
    app.change_page('Help')
    app.draw()
    assert app.scroll_max>0
    app.scroll=app.scroll_max
    app.draw()
    assert app.page_rect.right<=1100


def test_loading_failure_keeps_current_campaign(session):
    game=session.game
    with pytest.raises((ValueError,RuntimeError,OSError)):
        session.load_saved()
    assert session.game is game


def button_lookup(app, monkeypatch):
    """Capture labels while exercising the renderer's real clipped hit rectangles."""
    labels = {}
    original = app.p.button
    def capture(label, rect, callback, **kwargs):
        original(label, rect, callback, **kwargs)
        clipped = pygame.Rect(rect).clip(app.screen.get_clip())
        if kwargs.get('enabled', True) and clipped.width and clipped.height:
            labels[label] = clipped
    monkeypatch.setattr(app.p, 'button', capture)
    return labels


def test_expansion_city_hospital_through_graphical_button(app, monkeypatch):
    if not getattr(app.game, 'cities', {}):
        pytest.skip('Expansion 1.1 is not installed')
    labels = button_lookup(app, monkeypatch)
    app.open_city('Aldmark')
    # Capture the first Build button: Hospital, in the engine's catalogue order.
    captured = []
    original = app.p.button
    def first_build(label, rect, callback, **kwargs):
        original(label, rect, callback, **kwargs)
        if label == 'Build':
            captured.append(pygame.Rect(rect))
    monkeypatch.setattr(app.p, 'button', first_build)
    app.draw()
    before = app.game.player.treasury
    click(app, captured[0].center)
    city = app.game.cities['Aldmark']
    assert city.construction_queue[0]['building'] == 'hospital'
    assert app.game.player.treasury == before-app.game.catalog['cities']['buildings']['hospital']['cost']


def test_expansion_diplomacy_envoy_and_purchase(app, monkeypatch):
    if not getattr(app.game, 'foreign', {}):
        pytest.skip('Expansion 1.1 is not installed')
    from src.engine import diplomacy
    labels = button_lookup(app, monkeypatch)
    app.change_page('Diplomacy')
    app.draw()
    power = next(iter(diplomacy.nations(app.game)))
    before = diplomacy.relation(app.game,power)
    envoy = next(rect for text,rect in labels.items() if text.startswith('Send envoy'))
    click(app,envoy.center)
    assert diplomacy.relation(app.game,power) > before
    # Set eligibility in this fixture, then buy using the real rendered button.
    app.game.foreign[power]['alignment'] = 100
    app.draw()
    app.scroll = app.scroll_max
    labels.clear()
    app.draw()
    assert 'Purchase cargo' in labels
    click(app,labels['Purchase cargo'].center)
    assert any(s['to']==app.game.player.id and s['from']==power for s in app.game.shipments)
    app.session.save()
    restored = load_game(app.session.save_path)
    assert restored.shipments == app.game.shipments


def test_expansion_hospital_survives_graphical_save_load(app):
    if not getattr(app.game,'cities',{}):
        pytest.skip('Expansion 1.1 is not installed')
    from src.engine.cities import order_building
    app.session.execute(order_building,'Aldmark','hospital')
    app.session.save()
    app.session.load_saved()
    assert app.game.cities['Aldmark'].construction_queue[0]['building']=='hospital'
