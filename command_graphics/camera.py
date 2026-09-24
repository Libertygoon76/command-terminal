"""Map projection preserves the engine's non-square geographic cells."""
from dataclasses import dataclass
import math


@dataclass
class Camera:
    width: int
    height: int
    column_scale: float = 0.5
    zoom: float = 1.0
    x: float = 0.0
    y: float = 0.0

    def fit(self, rect):
        self.zoom = min(rect.width / (self.width * self.column_scale), rect.height / self.height) * .91
        self.center((self.width / 2 - .5, self.height / 2 - .5), rect)

    def center(self, cell, rect):
        self.x = rect.centerx - (cell[0] + .5) * self.zoom * self.column_scale
        self.y = rect.centery - (cell[1] + .5) * self.zoom

    def screen(self, cell):
        return (round(self.x + (cell[0] + .5) * self.zoom * self.column_scale),
                round(self.y + (cell[1] + .5) * self.zoom))

    def cell(self, point):
        return (math.floor((point[0] - self.x) / (self.zoom * self.column_scale)),
                math.floor((point[1] - self.y) / self.zoom))

    def zoom_at(self, point, factor):
        previous = self.zoom
        self.zoom = max(2.0, min(70.0, self.zoom * factor))
        ratio = self.zoom / previous
        self.x = point[0] - (point[0] - self.x) * ratio
        self.y = point[1] - (point[1] - self.y) * ratio
