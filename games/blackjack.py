"""Блэкджек по лучшим из настоящих правил.

* шуз из 6 колод, перетасовка при проходе 75% — остаток виден, карты можно
  считать и честно получить перевес над заведением;
* блэкджек платит 3:2 (никогда 6:5 — именно на этой подмене
  современные казино забирают лишний процент);
* дилер стоит на мягких 17;
* дабл на любых двух картах и после сплита, сплит до четырёх рук,
  поздняя сдача, страховка 2:1.

С базовой стратегией такой набор даёт отдачу около 99.6%.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field

import pygame

from bank import MIN_BET, Bank
from cards import FULL_DECK, Card
from games.base import BetBox, Table
from widgets import (BG, CARD_ASPECT, FAINT, FELT, FELT_EDGE, GOLD, GOLD_BRIGHT, GREEN,
                     GREEN_DIM, GOLD_DIM, MUTED, RED, TEXT, R, UI, draw_card,
                     draw_card_back, draw_card_flip, fmt_money, fmt_signed, panel,
                     plural, text)

DECKS = 6
PENETRATION = 0.75
BLACKJACK_PAYOUT = 2.5      # ставка + 3:2
INSURANCE_PAYOUT = 3        # ставка страховки + 2:1
MAX_HANDS = 4

DEAL_STEP = 0.20            # пауза между картами начальной раздачи
DEALER_STEP = 0.48          # пауза между картами дилера
FLIP_TIME = 0.22

_rng = random.SystemRandom()


@dataclass
class Slot:
    """Карта на столе: когда легла и лежит ли рубашкой вверх."""

    card: Card
    at: int
    hidden: bool = False

    def reveal(self) -> None:
        self.hidden = False
        self.at = pygame.time.get_ticks()

    @property
    def pending(self) -> bool:
        """Карта ещё не легла на стол: её время не наступило."""
        return pygame.time.get_ticks() < self.at

    @property
    def progress(self) -> float:
        return min(1.0, max(0.0, (pygame.time.get_ticks() - self.at) / 1000 / FLIP_TIME))


class Shoe:
    """Шуз из нескольких колод с перетасовкой по пенетрации."""

    def __init__(self, decks: int = DECKS, penetration: float = PENETRATION) -> None:
        self.decks = decks
        self.penetration = penetration
        self.cards: list[Card] = []
        self.shuffle()

    @property
    def size(self) -> int:
        return self.decks * 52

    @property
    def left(self) -> int:
        return len(self.cards)

    @property
    def spent(self) -> int:
        return self.size - self.left

    @property
    def needs_shuffle(self) -> bool:
        return self.left <= self.size * (1 - self.penetration)

    def shuffle(self) -> None:
        self.cards = [card for _ in range(self.decks) for card in FULL_DECK]
        _rng.shuffle(self.cards)

    def deal(self) -> Card:
        if not self.cards:
            self.shuffle()
        return self.cards.pop()


def hand_value(cards) -> tuple[int, bool]:
    """Очки руки и признак «мягкая» (туз считается за 11)."""
    total = 0
    aces = 0
    for card in cards:
        if card.rank == 14:
            aces += 1
            total += 11
        elif card.rank >= 11:
            total += 10
        else:
            total += card.rank
    soft = False
    while total > 21 and aces:
        total -= 10
        aces -= 1
    if aces:
        soft = True
    return total, soft


@dataclass
class Hand:
    slots: list = field(default_factory=list)
    bet: int = 0
    done: bool = False
    doubled: bool = False
    surrendered: bool = False
    from_split: bool = False
    win: int = 0
    outcome: str = ""

    @property
    def cards(self) -> list[Card]:
        return [slot.card for slot in self.slots]

    @property
    def total(self) -> int:
        return hand_value(self.cards)[0]

    @property
    def soft(self) -> bool:
        return hand_value(self.cards)[1]

    @property
    def busted(self) -> bool:
        return self.total > 21

    @property
    def blackjack(self) -> bool:
        return len(self.slots) == 2 and self.total == 21 and not self.from_split

    @property
    def can_split(self) -> bool:
        if len(self.slots) != 2:
            return False
        first, second = self.cards
        value = lambda card: 10 if card.rank >= 11 and card.rank != 14 else card.rank
        return value(first) == value(second)

    def label(self) -> str:
        total, soft = hand_value(self.cards)
        if self.blackjack:
            return "блэкджек"
        if total > 21:
            return f"перебор {total}"
        return f"{'мягкие ' if soft else ''}{total}"


class BlackjackTable(Table):
    key = "blackjack"
    title = "Блэкджек"

    def __init__(self, bank: Bank) -> None:
        super().__init__(bank)
        self.betbox = BetBox()
        self.betbox.can_edit = lambda: self.phase in ('bets', 'result')
        self.shoe = Shoe()
        self.hands: list[Hand] = []
        self.dealer = Hand()
        self.active = 0
        self.phase = "bets"          # bets | dealing | insurance | player | dealer | result
        self.insurance = 0
        self.insurance_win = 0
        self.next_at = 0
        self.round_bet = 0
        self.round_win = 0

    # --- состояние ----------------------------------------------------------

    @property
    def busy(self) -> bool:
        return self.phase not in ("bets", "result")

    @property
    def hand(self) -> Hand | None:
        return self.hands[self.active] if 0 <= self.active < len(self.hands) else None

    @property
    def waiting(self) -> bool:
        return pygame.time.get_ticks() < self.next_at

    def reset(self) -> None:
        self.shoe = Shoe()
        self.hands = []
        self.dealer = Hand()
        self.phase = "bets"
        self.betbox.set(MIN_BET)

    def _wait(self, seconds: float) -> None:
        self.next_at = pygame.time.get_ticks() + int(seconds * 1000)

    # --- раздача ------------------------------------------------------------

    def deal(self) -> None:
        if self.phase not in ("bets", "result"):
            return
        self.betbox.commit()
        bet = self.betbox.bet
        if not self.bank.take(bet):
            return
        if self.shoe.needs_shuffle:
            self.shoe.shuffle()

        now = pygame.time.get_ticks()
        step = int(DEAL_STEP * 1000)
        player = Hand(bet=bet)
        self.hands = [player]
        self.dealer = Hand()
        self.active = 0
        self.round_bet = bet
        self.round_win = 0
        self.insurance = 0
        self.insurance_win = 0

        player.slots.append(Slot(self.shoe.deal(), now))
        self.dealer.slots.append(Slot(self.shoe.deal(), now + step))
        player.slots.append(Slot(self.shoe.deal(), now + 2 * step))
        self.dealer.slots.append(Slot(self.shoe.deal(), now + 3 * step, hidden=True))

        self.phase = "dealing"
        self._wait(4 * DEAL_STEP + FLIP_TIME)

    def _after_deal(self) -> None:
        """Карты розданы: страховка, проверка блэкджеков, ход игрока."""
        if self.dealer.cards[0].rank == 14 and self.bank.can_afford(self.round_bet // 2):
            self.phase = "insurance"
            return
        self._peek()

    def _peek(self) -> None:
        """Дилер смотрит в закрытую карту, если на столе туз или десятка."""
        upcard = self.dealer.cards[0]
        if upcard.rank >= 10 and self.dealer.total == 21:
            self.dealer.slots[1].reveal()
            self._settle()
            return
        if self.hands[0].blackjack:
            # Блэкджек игрока платится сразу: дилер уже не добирает.
            self.dealer.slots[1].reveal()
            self._settle()
            return
        self.phase = "player"

    def take_insurance(self, yes: bool) -> None:
        if self.phase != "insurance":
            return
        if yes:
            amount = self.round_bet // 2
            if self.bank.take(amount):
                self.insurance = amount
        self._peek()

    # --- ход игрока ---------------------------------------------------------

    @property
    def can_act(self) -> bool:
        return self.phase == "player" and self.hand is not None and not self.waiting

    def hit(self) -> None:
        hand = self.hand
        if not self.can_act:
            return
        hand.slots.append(Slot(self.shoe.deal(), pygame.time.get_ticks()))
        if hand.busted or hand.total == 21:
            self._finish_hand()
        else:
            self._wait(FLIP_TIME)

    def stand(self) -> None:
        if self.can_act:
            self._finish_hand()

    def double(self) -> None:
        hand = self.hand
        if not self.can_act or len(hand.slots) != 2 or not self.bank.take(hand.bet):
            return
        hand.bet *= 2
        hand.doubled = True
        hand.slots.append(Slot(self.shoe.deal(), pygame.time.get_ticks()))
        self._finish_hand()

    def surrender(self) -> None:
        hand = self.hand
        if not self.can_act or len(hand.slots) != 2 or hand.from_split or len(self.hands) > 1:
            return
        hand.surrendered = True
        self._finish_hand()

    def split(self) -> None:
        hand = self.hand
        if (not self.can_act or not hand.can_split or len(self.hands) >= MAX_HANDS
                or not self.bank.take(hand.bet)):
            return
        now = pygame.time.get_ticks()
        moved = hand.slots.pop()
        new_hand = Hand(slots=[moved], bet=hand.bet, from_split=True)
        hand.from_split = True
        self.hands.insert(self.active + 1, new_hand)
        hand.slots.append(Slot(self.shoe.deal(), now))
        new_hand.slots.append(Slot(self.shoe.deal(), now + int(DEAL_STEP * 1000)))
        self._wait(DEAL_STEP + FLIP_TIME)
        # Разбитые тузы получают по одной карте и больше не играют.
        if moved.card.rank == 14:
            for part in (hand, new_hand):
                part.done = True
            self._advance()

    def _finish_hand(self) -> None:
        self.hand.done = True
        self._advance()

    def _advance(self) -> None:
        for index, hand in enumerate(self.hands):
            if not hand.done:
                self.active = index
                self.phase = "player"
                self._wait(FLIP_TIME)
                return
        self._stand_all()

    def _stand_all(self) -> None:
        self.phase = "dealer"
        self.dealer.slots[1].reveal()
        self._wait(DEALER_STEP)

    # --- дилер и расчёт -----------------------------------------------------

    def tick(self) -> None:
        if self.waiting:
            return
        if self.phase == "dealing":
            self._after_deal()
        elif self.phase == "dealer":
            alive = any(not hand.busted and not hand.surrendered for hand in self.hands)
            total, _ = hand_value(self.dealer.cards)
            if alive and total < 17:   # дилер стоит на мягких 17
                self.dealer.slots.append(Slot(self.shoe.deal(), pygame.time.get_ticks()))
                self._wait(DEALER_STEP)
                return
            self._settle()

    def _settle(self) -> None:
        if self.dealer.slots[1].hidden:
            self.dealer.slots[1].reveal()
        dealer_bj = self.dealer.blackjack
        dealer_total = self.dealer.total
        total_win = 0

        for hand in self.hands:
            if hand.surrendered:
                hand.win, hand.outcome = hand.bet // 2, "сдача"
            elif hand.busted:
                hand.win, hand.outcome = 0, "перебор"
            elif hand.blackjack and not dealer_bj:
                hand.win, hand.outcome = int(hand.bet * BLACKJACK_PAYOUT), "блэкджек 3:2"
            elif dealer_bj and not hand.blackjack:
                hand.win, hand.outcome = 0, "блэкджек у дилера"
            elif dealer_bj and hand.blackjack:
                hand.win, hand.outcome = hand.bet, "пуш"
            elif dealer_total > 21:
                hand.win, hand.outcome = hand.bet * 2, "дилер перебрал"
            elif hand.total > dealer_total:
                hand.win, hand.outcome = hand.bet * 2, "выигрыш"
            elif hand.total == dealer_total:
                hand.win, hand.outcome = hand.bet, "пуш"
            else:
                hand.win, hand.outcome = 0, "проигрыш"
            total_win += hand.win

        if self.insurance:
            self.insurance_win = self.insurance * INSURANCE_PAYOUT if dealer_bj else 0
            total_win += self.insurance_win

        wagered = sum(hand.bet for hand in self.hands) + self.insurance
        self.bank.give(total_win)
        self.round_bet = wagered
        self.round_win = total_win
        if len(self.hands) == 1:
            tag = f"{fmt_money(self.hands[0].bet)} · {self.hands[0].outcome}"
        else:
            tag = (f"{len(self.hands)} {plural(len(self.hands), 'рука', 'руки', 'рук')} · "
                   f"дилер {dealer_total}")
        self.bank.record(self.key, tag, wagered, total_win)
        self.phase = "result"

    # --- ввод ---------------------------------------------------------------

    def handle_key(self, event: pygame.event.Event) -> None:
        if self.betbox.handle_key(event):
            return
        if self.phase == "insurance":
            if event.key in (pygame.K_y, pygame.K_SPACE):
                self.take_insurance(True)
            elif event.key in (pygame.K_n, pygame.K_ESCAPE):
                self.take_insurance(False)
            return
        if self.phase == "player":
            if event.key in (pygame.K_h, pygame.K_SPACE):
                self.hit()
            elif event.key in (pygame.K_s, pygame.K_RETURN, pygame.K_KP_ENTER):
                self.stand()
            elif event.key == pygame.K_d:
                self.double()
            elif event.key == pygame.K_p:
                self.split()
            elif event.key == pygame.K_u:
                self.surrender()
            return
        if event.key in (pygame.K_SPACE, pygame.K_RETURN, pygame.K_KP_ENTER):
            self.deal()

    def click_missed(self) -> None:
        self.betbox.commit()

    def status(self) -> tuple[str, tuple[int, int, int]]:
        if self.phase == "insurance":
            return (f"Дилер показывает туза. Страховка стоит "
                    f"{fmt_money(self.round_bet // 2)} и платит 2:1, если у него блэкджек. "
                    f"Математика против неё — брать невыгодно.", GOLD_BRIGHT)
        if self.phase == "player" and self.hand is not None:
            return (f"Ваш ход: {self.hand.label()}. "
                    f"H — взять, S — хватит, D — дабл, P — сплит, U — сдаться.", MUTED)
        if self.phase == "dealer":
            return ("Дилер добирает до 17…", MUTED)
        if self.phase == "result":
            net = self.round_win - self.round_bet
            colour = GREEN if net > 0 else RED if net < 0 else MUTED
            parts = " · ".join(hand.outcome for hand in self.hands)
            return (f"{parts} · итог {fmt_signed(net)}", colour)
        if self.bank.is_broke:
            return ("Фишки кончились. «Новая сессия» вернёт $10 000.", RED)
        return (f"Шуз: {self.shoe.left} карт из {self.shoe.size}. "
                f"Перетасовка при проходе 75% — карты можно считать.", FAINT)

    # --- стол ---------------------------------------------------------------

    def render_table(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        panel(screen, area, s, FELT, FELT_EDGE)
        inner = area.inflate(-int(24 * s), -int(20 * s))
        self._shoe_meter(ui, R(inner.right - 210 * s, inner.y, 210 * s, 44 * s), s)

        if not self.hands:
            text(screen, "Нажмите РАЗДАТЬ или пробел", 19 * s, (150, 200, 178), bold=True,
                 center=(inner.centerx, inner.centery - 10 * s), max_width=inner.w)
            text(screen, "блэкджек 3:2 · дилер стоит на мягких 17 · шуз 6 колод",
                 12.5 * s, (104, 150, 132),
                 center=(inner.centerx, inner.centery + 18 * s), max_width=inner.w)
            return

        count = len(self.hands)
        card_w = min(inner.w * 0.095, inner.h * 0.30 / CARD_ASPECT, 128 * s)
        if count > 2:
            card_w *= 0.78

        self._draw_hand(ui, self.dealer, inner.centerx, inner.y + inner.h * 0.10, card_w, s,
                        title="ДИЛЕР")

        slot_w = inner.w / count
        player_y = inner.y + inner.h * 0.56
        for index, hand in enumerate(self.hands):
            active = index == self.active and self.phase == "player"
            self._draw_hand(ui, hand, inner.x + slot_w * (index + 0.5), player_y, card_w, s,
                            title=None, active=active)

    def _draw_hand(self, ui: UI, hand: Hand, cx: float, y: float, card_w: float,
                   s: float, title: str | None, active: bool = False) -> None:
        screen = ui.screen
        card_h = card_w * CARD_ASPECT
        offset = card_w * 0.74
        visible = [slot for slot in hand.slots if not slot.pending]
        if not visible:
            return
        width = card_w + offset * (len(visible) - 1)
        x0 = cx - width / 2

        if active:
            frame = R(x0 - 10 * s, y - 10 * s, width + 20 * s, card_h + 20 * s)
            pygame.draw.rect(screen, GOLD, frame, width=max(2, int(2 * s)),
                             border_radius=int(8 * s))

        if title:
            text(screen, title, 11 * s, (120, 170, 150), center=(cx, y - 14 * s))

        for index, slot in enumerate(visible):
            rect = R(x0 + index * offset, y, card_w, card_h)
            if slot.hidden:
                draw_card_back(screen, rect)
            elif slot.progress < 1:
                draw_card_flip(screen, rect, slot.card, slot.progress)
            else:
                draw_card(screen, rect, slot.card)

        hidden = any(slot.hidden for slot in hand.slots)
        if hidden:
            shown = [slot.card for slot in visible if not slot.hidden]
            total, _ = hand_value(shown)
            label = f"показывает {total}"
        else:
            label = hand.label()
        colour = TEXT
        if hand.outcome:
            label = f"{label} · {hand.outcome}"
            colour = GREEN if hand.win > hand.bet else RED if hand.win < hand.bet else MUTED
        text(screen, label, 14 * s, colour, bold=True,
             center=(cx, y + card_h + 16 * s), max_width=width + 60 * s)
        if hand.bet:
            extra = " (дабл)" if hand.doubled else ""
            text(screen, f"{fmt_money(hand.bet)}{extra}", 11 * s, GOLD_BRIGHT,
                 center=(cx, y + card_h + 32 * s))

    def _shoe_meter(self, ui: UI, rect: pygame.Rect, s: float) -> None:
        screen = ui.screen
        left, size = self.shoe.left, self.shoe.size
        text(screen, f"ШУЗ: {left} из {size} карт", 11 * s, (120, 170, 150),
             topright=(rect.right, rect.y))
        bar = R(rect.x, rect.y + 18 * s, rect.w, 8 * s)
        pygame.draw.rect(screen, (18, 48, 40), bar, border_radius=int(4 * s))
        filled = R(bar.x, bar.y, bar.w * left / size, bar.h)
        pygame.draw.rect(screen, GOLD if not self.shoe.needs_shuffle else RED, filled,
                         border_radius=int(4 * s))
        text(screen, f"≈{left / 52:.1f} колоды до перетасовки", 10.5 * s, FAINT,
             topright=(rect.right, bar.bottom + 4 * s))

    # --- правая панель и управление -----------------------------------------

    RULES = (
        ("Блэкджек", "3 : 2"),
        ("Обычный выигрыш", "1 : 1"),
        ("Страховка", "2 : 1"),
        ("Дилер стоит на мягких 17", ""),
        ("Дабл на любых двух и после сплита", ""),
        ("Сплит до четырёх рук", ""),
        ("Поздняя сдача — половина", ""),
    )

    def info_height(self, s: float) -> float:
        return (14 + 24 + 16 + len(self.RULES) * 23 + 58) * s

    def render_info(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        pad = int(14 * s)
        y = area.y + int(10 * s)
        title = text(screen, "Блэкджек", 17 * s, GOLD, bold=True, topleft=(area.x + pad, y))
        text(screen, "отдача ~99.6%", 11 * s, MUTED,
             midright=(area.right - pad, title.centery))
        y += int(24 * s)
        text(screen, f"шуз {DECKS} колод, перетасовка при проходе 75%", 11 * s, FAINT,
             topleft=(area.x + pad, y), max_width=area.w - 2 * pad)
        y += int(16 * s)

        row_h = max(15, int(23 * s))
        for name, odds in self.RULES:
            line = R(area.x + int(6 * s), y, area.w - int(12 * s), row_h - 2)
            text(screen, name, 12 * s, TEXT if odds else MUTED,
                 midleft=(line.x + int(8 * s), line.centery), max_width=line.w - int(70 * s))
            if odds:
                text(screen, odds, 12.5 * s, GOLD, bold=True,
                     midright=(line.right - int(8 * s), line.centery))
            y += row_h

        y += int(6 * s)
        text(screen, "остаток шуза виден — счёт карт работает", 10.5 * s, FAINT,
             topleft=(area.x + pad, y), max_width=area.w - 2 * pad)
        stats = self.bank.stats.get(self.key)
        share = f"возврат {stats.rtp * 100:.1f}%" if stats and stats.rtp else "ещё не играли"
        text(screen, f"блэкджек: {share}", 10.5 * s, FAINT,
             topleft=(area.x + pad, y + int(16 * s)), max_width=area.w - 2 * pad)

    def render_controls(self, ui: UI, area: pygame.Rect, s: float) -> None:
        screen = ui.screen
        pad = int(16 * s)
        gap = int(8 * s)
        label_y = area.y + int(12 * s)
        row_y = label_y + int(18 * s)
        free = self.phase in ("bets", "result")

        bet_w = int(BetBox.WIDTH * s)
        self.betbox.render(ui, R(area.x + pad, label_y, bet_w, BetBox.HEIGHT * s), s,
                           enabled=free, label="СТАВКА")

        action_w = int(216 * s)
        action_x = area.right - pad - action_w
        action = R(action_x, row_y, action_w, 58 * s)
        if self.phase == "insurance":
            half = action_w // 2 - gap // 2
            ui.button(R(action_x, row_y, half, 58 * s), "СТРАХОВКА",
                      (lambda: self.take_insurance(True)), accent=True, size=14 * s, bold=True)
            ui.button(R(action_x + half + gap, row_y, half, 58 * s), "НЕТ",
                      (lambda: self.take_insurance(False)), size=14 * s, bold=True)
            text(screen, "Y / N", 10.5 * s, FAINT,
                 center=(action.centerx, action.bottom + 13 * s))
        else:
            ui.button(action, "РАЗДАТЬ", self.deal,
                      enabled=free and self.bank.can_afford(self.betbox.bet),
                      accent=True, size=20 * s, bold=True)
            text(screen, "пробел", 10.5 * s, FAINT,
                 center=(action.centerx, action.bottom + 13 * s))

        hand = self.hand
        acting = self.phase == "player" and hand is not None
        buttons_x = area.x + pad + bet_w + int(26 * s)
        text(screen, "ХОД", 11 * s, MUTED, topleft=(buttons_x, label_y))
        width = (action_x - int(26 * s) - buttons_x - gap * 4) / 5
        options = (
            ("ВЗЯТЬ", self.hit, acting),
            ("ХВАТИТ", self.stand, acting),
            ("ДАБЛ", self.double,
             acting and len(hand.slots) == 2 and self.bank.can_afford(hand.bet)),
            ("СПЛИТ", self.split,
             acting and hand.can_split and len(self.hands) < MAX_HANDS
             and self.bank.can_afford(hand.bet)),
            ("СДАТЬСЯ", self.surrender,
             acting and len(hand.slots) == 2 and len(self.hands) == 1 and not hand.from_split),
        )
        for index, (label, action_fn, enabled) in enumerate(options):
            ui.button(R(buttons_x + index * (width + gap), row_y, width, 46 * s), label,
                      action_fn, enabled=bool(enabled), size=12.5 * s, bold=True)

        if self.phase == "result":
            net = self.round_win - self.round_bet
            colour = GREEN if net > 0 else RED if net < 0 else MUTED
            summary = f"дилер {self.dealer.total} · итог раунда {fmt_signed(net)}"
        elif acting:
            summary = (f"рука {self.active + 1} из {len(self.hands)} · "
                       f"{hand.label()} · ставка {fmt_money(hand.bet)}")
            colour = TEXT
        else:
            summary = f"ставка {fmt_money(self.betbox.bet)}"
            colour = TEXT if self.bank.can_afford(self.betbox.bet) else RED
        text(screen, summary, 15 * s, colour, bold=True,
             topleft=(buttons_x, row_y + int(46 * s) + gap + int(4 * s)),
             max_width=action_x - buttons_x - int(26 * s))
