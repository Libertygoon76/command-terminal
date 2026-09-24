"""Small, clipped immediate-mode drawing primitives."""
import os
from pathlib import Path
import pygame

INK = (16, 25, 29)
PANEL = (23, 34, 38)
EDGE = (60, 77, 78)
TEXT = (223, 228, 216)
MUTED = (148, 163, 158)
GOLD = (213, 184, 111)
GREEN = (116, 182, 153)
RED = (211, 119, 105)
BLUE = (113, 166, 190)


class Painter:
    def __init__(self, surface):
        self.surface = surface
        self.fonts = {}
        self.buttons = []
        self.mouse = (0, 0)

    def font(self, size=16, bold=False):
        key = size, bold
        if key not in self.fonts:
            # Windows font discovery can select Segoe UI Light for the regular face.
            face = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / ('segoeuib.ttf' if bold else 'segoeui.ttf')
            self.fonts[key] = pygame.font.Font(str(face), size) if face.is_file() else pygame.font.SysFont("Arial", size, bold=bold)
        return self.fonts[key]

    def text(self, text, pos, color=TEXT, size=16, bold=False, width=None):
        font = self.font(size, bold)
        text = str(text)
        if width is not None and font.size(text)[0] > width:
            while text and font.size(text + "...")[0] > width:
                text = text[:-1]
            text += "..."
        self.surface.blit(font.render(text, True, color), pos)

    def wrap(self, text, x, y, width, color=TEXT, size=16):
        font = self.font(size)
        line_height = font.get_linesize() + 4
        for paragraph in str(text).splitlines():
            line = ""
            for word in paragraph.split():
                if line and font.size(line + " " + word)[0] > width:
                    self.text(line, (x, y), color, size)
                    y += line_height
                    line = ""
                # Split unusually long identifiers so they cannot escape the panel.
                while font.size(word)[0] > width:
                    cut = max(1, len(word) - 1)
                    while cut > 1 and font.size(word[:cut])[0] > width:
                        cut -= 1
                    if line:
                        self.text(line, (x, y), color, size)
                        y += line_height
                        line = ""
                    self.text(word[:cut], (x, y), color, size)
                    y += line_height
                    word = word[cut:]
                line = (line + " " + word).strip()
            self.text(line, (x, y), color, size)
            y += line_height
        return y

    def box(self, rect, color=PANEL, border=EDGE):
        pygame.draw.rect(self.surface, color, rect, border_radius=4)
        if border:
            pygame.draw.rect(self.surface, border, rect, 1, border_radius=4)

    def button(self, label, rect, action, *, active=False, enabled=True, accent=False):
        rect = pygame.Rect(rect)
        clip = self.surface.get_clip()
        hot = rect.collidepoint(self.mouse) and clip.collidepoint(self.mouse)
        color = (48, 66, 65) if hot and enabled else PANEL
        if active:
            color = (51, 69, 65)
        self.box(rect, color, GOLD if accent or active else EDGE)
        self.text(label, (rect.x + 10, rect.y + (rect.height - 20) // 2),
                  GOLD if accent else TEXT if enabled else MUTED, 15, width=rect.width - 20)
        visible = rect.clip(clip)
        if enabled and visible.width and visible.height:
            self.buttons.append((visible, action))

    def bar(self, label, value, x, y, width, color=GREEN):
        self.text(label, (x, y), MUTED, 13)
        self.text(f"{value:.0f}%", (x + width - 43, y), TEXT, 13)
        pygame.draw.rect(self.surface, INK, (x, y + 22, width, 5))
        pygame.draw.rect(self.surface, color, (x, y + 22, int(width * max(0, min(100, value)) / 100), 5))
