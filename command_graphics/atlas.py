"""Procedural atlas artwork and map overlays, derived from the real world data."""
from __future__ import annotations

import math
import pygame

from command_graphics.painting import BLUE, GOLD, GREEN, INK, MUTED, RED, TEXT

TERRAINS = {
    "plains": (106, 121, 87), "forest": (67, 99, 78), "mountains": (113, 116, 106),
    "mountain": (113, 116, 106), "hills": (119, 120, 91), "marsh": (79, 108, 96),
    "coastal": (135, 138, 105), "urban": (138, 129, 102), "river": (69, 112, 126),
}
SEA = (35, 66, 79)


class Atlas:
    def __init__(self, world, player_id):
        self.world = world
        self.player_id = player_id
        self.sheets = {}
        self.cached = None
        self.hits = []
        self.mini = pygame.Rect(0, 0, 1, 1)
        self._build()

    def _build(self):
        # A high-resolution cached atlas avoids drawing 19,200 cells every frame.
        world = self.world
        for mode in ("Terrain", "Political"):
            sheet = pygame.Surface((world.width * 6, world.height * 12))
            sheet.fill(SEA)
            for y in range(world.height):
                for x in range(world.width):
                    region = world.region_at(x, y)
                    if region:
                        base = TERRAINS.get(region.terrain, TERRAINS["plains"])
                        if mode == "Political":
                            tint = (95, 137, 135) if region.owner == self.player_id else (151, 105, 86) if region.owner in ("vosk",) else (137, 126, 101)
                            base = tuple((a + b * 2) // 3 for a, b in zip(base, tint))
                    else:
                        base = SEA
                    noise = ((x * 157 + y * 73 + x * y * 3) % 11) - 5
                    color = tuple(max(0, min(255, c + noise)) for c in base)
                    pygame.draw.rect(sheet, color, (x * 6, y * 12, 6, 12))
                    if region and region.terrain in ("forest", "mountain", "mountains", "hills") and (x + y) % 3 == 0:
                        px, py = x * 6 + 3, y * 12 + 6
                        dark = tuple(max(0, c - 22) for c in base)
                        pygame.draw.lines(sheet, dark, False, [(px-3, py+3), (px, py-3), (px+3, py+3)], 1)
                    if region:
                        for dx, dy in ((1, 0), (0, 1)):
                            other = world.region_at(x + dx, y + dy)
                            if other is None or other.owner != region.owner:
                                if dx:
                                    points = [(x*6+6, y*12), (x*6+6, y*12+12)]
                                else:
                                    points = [(x*6, y*12+12), (x*6+6, y*12+12)]
                                pygame.draw.line(sheet, (188, 183, 146) if other else (162, 174, 149), *points, 2)
            self.sheets[mode] = sheet

    def draw(self, painter, rect, camera, contacts, selected, mode, game, route=None):
        surface = painter.surface
        old_clip = surface.get_clip()
        surface.set_clip(rect)
        surface.fill(SEA, rect)
        key = mode if mode in self.sheets else "Political"
        # Only transform the visible source area; high zoom cannot allocate giant surfaces.
        source = self.sheets[key]
        sx = camera.zoom * camera.column_scale / 6
        sy = camera.zoom / 12
        src_rect = pygame.Rect(math.floor((rect.left-camera.x)/sx), math.floor((rect.top-camera.y)/sy),
                               math.ceil(rect.width/sx)+2, math.ceil(rect.height/sy)+2).clip(source.get_rect())
        cache_key = (key, tuple(src_rect), round(sx, 6), round(sy, 6))
        if src_rect.width and src_rect.height:
            if not self.cached or self.cached[0] != cache_key:
                scaled = pygame.transform.smoothscale(source.subsurface(src_rect),
                    (max(1, round(src_rect.width*sx)), max(1, round(src_rect.height*sy))))
                self.cached = cache_key, scaled
            surface.blit(self.cached[1], (round(camera.x+src_rect.x*sx), round(camera.y+src_rect.y*sy)))
        # Cartographic reference grid.
        for x in range(0, self.world.width, 20):
            a, b = camera.screen((x, 0)), camera.screen((x, self.world.height))
            pygame.draw.line(surface, (73, 100, 102), a, b)
        for y in range(0, self.world.height, 10):
            a, b = camera.screen((0, y)), camera.screen((self.world.width, y))
            pygame.draw.line(surface, (73, 100, 102), a, b)
        for (x, y), kind in self.world.transport.items():
            pos = camera.screen((x, y))
            if not rect.collidepoint(pos):
                continue
            color = (206, 184, 135) if kind == "rail" else (146, 137, 103) if kind == "road" else RED
            pygame.draw.rect(surface, color, (pos[0]-max(1, int(camera.zoom*.2)), pos[1]-1,
                                               max(2, math.ceil(camera.zoom*camera.column_scale)), 2))
        for region in self.world.regions.values():
            px, py = camera.screen(region.label)
            label = region.name.upper()
            font = painter.font(13, True)
            painter.text(label, (px-font.size(label)[0]//2, py), (215, 215, 186), 13, True)
        for zone in self.world.sea_zones:
            x0, y0, x1, y1 = zone.rect
            px, py = camera.screen(((x0+x1)/2, (y0+y1)/2))
            painter.text(zone.name.upper(), (px-65, py), (123, 154, 164), 12)
        for feature in self.world.features:
            if camera.zoom < 10 and feature.type == "town":
                continue
            px, py = camera.screen(feature.location)
            if not rect.collidepoint(px, py):
                continue
            pygame.draw.circle(surface, INK, (px, py), 5)
            pygame.draw.circle(surface, GOLD if feature.type == "capital" else TEXT, (px, py), 3)
            if feature.type == "port":
                pygame.draw.rect(surface, BLUE, (px-5, py-5, 10, 10), 1)
            label = feature.name.upper() if feature.type == "capital" else feature.name
            painter.text(label, (px+8, py-8), (12, 24, 26), 15, feature.type == "capital")
            painter.text(label, (px+7, py-9), TEXT, 15, feature.type == "capital")
        if route:
            points = [camera.screen(cell) for cell in route]
            if len(points) > 1:
                pygame.draw.lines(surface, GOLD, False, points, 3)
                ax, ay = points[-1]
                angle = math.atan2(ay-points[-2][1], ax-points[-2][0])
                pygame.draw.polygon(surface, GOLD, [(ax, ay),
                    (ax-12*math.cos(angle-.45), ay-12*math.sin(angle-.45)),
                    (ax-12*math.cos(angle+.45), ay-12*math.sin(angle+.45))])
        self.hits = []
        stacks = {}
        for contact in contacts:
            px, py = camera.screen(contact.location)
            count = stacks.get(contact.location, 0)
            stacks[contact.location] = count+1
            px += (count % 3)*12
            py += (count // 3)*28 + (count % 3)*8
            counter = pygame.Rect(px-20, py-13, 40, 26)
            if not rect.colliderect(counter):
                continue
            color = BLUE if contact.side == "friendly" else RED if contact.side == "hostile" else MUTED
            if mode == "Supply" and contact.supply is not None:
                color = GREEN if contact.supply >= 60 else GOLD if contact.supply >= 30 else RED
            pygame.draw.rect(surface, INK, counter.move(2, 3), border_radius=2)
            pygame.draw.rect(surface, color, counter, border_radius=2)
            pygame.draw.rect(surface, (24, 42, 45), counter.inflate(-6, -6), 1)
            if contact.side in ("ghost", "lost"):
                painter.text("?", (px-5, py-11), INK, 19, True)
            elif any(k in contact.kind for k in ("armor", "tank")):
                pygame.draw.ellipse(surface, INK, (px-11, py-6, 22, 12), 2)
            elif "artillery" in contact.kind:
                pygame.draw.circle(surface, INK, (px, py), 4)
            elif any(k in contact.kind for k in ("destroyer", "battleship", "submarine")):
                pygame.draw.polygon(surface, INK, [(px-12, py+1), (px+12, py+1), (px+7, py+6), (px-7, py+6)])
                pygame.draw.line(surface, INK, (px, py-8), (px, py+2), 2)
            else:
                pygame.draw.line(surface, INK, (px-11, py-6), (px+11, py+6), 2)
                pygame.draw.line(surface, INK, (px+11, py-6), (px-11, py+6), 2)
            if contact.id == selected:
                pygame.draw.rect(surface, GOLD, counter.inflate(8, 8), 2, border_radius=3)
            if contact.status == "ENGAGED":
                pygame.draw.circle(surface, GOLD, counter.topright, 5)
            if camera.zoom >= 9 or contact.id == selected:
                painter.text(contact.label, (px-20, py+15), TEXT, 11, True)
            self.hits.append((counter, contact.id))
        # Scale and orientation.
        painter.text("N", (rect.right-34, rect.y+24), GOLD, 18, True)
        pygame.draw.line(surface, GOLD, (rect.right-28, rect.y+50), (rect.right-28, rect.y+75), 2)
        length = round(camera.zoom * 10)
        length = min(length, rect.width // 3)
        miles = length / camera.zoom * float(game.config.get("map", {}).get("miles_per_cell", 10))
        pygame.draw.line(surface, TEXT, (rect.x+24, rect.bottom-42), (rect.x+24+length, rect.bottom-42), 2)
        painter.text(f"{miles:.0f} miles", (rect.x+24, rect.bottom-35), TEXT, 12)
        surface.set_clip(old_clip)

    def minimap(self, painter, rect, camera, view):
        self.mini = rect
        painter.surface.blit(pygame.transform.smoothscale(self.sheets["Political"], rect.size), rect)
        pygame.draw.rect(painter.surface, GOLD, rect, 1)
        x0 = (view.left-camera.x)/(camera.zoom*camera.column_scale)/self.world.width
        y0 = (view.top-camera.y)/camera.zoom/self.world.height
        width = view.width/(camera.zoom*camera.column_scale)/self.world.width
        height = view.height/camera.zoom/self.world.height
        viewport = pygame.Rect(rect.x+x0*rect.width, rect.y+y0*rect.height, width*rect.width, height*rect.height)
        pygame.draw.rect(painter.surface, TEXT, viewport.clip(rect), 1)
