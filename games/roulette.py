"""Европейская рулетка: одно зеро, 37 чисел.

Край заведения 2.70% на любой ставке — это прямое следствие того, что колесо
платит как за 36 чисел, а чисел 37. Включённое правило la partage возвращает
половину ставки на равных шансах при зеро и режет край на них до 1.35%.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field

import pygame

from bank import MIN_BET, Bank
from games.base import BetBox, Table
from widgets import (BG, FAINT, FELT, FELT_EDGE, GOLD, GOLD_BRIGHT, GREEN, MUTED, RED,
                     TEXT, R, UI, clamp, fmt_money, fmt_signed, panel, plural, text)

# Порядок чисел на настоящем европейском колесе.
WHEEL: tuple[int, ...] = (
    0, 32, 15, 19, 4, 21, 2, 25, 17, 34, 6, 27, 13, 36, 11, 30, 8, 23,
    10, 5, 24, 16, 33, 1, 20, 14, 31, 9, 22, 18, 29, 7, 28, 12, 35, 3, 26,
)
REDS = frozenset({1, 3, 5, 7, 9, 12, 14, 16, 18, 19, 21, 23, 25, 27, 30, 32, 34, 36})

TABLE_RED = (168, 44, 44)
TABLE_BLACK = (34, 42, 52)
TABLE_GREEN = (22, 96, 68)
CELL_EDGE = (74, 102, 92)

SPIN_TIME = 2.9     # секунды до остановки шарика
REST_TIME = 0.7     # пауза с подсветкой результата

_rng = random.SystemRandom()


def colour_of(number: int) -> tuple[int, int, int]:
    if number == 0:
        return TABLE_GREEN
    return TABLE_RED if number in REDS else TABLE_BLACK


@dataclass(frozen=True)
class Bet:
    """Ставка на столе: какие числа закрывает и во сколько раз платит.

    Выплата — полный возврат: x36 на число это классические 35:1 плюс ставка.
    """

    kind: str
    numbers: frozenset
    payout: int
    label: str

    @property
    def key(self) -> tuple:
        return (self.kind, tuple(sorted(self.numbers)))


def straight(n: int) -> Bet:
    return Bet("straight", frozenset({n}), 36, str(n))


def split(a: int, b: int) -> Bet:
    return Bet("split", frozenset({a, b}), 18, f"{a}/{b}")


def street(numbers) -> Bet:
    nums = sorted(numbers)
    return Bet("street", frozenset(nums), 12, f"{nums[0]}-{nums[-1]}")


def corner(numbers) -> Bet:
    nums = sorted(numbers)
    return Bet("corner", frozenset(nums), 9, f"каре {nums[0]}")


def sixline(numbers) -> Bet:
    nums = sorted(numbers)
    return Bet("line", frozenset(nums), 6, f"{nums[0]}-{nums[-1]}")


def column(index: int) -> Bet:
    nums = frozenset(n for n in range(1, 37) if n % 3 == (index + 1) % 3)
    return Bet("column", nums, 3, f"колонка {index + 1}")


def dozen(index: int) -> Bet:
    nums = frozenset(range(index * 12 + 1, index * 12 + 13))
    return Bet("dozen", nums, 3, f"дюжина {index + 1}")


EVEN_BETS: tuple[Bet, ...] = (
    Bet("even", frozenset(range(1, 19)), 2, "1-18"),
    Bet("even", frozenset(n for n in range(1, 37) if n % 2 == 0), 2, "чёт"),
    Bet("even", frozenset(REDS), 2, "красное"),
    Bet("even", frozenset(n for n in range(1, 37) if n not in REDS), 2, "чёрное"),
    Bet("even", frozenset(n for n in range(1, 37) if n % 2 == 1), 2, "нечёт"),
    Bet("even", frozenset(range(19, 37)), 2, "19-36"),
)


def _outside(rect: pygame.Rect) -> tuple[float, float]:
    """Фишка на внешней ставке лежит у края клетки, чтобы не закрыть подпись."""
    return rect.x + rect.w * 0.13, rect.centery


@dataclass
class Geometry:
    """Геометрия сукна: где какая клетка. Пересчитывается каждый кадр."""

    cells: dict = field(default_factory=dict)      # число -> прямоугольник
    columns: list = field(default_factory=list)    # три клетки "2:1"
    dozens: list = field(default_factory=list)
    evens: list = field(default_factory=list)

    def centre(self, number: int) -> tuple[float, float]:
        rect = self.cells[number]
        return rect.centerx, rect.centery

    def anchor(self, bet: Bet) -> tuple[float, float]:
        """Где лежит фишка этой ставки."""
        nums = sorted(bet.numbers)
        if bet.kind == "straight":
            return self.centre(nums[0])
        if bet.kind in ("split", "corner"):
            xs = [self.centre(n)[0] for n in nums]
            ys = [self.centre(n)[1] for n in nums]
            return sum(xs) / len(xs), sum(ys) / len(ys)
        if bet.kind == "street":
            rect = self.cells[nums[0]]      # нижняя клетка колонки
            return rect.centerx, rect.bottom
        if bet.kind == "line":
            rect = self.cells[nums[0]]
            return rect.right, rect.bottom
        if bet.kind == "column":
            return _outside(self.columns[(nums[0] - 1) % 3])
        if bet.kind == "dozen":
            return _outside(self.dozens[(nums[0] - 1) // 12])
        for index, bet_even in enumerate(EVEN_BETS):
            if bet_even.label == bet.label:
                return _outside(self.evens[index])
        return (0, 0)


def _neighbours(number: int) -> dict:
    col, row = (number - 1) // 3, 2 - (number - 1) % 3
    return {"col": col, "row": row,
            "left": number - 3, "right": number + 3,
            "up": number + 1, "down": number - 1}


def bet_at(number: int, rect: pygame.Rect, pos: tuple[int, int]) -> Bet:
    """Разобрать клик по клетке: центр - число, граница - сплит, угол - каре."""
    x, y = pos
    info = _neighbours(number)
    col, row = info["col"], info["row"]
    tol_x, tol_y = rect.w * 0.24, rect.h * 0.24
    left = x - rect.x < tol_x
    right = rect.right - x < tol_x
    top = y - rect.y < tol_y
    bottom = rect.bottom - y < tol_y

    outer_top = top and row == 0
    outer_bottom = bottom and row == 2
    if outer_top or outer_bottom:
        if left and col > 0:
            return sixline([col * 3 + k for k in (-2, -1, 0, 1, 2, 3)])
        if right and col < 11:
            return sixline([col * 3 + k for k in (1, 2, 3, 4, 5, 6)])
        return street([col * 3 + 1, col * 3 + 2, col * 3 + 3])

    if left and top and col > 0 and row > 0:
        return corner({number, number + 1, number - 3, number - 2})
    if left and bottom and col > 0 and row < 2:
        return corner({number, number - 1, number - 3, number - 4})
    if right and top and col < 11 and row > 0:
        return corner({number, number + 1, number + 3, number + 4})
    if right and bottom and col < 11 and row < 2:
        return corner({number, number - 1, number + 3, number + 2})

    if left:
        return split(number, info["left"]) if col > 0 else split(0, number)
    if right and col < 11:
        return split(number, info["right"])
    if top and row > 0:
        return split(number, info["up"])
    if bottom and row < 2:
        return split(number, info["down"])
    return straight(number)


class RouletteTable(Table):
    key = "roulette"
    title = "Рулетка"

    def __init__(self, bank: Bank) -> None:
        super().__init__(bank)
        self.chip = BetBox()
        self.chip.can_edit = lambda: not self.spinning
        self.bets: dict[tuple, tuple[Bet, int]] = {}
        self.last_bets: dict[tuple, tuple[Bet, int]] = {}
        self.la_partage = True
        self.result: int | None = None
        self.results: list[int] = []
        self.spin_start = 0
        self.settled = True
        self.round_total = 0
        self.round_win = 0

    # --- состояние ----------------------------------------------------------

    @property
    def staked(self) -> int:
        return sum(amount for _, amount in self.bets.values())

    @property
    def elapsed(self) -> float:
        return (pygame.time.get_ticks() - self.spin_start) / 1000

    @property
    def spinning(self) -> bool:
        return not self.settled

    @property
    def busy(self) -> bool:
        return self.spinning

    @property
    def edge(self) -> str:
        """Честный край заведения при текущих правилах."""
        return "край 2.70%, на равных шансах 1.35%" if self.la_partage else "край 2.70%"

    def reset(self) -> None:
        self.bets = {}
        self.last_bets = {}
        self.result = None
        self.results = []
        self.settled = True
        self.chip.set(MIN_BET)

    # --- ставки -------------------------------------------------------------

    def place(self, bet: Bet) -> None:
        if self.spinning:
            return
        self.chip.commit()
        amount = self.chip.bet
        if self.staked + amount > self.bank.balance:
            return  # нельзя поставить больше, чем лежит в кармане
        current = self.bets.get(bet.key)
        self.bets[bet.key] = (bet, (current[1] if current else 0) + amount)

    def clear_bets(self) -> None:
        if not self.spinning:
            self.bets = {}

    def repeat_bets(self) -> None:
        if self.spinning or not self.last_bets:
            return
        total = sum(amount for _, amount in self.last_bets.values())
        if total <= self.bank.balance:
            self.bets = dict(self.last_bets)

    def spin(self) -> None:
        if self.spinning or not self.bets:
            return
        total = self.staked
        if not self.bank.take(total):
            return
        self.round_total = total
        self.round_win = 0
        self.last_bets = dict(self.bets)
        self.result = _rng.choice(WHEEL)
        self.spin_start = pygame.time.get_ticks()
        self.settled = False

    def tick(self) -> None:
        if self.settled or self.elapsed < SPIN_TIME:
            return
        win = 0
        for bet, amount in self.bets.values():
            if self.result in bet.numbers:
                win += amount * bet.payout
            elif self.la_partage and bet.kind == "even" and self.result == 0:
                win += amount // 2  # la partage: половина возвращается
        self.round_win = win
        self.bank.give(win)
        count = len(self.bets)
        tag = (f"выпало {self.result} · {count} "
               f"{plural(count, 'ставка', 'ставки', 'ставок')}")
        self.bank.record(self.key, tag, self.round_total, win)
        self.results.insert(0, self.result)
        del self.results[24:]
        self.bets = {}
        self.settled = True

    def handle_key(self, event: pygame.event.Event) -> None:
        if self.chip.handle_key(event):
            return
        if event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.spin()
        elif event.key == pygame.K_BACKSPACE:
            self.clear_bets()
        elif event.key == pygame.K_r:
            self.repeat_bets()

    def click_missed(self) -> None:
        self.chip.commit()

    def status(self) -> tuple[str, tuple[int, int, int]]:
        if self.spinning:
            return ("Шарик в колесе…", MUTED)
        if self.result is not None and self.round_total:
            net = self.round_win - self.round_total
            colour = GREEN if net > 0 else RED if net < 0 else MUTED
            zero_note = ""
            if self.result == 0 and self.la_partage:
                zero_note = " · зеро: равные шансы вернули половину"
            return (f"Выпало {self.result} · итог {fmt_signed(net)}{zero_note}", colour)
        if self.bank.is_broke:
            return ("Фишки кончились. «Новая сессия» вернёт $10 000.", RED)
        return ("Клик по числу — ставка фишкой. По границе чисел — сплит, по углу — каре.",
                FAINT)

    # --- стол ---------------------------------------------------------------

    def ball_angle(self) -> float:
        if self.result is None:
            return -math.pi / 2
        index = WHEEL.index(self.result)
        end = -math.pi / 2 + index * (2 * math.pi / 37)
        if self.settled:
            return end
        eased = 1 - (1 - clamp(self.elapsed / SPIN_TIME)) ** 3
        return end - (1 - eased) * 6 * 2 * math.pi   # шесть оборотов с замедлением

    def render_table(self, ui: UI, area: pygame.Rect, s: float) -> None:
        panel(ui.screen, area, s, FELT, FELT_EDGE)
        inner = area.inflate(-int(24 * s), -int(20 * s))
        wheel_h = min(inner.h * 0.42, 300 * s)
        wheel_area = R(inner.x, inner.y, inner.w, wheel_h)
        felt_area = R(inner.x, wheel_area.bottom + 10 * s, inner.w,
                      inner.bottom - wheel_area.bottom - 10 * s)
        self._wheel(ui, wheel_area, s)
        self._felt(ui, felt_area, s)

    def _wheel(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        radius = max(36.0, min(area.h / 2 - 6 * s, 136 * s))
        cx = area.x + radius + 12 * s
        cy = area.centery
        step = 2 * math.pi / 37
        pygame.draw.circle(screen, (22, 28, 36), (int(cx), int(cy)), int(radius + 6 * s))

        for index, number in enumerate(WHEEL):
            start = -math.pi / 2 + (index - 0.5) * step
            points = [(cx, cy)]
            for part in range(5):
                angle = start + step * part / 4
                points.append((cx + math.cos(angle) * radius, cy + math.sin(angle) * radius))
            pygame.draw.polygon(screen, colour_of(number), points)
        pygame.draw.circle(screen, FELT_EDGE, (int(cx), int(cy)), int(radius), max(1, int(2 * s)))

        hub = radius * 0.58
        pygame.draw.circle(screen, (16, 44, 37), (int(cx), int(cy)), int(hub))
        pygame.draw.circle(screen, FELT_EDGE, (int(cx), int(cy)), int(hub), 1)

        angle = self.ball_angle()
        ball_r = radius * 0.80
        pygame.draw.circle(screen, (246, 246, 244),
                           (int(cx + math.cos(angle) * ball_r), int(cy + math.sin(angle) * ball_r)),
                           max(3, int(radius * 0.07)))

        if self.result is not None and self.settled:
            pygame.draw.circle(screen, colour_of(self.result), (int(cx), int(cy)), int(hub * 0.74))
            text(screen, str(self.result), hub * 0.8, (255, 255, 255), bold=True,
                 center=(cx, cy))
        else:
            text(screen, "…" if self.result is not None else "?", hub * 0.7, FAINT, bold=True,
                 center=(cx, cy))

        # Справа от колеса: последние числа и сумма на столе.
        right_x = cx + radius + 24 * s
        width = area.right - right_x
        if width < 120 * s:
            return
        text(screen, "ПОСЛЕДНИЕ ЧИСЛА", 11 * s, (120, 170, 150), topleft=(right_x, area.y))
        chip = min(34 * s, width / 12 - 4 * s)
        for index, number in enumerate(self.results[:24]):
            row, col = divmod(index, 12)
            rect = R(right_x + col * (chip + 4 * s), area.y + 20 * s + row * (chip + 4 * s),
                     chip, chip)
            pygame.draw.rect(screen, colour_of(number), rect, border_radius=int(4 * s))
            text(screen, str(number), chip * 0.46, (255, 255, 255), bold=True, center=rect.center)

        staked_y = area.y + 20 * s + 2 * (chip + 4 * s) + 14 * s
        text(screen, f"на столе {fmt_money(self.staked)}", 14 * s,
             GOLD_BRIGHT if self.staked else FAINT, bold=True, topleft=(right_x, staked_y))
        text(screen, self.edge, 11 * s, FAINT, topleft=(right_x, staked_y + 20 * s),
             max_width=width)

    def _felt(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        free = not self.spinning
        cell_w = area.w / 14
        cell_h = min(area.h / 4.6, cell_w * 1.05)
        outside_h = cell_h * 0.8
        ox = area.x
        oy = area.y + max(0.0, (area.h - (cell_h * 3 + outside_h * 2)) / 2)
        geom = Geometry(columns=[None, None, None])
        number_font = cell_h * 0.40

        def cell(rect: pygame.Rect, label: str, colour, action, font: float = 0.0) -> None:
            pygame.draw.rect(screen, colour, rect)
            pygame.draw.rect(screen, CELL_EDGE, rect, width=1)
            text(screen, label, font or number_font, (255, 255, 255), bold=True,
                 center=rect.center, max_width=rect.w - 4)
            ui.spot(rect, action, free)

        # Зеро: одна высокая клетка слева. По правой границе - сплиты 0/1, 0/2, 0/3.
        zero = R(ox, oy, cell_w, cell_h * 3)
        geom.cells[0] = zero

        def on_zero() -> None:
            x, y = ui.mouse
            if zero.right - x < zero.w * 0.24:
                third = min(2, max(0, int((y - zero.y) / max(1, zero.h / 3))))
                self.place(split(0, 3 - third))
            else:
                self.place(straight(0))

        cell(zero, "0", TABLE_GREEN, on_zero)

        for col in range(12):
            for row in range(3):
                number = col * 3 + (3 - row)
                rect = R(ox + cell_w * (1 + col), oy + cell_h * row, cell_w, cell_h)
                geom.cells[number] = rect
                cell(rect, str(number), colour_of(number),
                     (lambda n=number, r=rect: self.place(bet_at(n, r, ui.mouse))))

        for row in range(3):
            rect = R(ox + cell_w * 13, oy + cell_h * row, cell_w, cell_h)
            index = [2, 1, 0][row]         # верхний ряд - третья колонка
            geom.columns[index] = rect
            cell(rect, "2:1", (20, 60, 48), (lambda i=index: self.place(column(i))),
                 font=number_font * 0.7)

        dozen_y = oy + cell_h * 3
        for index in range(3):
            rect = R(ox + cell_w * (1 + index * 4), dozen_y, cell_w * 4, outside_h)
            geom.dozens.append(rect)
            cell(rect, f"{index * 12 + 1}-{index * 12 + 12}", (20, 60, 48),
                 (lambda i=index: self.place(dozen(i))), font=number_font * 0.62)

        even_y = dozen_y + outside_h
        for index, bet in enumerate(EVEN_BETS):
            rect = R(ox + cell_w * (1 + index * 2), even_y, cell_w * 2, outside_h)
            geom.evens.append(rect)
            if bet.label == "красное":
                colour = TABLE_RED
            elif bet.label == "чёрное":
                colour = TABLE_BLACK
            else:
                colour = (20, 60, 48)
            cell(rect, bet.label, colour, (lambda b=bet: self.place(b)),
                 font=number_font * 0.62)

        # Подсветка выигравших клеток и фишки поверх сукна.
        if self.result is not None and self.settled:
            win_rect = geom.cells.get(self.result)
            if win_rect:
                pygame.draw.rect(screen, GOLD_BRIGHT, win_rect, width=max(2, int(3 * s)))

        chip_r = max(7.0, cell_w * 0.26)
        for bet, amount in self.bets.values():
            x, y = geom.anchor(bet)
            pygame.draw.circle(screen, GOLD, (int(x), int(y)), int(chip_r))
            pygame.draw.circle(screen, BG, (int(x), int(y)), int(chip_r), max(1, int(s)))
            short = f"{amount // 1000}k" if amount >= 1000 else str(amount)
            text(screen, short, chip_r * 0.9, BG, bold=True, center=(x, y))

    # --- правая панель и управление -----------------------------------------

    PAYOUTS = (
        ("Число", "35 : 1", "1 из 37"),
        ("Сплит — две", "17 : 1", "2 из 37"),
        ("Стрит — три", "11 : 1", "3 из 37"),
        ("Каре — четыре", "8 : 1", "4 из 37"),
        ("Линия — шесть", "5 : 1", "6 из 37"),
        ("Колонка, дюжина", "2 : 1", "12 из 37"),
        ("Красное, чёт, 1-18", "1 : 1", "18 из 37"),
    )

    def info_height(self, s: float) -> float:
        return (14 + 20 + 34 + 16 + len(self.PAYOUTS) * 23 + 46) * s

    def render_info(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        pad = int(14 * s)
        y = area.y + int(10 * s)
        title = text(screen, "Европейская рулетка", 17 * s, GOLD, bold=True,
                     topleft=(area.x + pad, y), max_width=area.w * 0.68)
        text(screen, "одно зеро", 11 * s, MUTED, midright=(area.right - pad, title.centery))
        y += int(24 * s)

        ui.button(R(area.x + pad, y, area.w - 2 * pad, 30 * s),
                  f"la partage: {'включено' if self.la_partage else 'выключено'}",
                  (lambda: setattr(self, "la_partage", not self.la_partage)),
                  enabled=not self.spinning, active=self.la_partage, size=12 * s)
        y += int(34 * s)
        note = ("при зеро равные шансы возвращают половину — край на них 1.35%"
                if self.la_partage else "при зеро равные шансы сгорают целиком — край 2.70%")
        text(screen, note, 10.5 * s, FAINT, topleft=(area.x + pad, y), max_width=area.w - 2 * pad)
        y += int(16 * s)

        row_h = max(15, int(23 * s))
        for name, odds, chance in self.PAYOUTS:
            line = R(area.x + int(6 * s), y, area.w - int(12 * s), row_h - 2)
            text(screen, name, 12.5 * s, TEXT, midleft=(line.x + int(8 * s), line.centery),
                 max_width=line.w * 0.52)
            text(screen, chance, 10.5 * s, FAINT,
                 midright=(line.right - int(62 * s), line.centery))
            text(screen, odds, 12.5 * s, GOLD, bold=True,
                 midright=(line.right - int(8 * s), line.centery))
            y += row_h

        stats = self.bank.stats.get(self.key)
        share = f"возврат {stats.rtp * 100:.1f}%" if stats and stats.rtp else "ещё не играли"
        text(screen, f"рулетка: {share}", 10.5 * s, FAINT,
             topleft=(area.x + pad, y + int(8 * s)), max_width=area.w - 2 * pad)

    def render_controls(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        pad = int(16 * s)
        gap = int(8 * s)
        free = not self.spinning
        label_y = area.y + int(12 * s)
        row_y = label_y + int(18 * s)

        chip_w = int(BetBox.WIDTH * s)
        self.chip.render(ui, R(area.x + pad, label_y, chip_w, BetBox.HEIGHT * s), s,
                         enabled=free, label="НОМИНАЛ ФИШКИ")

        action_w = int(216 * s)
        action_x = area.right - pad - action_w
        action = R(action_x, row_y, action_w, 58 * s)
        ui.button(action, "ВРАЩАТЬ", self.spin, enabled=free and bool(self.bets),
                  accent=True, size=20 * s, bold=True)
        text(screen, "пробел", 10.5 * s, FAINT,
             center=(action.centerx, action.bottom + 13 * s))

        buttons_x = area.x + pad + chip_w + int(26 * s)
        text(screen, "СТАВКИ НА СТОЛЕ", 11 * s, MUTED, topleft=(buttons_x, label_y))
        button_w = int(150 * s)
        ui.button(R(buttons_x, row_y, button_w, 46 * s), "ОЧИСТИТЬ", self.clear_bets,
                  enabled=free and bool(self.bets), size=13 * s)
        ui.button(R(buttons_x + button_w + gap, row_y, button_w, 46 * s), "ПОВТОР",
                  self.repeat_bets, enabled=free and bool(self.last_bets), size=13 * s)

        total = self.staked
        colour = TEXT if total else FAINT
        text(screen, f"{len(self.bets)} "
                     f"{plural(len(self.bets), 'ставка', 'ставки', 'ставок')} "
                     f"на {fmt_money(total)}", 15 * s, colour, bold=True,
             topleft=(buttons_x, row_y + int(46 * s) + gap + int(4 * s)),
             max_width=action_x - buttons_x - int(26 * s))
