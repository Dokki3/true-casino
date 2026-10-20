"""Проверки честности и корректности. Запуск: python tests.py

Зависимостей нет, pygame не нужен — тестируется только логика.

Главные тесты:

* полный перебор всех 2 598 960 пятикарточных рук и сверка количества каждой
  комбинации с известными математическими значениями;
* эвалюатор с дикими двойками сверяется с тупым полным перебором всех
  подстановок (проверка той оптимизации, на которой он работает);
* средняя выплата на реально раздаваемых колодах сверяется с точным
  математическим ожиданием из перебора — это проверка самой раздачи, а не
  только таблиц;
* равномерность перетасовки по критерию хи-квадрат;
* правила игры: списание ставки, закреплённые карты, независимость рук,
  баланс до копейки.
"""

from __future__ import annotations

import random
import sys
import time
from collections import Counter
from itertools import combinations, combinations_with_replacement

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame

from bank import JACKPOT_SEED, MIN_BET, START_BALANCE, Bank
from cards import FULL_DECK, RANKS, WILD_RANK, Card, Deck, parse_hand
from evaluator import Category, classify_natural, classify_wild
from games.blackjack import DECKS, BlackjackTable, hand_value
from games.dice import AUTHOR_RULES, FAIR_RULES, LOSE_COMEOUT, WIN_COMEOUT, DiceTable, Rules
from games.poker import HAND_COUNTS, Phase, PokerTable
from games.roulette import (EVEN_BETS, REDS, SPIN_TIME, WHEEL, RouletteTable, bet_at,
                            column, corner, dozen, sixline, split, straight, street)
from paytable import DEUCES_WILD, JACKS_OR_BETTER

pygame.init()

# Известные значения для 5 карт из 52 (любой учебник по теории вероятностей).
EXPECTED_COUNTS = {
    Category.ROYAL_FLUSH: 4,
    Category.STRAIGHT_FLUSH: 36,
    Category.FOUR_KIND: 624,
    Category.FULL_HOUSE: 3744,
    Category.FLUSH: 5108,
    Category.STRAIGHT: 10200,
    Category.THREE_KIND: 54912,
    Category.TWO_PAIR: 123552,
    Category.JACKS_OR_BETTER: 337920,   # пара J, Q, K, A: 1098240 * 4/13
    Category.PAIR: 760320,              # пара от 2 до 10
    Category.NOTHING: 1302540,
}
TOTAL_HANDS = 2598960

_enumeration: Counter | None = None


def enumeration() -> Counter:
    """Посчитать все руки один раз и переиспользовать в разных тестах."""
    global _enumeration
    if _enumeration is None:
        counts: Counter = Counter()
        for hand in combinations(FULL_DECK, 5):
            counts[classify_natural(hand)] += 1
        _enumeration = counts
    return _enumeration


def test_full_enumeration() -> str:
    counts = enumeration()
    assert sum(counts.values()) == TOTAL_HANDS, sum(counts.values())
    for category, expected in EXPECTED_COUNTS.items():
        got = counts[category]
        assert got == expected, f"{category.name}: получено {got}, ожидалось {expected}"
    assert set(counts) == set(EXPECTED_COUNTS), set(counts) ^ set(EXPECTED_COUNTS)
    return f"все {TOTAL_HANDS} рук разложены по комбинациям точно"


def exact_no_draw_return() -> float:
    """Точная отдача, если взять 5 карт и ничего не менять (режим Jacks or Better)."""
    counts = enumeration()
    total = sum(JACKS_OR_BETTER.payout(category) * number
                for category, number in counts.items())
    return total / TOTAL_HANDS


def test_deck_integrity() -> str:
    for _ in range(200):
        deck = Deck()
        assert len(deck) == 52
        cards = deck.deal(52)
        assert len(set(cards)) == 52, "в колоде дубликаты"
        assert set(cards) == set(FULL_DECK), "колода не совпадает с полной"
        assert len(deck) == 0

    hand = parse_hand("As Ks 7d")
    deck = Deck(exclude=hand)
    assert len(deck) == 49
    rest = deck.deal(49)
    assert not set(rest) & set(hand), "исключённые карты всё равно попали в колоду"
    return "колода полна, без дубликатов, exclude работает"


def test_shuffle_uniformity() -> str:
    """Верхняя карта должна быть равномерной по всем 52 вариантам."""
    trials = 52 * 250
    counts: Counter = Counter()
    for _ in range(trials):
        counts[Deck().deal_one()] += 1
    assert len(counts) == 52, f"не все карты побывали сверху: {len(counts)}"
    expected = trials / 52
    chi2 = sum((count - expected) ** 2 / expected for count in counts.values())
    # 51 степень свободы: при честной тасовке chi2 около 51, порог 110 это
    # примерно p < 0.000001, так что ложные срабатывания исключены.
    assert chi2 < 110, f"распределение верхней карты не похоже на равномерное: chi2={chi2:.1f}"
    return f"тасовка равномерна (chi2={chi2:.1f} при 51 степени свободы)"


def brute_force_wild(cards: list[Card]) -> Category:
    """Честный тупой перебор: каждая двойка становится любой из 52 карт.

    Медленно, зато очевидно правильно. Эталон для быстрого эвалюатора.
    """
    naturals = [card for card in cards if card.rank != WILD_RANK]
    wilds = len(cards) - len(naturals)
    if wilds == 0:
        return classify_natural(cards)
    if wilds == 4:
        return Category.FOUR_DEUCES
    best = Category.NOTHING
    for substitutes in combinations_with_replacement(FULL_DECK, wilds):
        category = classify_natural(naturals + list(substitutes))
        if category > best:
            best = category
    return Category.WILD_ROYAL if best == Category.ROYAL_FLUSH else best


def test_wild_matches_brute_force() -> str:
    rng = random.Random(20261020)
    deuces = [Card(WILD_RANK, suit) for suit in range(4)]
    others = [card for card in FULL_DECK if card.rank != WILD_RANK]
    checked = 0
    for wilds, samples in ((1, 120), (2, 60), (3, 10)):
        for _ in range(samples):
            hand = rng.sample(deuces, wilds) + rng.sample(others, 5 - wilds)
            fast = classify_wild(hand)
            slow = brute_force_wild(hand)
            assert fast == slow, (
                f"{[str(c) for c in hand]}: быстрый {fast.name}, перебор {slow.name}")
            checked += 1
    return f"{checked} рук с двойками совпали с полным перебором подстановок"


def test_wild_examples() -> str:
    cases = {
        "2s 3s 4s 5s 6s": Category.STRAIGHT_FLUSH,
        "2s 3s 4s 5s 9h": Category.PAIR,
        "2s Ks Qs Js Ts": Category.WILD_ROYAL,
        "As Ks Qs Js Ts": Category.ROYAL_FLUSH,
        "2s 2h 2d 2c 7h": Category.FOUR_DEUCES,
        "2s 2h 9d 9c 9h": Category.FIVE_KIND,
        "2s 2h Kd Kc 7h": Category.FOUR_KIND,
        "2s 7h 7d 7c Kh": Category.FOUR_KIND,
        "2s 8h 9d Tc Jh": Category.STRAIGHT,
        "2s 3h 7d 9c Kh": Category.JACKS_OR_BETTER,  # двойка встаёт в пару к королю
        "2h 2d 3s 4s 5s": Category.STRAIGHT_FLUSH,
        "2h 3s 5s 7s 9s": Category.FLUSH,
    }
    for notation, expected in cases.items():
        got = classify_wild(parse_hand(notation))
        assert got == expected, f"{notation}: получено {got.name}, ожидалось {expected.name}"

    # Натуральный роял не должен превращаться в "роял с двойкой" и наоборот.
    assert classify_wild(parse_hand("As Ks Qs Js Ts")) == Category.ROYAL_FLUSH
    assert JACKS_OR_BETTER.payout(Category.ROYAL_FLUSH) == 800
    assert DEUCES_WILD.payout(Category.WILD_ROYAL) == 25
    assert DEUCES_WILD.payout(Category.TWO_PAIR) == 0, "в Deuces Wild пары не платят"
    assert JACKS_OR_BETTER.payout(Category.PAIR) == 0, "младшая пара не платит"
    return f"{len(cases)} эталонных рук с джокерами разобраны верно"


def test_dealing_matches_math() -> str:
    """Самая важная проверка: реальная раздача против точной математики.

    Для стратегии "взял пять карт и ничего не меняю" точная отдача считается
    перебором всех рук. Если бы колода была подкручена, средняя выплата на
    реально раздаваемых колодах отъехала бы от этого числа.
    """
    exact = exact_no_draw_return()
    rounds = 150_000
    total = 0
    for _ in range(rounds):
        total += JACKS_OR_BETTER.payout(classify_natural(Deck().deal(5)))
    observed = total / rounds
    assert abs(observed - exact) < 0.02, (
        f"математика даёт {exact:.4f}, раздача дала {observed:.4f}")
    return (f"без замены: математика {exact * 100:.2f}%, "
            f"{rounds} реальных раздач дали {observed * 100:.2f}%")


def test_paytable_order() -> str:
    for table in (JACKS_OR_BETTER, DEUCES_WILD):
        rows = sorted(table.rows, key=lambda row: row.category)
        multipliers = [row.multiplier for row in rows]
        assert multipliers == sorted(multipliers), table.key
        assert len(set(row.category for row in table.rows)) == len(table.rows)
    assert JACKS_OR_BETTER.payout(Category.FIVE_KIND) == 0, "без джокеров пятёрки не бывает"
    assert DEUCES_WILD.payout(Category.ROYAL_FLUSH) == 800
    assert DEUCES_WILD.payout(Category.FOUR_DEUCES) == 200
    return "таблицы согласованы с порядком силы комбинаций"



def finish_poker_round(table: PokerTable) -> None:
    """Досмотреть анимацию мгновенно и начислить деньги."""
    table.stop_anim()
    table.tick()


def test_poker_bets_and_phases() -> str:
    bank = Bank()
    table = PokerTable(bank)
    assert (bank.balance, table.bet, table.hands) == (START_BALANCE, MIN_BET, 1)

    table.betbox.set(50)
    assert table.bet == MIN_BET, "ставка не должна падать ниже минимума"
    table.betbox.scale(100, 1)
    assert table.bet == 10_000
    table.betbox.scale(1, 10)
    assert table.bet == 1_000

    table.set_hands(29)
    assert table.total_bet == 29_000 > START_BALANCE
    assert not table.can_deal, "нельзя раздавать больше, чем есть в кармане"
    assert table.deal() is False
    assert bank.balance == START_BALANCE, "отклонённая раздача не трогает баланс"

    table.betbox.set(START_BALANCE // 29)
    assert table.can_deal, "ставка ровно по балансу должна проходить"
    table.betbox.set(START_BALANCE // 29 + 1)
    assert not table.can_deal, "на рубль больше баланса - уже нельзя"

    table.betbox.set(100)
    table.set_hands(5)
    assert table.deal()
    assert bank.balance == START_BALANCE - 500, "списывается ставка за каждую руку"

    # Посреди раздачи ставка, руки и режим заморожены.
    table.betbox.set(900)
    table.set_hands(29)
    table.set_mode(DEUCES_WILD)
    assert (table.bet, table.hands) == (100, 5) and table.mode is JACKS_OR_BETTER
    assert table.busy and table.phase is Phase.HOLDING

    table.stop_anim()
    table.draw()
    finish_poker_round(table)
    assert table.phase is Phase.RESULT and not table.busy
    table.set_mode(DEUCES_WILD)
    assert table.mode is DEUCES_WILD
    return "списание, минимум ставки и заморозка настроек в раздаче работают"


def test_poker_holds_and_independence() -> str:
    bank = Bank()
    bank.balance = 10 ** 7
    table = PokerTable(bank)
    table.set_hands(29)
    table.betbox.set(100)
    holds = [True, True, False, False, False]
    discard_reuse = 0

    for _ in range(40):
        assert table.deal()
        table.stop_anim()
        initial = list(table.dealt)
        for index, hold in enumerate(holds):
            if hold:
                table.toggle_hold(index)
        table.draw()
        finish_poker_round(table)

        discarded = {card for card, hold in zip(initial, holds) if not hold}
        assert len(table.results) == 29
        for result in table.results:
            assert len(set(result.cards)) == 5, "дубликат карты внутри руки"
            for index, hold in enumerate(holds):
                if hold:
                    assert result.cards[index] == initial[index], "закреп сдвинулся"
        assert not set(table.results[0].cards) & discarded, (
            "главная рука вернула сброшенную карту")
        discard_reuse += sum(1 for result in table.results[1:]
                             if set(result.cards) & discarded)
        unique = len({tuple(result.cards) for result in table.results})
        assert unique > 20, f"руки похожи друг на друга: уникальных {unique} из 29"

    assert discard_reuse > 0, "дополнительные руки обязаны тянуть из своей колоды"
    return (f"закреп держится во всех руках, главная не возвращает сброс, "
            f"остальные независимы ({discard_reuse} раз брали сброшенную карту)")


def test_poker_jackpot() -> str:
    bank = Bank()
    bank.balance = 10 ** 6
    table = PokerTable(bank)
    table.betbox.set(1000)
    table.set_hands(1)

    assert table.deal()
    assert bank.jackpot == JACKPOT_SEED + 10, "в джекпот уходит 1% ставки"
    assert bank.jackpot_paid_in == 10
    table.stop_anim()
    table.draw()
    finish_poker_round(table)

    # Подкладываем роял-флеш и проверяем, что он забирает счётчик.
    # При ставке $100 обычная выплата x800 это $80 000 - джекпот больше.
    bank.jackpot = 400_000
    table.betbox.set(100)
    table.deal()
    table.stop_anim()
    prize = bank.jackpot          # 1% этой раздачи уже внутри
    table.results = [table._score(parse_hand("As Ks Qs Js Ts"), False)]
    table._apply_jackpot()
    assert table.results[0].jackpot, "роял должен забрать джекпот"
    assert table.results[0].win == prize, (table.results[0].win, prize)
    table.round_win = table.results[0].win
    table.credited = 0
    table.phase = Phase.RESULT
    before = bank.balance
    finish_poker_round(table)
    assert bank.balance == before + prize
    assert bank.jackpot == JACKPOT_SEED, "после выплаты счётчик начинает с сида"

    # Маленький джекпот не должен ухудшать обычную выплату x800.
    bank.jackpot = 1000
    result = table._score(parse_hand("As Ks Qs Js Ts"), False)
    table.results = [result]
    table._apply_jackpot()
    assert not result.jackpot and result.win == 800 * table.round_bet
    return "джекпот копится по 1%, забирается роялем и не портит выплату x800"

def spin_to(table: RouletteTable, number: int) -> None:
    """Прокрутить рулетку с заранее известным результатом."""
    table.spin()
    table.result = number
    table.spin_start -= int(SPIN_TIME * 1000) + 100
    table.tick()


def test_roulette_edge() -> str:
    """Любая ставка должна давать ровно 36/37 — это и есть край 2.70%."""
    table = RouletteTable(Bank())
    samples = {
        "число": straight(17),
        "сплит": split(5, 8),
        "стрит": street([1, 2, 3]),
        "каре": corner({1, 2, 4, 5}),
        "линия": sixline([1, 2, 3, 4, 5, 6]),
        "колонка": column(0),
        "дюжина": dozen(1),
        "равные шансы": EVEN_BETS[2],
    }
    expected = 36 / 37
    for name, bet in samples.items():
        table.la_partage = False
        payout = sum(bet.payout for number in range(37) if number in bet.numbers)
        assert abs(payout / 37 - expected) < 1e-12, f"{name}: отдача {payout / 37:.6f}"

    partage = (EVEN_BETS[2].payout * 18 + 0.5) / 37
    assert abs(partage - 0.9864864864864865) < 1e-12, partage
    assert len(WHEEL) == 37 and len(set(WHEEL)) == 37
    assert len(REDS) == 18
    return (f"все восемь типов ставок дают {expected * 100:.2f}%, "
            f"с la partage равные шансы {partage * 100:.2f}%")


def test_roulette_settlement() -> str:
    bank = Bank()
    table = RouletteTable(bank)
    table.chip.set(100)
    table.place(straight(17))
    table.place(EVEN_BETS[2])      # красное
    table.place(column(1))         # числа 2, 5, 8, ...
    assert table.staked == 300
    before = bank.balance

    spin_to(table, 17)             # 17 чёрное, колонка 2 (17 % 3 == 2)
    assert bank.balance == before - 300 + 3600 + 300, bank.balance
    assert not table.bets, "после розыгрыша фишки уходят со стола"
    assert table.results[0] == 17

    table.repeat_bets()
    assert table.staked == 300, "повтор возвращает прошлые ставки"
    before = bank.balance
    table.la_partage = True
    spin_to(table, 0)
    # Зеро: число и колонка сгорают, красное возвращает половину.
    assert bank.balance == before - 300 + 50, bank.balance

    table.chip.set(100)
    table.place(straight(5))
    table.clear_bets()
    assert not table.bets
    return "выплаты, la partage на зеро, повтор и очистка ставок считаются верно"


def test_roulette_click_zones() -> str:
    rect = pygame.Rect(100, 100, 100, 100)
    cases = {
        (150, 150): ("straight", [5]),
        (105, 150): ("split", [2, 5]),
        (195, 150): ("split", [5, 8]),
        (150, 105): ("split", [5, 6]),
        (105, 105): ("corner", [2, 3, 5, 6]),
        (195, 195): ("corner", [4, 5, 7, 8]),
    }
    for pos, (kind, numbers) in cases.items():
        bet = bet_at(5, rect, pos)
        assert bet.kind == kind and sorted(bet.numbers) == numbers, (pos, bet.kind,
                                                                    sorted(bet.numbers))
    top = pygame.Rect(100, 0, 100, 100)
    assert sorted(bet_at(6, top, (150, 5)).numbers) == [4, 5, 6]        # стрит
    assert sorted(bet_at(6, top, (105, 5)).numbers) == [1, 2, 3, 4, 5, 6]  # линия
    return f"{len(cases) + 2} зоны клика по сукну разбираются правильно"

def bj_scenario(table: BlackjackTable, cards: str, bet: int = 1000) -> int:
    """Разложить шуз так, чтобы карты пришли в заданном порядке."""
    filler = [card for _ in range(4) for card in FULL_DECK]
    table.shoe.cards = filler + list(reversed(parse_hand(cards)))
    table.phase = "bets"
    table.betbox.set(bet)
    before = table.bank.balance
    table.deal()
    table.next_at = 0
    table.tick()
    return before


def bj_play_out(table: BlackjackTable) -> None:
    for _ in range(40):
        table.next_at = 0
        if table.phase == "insurance":
            table.take_insurance(False)
        elif table.phase == "player":
            table.stand()
        table.tick()
        if table.phase == "result":
            return
    raise AssertionError("раздача не завершилась")


def test_blackjack_hand_values() -> str:
    cases = {
        "As Ks": (21, True),
        "As 9d 5h": (15, False),
        "As Ad 9h": (21, True),
        "Ks Qd Jh": (30, False),
        "As As As": (13, True),
        "5s 6d": (11, False),
    }
    for notation, expected in cases.items():
        assert hand_value(parse_hand(notation)) == expected, (notation,
                                                              hand_value(parse_hand(notation)))
    return f"{len(cases)} раздач посчитаны с правильным туз-за-11"


def test_blackjack_rules() -> str:
    bank = Bank()
    bank.balance = 100_000
    table = BlackjackTable(bank)

    before = bj_scenario(table, "As 9d Ks 7c")
    assert table.hands[0].outcome == "блэкджек 3:2"
    assert bank.balance - before == 1500, "блэкджек должен платить 3:2"

    before = bj_scenario(table, "8s Ad 7h Kc")
    assert table.phase == "insurance", "на тузе дилера предлагается страховка"
    table.take_insurance(True)
    table.next_at = 0
    table.tick()
    assert bank.balance - before == 0, "страховка ровно компенсирует проигрыш"

    before = bj_scenario(table, "8s Ad 7h Kc")
    table.take_insurance(False)
    bj_play_out(table)
    assert bank.balance - before == -1000

    before = bj_scenario(table, "Ks Td Qh Tc")
    table.next_at = 0
    table.stand()
    bj_play_out(table)
    assert table.hands[0].outcome == "пуш" and bank.balance == before

    before = bj_scenario(table, "6s 5d 5h 6c 9s")
    table.next_at = 0
    table.double()
    bj_play_out(table)
    assert table.hands[0].doubled and table.hands[0].bet == 2000
    assert bank.balance - before == 2000, "дабл против перебора дилера"

    before = bj_scenario(table, "8s 9d 8h 7c 3s Kd 4h")
    table.next_at = 0
    table.split()
    assert len(table.hands) == 2 and all(hand.bet == 1000 for hand in table.hands)
    while table.phase == "player":
        table.next_at = 0
        table.stand()
    bj_play_out(table)
    assert sum(hand.win for hand in table.hands) == table.round_win

    before = bj_scenario(table, "Ts 9d 6h 7c")
    table.next_at = 0
    table.surrender()
    bj_play_out(table)
    assert table.hands[0].outcome == "сдача"
    assert bank.balance - before == -500, "сдача возвращает половину"

    # Дилер стоит на мягких 17.
    bj_scenario(table, "Ts 6d 7h Ac")
    table.next_at = 0
    table.stand()
    bj_play_out(table)
    assert len(table.dealer.slots) == 2 and table.dealer.total == 17
    return "3:2, страховка, пуш, дабл, сплит, сдача и мягкие 17 отрабатывают по правилам"


def test_blackjack_shoe() -> str:
    table = BlackjackTable(Bank())
    shoe = table.shoe
    assert shoe.size == DECKS * 52 == 312
    counts = Counter(shoe.cards)
    assert len(counts) == 52 and set(counts.values()) == {DECKS}
    assert not shoe.needs_shuffle
    shoe.cards = shoe.cards[:78]
    assert shoe.needs_shuffle, "перетасовка при проходе 75%"
    shoe.shuffle()
    assert shoe.left == 312
    return f"шуз {DECKS} колод: по {DECKS} копии каждой карты, перетасовка на 75%"

def simulate_dice(rules: Rules, rounds: int, rng: random.Random) -> float:
    """Независимая симуляция правил: проверка формулы отдачи."""
    returned = 0.0
    for _ in range(rounds):
        first = rng.randint(1, 6) + rng.randint(1, 6)
        if first in LOSE_COMEOUT:
            continue
        if first in WIN_COMEOUT:
            returned += rules.comeout
            continue
        for _ in range(rules.rolls):
            roll = rng.randint(1, 6) + rng.randint(1, 6)
            if roll == 7:
                break
            if roll == first:
                returned += rules.point
                break
        else:
            if rules.refund:
                returned += 1.0
    return returned / rounds


def test_dice_rtp() -> str:
    assert abs(AUTHOR_RULES.rtp() - 1.3774934) < 1e-6, AUTHOR_RULES.rtp()
    assert abs(FAIR_RULES.rtp() - 0.9916213) < 1e-6, FAIR_RULES.rtp()

    # Классический крэпс обязан дать известный край 1.41% — это проверка формулы.
    classic = Rules("classic", "крэпс", comeout=2, point=2, rolls=400, refund=False)
    assert abs(classic.rtp() - 0.9858586) < 1e-6, classic.rtp()

    rng = random.Random(4242)
    rounds = 300_000
    for rules in (AUTHOR_RULES, FAIR_RULES):
        observed = simulate_dice(rules, rounds, rng)
        exact = rules.rtp()
        assert abs(observed - exact) < 0.01, (rules.key, observed, exact)
    return (f"формула сошлась с {rounds} симуляциями; классический крэпс "
            f"даёт известные {classic.rtp() * 100:.2f}%")


def test_dice_round() -> str:
    bank = Bank()
    bank.balance = 100_000
    table = DiceTable(bank)
    table.betbox.set(1000)

    def roll_with(total: int) -> None:
        table.roll()
        table.dice = (total - 1, 1) if total > 6 else (1, total - 1)
        table.roll_start -= 5000
        table.tick()

    before = bank.balance
    roll_with(7)
    assert table.outcome == "7 сразу" and bank.balance - before == 1000

    before = bank.balance
    roll_with(3)
    assert table.win == 0 and bank.balance - before == -1000

    before = bank.balance
    roll_with(8)
    assert table.point == 8 and table.phase == "point"
    roll_with(5)
    roll_with(8)
    assert table.outcome == "повтор 8"
    assert bank.balance - before == 3000, "x4 это ставка плюс три"

    before = bank.balance
    roll_with(6)
    for _ in range(3):
        roll_with(9)
    assert table.outcome == "броски кончились"
    assert bank.balance - before == 0, "возврат ставки при пустых бросках"

    before = bank.balance
    roll_with(10)
    roll_with(7)
    assert table.outcome == "семёрка" and bank.balance - before == -1000
    return "первый бросок, точка, повтор x4, семёрка и возврат за три броска"


def test_shared_bank() -> str:
    """Один кошелёк на все столы: баланс обязан сходиться до рубля."""
    bank = Bank()
    bank.balance = 500_000
    poker = PokerTable(bank)
    roulette = RouletteTable(bank)
    blackjack = BlackjackTable(bank)
    dice = DiceTable(bank)
    start = bank.balance
    rng = random.Random(77)

    for _ in range(25):
        poker.betbox.set(rng.choice([100, 500]))
        poker.set_hands(rng.choice(HAND_COUNTS))
        if poker.deal():
            poker.stop_anim()
            for index in range(5):
                if rng.random() < 0.5:
                    poker.toggle_hold(index)
            poker.draw()
            finish_poker_round(poker)

        roulette.chip.set(200)
        roulette.place(straight(rng.randrange(37)))
        roulette.place(EVEN_BETS[rng.randrange(6)])
        spin_to(roulette, rng.randrange(37))

        blackjack.phase = "bets"
        blackjack.betbox.set(300)
        blackjack.deal()
        blackjack.next_at = 0
        blackjack.tick()
        while blackjack.phase == "player":
            blackjack.next_at = 0
            blackjack.stand()
        bj_play_out(blackjack)

        dice.betbox.set(400)
        dice.roll()
        while dice.phase != "result":
            dice.roll_start -= 5000
            dice.tick()
            if dice.phase == "point":
                dice.roll()

    jackpot_paid = 0
    for entry in bank.history:
        if entry.game == "poker" and entry.win > 800 * 500:
            jackpot_paid += 1
    assert bank.balance == start - bank.wagered + bank.returned, (
        f"баланс {bank.balance} не сходится с {start - bank.wagered + bank.returned}")
    assert set(bank.stats) == {"poker", "roulette", "blackjack", "dice"}
    assert bank.rounds == sum(stats.rounds for stats in bank.stats.values())
    return (f"{bank.rounds} раундов на четырёх столах, общий баланс сошёлся "
            f"(оборот {bank.wagered})")

TESTS = (
    test_full_enumeration,
    test_deck_integrity,
    test_shuffle_uniformity,
    test_wild_examples,
    test_wild_matches_brute_force,
    test_paytable_order,
    test_poker_bets_and_phases,
    test_poker_holds_and_independence,
    test_poker_jackpot,
    test_dealing_matches_math,
    test_roulette_edge,
    test_roulette_settlement,
    test_roulette_click_zones,
    test_blackjack_hand_values,
    test_blackjack_rules,
    test_blackjack_shoe,
    test_dice_rtp,
    test_dice_round,
    test_shared_bank,
)


def main() -> int:
    print("Проверка честного казино\n")
    failures = 0
    started = time.perf_counter()
    for test in TESTS:
        name = test.__name__.removeprefix("test_")
        mark = time.perf_counter()
        try:
            note = test()
        except AssertionError as error:
            failures += 1
            print(f"  ПРОВАЛ  {name}\n          {error}")
        else:
            print(f"  ok  {name:28} {note}  [{time.perf_counter() - mark:.1f}s]")
    print(f"\nитого {len(TESTS) - failures}/{len(TESTS)} за {time.perf_counter() - started:.1f}s")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
