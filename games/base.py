"""Общий интерфейс стола и поле ставки, которым пользуются все игры."""

from __future__ import annotations

import pygame

from bank import MIN_BET, Bank
from widgets import (FAINT, GOLD, INPUT_BG, MUTED, PANEL_EDGE, TEXT, R, UI,
                     fmt_money, text)


class Table:
    """Стол казино. Шелл отдаёт ему области экрана и ничего больше не знает."""

    key = "table"
    title = "Стол"

    def __init__(self, bank: Bank) -> None:
        self.bank = bank

    @property
    def busy(self) -> bool:
        """Идёт раздача или анимация: уходить со стола и сбрасывать сессию нельзя."""
        return False

    def tick(self) -> None:
        """Вызывается каждый кадр: анимации, доначисление выигрышей."""

    def reset(self) -> None:
        """Новая сессия."""

    def handle_key(self, event: pygame.event.Event) -> None:
        pass

    def click_missed(self) -> None:
        """Клик мимо всех кнопок — обычно снимает фокус с поля ставки."""

    def controls_height(self, s: float) -> float:
        return 178 * s

    def info_height(self, s: float) -> float:
        return 300 * s

    def render_table(self, ui: UI, area: pygame.Rect, s: float) -> None:
        raise NotImplementedError

    def render_info(self, ui: UI, area: pygame.Rect, s: float) -> None:
        raise NotImplementedError

    def render_controls(self, ui: UI, area: pygame.Rect, s: float) -> None:
        raise NotImplementedError

    def status(self) -> tuple[str, tuple[int, int, int]]:
        return ("", MUTED)


class BetBox:
    """Поле ставки с кнопками умножения и деления.

    Первая набранная цифра заменяет значение целиком, а не дописывается к нему.
    """

    WIDTH = 200
    HEIGHT = 104

    def __init__(self, bet: int = MIN_BET, minimum: int = MIN_BET) -> None:
        self.minimum = minimum
        self.bet = max(minimum, bet)
        self.text = str(self.bet)
        self.focus = False
        self.fresh = False
        # Стол может запретить менять ставку, пока раздача в игре.
        self.can_edit = None

    @property
    def editable(self) -> bool:
        return self.can_edit is None or self.can_edit()

    def set(self, value: int) -> None:
        if not self.editable:
            return
        self.bet = max(self.minimum, int(value))
        self.text = str(self.bet)

    def scale(self, mult: int, div: int) -> None:
        self.commit()
        self.set(self.bet * mult // div)

    def commit(self) -> None:
        self.focus = False
        self.fresh = False
        if not self.editable:
            self.text = str(self.bet)
            return
        digits = "".join(ch for ch in self.text if ch.isdigit())
        self.set(int(digits) if digits else 0)

    def handle_key(self, event: pygame.event.Event) -> bool:
        """True, если клавиша ушла в поле ставки и стол её обрабатывать не должен."""
        if not self.focus:
            return False
        if not self.editable:
            self.focus = False
            return False
        if event.key in (pygame.K_RETURN, pygame.K_KP_ENTER, pygame.K_ESCAPE, pygame.K_TAB):
            self.commit()
        elif event.key == pygame.K_BACKSPACE:
            self.text = "" if self.fresh else self.text[:-1]
            self.fresh = False
        elif event.unicode.isdigit():
            if self.fresh:
                self.text = ""
                self.fresh = False
            if len(self.text) < 12:
                self.text = (self.text + event.unicode).lstrip("0")
        return True

    def render(self, ui: UI, rect: pygame.Rect, s: float, enabled: bool = True,
               label: str = "СТАВКА") -> None:
        screen = ui.screen
        gap = int(8 * s)
        text(screen, label, 11 * s, MUTED, topleft=(rect.x, rect.y))
        box = R(rect.x, rect.y + 16 * s, rect.w, 46 * s)
        focused = self.focus and enabled
        pygame.draw.rect(screen, INPUT_BG, box, border_radius=int(6 * s))
        pygame.draw.rect(screen, GOLD if focused else PANEL_EDGE, box,
                         width=2 if focused else 1, border_radius=int(6 * s))

        digits = "".join(ch for ch in self.text if ch.isdigit())
        shown = fmt_money(int(digits)) if digits else "$0"
        if focused and (pygame.time.get_ticks() // 500) % 2 == 0:
            shown += "|"
        text(screen, shown, 22 * s, TEXT if enabled else FAINT, bold=True,
             midleft=(box.x + 12 * s, box.centery), max_width=box.w - 20 * s)

        def focus() -> None:
            self.focus = True
            self.fresh = True

        ui.spot(box, focus, enabled)

        button_w = (rect.w - 3 * gap) / 4
        y = box.bottom + gap
        for index, (title, mult, div) in enumerate(
            (("/100", 1, 100), ("/10", 1, 10), ("x10", 10, 1), ("x100", 100, 1))
        ):
            ui.button(R(rect.x + index * (button_w + gap), y, button_w, 34 * s), title,
                      (lambda m=mult, d=div: self.scale(m, d)), enabled=enabled, size=13 * s)
