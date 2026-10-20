"""Общие кирпичики интерфейса: палитра, шрифты, кнопки, отрисовка карт.

Ничего игрового здесь нет — этим пользуются все столы казино.
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass
from typing import Callable

import pygame

from cards import SUIT_LETTERS, SUIT_SYMBOLS, Card

# --- палитра ---------------------------------------------------------------

BG = (13, 17, 22)
PANEL = (23, 30, 38)
PANEL_EDGE = (40, 51, 63)
FELT = (15, 42, 35)
FELT_EDGE = (28, 68, 56)
TEXT = (231, 238, 243)
MUTED = (136, 154, 169)
FAINT = (82, 97, 111)
GOLD = (210, 164, 58)
GOLD_BRIGHT = (244, 203, 99)
GOLD_DIM = (62, 50, 22)
GREEN = (80, 222, 136)
GREEN_DIM = (24, 48, 38)
RED = (243, 109, 109)
CARD_FACE = (242, 239, 231)
CARD_WILD_FACE = (247, 236, 199)
CARD_EDGE = (189, 185, 175)
HAND_FRAME = (19, 50, 42)
HAND_FRAME_WAIT = (14, 36, 31)
HAND_FRAME_EDGE = (34, 78, 64)
CARD_BACK = (30, 40, 50)
CARD_BACK_INNER = (38, 52, 65)
CARD_RED = (184, 48, 45)
CARD_BLACK = (25, 33, 42)
BTN = (38, 49, 60)
BTN_HOVER = (52, 67, 82)
BTN_OFF = (28, 35, 43)
INPUT_BG = (16, 22, 28)

CARD_ASPECT = 1.45  # высота / ширина

# Геометрия рамки вокруг руки в сетке (в долях ширины карты).
HAND_PAD_X = 0.14
HAND_PAD_Y = 0.12
LABEL_RATIO = 0.34       # полоска под рукой с названием комбинации
HAND_SPAN = 5.28         # 5 карт + 4 промежутка по 0.07
FRAME_W_FACTOR = HAND_SPAN + 2 * HAND_PAD_X
FRAME_H_FACTOR = CARD_ASPECT * (1 + LABEL_RATIO) + 2 * HAND_PAD_Y

# --- тайминги анимации (секунды) -------------------------------------------
#
# Одна рука должна проигрываться примерно за полсекунды, 29 рук - за 3-5.
# Поэтому шаг между руками не фиксированный, а сжимается, когда рук много:
# 3 руки - 0.9с, 9 рук - 2.2с, 15 рук - 3.4с, 29 рук - 4.0с.

FLIP_TIME = 0.18         # переворот одной карты
FLIP_STAGGER = 0.05      # задержка между соседними картами при сдаче
MAIN_DRAW_TIME = 4 * FLIP_STAGGER + FLIP_TIME   # главная рука целиком
REVEAL_BUDGET = 3.50     # максимум на все дополнительные руки
REVEAL_STEP_MAX = 0.22   # шаг между руками, когда их мало
HAND_POP = 0.18          # вскрытие одной дополнительной руки
CARD_POP_STAGGER = 0.022


# --- шрифты ----------------------------------------------------------------


class Fonts:
    """Подбирает системный шрифт с кириллицей и, по возможности, с мастями.

    Сначала пробуются конкретные файлы, потому что `match_font("Segoe UI")`
    на Windows норовит вернуть Segoe UI Light — на тёмном фоне он слишком
    тонкий. Для жирного начертания берётся отдельный файл, а не синтетический
    bold, если такой файл есть.
    """

    FILES = (
        ("segoeui.ttf", "segoeuib.ttf"),
        ("tahoma.ttf", "tahomabd.ttf"),
        ("verdana.ttf", "verdanab.ttf"),
        ("arial.ttf", "arialbd.ttf"),
        ("DejaVuSans.ttf", "DejaVuSans-Bold.ttf"),
    )
    NAMES = ("Segoe UI", "Tahoma", "Verdana", "DejaVu Sans", "Arial", "Liberation Sans")

    def __init__(self) -> None:
        self.path: str | None = None
        self.bold_path: str | None = None
        self.symbols = False
        for regular, bold in self._candidates():
            if not regular:
                continue
            try:
                probe = pygame.font.Font(regular, 24)
            except Exception:
                continue
            if probe.metrics("Б")[0] is None:
                continue  # нет кириллицы - не подходит
            self.path = regular
            self.bold_path = bold if bold and os.path.isfile(bold) else None
            self.symbols = probe.metrics(SUIT_SYMBOLS[0])[0] is not None
            break
        self._cache: dict[tuple[int, bool], pygame.font.Font] = {}

    def _candidates(self):
        font_dir = os.path.join(os.environ.get("WINDIR", r"C:\Windows"), "Fonts")
        for regular, bold in self.FILES:
            path = os.path.join(font_dir, regular)
            if os.path.isfile(path):
                yield path, os.path.join(font_dir, bold)
        for name in self.NAMES:
            yield pygame.font.match_font(name), pygame.font.match_font(name, bold=True)

    def get(self, size: float, bold: bool = False) -> pygame.font.Font:
        key = (max(8, int(size)), bold)
        font = self._cache.get(key)
        if font is None:
            if bold and self.bold_path:
                font = pygame.font.Font(self.bold_path, key[0])
            elif self.path:
                font = pygame.font.Font(self.path, key[0])
                font.set_bold(bold)
            else:  # крайний случай: встроенный шрифт pygame
                font = pygame.font.SysFont(None, int(key[0] * 1.3), bold=bold)
            self._cache[key] = font
        return font


FONTS: Fonts = None  # type: ignore[assignment]  # создаётся после pygame.font.init()


# Кадр содержит до двухсот строк текста, а картинка почти всегда статична,
# поэтому готовые поверхности кэшируются: это разница между 60 и 200 fps.
_TEXT_CACHE: dict[tuple, pygame.Surface] = {}


def render_text(value: str, size: float, color: tuple[int, int, int],
                bold: bool) -> pygame.Surface:
    key = (value, max(8, int(size)), bold, color)
    image = _TEXT_CACHE.get(key)
    if image is None:
        if len(_TEXT_CACHE) > 4000:
            _TEXT_CACHE.clear()
        image = FONTS.get(size, bold).render(value, True, color)
        _TEXT_CACHE[key] = image
    return image


def text(
    surf: pygame.Surface,
    value: str,
    size: float,
    color: tuple[int, int, int],
    bold: bool = False,
    max_width: float | None = None,
    **anchor,
) -> pygame.Rect:
    """Нарисовать строку. `anchor` — любой якорь pygame.Rect (center=, topleft=...)."""
    if max_width is not None:
        font = FONTS.get(size, bold)
        if font.size(value)[0] > max_width:
            value = clip_text(value, font, max_width)
    image = render_text(value, size, color, bold)
    rect = image.get_rect(**(anchor or {"topleft": (0, 0)}))
    surf.blit(image, rect)
    return rect


def clip_text(value: str, font: pygame.font.Font, max_width: float) -> str:
    if font.size(value)[0] <= max_width:
        return value
    while value and font.size(value + "…")[0] > max_width:
        value = value[:-1]
    return value + "…"


def fmt_money(value: int) -> str:
    sign = "-" if value < 0 else ""
    return f"{sign}${abs(value):,}".replace(",", " ")


def fmt_signed(value: int) -> str:
    return ("+" + fmt_money(value)) if value > 0 else fmt_money(value)


def panel(surf: pygame.Surface, rect: pygame.Rect, s: float, color=PANEL, edge=PANEL_EDGE) -> None:
    radius = max(4, int(10 * s))
    pygame.draw.rect(surf, color, rect, border_radius=radius)
    pygame.draw.rect(surf, edge, rect, width=1, border_radius=radius)


# --- карты -----------------------------------------------------------------


def card_label(card: Card) -> str:
    suit = SUIT_SYMBOLS[card.suit] if FONTS.symbols else SUIT_LETTERS[card.suit]
    return f"{card.rank_label}{suit}"


def draw_card(
    surf: pygame.Surface,
    rect: pygame.Rect,
    card: Card,
    held: bool = False,
    wild: bool = False,
    big: bool = False,
) -> None:
    radius = max(3, int(min(rect.w, rect.h) * 0.12))
    pygame.draw.rect(surf, CARD_WILD_FACE if wild else CARD_FACE, rect, border_radius=radius)
    ink = CARD_RED if card.is_red else CARD_BLACK
    suit = SUIT_SYMBOLS[card.suit] if FONTS.symbols else SUIT_LETTERS[card.suit]

    if big:
        text(surf, card.rank_label, rect.h * 0.30, ink, bold=True,
             center=(rect.centerx, rect.top + rect.h * 0.32))
        text(surf, suit, rect.h * 0.30, ink,
             center=(rect.centerx, rect.top + rect.h * 0.70))
        if wild:
            text(surf, "W", rect.h * 0.13, GOLD, bold=True,
                 topleft=(rect.x + rect.w * 0.09, rect.y + rect.h * 0.05))
    else:
        label = card_label(card)
        size = int(min(rect.h * 0.40, rect.w * 0.52))
        font = FONTS.get(size, True)
        while size > 7 and font.size(label)[0] > rect.w * 0.88:
            size -= 1
            font = FONTS.get(size, True)
        text(surf, label, size, ink, bold=True, center=rect.center)

    if held:
        pygame.draw.rect(surf, GOLD_BRIGHT, rect, width=max(2, int(rect.w * 0.055)),
                         border_radius=radius)
    else:
        pygame.draw.rect(surf, CARD_EDGE, rect, width=1, border_radius=radius)


def draw_card_back(surf: pygame.Surface, rect: pygame.Rect) -> None:
    radius = max(3, int(min(rect.w, rect.h) * 0.12))
    pygame.draw.rect(surf, CARD_BACK, rect, border_radius=radius)
    inner = rect.inflate(-max(4, rect.w * 0.20), -max(4, rect.h * 0.14))
    pygame.draw.rect(surf, CARD_BACK_INNER, inner, border_radius=max(2, radius // 2))
    pygame.draw.rect(surf, PANEL_EDGE, rect, width=1, border_radius=radius)


def hand_width(card_w: float) -> float:
    """Ширина пятикарточной руки вместе с промежутками."""
    return 5 * card_w + 4 * max(1.0, card_w * 0.07)


def clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def hand_frame_size(card_w: float) -> tuple[float, float]:
    """Размер рамки вокруг одной руки: карты плюс подпись плюс поля."""
    return card_w * FRAME_W_FACTOR, card_w * FRAME_H_FACTOR


def fit_grid(count: int, area: pygame.Rect, s: float) -> tuple[float, int, int, float]:
    """Подобрать сетку рук так, чтобы карты вышли максимально крупными."""
    gap = max(3.0, 7 * s)
    best = (0.0, 1, count, gap)
    for cols in range(1, count + 1):
        rows = math.ceil(count / cols)
        cell_w = (area.w - gap * (cols - 1)) / cols
        cell_h = (area.h - gap * (rows - 1)) / rows
        if cell_w <= 0 or cell_h <= 0:
            continue
        card_w = min(cell_w / FRAME_W_FACTOR, cell_h / FRAME_H_FACTOR)
        if card_w > best[0]:
            best = (card_w, cols, rows, gap)
    return best


def draw_card_flip(surf: pygame.Surface, rect: pygame.Rect, card: Card, progress: float,
                   held: bool = False, wild: bool = False, big: bool = False) -> None:
    """Переворот карты: рубашка сжимается в полоску и раскрывается лицом."""
    squeeze = abs(math.cos(math.pi * max(0.0, min(1.0, progress))))
    width = max(2.0, rect.w * squeeze)
    slim = R(rect.centerx - width / 2, rect.y, width, rect.h)
    if progress < 0.5:
        draw_card_back(surf, slim)
    else:
        draw_card(surf, slim, card, held=held, wild=wild, big=big and width > rect.w * 0.6)


def R(x: float, y: float, w: float, h: float) -> pygame.Rect:
    return pygame.Rect(int(x), int(y), int(max(1, w)), int(max(1, h)))


def hands_word(count: int) -> str:
    return plural(count, "рука", "руки", "рук")


def maximize_window() -> bool:
    """Развернуть окно средствами ОС. Окно остаётся обычным, его можно свернуть."""
    try:
        import ctypes

        hwnd = pygame.display.get_wm_info().get("window")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 3)  # SW_MAXIMIZE
            return True
    except Exception:
        pass
    return False




def plural(count: int, one: str, few: str, many: str) -> str:
    """Русское склонение: 1 рука, 2 руки, 5 рук."""
    if count % 100 in (11, 12, 13, 14):
        return many
    last = count % 10
    if last == 1:
        return one
    if last in (2, 3, 4):
        return few
    return many


@dataclass
class Hotspot:
    rect: pygame.Rect
    action: Callable[[], None]


class UI:
    """Контекст кадра: поверхность, мышь и собранные за кадр кликабельные зоны.

    Кнопка сразу получает функцию, которую надо дёрнуть по клику, поэтому
    никакой диспетчеризации по строковым кодам в столах нет.
    """

    def __init__(self, screen: pygame.Surface) -> None:
        self.screen = screen
        self.mouse: tuple[int, int] = (0, 0)
        self.hotspots: list[Hotspot] = []
        self.scale = 1.0

    def begin(self, screen: pygame.Surface, mouse: tuple[int, int]) -> float:
        self.screen = screen
        self.mouse = mouse
        self.hotspots = []
        width, height = screen.get_size()
        self.scale = max(0.62, min(width / 1600, height / 950, 1.9))
        return self.scale

    def spot(self, rect: pygame.Rect, action: Callable[[], None] | None,
             enabled: bool = True) -> None:
        if enabled and action is not None:
            self.hotspots.append(Hotspot(rect, action))

    def click(self, pos: tuple[int, int]) -> bool:
        for hotspot in reversed(self.hotspots):
            if hotspot.rect.collidepoint(pos):
                hotspot.action()
                return True
        return False

    def hovered(self, rect: pygame.Rect) -> bool:
        return bool(rect.collidepoint(self.mouse))

    def button(
        self,
        rect: pygame.Rect,
        label: str,
        action: Callable[[], None] | None,
        enabled: bool = True,
        active: bool = False,
        accent: bool = False,
        size: float = 14,
        bold: bool = False,
        colors: tuple[tuple[int, int, int], tuple[int, int, int]] | None = None,
    ) -> pygame.Rect:
        hovered = enabled and self.hovered(rect)
        if not enabled:
            bg, fg = BTN_OFF, FAINT
        elif colors is not None:
            bg, fg = colors
            if hovered:
                bg = tuple(min(255, channel + 26) for channel in bg)
        elif active or accent:
            bg, fg = (GOLD_BRIGHT if hovered else GOLD), BG
        else:
            bg, fg = (BTN_HOVER if hovered else BTN), TEXT
        radius = max(3, int(6 * self.scale))
        pygame.draw.rect(self.screen, bg, rect, border_radius=radius)
        if not (active or accent or colors):
            pygame.draw.rect(self.screen, PANEL_EDGE, rect, width=1, border_radius=radius)
        text(self.screen, label, size, fg, bold=bold or active or accent,
             center=rect.center, max_width=rect.w - 8)
        self.spot(rect, action, enabled)
        return rect
