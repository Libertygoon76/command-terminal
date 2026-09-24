"""A map-first desktop client. All game mutations go through the shared engine."""
from __future__ import annotations

import math
import time
import pygame

from command_graphics.atlas import Atlas
from command_graphics.camera import Camera
from command_graphics.painting import Painter, INK, PANEL, EDGE, TEXT, MUTED, GOLD, GREEN, RED, BLUE
from src.engine import air_engine, combat_engine, dilemmas, economy_engine, event_manager
from src.engine import movement, naval_engine, production, recruitment, research
from src.engine.electronic_warfare import is_dark

PAGES = ["Map", "Dispatches", "Industry", "Research", "Forces", "Air", "Cities", "Diplomacy"]


class CommandApp:
    def __init__(self, session, size=(1440, 900)):
        pygame.display.init()
        pygame.font.init()
        self.screen = pygame.display.set_mode(size, pygame.RESIZABLE)
        pygame.display.set_caption("COMMAND TERMINAL — Strategic Command")
        self.p = Painter(self.screen)
        self.session = session
        self.page = "Map"
        self.mode = "Political"
        self.selected = None
        self.selected_mail = None
        self.selected_city = None
        self.selected_power = None
        self.scroll = 0
        self.scroll_max = 0
        self.side_scroll = 0
        self.side_max = 0
        self.modal_scroll = 0
        self.modal_max = 0
        self.confirm = None
        self.summary_open = False
        self.dismissed_game_over = False
        self.running = True
        self.drag = None
        self.notice = "Left-click a counter to inspect. Right-click terrain to issue a movement order."
        self.notice_until = time.monotonic() + 16
        self.route_key = None
        self.preview_route = None
        self.route_label = ""
        self.contacts_revision = -1
        self.contacts = []
        self.rebuild_map()

    @property
    def game(self):
        return self.session.game

    def layout(self):
        w, h = self.screen.get_size()
        self.map_rect = pygame.Rect(0, 142, w-350, h-180)
        self.side_rect = pygame.Rect(w-350, 142, 350, h-180)
        self.page_rect = pygame.Rect(24, 160, w-48, h-218)
        self.body_rect = pygame.Rect(self.page_rect.x+20, self.page_rect.y+92,
                                     self.page_rect.width-40, self.page_rect.height-132)

    def rebuild_map(self):
        self.layout()
        world = self.game.world_map
        self.atlas = Atlas(world, self.game.player.id)
        self.camera = Camera(world.width, world.height, float(self.game.config.get("map", {}).get("column_scale", .5)))
        self.camera.fit(self.map_rect)
        self.contacts_revision = -1
        self.route_key = None

    def toast(self, message):
        self.notice = str(message)
        self.notice_until = time.monotonic()+9

    def action(self, callback, success="Orders recorded."):
        try:
            callback()
            if success:
                self.toast(success)
        except (ValueError, RuntimeError, OSError) as error:
            self.toast(error)
        self.route_key = None

    def command(self, function, *args, success="Orders recorded.", **kwargs):
        self.action(lambda: self.session.execute(function, *args, **kwargs), success)

    def change_page(self, page):
        self.page = page
        self.scroll = 0
        self.route_key = None

    def select(self, unit_id, center=False):
        self.selected = unit_id
        self.side_scroll = 0
        self.route_key = None
        if center:
            contact = next((c for c in self.contacts if c.id == unit_id), None)
            if contact:
                self.camera.center(contact.location, self.map_rect)
        self.page = "Map"

    def orderable(self):
        if self.game.game_over or self.game.pending_dilemma:
            return None
        unit = self.game.unit(self.selected) if self.selected else None
        if unit and unit.nation_id == self.game.player.id and not is_dark(self.game, unit):
            return unit
        return None

    def advance(self):
        if self.game.pending_dilemma or self.game.game_over:
            self.dismissed_game_over = False
            return
        self.toast("Resolving the week — movement, combat, logistics and national affairs...")
        self.draw()
        pygame.display.flip()
        try:
            self.session.advance()
            self.summary_open = not self.game.pending_dilemma and not self.game.game_over
            self.modal_scroll = 0
            self.toast(f"Week {self.game.clock.turn:03d} resolved. Reports received.")
        except (ValueError, RuntimeError) as error:
            self.toast(error)
        self.route_key = None

    def save(self):
        self.action(self.session.save, f"Campaign saved to {self.session.save_path.name}.")

    def load(self):
        try:
            self.session.load_saved()
            self.selected = None
            self.selected_mail = None
            self.dismissed_game_over = False
            self.summary_open = False
            self.confirm = None
            self.modal_scroll = 0
            self.rebuild_map()
            self.toast("Campaign restored.")
        except (ValueError, RuntimeError, OSError) as error:
            self.toast(error)

    def update_contacts(self):
        if self.contacts_revision != self.session.revision:
            self.contacts = self.session.contacts()
            self.contacts_revision = self.session.revision
            if self.selected and not any(c.id == self.selected for c in self.contacts):
                self.selected = None

    def draw(self):
        self.layout()
        self.update_contacts()
        p = self.p
        p.buttons = []
        p.mouse = pygame.mouse.get_pos()
        self.screen.fill(INK)
        self.draw_header()
        header_buttons = list(p.buttons)
        self.update_route()
        self.atlas.draw(p, self.map_rect, self.camera, self.contacts, self.selected, self.mode,
                        self.game, self.preview_route)
        self.draw_inspector()
        if self.page != "Map":
            p.buttons = header_buttons  # Covered map/inspector buttons must not receive clicks.
            self.draw_page()
        else:
            for i, label in enumerate(("Political", "Terrain", "Supply")):
                p.button(label, (18+i*100, 157, 94, 31), lambda m=label: setattr(self, "mode", m), active=self.mode == label)
            p.button("Fit map [Home]", (18, 197, 136, 30), lambda: self.camera.fit(self.map_rect))
            if self.route_label:
                p.box((18, self.map_rect.bottom-90, min(560, self.map_rect.width-36), 34), INK)
                p.text(self.route_label, (28, self.map_rect.bottom-84), GOLD, 14, width=self.map_rect.width-65)
        # Footer is outside every scrolling region.
        w, h = self.screen.get_size()
        pygame.draw.line(self.screen, EDGE, (0, h-38), (w, h-38))
        footer = self.notice if time.monotonic() < self.notice_until else "SCROLL: zoom / lists   •   MIDDLE DRAG: pan   •   RIGHT CLICK: move   •   N: next week   •   F5: save   •   F9: load   •   F11: fullscreen"
        p.text(footer, (18, h-28), GOLD if time.monotonic() < self.notice_until else MUTED, 14, width=w-36)
        if self.modal_active():
            self.draw_modal()

    def draw_header(self):
        p, game = self.p, self.game
        w = self.screen.get_width()
        p.text("COMMAND", (22, 12), TEXT, 25, True)
        p.text("T E R M I N A L  /  S T R A T E G I C  C O M M A N D", (23, 44), GOLD, 11)
        player = game.player
        stats = [("TREASURY", f"{player.treasury:,.0f} CR"), ("RESERVES", f"{player.manpower:,}"),
                 ("CIVIL MORALE", f"{player.morale:.0f}%"), ("FACTORIES", f"{player.free_factories} / {player.military_factories} free")]
        left, space = 345, max(110, (w-620)//4)
        for i, (label, value) in enumerate(stats):
            x = left+i*space
            p.text(label, (x, 15), MUTED, 11, True)
            p.text(value, (x, 34), TEXT, 16 if w<1250 else 18, True, width=space-8)
        p.text(f"WEEK {game.clock.turn:03d}  /  {game.clock.date_str}", (w-245, 11), GOLD, 14, True)
        p.button("ADVANCE WEEK  [N]", (w-245, 34, 225, 37), self.advance, accent=True,
                 enabled=not bool(game.game_over))
        pygame.draw.line(self.screen, EDGE, (20, 86), (w-20, 86))
        nav_width = (w-225)//len(PAGES)
        for i, page in enumerate(PAGES):
            label = page
            if page == "Dispatches":
                label = ("Mail" if w<1250 else label) + f" ({game.inbox.unread_count})"
            p.button(label, (18+i*nav_width, 100, nav_width-6, 32), lambda page=page: self.change_page(page), active=self.page == page)
        p.button("Save", (w-193, 100, 74, 32), self.save)
        p.button("Help", (w-111, 100, 90, 32), lambda: self.change_page("Help"))

    def update_route(self):
        unit = self.orderable()
        mouse = self.p.mouse
        target = self.camera.cell(mouse) if self.map_rect.collidepoint(mouse) and self.page == "Map" else None
        if unit and target is None and unit.active_order:
            target = unit.active_order.target
        key = self.session.revision, self.selected, target, self.page
        if key == self.route_key:
            return
        self.route_key = key
        self.preview_route = None
        self.route_label = ""
        if not unit or target is None or not self.game.world_map.in_bounds(*target):
            return
        route = movement.plan_route(self.game, unit, target)
        if route:
            self.preview_route = [unit.location] + route.path
            self.route_label = f"{unit.designation}  →  {target[0]:03d}-{target[1]:03d}  /  ETA {route.eta_weeks} weeks  /  right-click to order"
        else:
            self.route_label = "No traversable route to this location."

    def draw_inspector(self):
        p, rect = self.p, self.side_rect
        self.screen.fill(PANEL, rect)
        pygame.draw.line(self.screen, EDGE, rect.topleft, rect.bottomleft)
        x, width = rect.x+22, rect.width-44
        p.text("FIELD COMMAND", (x, rect.y+19), GOLD, 12, True)
        content = pygame.Rect(x, rect.y+50, width, rect.height-205)
        old_clip = self.screen.get_clip()
        self.screen.set_clip(content)
        y = content.y-self.side_scroll
        contact = next((c for c in self.contacts if c.id == self.selected), None)
        if contact:
            y = p.wrap(contact.name, x, y, width, TEXT, 23)+8
            p.text(contact.label + " / " + contact.status, (x, y), BLUE if contact.side == "friendly" else GOLD, 13, True, width=width)
            y += 33
            y = p.wrap("Reported strength: " + contact.strength, x, y, width, TEXT, 16)+10
            if contact.supply is not None:
                p.bar("SUPPLY", contact.supply, x, y, width, GREEN if contact.supply > 40 else RED)
                y += 45
                p.bar("MORALE", contact.morale, x, y, width)
                y += 45
            if contact.can_order:
                unit = self.game.unit(contact.id)
                y = p.wrap("Command: " + unit.commander, x, y, width, MUTED, 14)+7
                if unit.traits_known:
                    from src.engine.command import trait_names
                    y = p.wrap(trait_names(self.game, unit), x, y, width, GOLD, 13)+7
                mode = unit.mission if self.game.domain(unit) == "sea" else unit.stance
                y = p.wrap("Orders: " + mode.upper() + " / " + unit.supply_state.upper(), x, y, width, MUTED, 13)+9
                if unit.active_order:
                    p.text(f"Destination: {unit.active_order.x:03d}-{unit.active_order.y:03d}", (x, y), GOLD, 14)
                    y += 27
                options = ("patrol", "blockade", "bombard") if self.game.domain(unit) == "sea" else ("defend", "assault", "withdraw")
                function = naval_engine.set_mission if self.game.domain(unit) == "sea" else combat_engine.set_stance
                for i, option in enumerate(options):
                    p.button(option.title(), (x+i*(width//3), y, width//3-4, 33),
                             lambda v=option, f=function, uid=unit.id: self.command(f, uid, v), active=mode == option)
                y += 42
                p.button("Cancel movement", (x, y, width, 32), lambda uid=unit.id: self.command(movement.cancel_order, uid))
                y += 48
                p.text("EQUIPMENT CARRIED", (x, y), GOLD, 12, True)
                y += 27
                for name, quantity in unit.equipment_inventory.items():
                    p.text(name.replace("_", " ").title(), (x, y), MUTED, 14, width=width-75)
                    p.text(f"{quantity:,}", (x+width-68, y), TEXT, 14)
                    y += 24
            else:
                y = p.wrap("Intelligence is incomplete. Stale contacts remain at their last reported position; no current strength or movement is known.", x, y, width, MUTED, 15)+12
            y += 18
            p.button("Clear selection", (x, y, width, 32), lambda: self.select(None))
            y += 52
        else:
            p.text("The Kestrian theatre", (x, y), TEXT, 23, True)
            y += 44
            y = p.wrap("Select a formation on the map or in the order of battle. Your orders resolve when the week advances.", x, y, width, MUTED, 15)+15
        p.text("ORDER OF BATTLE", (x, y), GOLD, 12, True)
        y += 28
        for c in self.contacts:
            if c.side not in ("friendly", "lost"):
                continue
            p.button(c.label + "  " + c.name, (x, y, width, 34), lambda uid=c.id: self.select(uid, True), active=c.id == self.selected)
            y += 40
        self.side_max = max(0, y+self.side_scroll-content.bottom)
        self.screen.set_clip(old_clip)
        if self.side_max:
            p.text("Scroll here for more formations", (x, rect.bottom-151), MUTED, 12)
        mini = pygame.Rect(x, rect.bottom-127, width, 104)
        self.atlas.minimap(p, mini, self.camera, self.map_rect)

    def draw_page(self):
        p, rect = self.p, self.page_rect
        p.box(rect, PANEL)
        p.text(self.page.upper(), (rect.x+22, rect.y+17), GOLD, 13, True)
        subtitles = {"Dispatches": "The Lord Protector’s dispatch box", "Industry": "The machinery of war",
            "Research": "Research & development", "Forces": "Mobilization & recruitment", "Air": "Air operations",
            "Cities": "Settlements of the Commonwealth", "Diplomacy": "Foreign affairs", "Help": "Field manual"}
        p.text(subtitles.get(self.page, self.page), (rect.x+22, rect.y+38), TEXT, 26, True)
        p.button("Return to map [Esc]", (rect.right-200, rect.y+24, 178, 34), lambda: self.change_page("Map"))
        old_clip = self.screen.get_clip()
        self.screen.set_clip(self.body_rect)
        x, y, width = self.body_rect.x, self.body_rect.y-self.scroll, self.body_rect.width
        methods = {"Dispatches": self.dispatches, "Industry": self.industry, "Research": self.research_page,
                   "Forces": self.forces, "Air": self.air, "Cities": self.cities, "Diplomacy": self.diplomacy,
                   "Help": self.help_page}
        end = methods[self.page](x, y, width)
        self.scroll_max = max(0, end+self.scroll-self.body_rect.bottom+20)
        self.screen.set_clip(old_clip)
        if self.scroll_max:
            p.text("Mouse wheel to scroll", (rect.x+22, rect.bottom-24), MUTED, 12)
            track = pygame.Rect(rect.right-12, self.body_rect.y, 3, self.body_rect.height)
            pygame.draw.rect(self.screen, EDGE, track)
            knob = max(25, int(track.height*track.height/(track.height+self.scroll_max)))
            pygame.draw.rect(self.screen, GOLD, (track.x, track.y+(track.height-knob)*min(1,self.scroll/max(1,self.scroll_max)),3,knob))

    def dispatches(self, x, y, width):
        p = self.p
        email = self.game.inbox.get(self.selected_mail) if self.selected_mail else None
        if email:
            p.button("← All dispatches", (x, y, 160, 34), self.clear_mail)
            y += 51
            y = p.wrap(email.subject, x, y, width, TEXT, 24)+12
            y = p.wrap(f"{email.classification}  /  {email.sender}  /  {email.received_date}", x, y, width, GOLD, 14)+12
            if email.awaiting_response and email.reply_by_turn:
                p.text(f"REPLY REQUIRED BY WEEK {email.reply_by_turn:03d}", (x, y), RED, 14, True)
                y += 29
            y = p.wrap(email.body, x, y, min(950,width), TEXT, 17)+24
            if email.awaiting_response:
                for option in email.options:
                    y = self.choice_button(option.label, x, y, width,
                        lambda oid=option.id, eid=email.id: self.command(event_manager.respond, eid, oid, success="Reply sent. Consequences recorded."))
            elif email.response_label:
                y = p.wrap("DECISION: " + email.response_label, x, y, width, GOLD, 16)+10
                y = p.wrap("\n".join(email.consequences), x, y, width, MUTED, 15)
            return y
        for mail in self.game.inbox.newest_first():
            p.button(mail.subject, (x, y, width-240, 39), lambda eid=mail.id: self.open_mail(eid))
            status = "REPLY NEEDED" if mail.awaiting_response else "UNREAD" if not mail.read else "FILED"
            p.text(status, (x+width-222, y+5), GOLD if mail.awaiting_response else MUTED, 13, True)
            p.text(f"WK {mail.received_turn or 0:03d}", (x+width-90, y+5), MUTED, 13)
            y += 51
        return y

    def open_mail(self, email_id):
        self.selected_mail = email_id
        event_manager.mark_read(self.game, email_id)
        self.scroll = 0

    def clear_mail(self):
        self.selected_mail = None
        self.scroll = 0

    def industry(self, x, y, width):
        p, nation = self.p, self.game.player
        policy = economy_engine.tax_policy(self.game, nation)
        p.text(f"TAX POLICY: {policy['name'].upper()}", (x,y), GOLD, 16, True)
        p.button("Lower taxes", (x+320,y-4,125,32), lambda: self.command(economy_engine.shift_tax_policy,nation.id,-1))
        p.button("Raise taxes", (x+455,y-4,125,32), lambda: self.command(economy_engine.shift_tax_policy,nation.id,1))
        y += 49
        p.text(f"{nation.free_factories} factories unassigned / {nation.military_factories} total", (x,y), TEXT, 19)
        y += 32
        y = p.wrap("Assign factories to production lines. Forecasts assume sufficient raw materials; finished equipment enters the national depot.", x,y,width,MUTED,15)+22
        for item_id, item in production.equipment_by_id(self.game).items():
            unlocked = production.is_unlocked(nation,item)
            p.text(item['name'],(x,y),TEXT if unlocked else MUTED,18,True,width=width-500)
            p.text(f"Depot {nation.national_stockpile.get(item_id,0):,}", (x+width-470,y), MUTED,14)
            p.text(f"~{production.forecast(self.game,nation,item_id):,.0f}/wk", (x+width-320,y), GREEN,14)
            assigned = nation.production.get(item_id,0)
            p.button("−",(x+width-170,y-3,42,33),lambda eid=item_id: self.command(production.assign_factories,nation.id,eid,-1), enabled=assigned>0)
            p.text(str(assigned),(x+width-112,y+3),TEXT,17)
            p.button("+",(x+width-60,y-3,42,33),lambda eid=item_id: self.command(production.assign_factories,nation.id,eid,1),enabled=unlocked and nation.free_factories>0)
            y += 48
        y += 12
        p.text("RAW MATERIALS",(x,y),GOLD,13,True)
        y += 28
        for key,value in nation.stockpiles.items():
            p.text(f"{key.replace('_',' ').title()}: {value:,}",(x,y),TEXT,16)
            y += 26
        return y

    def research_page(self,x,y,width):
        p,nation = self.p,self.game.player
        for tid,tech in research.techs(self.game).items():
            state = research.status(self.game,nation,tid)
            p.text(tech['name'],(x,y),TEXT,20,True,width=width-270)
            p.button("Research" if state=='AVAILABLE' else state.title(), (x+width-165,y,150,34),
                lambda t=tid: self.command(research.start_research,nation.id,t),enabled=state=='AVAILABLE',active=state=='ACTIVE')
            y += 32
            y = p.wrap(tech.get('description',''),x,y,width-200,MUTED,15)
            y = p.wrap(f"{research.weeks_remaining(self.game,nation,tid):g} weeks remaining / {tech.get('weekly_cost',0):,} CR per week" +
                (" / Requires: " + ", ".join(tech.get('requires',[])) if state=='LOCKED' else ''),x,y,width-200,GOLD,14)+24
        if nation.research_project:
            p.button("Pause active research",(x,y,240,34),lambda: self.command(research.stop_research,nation.id))
            y += 45
        return y

    def forces(self,x,y,width):
        p,nation = self.p,self.game.player
        p.text(f"{nation.manpower:,} personnel available for mobilization",(x,y),GOLD,18)
        y += 40
        for tid,template in recruitment.templates(self.game).items():
            p.text(template['name'],(x,y),TEXT,20,True,width=width-220)
            p.button("Raise formation",(x+width-180,y,165,34),lambda t=tid:self.command(recruitment.raise_formation,nation.id,t,success="Formation entered training."))
            y += 31
            p.text(f"{template['manpower']:,} personnel / {template['recruit_cost']:,} CR / {template['training_weeks']} weeks training",(x,y),MUTED,15)
            y += 41
        p.text("TRAINING QUEUE",(x,y),GOLD,13,True)
        y += 30
        for order in self.game.training:
            if order.nation_id != nation.id:
                continue
            p.text(f"{order.name} / {order.weeks_left} weeks remaining",(x,y),TEXT,16,width=width-180)
            p.button("Cancel training",(x+width-165,y,150,32),lambda oid=order.id:self.command(recruitment.cancel_training,oid))
            y += 45
        return y

    def air(self,x,y,width):
        p = self.p
        for wing in air_engine.wings(self.game, self.game.player.id):
            p.text(wing.name,(x,y),TEXT,22,True)
            y += 34
            p.text(f"{wing.aircraft} aircraft / {wing.status} / Sector: {wing.sector or 'BASE'}",(x,y),GOLD,16)
            y += 31
            p.button("Previous sector",(x,y,160,34),lambda wid=wing.id:self.command(air_engine.cycle_wing,wid,-1))
            p.button("Next sector",(x+174,y,160,34),lambda wid=wing.id:self.command(air_engine.cycle_wing,wid,1))
            p.button("Recall to base",(x+348,y,160,34),lambda wid=wing.id:self.command(air_engine.assign_wing,wid,None))
            y += 63
        return p.wrap("Wings contest their assigned sectors during weekly resolution. Fuel, aircraft and bombs come from your national stockpile.",x,y,width,MUTED,16)

    def cities(self,x,y,width):
        if hasattr(self.game, 'cities') and self.game.cities:
            from command_graphics.expansion import city_page
            return city_page(self,x,y,width)
        p = self.p
        y = p.wrap("Settlement atlas. Construction and local administration will connect to Expansion 1.1 when its city systems are available.",x,y,width,MUTED,16)+23
        for feature in self.game.world_map.features:
            if self.game.world_map.owner_at(feature.x,feature.y) != self.game.player.id:
                continue
            p.text(feature.name,(x,y),TEXT,20,True)
            p.text(feature.type.upper(),(x+300,y),MUTED,13)
            status = "BLOCKADED" if feature.name in self.game.blockades else f"Trade {feature.trade:,} CR/wk" if feature.type=='port' else f"Grid {feature.x:03d}-{feature.y:03d}"
            p.text(status,(x+width-400,y),RED if status=='BLOCKADED' else GOLD,14)
            p.button("Locate",(x+width-115,y-4,100,33),lambda cell=feature.location:self.locate(cell))
            y += 50
        return y

    def locate(self,cell):
        self.page='Map'
        self.camera.zoom=max(14,self.camera.zoom)
        self.camera.center(cell,self.map_rect)

    def diplomacy(self,x,y,width):
        if hasattr(self.game, 'foreign') and self.game.foreign:
            from command_graphics.expansion import diplomacy_page
            return diplomacy_page(self,x,y,width)
        return self.p.wrap("FOREIGN AFFAIRS / EXPANSION 1.1\n\nThe diplomacy simulation is being developed separately. This screen is reserved for relations, trade agreements and seaborne lend-lease once the engine APIs are ready.\n\nExisting diplomatic dispatches and their replies are available in Dispatches.",x,y,min(width,850),TEXT,19)

    def open_city(self,name):
        self.selected_city=name
        self.change_page('Cities')

    def open_power(self,nation):
        self.selected_power=nation
        self.scroll=0

    def help_page(self,x,y,width):
        return self.p.wrap("YOUR CAMPAIGN\nRead dispatches, equip your formations, issue orders, then advance one week. The simulation is shared with the terminal version.\n\nMAP CONTROLS\nLeft-click counters to select. Right-click a destination to issue a move order. Hover terrain with a formation selected to preview its route. Middle-click and drag to pan; the mouse wheel zooms around the pointer. Arrow keys pan. Home fits the theatre; the minimap recenters it. Stacked counters are offset so each can be selected.\n\nN advances the week. F5 or Ctrl+S saves. F9 offers to load the graphical save. F11 toggles fullscreen. Escape returns to the map, or opens the exit prompt.\n\nINTELLIGENCE\nBlue counters are friendly, red counters are observed enemies. Grey question marks are stale or jammed contacts. Enemy strength is an estimate with stated certainty. Supply mode colors friendly counters by reported supply; it does not reveal enemy logistics.\n\nADMINISTRATION\nIndustry assigns factories and taxes. Research funds one project. Forces raises formations. Air assigns wings to sectors. Scroll inside panels to see all entries and replies. Mandatory emergencies block other orders until answered.\n\nSAVES\nThe default graphical save is savegame-graphical.json, separate from the terminal's default savegame.json. Both use the same engine format. Start with --load SAVEFILE to import an existing campaign; F5 writes to the graphical save unless --save specifies another path.\n\nEXPANSION\nWith Expansion 1.1 installed, Cities manages hospitals, bunkers, local industry and local news. Diplomacy sends envoys, signs trade agreements and purchases lend-lease cargo. Select a power, improve alignment, then buy a package and track its convoy. Older campaigns without expansion state show an atlas and availability notice.",x,y,min(950,width),TEXT,17)

    def choice_button(self,label,x,y,width,callback):
        # Wrap the entire choice; long decisions must never be silently ellipsized.
        start = y
        y = self.p.wrap(label,x+14,y+12,width-28,TEXT,16)+12
        rect = pygame.Rect(x,start,width,y-start)
        pygame.draw.rect(self.screen,GOLD if rect.collidepoint(self.p.mouse) else EDGE,rect,1,border_radius=3)
        clipped = rect.clip(self.screen.get_clip())
        if clipped.width and clipped.height:
            self.p.buttons.append((clipped,callback))
        return y+12

    def modal_active(self):
        return bool(self.confirm or self.game.pending_dilemma or self.summary_open or
                    (self.game.game_over and not self.dismissed_game_over))

    def draw_modal(self):
        p = self.p
        shade = pygame.Surface(self.screen.get_size(),pygame.SRCALPHA)
        shade.fill((0,0,0,170))
        self.screen.blit(shade,(0,0))
        p.buttons=[]  # Modal captures every click; background orders are inaccessible.
        rect=pygame.Rect(0,0,min(900,self.screen.get_width()-80),self.screen.get_height()-140)
        rect.center=self.screen.get_rect().center
        p.box(rect,PANEL,GOLD)
        title="COMMAND DECISION"
        body=""
        choices=[]
        if self.confirm:
            title,body,choices=self.confirm
        elif self.game.game_over:
            title="VICTORY" if self.game.game_over.cause=='victory' else "GOVERNMENT FALLEN"
            alert=next((m for m in self.game.inbox.newest_first() if m.pinned),None)
            body=alert.body if alert else self.game.game_over.cause
            choices=[("Review the campaign",lambda:setattr(self,'dismissed_game_over',True)),("Save campaign",self.save)]
        elif self.game.pending_dilemma:
            entry=dilemmas.card(self.game,self.game.pending_dilemma)
            title=entry['title']
            body=dilemmas.card_text(self.game,entry)
            for choice in entry['choices']:
                preview=dilemmas.choice_preview(self.game,choice)
                label=choice['label'] + ("\n"+choice['hint'] if choice.get('hint') else '')
                if preview:
                    label+='\n'+" / ".join(preview)
                choices.append((label,lambda cid=choice['id']:self.decide(cid)))
        else:
            title=f"WEEK {self.game.clock.turn:03d} / SITUATION REPORT"
            body=self.session.last_summary
            choices=[("Return to command",lambda:setattr(self,'summary_open',False)),("Read dispatches",self.summary_mail)]
        p.text(title.upper(),(rect.x+25,rect.y+22),GOLD,22,True,width=rect.width-50)
        content=pygame.Rect(rect.x+25,rect.y+70,rect.width-50,rect.height-110)
        previous=self.screen.get_clip()
        self.screen.set_clip(content)
        y=p.wrap(body,content.x,content.y-self.modal_scroll,content.width,TEXT,17)+26
        for label,callback in choices:
            y=self.choice_button(label,content.x,y,content.width,callback)
        self.modal_max=max(0,y+self.modal_scroll-content.bottom)
        self.screen.set_clip(previous)
        p.text("Scroll to read all details and decisions" if self.modal_max else "FOR THE LORD PROTECTOR / EYES ONLY",(rect.x+25,rect.bottom-27),MUTED,12)
        if time.monotonic()<self.notice_until:
            p.text(self.notice,(18,self.screen.get_height()-28),GOLD,14,width=self.screen.get_width()-36)

    def decide(self,choice):
        self.action(lambda:self.session.decide(choice),"Decision recorded.")
        self.modal_scroll=0

    def summary_mail(self):
        self.summary_open=False
        self.selected_mail=None
        self.change_page('Dispatches')

    def ask_exit(self):
        self.modal_scroll=0
        self.confirm=("END COMMAND SESSION", "Save the current campaign before closing?",[
            ("Save and exit",self.save_exit),("Exit without saving",lambda:setattr(self,'running',False)),
            ("Keep playing",lambda:setattr(self,'confirm',None))])

    def save_exit(self):
        try:
            self.session.save()
            self.running=False
        except (ValueError,RuntimeError,OSError) as error:
            self.toast(error)

    def handle(self,event):
        if event.type==pygame.QUIT:
            self.ask_exit()
        elif event.type==pygame.VIDEORESIZE:
            self.screen=pygame.display.set_mode((max(1100,event.w),max(720,event.h)),pygame.RESIZABLE)
            self.p.surface=self.screen
            self.layout()
            self.route_key=None
        elif event.type==pygame.KEYDOWN:
            if event.key==pygame.K_F11:
                pygame.display.toggle_fullscreen()
                self.layout()
                return
            if event.key==pygame.K_F5 or (event.key==pygame.K_s and event.mod & pygame.KMOD_CTRL):
                self.save()
                return
            if self.modal_active():
                if event.key==pygame.K_ESCAPE and self.confirm:
                    self.confirm=None
                return
            if event.key==pygame.K_ESCAPE:
                if self.page!='Map':self.change_page('Map')
                else:self.ask_exit()
            elif event.key==pygame.K_n:self.advance()
            elif event.key==pygame.K_HOME:self.camera.fit(self.map_rect)
            elif event.key==pygame.K_F9:
                self.modal_scroll=0
                self.confirm=("RESTORE SAVED CAMPAIGN",f"Load {self.session.save_path.name}? Unsaved progress in this campaign will be replaced.",[
                    ("Load saved campaign",self.load),("Cancel",lambda:setattr(self,'confirm',None))])
            elif event.key in (pygame.K_LEFT,pygame.K_RIGHT,pygame.K_UP,pygame.K_DOWN):
                self.camera.x+={pygame.K_LEFT:65,pygame.K_RIGHT:-65}.get(event.key,0)
                self.camera.y+={pygame.K_UP:65,pygame.K_DOWN:-65}.get(event.key,0)
        elif event.type==pygame.MOUSEWHEEL:
            if self.modal_active():
                self.modal_scroll=max(0,min(self.modal_max,self.modal_scroll-event.y*55))
            elif self.page!='Map':
                self.scroll=max(0,min(self.scroll_max,self.scroll-event.y*55))
            elif self.side_rect.collidepoint(pygame.mouse.get_pos()):
                self.side_scroll=max(0,min(self.side_max,self.side_scroll-event.y*48))
            elif self.map_rect.collidepoint(pygame.mouse.get_pos()):
                self.camera.zoom_at(pygame.mouse.get_pos(),1.15**event.y)
        elif event.type==pygame.MOUSEBUTTONDOWN:
            if event.button==1:
                for rect,callback in reversed(self.p.buttons):
                    if rect.collidepoint(event.pos):
                        callback()
                        return
            if self.modal_active() or self.page!='Map':return
            if event.button==1 and self.atlas.mini.collidepoint(event.pos):
                rect=self.atlas.mini
                self.camera.center(((event.pos[0]-rect.x)/rect.width*self.game.world_map.width-.5,
                                    (event.pos[1]-rect.y)/rect.height*self.game.world_map.height-.5),self.map_rect)
            elif self.map_rect.collidepoint(event.pos):
                if event.button==1:
                    for rect,uid in reversed(self.atlas.hits):
                        if rect.collidepoint(event.pos):self.select(uid);return
                    for feature in self.game.world_map.features:
                        px,py=self.camera.screen(feature.location)
                        if math.hypot(px-event.pos[0],py-event.pos[1])<12 and feature.name in getattr(self.game,'cities',{}):
                            self.open_city(feature.name)
                            return
                    self.select(None)
                elif event.button==2:self.drag=event.pos
                elif event.button==3:
                    unit=self.orderable()
                    if unit:
                        self.command(movement.issue_move_order,unit.id,self.camera.cell(event.pos),success="Movement order transmitted. Execution begins next week.")
                    else:self.toast("Select a friendly formation in radio contact first.")
        elif event.type==pygame.MOUSEBUTTONUP and event.button==2:self.drag=None
        elif event.type==pygame.MOUSEMOTION and self.drag and not self.modal_active():
            self.camera.x+=event.pos[0]-self.drag[0]
            self.camera.y+=event.pos[1]-self.drag[1]
            self.drag=event.pos

    def run(self):
        clock=pygame.time.Clock()
        self.draw()
        while self.running:
            for event in pygame.event.get():
                self.handle(event)
                # Refresh hit targets after each event; two queued clicks cannot replay stale actions.
                self.draw()
            self.draw()
            pygame.display.flip()
            clock.tick(30)

    def close(self):
        pygame.quit()

