"""Каркас казино: верхняя панель, вкладки столов, общая история.

Столы ничего не знают друг о друге и о раскладке окна. Каждый получает свою
область и контекст интерфейса, а общий баланс берёт из банка.
"""

from __future__ import annotations

import pygame

import widgets as w
from bank import MIN_BET, Bank
from widgets import (BG, FAINT, GOLD, GOLD_BRIGHT, GREEN, MUTED, PANEL_EDGE, RED, TEXT,
                     R, UI, fmt_money, fmt_signed, maximize_window, panel, text)

TABLE_NAMES = {
    "poker": "ПОКЕР",
    "roulette": "РУЛЕТКА",
    "blackjack": "БЛЭКДЖЕК",
    "dice": "КОСТИ",
}


class Casino:
    def __init__(self) -> None:
        pygame.init()
        w.FONTS = w.Fonts()
        pygame.display.set_caption("Честное казино")
        # Иначе SDL иногда открывает окно на втором мониторе и разворачивает
        # его там же. Начало координат - основной экран.
        import os
        os.environ.setdefault("SDL_VIDEO_WINDOW_POS", "40,30")
        try:
            desktop = pygame.display.get_desktop_sizes()[0]
        except Exception:
            desktop = (1440, 900)
        start = (max(980, int(desktop[0] * 0.86)), max(660, int(desktop[1] * 0.84)))
        self.screen = pygame.display.set_mode(start, pygame.RESIZABLE)
        maximize_window()

        self.clock = pygame.time.Clock()
        self.bank = Bank()
        self.ui = UI(self.screen)

        from games.blackjack import BlackjackTable
        from games.dice import DiceTable
        from games.poker import PokerTable
        from games.roulette import RouletteTable

        self.tables = [
            PokerTable(self.bank),
            RouletteTable(self.bank),
            BlackjackTable(self.bank),
            DiceTable(self.bank),
        ]
        self.current = 0
        self.running = True

    @property
    def table(self):
        return self.tables[self.current]

    # --- цикл ---------------------------------------------------------------

    def run(self) -> None:
        while self.running:
            self.handle_events()
            self.render()
            pygame.display.flip()
            self.clock.tick(60)
        pygame.quit()

    def handle_events(self) -> None:
        mouse = pygame.mouse.get_pos()
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                self.running = False
            elif event.type == pygame.VIDEORESIZE:
                self.screen = pygame.display.get_surface()
            elif event.type == pygame.MOUSEBUTTONDOWN and event.button == 1:
                if not self.ui.click(event.pos):
                    self.table.click_missed()
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_TAB:
                    step = -1 if event.mod & pygame.KMOD_SHIFT else 1
                    self.switch((self.current + step) % len(self.tables))
                else:
                    self.table.handle_key(event)
        self.mouse = mouse

    def switch(self, index: int) -> None:
        if index != self.current and not self.table.busy:
            self.current = index

    def new_session(self) -> None:
        if self.table.busy:
            return
        self.bank.reset()
        for table in self.tables:
            table.reset()

    # --- отрисовка ----------------------------------------------------------

    def render(self) -> None:
        surface = pygame.display.get_surface()
        if surface is not None:
            self.screen = surface
        for table in self.tables:
            table.tick()
        s = self.ui.begin(self.screen, pygame.mouse.get_pos())
        screen = self.screen
        width, height = screen.get_size()
        screen.fill(BG)

        pad = int(10 * s)
        top = R(pad, pad, width - 2 * pad, 68 * s)
        bottom_h = int(self.table.controls_height(s))
        bottom = R(pad, height - bottom_h - pad, width - 2 * pad, bottom_h)
        right_w = int(max(292 * s, min(width * 0.27, 430 * s)))
        right = R(width - right_w - pad, top.bottom + pad, right_w,
                  bottom.top - top.bottom - 2 * pad)
        table_area = R(pad, top.bottom + pad, right.left - 2 * pad, right.height)

        self._top_bar(top, s)

        info_h = min(int(self.table.info_height(s)), int(right.h * 0.68))
        info = R(right.x, right.y, right.w, info_h)
        history = R(right.x, info.bottom + int(10 * s), right.w,
                    right.bottom - info.bottom - int(10 * s))
        panel(screen, info, s)
        self.table.render_info(self.ui, info, s)
        self._history(history, s)

        panel(screen, bottom, s)
        self.table.render_controls(self.ui, bottom, s)
        message, colour = self.table.status()
        text(screen, message, 12 * s, colour,
             midleft=(bottom.x + int(16 * s), bottom.bottom - int(18 * s)),
             max_width=bottom.w - int(32 * s))

        self.table.render_table(self.ui, table_area, s)

    def _top_bar(self, rect: pygame.Rect, s: float) -> None:
        screen = self.screen
        bank = self.bank
        panel(screen, rect, s)
        pad = int(16 * s)
        free = not self.table.busy

        text(screen, "БАЛАНС", 11 * s, MUTED, midleft=(rect.x + pad, rect.centery - 13 * s))
        balance = text(screen, fmt_money(bank.balance), 25 * s, TEXT, bold=True,
                       midleft=(rect.x + pad, rect.centery + 8 * s))
        net = bank.session_net
        colour = GREEN if net > 0 else RED if net < 0 else FAINT
        text(screen, f"за сессию {fmt_signed(net)}", 12 * s, colour,
             midleft=(balance.right + 12 * s, rect.centery + 9 * s))

        jack_x = max(balance.right + 160 * s, rect.x + 330 * s)
        text(screen, "ДЖЕКПОТ", 11 * s, MUTED, midleft=(jack_x, rect.centery - 13 * s))
        text(screen, fmt_money(bank.jackpot), 22 * s, GOLD_BRIGHT, bold=True,
             midleft=(jack_x, rect.centery + 8 * s))

        button_h = int(34 * s)
        gap = int(6 * s)
        new_w = int(118 * s)
        tab_w = int(112 * s)
        button_y = rect.centery - button_h // 2
        new_x = rect.right - pad - new_w
        tabs_x = new_x - gap * 3 - (tab_w * len(self.tables) + gap * (len(self.tables) - 1))

        for index, table in enumerate(self.tables):
            rect_tab = R(tabs_x + index * (tab_w + gap), button_y, tab_w, button_h)
            self.ui.button(rect_tab, TABLE_NAMES.get(table.key, table.key),
                           (lambda i=index: self.switch(i)),
                           enabled=free or index == self.current,
                           active=(index == self.current), size=12.5 * s)
        self.ui.button(R(new_x, button_y, new_w, button_h), "Новая сессия",
                       self.new_session, enabled=free, size=12 * s)

    def _history(self, rect: pygame.Rect, s: float) -> None:
        screen = self.screen
        bank = self.bank
        panel(screen, rect, s)
        pad = int(14 * s)
        header = text(screen, "ИСТОРИЯ", 12 * s, MUTED, bold=True,
                      topleft=(rect.x + pad, rect.y + int(10 * s)))
        text(screen, f"раундов: {bank.rounds}", 11 * s, FAINT,
             midright=(rect.right - pad, header.centery))

        footer_h = int(38 * s)
        row_h = int(44 * s)
        top = header.bottom + int(8 * s)
        capacity = max(0, int((rect.bottom - footer_h - top) // row_h))

        if not bank.history:
            text(screen, "Пока пусто — сделайте первую ставку.", 11.5 * s, FAINT,
                 center=(rect.centerx, top + int(28 * s)), max_width=rect.w - 2 * pad)

        for index, entry in enumerate(list(bank.history)[:capacity]):
            y = top + index * row_h
            if index:
                pygame.draw.line(screen, (33, 42, 52), (rect.x + pad, y), (rect.right - pad, y))
            colour = GREEN if entry.net > 0 else RED if entry.net < 0 else MUTED
            text(screen, entry.tag, 12.5 * s, TEXT,
                 midleft=(rect.x + pad, y + row_h * 0.32), max_width=rect.w * 0.58)
            text(screen, fmt_signed(entry.net), 14 * s, colour, bold=True,
                 midright=(rect.right - pad, y + row_h * 0.32))
            text(screen, TABLE_NAMES.get(entry.game, entry.game), 10.5 * s, FAINT,
                 midleft=(rect.x + pad, y + row_h * 0.72), max_width=rect.w * 0.42)
            text(screen, f"баланс {fmt_money(entry.balance_after)}", 10.5 * s, FAINT,
                 midright=(rect.right - pad, y + row_h * 0.72))

        wagered, returned = bank.wagered, bank.returned
        share = f"{returned / wagered * 100:.1f}%" if wagered else "—"
        footer_y = rect.bottom - footer_h / 2
        pygame.draw.line(screen, (33, 42, 52), (rect.x + pad, rect.bottom - footer_h),
                         (rect.right - pad, rect.bottom - footer_h))
        text(screen, f"поставлено {fmt_money(wagered)}", 10.5 * s, FAINT,
             midleft=(rect.x + pad, footer_y))
        text(screen, f"возврат {share}", 10.5 * s, FAINT,
             midright=(rect.right - pad, footer_y))
