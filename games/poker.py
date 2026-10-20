"""Видеопокер: два режима, до 29 рук за раздачу, прогрессивный джекпот.

Правила раздачи при нескольких руках:

* главная рука тянет замену из своей же колоды, поэтому сброшенная карта в ней
  больше не появится;
* каждая дополнительная рука берёт свежую колоду минус закреплённые карты;
* ставка списывается за каждую руку.

Деньги за выигравшие руки начисляются по мере вскрытия, а не все сразу: пока
руки открываются, баланс растёт вместе с ними.
"""

from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from enum import Enum, auto

import pygame

from bank import MIN_BET, Bank
from cards import WILD_RANK, Card, Deck
from evaluator import CATEGORY_NAMES, CATEGORY_SHORT, Category, classify
from games.base import BetBox, Table
from paytable import MODES, PayTable
from widgets import (BG, CARD_ASPECT, FAINT, FELT, FELT_EDGE, GOLD, GOLD_BRIGHT,
                     GOLD_DIM, GREEN, GREEN_DIM, HAND_FRAME, HAND_FRAME_EDGE,
                     HAND_FRAME_WAIT, HAND_PAD_X, HAND_PAD_Y, LABEL_RATIO, MUTED, RED,
                     TEXT, R, UI, clamp, draw_card, draw_card_back, draw_card_flip,
                     fit_grid, fmt_money, fmt_signed, hand_frame_size, hands_word,
                     panel, text)

HAND_COUNTS: tuple[int, ...] = (1, 3, 5, 9, 15, 21, 29)

# Тайминги: одна рука примерно за полсекунды, 29 рук - за четыре.
FLIP_TIME = 0.18
FLIP_STAGGER = 0.05
MAIN_DRAW_TIME = 4 * FLIP_STAGGER + FLIP_TIME
REVEAL_BUDGET = 3.50
REVEAL_STEP_MAX = 0.22
HAND_POP = 0.18
CARD_POP_STAGGER = 0.022


class Phase(Enum):
    IDLE = auto()
    HOLDING = auto()
    RESULT = auto()


@dataclass
class HandResult:
    cards: list[Card]
    category: Category
    win: int
    jackpot: bool = False


class PokerTable(Table):
    key = "poker"
    title = "Видеопокер"

    def __init__(self, bank: Bank) -> None:
        super().__init__(bank)
        self.betbox = BetBox()
        self.betbox.can_edit = lambda: self.free
        self.hands = 1
        self.mode: PayTable = MODES[0]
        self.phase = Phase.IDLE
        self.dealt: list[Card] = []
        self.holds = [False] * 5
        self.results: list[HandResult] = []
        self.deck: Deck | None = None

        self.round_bet = MIN_BET
        self.round_hands = 1
        self.round_mode: PayTable = MODES[0]
        self.round_share = 0          # сколько ушло в джекпот в этой раздаче
        self.round_win = 0
        self.credited = 0             # сколько рук уже оплачено банком
        self.recorded = True

        self.anim_kind: str | None = None
        self.anim_start = 0
        self.anim_duration = 0.0

    # --- состояние ----------------------------------------------------------

    @property
    def bet(self) -> int:
        return self.betbox.bet

    @property
    def total_bet(self) -> int:
        return self.bet * self.hands

    @property
    def busy(self) -> bool:
        return self.phase == Phase.HOLDING or self.animating

    @property
    def can_deal(self) -> bool:
        return self.phase != Phase.HOLDING and self.bank.can_afford(self.total_bet)

    @property
    def free(self) -> bool:
        """Можно менять ставку, режим и количество рук."""
        return not self.busy

    def reset(self) -> None:
        self.stop_anim()
        self.betbox.set(MIN_BET)
        self.hands = 1
        self.clear_table()

    def clear_table(self) -> None:
        self.dealt = []
        self.holds = [False] * 5
        self.results = []
        self.deck = None
        self.phase = Phase.IDLE
        self.recorded = True

    def set_hands(self, count: int) -> None:
        if self.free:
            self.hands = count

    def set_mode(self, mode: PayTable) -> None:
        if self.free and mode is not self.mode:
            self.mode = mode
            self.clear_table()

    def toggle_hold(self, index: int) -> None:
        if self.phase == Phase.HOLDING and 0 <= index < 5:
            self.holds[index] = not self.holds[index]

    # --- анимация -----------------------------------------------------------

    @property
    def anim_elapsed(self) -> float:
        return (pygame.time.get_ticks() - self.anim_start) / 1000

    @property
    def animating(self) -> bool:
        return self.anim_kind is not None and self.anim_elapsed < self.anim_duration

    @staticmethod
    def reveal_step(extra: int) -> float:
        """Шаг между вскрытием соседних рук: чем их больше, тем быстрее."""
        return min(REVEAL_STEP_MAX, REVEAL_BUDGET / extra) if extra > 0 else 0.0

    def start_anim(self, kind: str) -> None:
        self.anim_kind = kind
        self.anim_start = pygame.time.get_ticks()
        if kind == "deal":
            self.anim_duration = MAIN_DRAW_TIME
        else:
            extra = max(0, self.round_hands - 1)
            self.anim_duration = MAIN_DRAW_TIME + self.reveal_step(extra) * extra + HAND_POP

    def stop_anim(self) -> None:
        self.anim_kind = None

    def hand_progress(self, index: int) -> float:
        """Насколько вскрыта рука с таким номером (0 - главная): от 0 до 1."""
        if not self.animating or self.anim_kind != "draw":
            return 1.0
        elapsed = self.anim_elapsed
        if index == 0:
            return clamp(elapsed / MAIN_DRAW_TIME)
        step = self.reveal_step(self.round_hands - 1)
        return clamp((elapsed - MAIN_DRAW_TIME - (index - 1) * step) / HAND_POP)

    def revealed_results(self) -> list[HandResult]:
        if self.phase != Phase.RESULT:
            return []
        if not (self.animating and self.anim_kind == "draw"):
            return list(self.results)
        return [result for index, result in enumerate(self.results)
                if self.hand_progress(index) >= 1]

    def revealed_win(self) -> int:
        return sum(result.win for result in self.revealed_results())

    # --- игра ---------------------------------------------------------------

    def primary(self) -> None:
        if self.animating:
            self.stop_anim()  # досматривать анимацию никто не обязан
            return
        if self.phase == Phase.HOLDING:
            self.draw()
        else:
            self.deal()

    def deal(self) -> bool:
        self.betbox.commit()
        if not self.can_deal or not self.bank.take(self.total_bet):
            return False
        self.round_bet = self.bet
        self.round_hands = self.hands
        self.round_mode = self.mode
        self.round_share = self.bank.contribute(self.total_bet)
        self.round_win = 0
        self.credited = 0
        self.recorded = False

        self.deck = Deck()
        self.dealt = self.deck.deal(5)
        self.holds = [False] * 5
        self.results = []
        self.phase = Phase.HOLDING
        self.start_anim("deal")
        return True

    def draw(self) -> bool:
        if self.phase != Phase.HOLDING or self.deck is None:
            return False
        wild = self.round_mode.wild
        held = [card for card, hold in zip(self.dealt, self.holds) if hold]

        # Главная рука: добор из той же колоды, сброшенные карты не вернутся.
        main = list(self.dealt)
        for index, hold in enumerate(self.holds):
            if not hold:
                main[index] = self.deck.deal_one()
        self.results = [self._score(main, wild)]

        # Остальные руки: каждая из своей свежей колоды без закреплённых карт.
        for _ in range(self.round_hands - 1):
            deck = Deck(exclude=held)
            hand = [card if hold else deck.deal_one()
                    for card, hold in zip(self.dealt, self.holds)]
            self.results.append(self._score(hand, wild))

        self._apply_jackpot()
        self.round_win = sum(result.win for result in self.results)
        self.credited = 0
        self.deck = None
        self.phase = Phase.RESULT
        self.start_anim("draw")
        return True

    def _score(self, hand: list[Card], wild: bool) -> HandResult:
        category = classify(hand, wild)
        return HandResult(cards=hand, category=category,
                          win=self.round_mode.payout(category) * self.round_bet)

    def _apply_jackpot(self) -> None:
        """Первый натуральный роял забирает джекпот, если тот больше выплаты."""
        prize = self.bank.jackpot
        for result in self.results:
            if result.category == Category.ROYAL_FLUSH and prize > result.win:
                result.win = prize
                result.jackpot = True
                break

    def tick(self) -> None:
        """Начислить выигрыши по вскрытым рукам и записать раунд в историю."""
        if self.phase != Phase.RESULT:
            return
        target = len(self.revealed_results())
        while self.credited < target:
            result = self.results[self.credited]
            if result.jackpot:
                self.bank.claim_jackpot()
            self.bank.give(result.win)
            self.credited += 1
        if not self.recorded and not self.animating and self.credited >= len(self.results):
            best = max(result.category for result in self.results)
            tag = (f"{self.round_hands} {hands_word(self.round_hands)} × "
                   f"{fmt_money(self.round_bet)} · {CATEGORY_SHORT[best]}")
            self.bank.record(self.key, tag, self.round_bet * self.round_hands, self.round_win)
            self.recorded = True

    # --- ввод ---------------------------------------------------------------

    def handle_key(self, event: pygame.event.Event) -> None:
        if self.betbox.handle_key(event):
            return
        if pygame.K_1 <= event.key <= pygame.K_5:
            self.toggle_hold(event.key - pygame.K_1)
        elif pygame.K_KP1 <= event.key <= pygame.K_KP5:
            self.toggle_hold(event.key - pygame.K_KP1)
        elif event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.primary()

    def click_missed(self) -> None:
        self.betbox.commit()

    # --- правая панель ------------------------------------------------------

    def info_height(self, s: float) -> float:
        return (14 + 32 + 8 + 20 + len(self.mode.rows) * 25 + 46) * s

    def render_info(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        pad = int(14 * s)
        gap = int(8 * s)

        button_w = (area.w - 2 * pad - gap) / 2
        y = area.y + pad
        for index, mode in enumerate(MODES):
            ui.button(R(area.x + pad + index * (button_w + gap), y, button_w, 32 * s),
                      mode.title, (lambda m=mode: self.set_mode(m)),
                      enabled=self.free, active=(mode is self.mode), size=12.5 * s)
        y += int(32 * s) + int(6 * s)
        text(screen, f"{self.mode.rtp} · {self.mode.subtitle}", 11 * s, FAINT,
             topleft=(area.x + pad, y), max_width=area.w - 2 * pad)
        y += int(20 * s)

        # Счётчики считаются по уже вскрытым рукам, чтобы цифры набегали
        # вместе с анимацией.
        counts = Counter(result.category for result in self.revealed_results())
        best = max(counts, default=None)
        row_h = max(16, int(25 * s))
        mult_w = int(56 * s)
        odds_w = int(62 * s)

        for row in self.mode.rows:
            line = R(area.x + int(6 * s), y, area.w - int(12 * s), row_h - 2)
            if row.category == best:
                pygame.draw.rect(screen, GOLD_DIM, line, border_radius=int(4 * s))
            elif row.category in counts:
                pygame.draw.rect(screen, GREEN_DIM, line, border_radius=int(4 * s))

            jackpot_row = (row.category == Category.ROYAL_FLUSH
                           and self.bank.jackpot > row.multiplier * self.bet)
            name_colour = GOLD_BRIGHT if row.category == best else TEXT
            text(screen, row.title, 12.5 * s, name_colour, bold=(row.category == best),
                 midleft=(line.x + int(8 * s), line.centery),
                 max_width=line.w - mult_w - odds_w - int(18 * s))

            number = counts.get(row.category, 0)
            if number:
                text(screen, f"выпало {number}", 10.5 * s, GOLD_BRIGHT, bold=True,
                     midright=(line.right - mult_w - int(6 * s), line.centery))
            else:
                text(screen, row.odds, 10.5 * s, FAINT,
                     midright=(line.right - mult_w - int(6 * s), line.centery))

            payout = "ДЖЕКПОТ" if jackpot_row else f"x{row.multiplier}"
            text(screen, payout, 12 * s if jackpot_row else 13 * s,
                 GOLD_BRIGHT if (jackpot_row or row.category == best) else GOLD,
                 bold=True, midright=(line.right - int(8 * s), line.centery))
            y += row_h

        y += int(6 * s)
        stats = self.bank.stats.get(self.key)
        share = f"возврат {stats.rtp * 100:.1f}%" if stats and stats.rtp else "ещё не играли"
        text(screen, f"ваш взнос в джекпот: {fmt_money(self.bank.jackpot_paid_in)}",
             10.5 * s, FAINT, topleft=(area.x + pad, y), max_width=area.w - 2 * pad)
        text(screen, f"покер: {share}", 10.5 * s, FAINT,
             topleft=(area.x + pad, y + int(14 * s)), max_width=area.w - 2 * pad)

    # --- нижняя панель ------------------------------------------------------

    def render_controls(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        pad = int(16 * s)
        gap = int(8 * s)
        free = self.free
        label_y = area.y + int(12 * s)
        row_y = label_y + int(18 * s)

        bet_w = int(BetBox.WIDTH * s)
        self.betbox.render(ui, R(area.x + pad, label_y, bet_w, BetBox.HEIGHT * s), s,
                           enabled=free, label="СТАВКА НА ОДНУ РУКУ")

        action_w = int(216 * s)
        action_x = area.right - pad - action_w
        holding = self.phase == Phase.HOLDING
        if self.animating and self.anim_kind == "draw":
            label, enabled = "ПРОПУСТИТЬ", True
        elif holding:
            label, enabled = "ЗАМЕНИТЬ", True
        else:
            label, enabled = "РАЗДАТЬ", self.can_deal
        action = R(action_x, row_y, action_w, 58 * s)
        ui.button(action, label, self.primary, enabled=enabled, accent=True,
                  size=20 * s, bold=True)
        text(screen, "пробел", 10.5 * s, FAINT,
             center=(action.centerx, action.bottom + 13 * s))

        hands_x = area.x + pad + bet_w + int(26 * s)
        hands_w = action_x - int(26 * s) - hands_x
        text(screen, "КОЛИЧЕСТВО РУК", 11 * s, MUTED, topleft=(hands_x, label_y))
        button_w = (hands_w - gap * (len(HAND_COUNTS) - 1)) / len(HAND_COUNTS)
        for index, count in enumerate(HAND_COUNTS):
            ui.button(R(hands_x + index * (button_w + gap), row_y, button_w, 46 * s),
                      str(count), (lambda c=count: self.set_hands(c)), enabled=free,
                      active=(count == self.hands), size=17 * s, bold=True)

        if holding or (self.animating and self.anim_kind == "draw"):
            count_now, bet_now = self.round_hands, self.round_bet
            total, tail, colour = count_now * bet_now, " в игре", MUTED
        else:
            count_now, bet_now = self.hands, self.bet
            total, tail = self.total_bet, " за раздачу"
            colour = TEXT if total <= self.bank.balance else RED
        text(screen, f"{count_now} {hands_word(count_now)} × {fmt_money(bet_now)}"
                     f"  =  {fmt_money(total)}{tail}", 15 * s, colour, bold=True,
             topleft=(hands_x, row_y + int(46 * s) + gap + int(4 * s)), max_width=hands_w)

    def status(self) -> tuple[str, tuple[int, int, int]]:
        if self.animating and self.anim_kind == "draw":
            opened = len(self.revealed_results())
            return (f"Вскрываю руки: {opened} из {self.round_hands}. "
                    f"Пробел — показать всё сразу.", MUTED)
        if self.phase == Phase.HOLDING:
            return ("Клик по карте или клавиши 1-5 — закрепить. Потом ЗАМЕНИТЬ (пробел).", MUTED)
        if self.bank.is_broke:
            return ("Фишки кончились. «Новая сессия» вернёт $10 000.", RED)
        if self.total_bet > self.bank.balance:
            top = self.bank.balance // self.hands
            return (f"Нужно {fmt_money(self.total_bet)}, а в кармане "
                    f"{fmt_money(self.bank.balance)}: максимум {fmt_money(top)} на руку "
                    f"при {self.hands} {hands_word(self.hands)}.", RED)
        if self.phase == Phase.RESULT and self.results:
            best = max(result.category for result in self.results)
            net = self.round_win - self.round_bet * self.round_hands
            colour = GREEN if net > 0 else RED if net < 0 else MUTED
            return (f"Лучшая комбинация: {CATEGORY_NAMES[best]} · "
                    f"итог раунда {fmt_signed(net)}", colour)
        return ("Колода тасуется системным генератором ОС. Ставка списывается за каждую руку.",
                FAINT)

    # --- стол ---------------------------------------------------------------

    def render_table(self, ui: UI, area: pygame.Rect, s: float) -> None:
        panel(ui.screen, area, s, FELT, FELT_EDGE)
        inner = area.inflate(-int(24 * s), -int(20 * s))
        if self.phase == Phase.IDLE:
            self._idle(ui, inner, s)
            return

        extra = self.round_hands - 1
        main_area = R(inner.x, inner.y, inner.w, inner.h if not extra else inner.h * 0.46)
        self._main_hand(ui, main_area, s)
        if extra:
            grid = R(inner.x, main_area.bottom + 10 * s, inner.w,
                     inner.bottom - main_area.bottom - 10 * s)
            self._hand_grid(ui, grid, extra, s)

    def _idle(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        card_w = min(area.w * 0.12, area.h * 0.40 / CARD_ASPECT, 150 * s)
        card_h = card_w * CARD_ASPECT
        gap = max(4.0, card_w * 0.1)
        x0 = area.centerx - (5 * card_w + 4 * gap) / 2
        y0 = area.centery - card_h * 0.72
        for index in range(5):
            draw_card_back(screen, R(x0 + index * (card_w + gap), y0, card_w, card_h))

        text(screen, "Нажмите РАЗДАТЬ или пробел", 19 * s, (150, 200, 178), bold=True,
             center=(area.centerx, y0 + card_h + 34 * s), max_width=area.w)
        text(screen, f"{self.mode.title} · {self.mode.subtitle} · {self.mode.rtp}",
             12.5 * s, (104, 150, 132), center=(area.centerx, y0 + card_h + 60 * s),
             max_width=area.w)
        text(screen, f"в игре будет {self.hands} {hands_word(self.hands)} по "
                     f"{fmt_money(self.bet)} — списание {fmt_money(self.total_bet)}",
             12.5 * s, (104, 150, 132), center=(area.centerx, y0 + card_h + 82 * s),
             max_width=area.w)

    def _main_hand(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        holding = self.phase == Phase.HOLDING
        wild = self.round_mode.wild
        cards = self.dealt if holding else self.results[0].cards
        dealing = self.animating and self.anim_kind == "deal"
        drawing = self.animating and self.anim_kind == "draw"
        elapsed = self.anim_elapsed

        head_h = int(26 * s) if holding else int(52 * s)
        foot_h = int(38 * s)
        card_w = min(area.w * 0.145, max(10.0, area.h - head_h - foot_h) / CARD_ASPECT, 164 * s)
        card_h = card_w * CARD_ASPECT
        gap = max(4.0, card_w * 0.1)
        x0 = area.centerx - (5 * card_w + 4 * gap) / 2
        y0 = area.y + head_h + max(0.0, (area.h - head_h - foot_h - card_h) / 2)

        header = ("ГЛАВНАЯ РУКА" if self.round_hands == 1 else
                  f"ГЛАВНАЯ РУКА · ещё {self.round_hands - 1} "
                  f"{hands_word(self.round_hands - 1)} ниже")
        text(screen, header, 11 * s, (120, 170, 150), topleft=(area.x, area.y),
             max_width=area.w * 0.5)

        if not holding:
            # Пока руки вскрываются, итог считается только по уже открытым.
            total_bet = self.round_bet * self.round_hands
            won = self.revealed_win()
            net = won - total_bet
            colour = GREEN if net > 0 else RED if net < 0 else MUTED
            net_rect = text(screen, fmt_signed(net), 26 * s, colour, bold=True,
                            topright=(area.right, area.y - 2 * s))
            text(screen, f"ставка {fmt_money(total_bet)} → выплата {fmt_money(won)}",
                 11 * s, MUTED, topright=(area.right, net_rect.bottom + 2 * s))

        replaced = 0
        for index, card in enumerate(cards):
            card_rect = R(x0 + index * (card_w + gap), y0, card_w, card_h)
            held = self.holds[index]
            is_wild = wild and card.rank == WILD_RANK

            if dealing:
                progress = clamp((elapsed - index * FLIP_STAGGER) / FLIP_TIME)
                if progress <= 0:
                    continue  # эта карта ещё не сдана
                draw_card_flip(screen, card_rect, card, progress, held=held,
                               wild=is_wild, big=True)
            elif drawing and not held:
                progress = clamp((elapsed - replaced * FLIP_STAGGER) / FLIP_TIME)
                replaced += 1
                draw_card_flip(screen, card_rect, card, progress, wild=is_wild, big=True)
            else:
                draw_card(screen, card_rect, card, held=held, wild=is_wild, big=True)

            if holding:
                ui.spot(card_rect, (lambda i=index: self.toggle_hold(i)))
                text(screen, str(index + 1), 12 * s, (120, 170, 150),
                     center=(card_rect.centerx, card_rect.top - 11 * s))
                if held:
                    badge = R(card_rect.centerx - card_w * 0.33, card_rect.bottom + 7 * s,
                              card_w * 0.66, 21 * s)
                    pygame.draw.rect(screen, GOLD, badge, border_radius=int(4 * s))
                    text(screen, "ДЕРЖУ", 11 * s, BG, bold=True, center=badge.center)

        if not holding and self.hand_progress(0) >= 1:
            result = self.results[0]
            label = CATEGORY_NAMES[result.category]
            if result.jackpot:
                label = "ДЖЕКПОТ! " + label
            if result.win:
                label += f" · {fmt_signed(result.win)}"
            text(screen, label, 17 * s, GOLD_BRIGHT if result.win else MUTED, bold=True,
                 center=(area.centerx, y0 + card_h + foot_h * 0.5), max_width=area.w)

    def _hand_grid(self, ui: UI, area: pygame.Rect, extra: int, s: float) -> None:
        screen = ui.screen
        holding = self.phase == Phase.HOLDING
        wild = self.round_mode.wild
        drawing = self.animating and self.anim_kind == "draw"

        card_w, cols, rows, gap = fit_grid(extra, area, s)
        card_h = card_w * CARD_ASPECT
        label_h = card_h * LABEL_RATIO
        inner_gap = max(1.0, card_w * 0.07)
        pad_x = card_w * HAND_PAD_X
        pad_y = card_w * HAND_PAD_Y
        frame_w, frame_h = hand_frame_size(card_w)
        grid_h = rows * frame_h + (rows - 1) * gap
        oy = area.y + max(0.0, (area.h - grid_h) / 2)
        radius = max(3, int(card_w * 0.18))
        flip_window = max(0.05, HAND_POP - 4 * CARD_POP_STAGGER)

        for index in range(extra):
            row, col = divmod(index, cols)
            in_row = min(cols, extra - row * cols)
            row_w = in_row * frame_w + (in_row - 1) * gap
            fx = area.centerx - row_w / 2 + col * (frame_w + gap)
            fy = oy + row * (frame_h + gap)
            frame = R(fx, fy, frame_w, frame_h)

            progress = self.hand_progress(index + 1)
            opened = None if holding or progress <= 0 else self.results[index + 1]

            # Каждая рука живёт в своей рамке, иначе при 29 руках они сливаются.
            if opened is not None and opened.win:
                big_win = opened.category >= Category.FOUR_KIND
                fill, edge = (GOLD_DIM, GOLD) if big_win else (GREEN_DIM, GREEN)
            elif opened is not None:
                fill, edge = HAND_FRAME, HAND_FRAME_EDGE
            else:
                fill, edge = HAND_FRAME_WAIT, HAND_FRAME_EDGE
            pygame.draw.rect(screen, fill, frame, border_radius=radius)
            pygame.draw.rect(screen, edge, frame, width=1, border_radius=radius)

            for position in range(5):
                card_rect = R(fx + pad_x + position * (card_w + inner_gap), fy + pad_y,
                              card_w, card_h)
                held = self.holds[position]
                if opened is None:
                    if held:
                        card = self.dealt[position]
                        draw_card(screen, card_rect, card, held=True,
                                  wild=wild and card.rank == WILD_RANK)
                    else:
                        draw_card_back(screen, card_rect)
                    continue

                card = opened.cards[position]
                is_wild = wild and card.rank == WILD_RANK
                if drawing and progress < 1 and not held:
                    flip = clamp((progress * HAND_POP - position * CARD_POP_STAGGER)
                                 / flip_window)
                    draw_card_flip(screen, card_rect, card, flip, wild=is_wild)
                else:
                    draw_card(screen, card_rect, card, held=held, wild=is_wild)

            if opened is not None and progress >= 1:
                label = CATEGORY_SHORT[opened.category]
                if opened.win:
                    label += f"  {fmt_signed(opened.win)}"
                text(screen, label, min(label_h * 0.56, 14 * s),
                     GOLD_BRIGHT if opened.win else FAINT, bold=bool(opened.win),
                     center=(fx + frame_w / 2, fy + pad_y + card_h + label_h * 0.5),
                     max_width=frame_w - 2 * pad_x)
