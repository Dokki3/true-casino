"""Кости: два кубика, первый бросок и до трёх бросков на повтор числа.

Правила (ваши):

* первый бросок: 2, 3, 12 — проигрыш; 7 или 11 — выплата x2;
* любое другое число становится точкой, и дальше до трёх бросков:
  выпала точка — выплата x4, выпала семёрка — проигрыш;
* если за три броска не вышло ни точки, ни семёрки — ставка возвращается.

Отдача считается точной формулой (а не на глаз) и показана на экране: при
ваших числах это 137.7%, то есть стол в вашу пользу. Рядом лежит честный
пресет, где точка платит x1.75 и отдача выходит 99.2%.
"""

from __future__ import annotations

import random
from dataclasses import dataclass

import pygame

from bank import MIN_BET, Bank
from games.base import BetBox, Table
from widgets import (BG, FAINT, FELT, FELT_EDGE, GOLD, GOLD_BRIGHT, GREEN, MUTED, RED,
                     TEXT, R, UI, clamp, fmt_money, fmt_signed, panel, plural, text)

LOSE_COMEOUT = (2, 3, 12)
WIN_COMEOUT = (7, 11)
WAYS = {2: 1, 3: 2, 4: 3, 5: 4, 6: 5, 7: 6, 8: 5, 9: 4, 10: 3, 11: 2, 12: 1}

ROLL_TIME = 0.70       # кубики крутятся
SETTLE_TIME = 0.45     # пауза после остановки

_rng = random.SystemRandom()


@dataclass(frozen=True)
class Rules:
    """Выплаты — полный возврат: x2 это ставка плюс столько же."""

    key: str
    title: str
    comeout: float      # за 7 или 11 на первом броске
    point: float        # за повтор числа
    rolls: int          # сколько бросков даётся на повтор
    refund: bool        # возвращать ли ставку, если броски кончились

    def rtp(self) -> float:
        """Точная отдача: перебор всех 36 исходов и геометрия по броскам."""
        total = 0.0
        for roll, ways in WAYS.items():
            chance = ways / 36
            if roll in LOSE_COMEOUT:
                continue
            if roll in WIN_COMEOUT:
                total += chance * self.comeout
                continue
            hit, seven = ways / 36, 6 / 36
            carry = 1 - hit - seven
            survive = sum(carry ** step for step in range(self.rolls))
            total += chance * (hit * survive * self.point
                               + (carry ** self.rolls) * (1.0 if self.refund else 0.0))
        return total


AUTHOR_RULES = Rules("author", "Авторские", comeout=2, point=4, rolls=3, refund=True)
FAIR_RULES = Rules("fair", "Честные", comeout=2, point=1.75, rolls=3, refund=True)
RULE_SETS = (AUTHOR_RULES, FAIR_RULES)

PIPS = {
    1: ((1, 1),),
    2: ((0, 0), (2, 2)),
    3: ((0, 0), (1, 1), (2, 2)),
    4: ((0, 0), (2, 0), (0, 2), (2, 2)),
    5: ((0, 0), (2, 0), (1, 1), (0, 2), (2, 2)),
    6: ((0, 0), (2, 0), (0, 1), (2, 1), (0, 2), (2, 2)),
}


def draw_die(surf: pygame.Surface, rect: pygame.Rect, value: int, hot: bool = False) -> None:
    """Кубик рисуется кодом: скруглённый квадрат и точки по сетке 3x3."""
    radius = max(4, int(rect.w * 0.17))
    pygame.draw.rect(surf, (243, 240, 232), rect, border_radius=radius)
    pygame.draw.rect(surf, GOLD if hot else (188, 184, 174), rect,
                     width=max(2, int(rect.w * 0.035)) if hot else 1, border_radius=radius)
    pip_r = max(2, int(rect.w * 0.095))
    step = rect.w * 0.27
    cx, cy = rect.centerx, rect.centery
    for gx, gy in PIPS[value]:
        pygame.draw.circle(surf, (32, 38, 48),
                           (int(cx + (gx - 1) * step), int(cy + (gy - 1) * step)), pip_r)


class DiceTable(Table):
    key = "dice"
    title = "Кости"

    def __init__(self, bank: Bank) -> None:
        super().__init__(bank)
        self.betbox = BetBox()
        self.betbox.can_edit = lambda: self.phase in ('bets', 'result')
        self.rules = AUTHOR_RULES
        self.phase = "bets"          # bets | rolling | point | result
        self.dice = (1, 1)
        self.point: int | None = None
        self.rolls_left = 0
        self.rolls: list[tuple[int, int]] = []
        self.roll_start = 0
        self.bet = 0
        self.win = 0
        self.outcome = ""

    # --- состояние ----------------------------------------------------------

    @property
    def elapsed(self) -> float:
        return (pygame.time.get_ticks() - self.roll_start) / 1000

    @property
    def rolling(self) -> bool:
        return self.phase == "rolling"

    @property
    def busy(self) -> bool:
        return self.phase in ("rolling", "point")

    def reset(self) -> None:
        self.phase = "bets"
        self.point = None
        self.rolls = []
        self.betbox.set(MIN_BET)

    def set_rules(self, rules: Rules) -> None:
        if not self.busy:
            self.rules = rules

    # --- игра ---------------------------------------------------------------

    def roll(self) -> None:
        if self.rolling:
            return
        if self.phase in ("bets", "result"):
            self.betbox.commit()
            bet = self.betbox.bet
            if not self.bank.take(bet):
                return
            self.bet = bet
            self.win = 0
            self.outcome = ""
            self.point = None
            self.rolls = []
            self.rolls_left = self.rules.rolls
        elif self.rolls_left <= 0:
            return
        self.dice = (_rng.randint(1, 6), _rng.randint(1, 6))
        self.roll_start = pygame.time.get_ticks()
        self.phase = "rolling"

    def tick(self) -> None:
        if not self.rolling or self.elapsed < ROLL_TIME:
            return
        total = sum(self.dice)
        self.rolls.append(self.dice)

        if self.point is None:
            if total in LOSE_COMEOUT:
                self._finish(0, f"{total} на первом броске")
            elif total in WIN_COMEOUT:
                self._finish(self.rules.comeout, f"{total} сразу")
            else:
                self.point = total
                self.phase = "point"
            return

        self.rolls_left -= 1
        if total == 7:
            self._finish(0, "семёрка")
        elif total == self.point:
            self._finish(self.rules.point, f"повтор {total}")
        elif self.rolls_left <= 0:
            self._finish(1.0 if self.rules.refund else 0.0, "броски кончились")
        else:
            self.phase = "point"

    def _finish(self, multiplier: float, outcome: str) -> None:
        self.win = int(self.bet * multiplier)
        self.bank.give(self.win)
        self.outcome = outcome
        self.bank.record(self.key, f"{outcome} · {fmt_money(self.bet)}", self.bet, self.win)
        self.phase = "result"

    def handle_key(self, event: pygame.event.Event) -> None:
        if self.betbox.handle_key(event):
            return
        if event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.roll()

    def click_missed(self) -> None:
        self.betbox.commit()

    def status(self) -> tuple[str, tuple[int, int, int]]:
        if self.rolling:
            return ("Кубики в воздухе…", MUTED)
        if self.phase == "point":
            return (f"Точка {self.point}. Нужен повтор {self.point} — это выплата "
                    f"x{self.rules.point:g}. Семёрка проигрывает. "
                    f"Осталось {self.rolls_left} "
                    f"{plural(self.rolls_left, 'бросок', 'броска', 'бросков')}.", GOLD_BRIGHT)
        if self.phase == "result":
            net = self.win - self.bet
            colour = GREEN if net > 0 else RED if net < 0 else MUTED
            return (f"{self.outcome} · итог {fmt_signed(net)}", colour)
        if self.bank.is_broke:
            return ("Фишки кончились. «Новая сессия» вернёт $10 000.", RED)
        return ("Первый бросок: 2, 3, 12 — проигрыш, 7 и 11 — выплата, "
                "остальное становится точкой.", FAINT)

    # --- стол ---------------------------------------------------------------

    def render_table(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        panel(screen, area, s, FELT, FELT_EDGE)
        inner = area.inflate(-int(24 * s), -int(20 * s))

        size = min(inner.w * 0.17, inner.h * 0.34, 150 * s)
        gap = size * 0.28
        left = R(inner.centerx - size - gap / 2, inner.y + inner.h * 0.26, size, size)
        right = R(inner.centerx + gap / 2, left.y, size, size)

        if self.rolling:
            # Пока кубики крутятся, лица меняются: результат уже определён,
            # но игрок его ещё не видит.
            spin = pygame.time.get_ticks() // 70
            faces = (spin % 6 + 1, (spin * 5 + 3) % 6 + 1)
        else:
            faces = self.dice
        hot = self.phase == "result" and self.win > self.bet
        draw_die(screen, left, faces[0], hot)
        draw_die(screen, right, faces[1], hot)

        text(screen, str(sum(faces)), size * 0.44, FAINT if self.rolling else TEXT,
             bold=True, center=(inner.centerx, left.bottom + size * 0.26))

        if self.point is not None:
            marker = R(inner.centerx - size * 0.6, left.y - size * 0.44, size * 1.2, size * 0.32)
            pygame.draw.rect(screen, GOLD, marker, border_radius=int(6 * s))
            text(screen, f"ТОЧКА {self.point}", marker.h * 0.56, BG, bold=True,
                 center=marker.center)

        if self.phase == "result" and self.outcome:
            colour = GREEN if self.win > self.bet else RED if self.win < self.bet else MUTED
            text(screen, f"{self.outcome} · {fmt_signed(self.win - self.bet)}", 18 * s,
                 colour, bold=True, center=(inner.centerx, left.bottom + size * 0.62),
                 max_width=inner.w)

        if self.rolls:
            text(screen, "БРОСКИ РАУНДА", 11 * s, (120, 170, 150),
                 topleft=(inner.x, inner.bottom - 84 * s))
            small = min(30 * s, inner.w / 18)
            for index, pair in enumerate(self.rolls):
                x = inner.x + index * (small * 2.6)
                y = inner.bottom - 64 * s
                draw_die(screen, R(x, y, small, small), pair[0])
                draw_die(screen, R(x + small * 1.15, y, small, small), pair[1])
                text(screen, str(sum(pair)), 11 * s, FAINT,
                     center=(x + small * 1.08, y + small + 9 * s))

    # --- правая панель ------------------------------------------------------

    def info_height(self, s: float) -> float:
        return (14 + 24 + 36 + 8 + 6 * 23 + 52) * s

    def render_info(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        pad = int(14 * s)
        gap = int(8 * s)
        y = area.y + int(10 * s)
        title = text(screen, "Кости", 17 * s, GOLD, bold=True, topleft=(area.x + pad, y))
        rtp = self.rules.rtp()
        text(screen, f"отдача {rtp * 100:.1f}%", 11 * s,
             GOLD_BRIGHT if rtp > 1 else MUTED, midright=(area.right - pad, title.centery))
        y += int(24 * s)

        button_w = (area.w - 2 * pad - gap) / 2
        for index, rules in enumerate(RULE_SETS):
            ui.button(R(area.x + pad + index * (button_w + gap), y, button_w, 30 * s),
                      rules.title, (lambda r=rules: self.set_rules(r)),
                      enabled=not self.busy, active=(rules is self.rules), size=12.5 * s)
        y += int(36 * s)

        rows = (
            ("7 или 11 сразу", f"x{self.rules.comeout:g}", "8 из 36"),
            ("2, 3 или 12 сразу", "0", "4 из 36"),
            ("Повтор точки", f"x{self.rules.point:g}", "до 5 из 36"),
            ("Семёрка после точки", "0", "6 из 36"),
            ("Броски кончились", "x1" if self.rules.refund else "0", "возврат"),
            ("Бросков на повтор", str(self.rules.rolls), ""),
        )
        row_h = max(15, int(23 * s))
        for name, payout, chance in rows:
            line = R(area.x + int(6 * s), y, area.w - int(12 * s), row_h - 2)
            text(screen, name, 12 * s, TEXT, midleft=(line.x + int(8 * s), line.centery),
                 max_width=line.w * 0.54)
            text(screen, chance, 10.5 * s, FAINT,
                 midright=(line.right - int(52 * s), line.centery))
            text(screen, payout, 12.5 * s, GOLD, bold=True,
                 midright=(line.right - int(8 * s), line.centery))
            y += row_h

        y += int(6 * s)
        note = ("отдача посчитана точной формулой по всем 36 исходам"
                if self.rules is FAIR_RULES else
                "x4 за повтор щедрее настоящего крэпса — отсюда перевес игрока")
        text(screen, note, 10.5 * s, FAINT, topleft=(area.x + pad, y),
             max_width=area.w - 2 * pad)
        stats = self.bank.stats.get(self.key)
        share = f"возврат {stats.rtp * 100:.1f}%" if stats and stats.rtp else "ещё не играли"
        text(screen, f"кости: {share}", 10.5 * s, FAINT,
             topleft=(area.x + pad, y + int(16 * s)), max_width=area.w - 2 * pad)

    def render_controls(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        pad = int(16 * s)
        label_y = area.y + int(12 * s)
        row_y = label_y + int(18 * s)
        free = self.phase in ("bets", "result")

        bet_w = int(BetBox.WIDTH * s)
        self.betbox.render(ui, R(area.x + pad, label_y, bet_w, BetBox.HEIGHT * s), s,
                           enabled=free, label="СТАВКА")

        action_w = int(216 * s)
        action_x = area.right - pad - action_w
        action = R(action_x, row_y, action_w, 58 * s)
        label = "ЕЩЁ БРОСОК" if self.phase == "point" else "БРОСИТЬ"
        enabled = not self.rolling and (not free or self.bank.can_afford(self.betbox.bet))
        ui.button(action, label, self.roll, enabled=enabled, accent=True, size=18 * s, bold=True)
        text(screen, "пробел", 10.5 * s, FAINT,
             center=(action.centerx, action.bottom + 13 * s))

        info_x = area.x + pad + bet_w + int(26 * s)
        text(screen, "РАУНД", 11 * s, MUTED, topleft=(info_x, label_y))
        if self.phase == "bets":
            line = f"ставка {fmt_money(self.betbox.bet)} · правила «{self.rules.title}»"
            colour = TEXT if self.bank.can_afford(self.betbox.bet) else RED
        elif self.point is not None and self.phase != "result":
            line = (f"в игре {fmt_money(self.bet)} · точка {self.point} · "
                    f"повтор платит {fmt_money(int(self.bet * self.rules.point))}")
            colour = GOLD_BRIGHT
        elif self.phase == "result":
            net = self.win - self.bet
            line = f"{self.outcome} · выплата {fmt_money(self.win)} · итог {fmt_signed(net)}"
            colour = GREEN if net > 0 else RED if net < 0 else MUTED
        else:
            line = f"в игре {fmt_money(self.bet)}"
            colour = TEXT
        text(screen, line, 15 * s, colour, bold=True,
             topleft=(info_x, row_y + int(10 * s)), max_width=action_x - info_x - int(26 * s))
