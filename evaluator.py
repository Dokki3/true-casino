"""Определение покерной комбинации — с джокерами и без.

Два режима:

* обычный (`classify_natural`) — классический видеопокер;
* с дикими двойками (`classify_wild`) — двойка превращается в любую карту,
  выбирается максимально дорогая комбинация.

Про скорость режима с джокерами. Наивный перебор "каждая двойка -> любая из 52
карт" даёт до 52**3 = 140 тысяч вариантов на руку, что при 29 руках уже
заметно. Вместо этого используется разбиение, которое доказуемо полное:

1. Комбинации, не являющиеся флешем, зависят **только от рангов**. Значит
   достаточно перебрать мультимножества рангов — C(13+w-1, w) вариантов
   (455 при трёх двойках), подставив мастью такую, которая гарантированно не
   даёт случайный флеш.
2. Флеш, стрит-флеш и роял требуют все 5 карт одной масти, а значит все
   натуральные карты руки обязаны быть одной масти. Если это так — перебираем
   те же мультимножества рангов, но уже в этой масти.

Максимум из двух наборов и есть лучшая комбинация. Итого ~910 проверок в
худшем случае вместо 140 000, плюс кэш по составу руки.
"""

from __future__ import annotations

from collections import Counter
from enum import IntEnum
from functools import lru_cache
from itertools import combinations_with_replacement
from typing import Sequence

from cards import RANKS, WILD_RANK, Card


class Category(IntEnum):
    """Комбинации по возрастанию силы.

    Порядок намеренно согласован с обеими таблицами выплат: в них выплата
    никогда не убывает при росте категории, поэтому "максимальная категория"
    всегда означает и "максимальную выплату".
    """

    NOTHING = 0
    PAIR = 1              # пара ниже валета — не платит нигде
    JACKS_OR_BETTER = 2   # пара J, Q, K, A
    TWO_PAIR = 3
    THREE_KIND = 4        # сет
    STRAIGHT = 5
    FLUSH = 6
    FULL_HOUSE = 7
    FOUR_KIND = 8
    STRAIGHT_FLUSH = 9
    FIVE_KIND = 10        # только с джокерами
    WILD_ROYAL = 11       # роял-флеш, собранный с двойкой
    FOUR_DEUCES = 12
    ROYAL_FLUSH = 13      # натуральный, без двоек


CATEGORY_NAMES: dict[Category, str] = {
    Category.NOTHING: "Нет комбинации",
    Category.PAIR: "Младшая пара",
    Category.JACKS_OR_BETTER: "Пара J и выше",
    Category.TWO_PAIR: "Две пары",
    Category.THREE_KIND: "Сет",
    Category.STRAIGHT: "Стрит",
    Category.FLUSH: "Флеш",
    Category.FULL_HOUSE: "Фулл-хаус",
    Category.FOUR_KIND: "Каре",
    Category.STRAIGHT_FLUSH: "Стрит-флеш",
    Category.FIVE_KIND: "Пять одного ранга",
    Category.WILD_ROYAL: "Роял-флеш с двойкой",
    Category.FOUR_DEUCES: "Четыре двойки",
    Category.ROYAL_FLUSH: "Роял-флеш",
}

CATEGORY_SHORT: dict[Category, str] = {
    Category.NOTHING: "—",
    Category.PAIR: "пара",
    Category.JACKS_OR_BETTER: "J+",
    Category.TWO_PAIR: "2 пары",
    Category.THREE_KIND: "сет",
    Category.STRAIGHT: "стрит",
    Category.FLUSH: "флеш",
    Category.FULL_HOUSE: "фулл",
    Category.FOUR_KIND: "каре",
    Category.STRAIGHT_FLUSH: "стрит-флеш",
    Category.FIVE_KIND: "пятёрка",
    Category.WILD_ROYAL: "роял (W)",
    Category.FOUR_DEUCES: "4 двойки",
    Category.ROYAL_FLUSH: "РОЯЛ-ФЛЕШ",
}

_ROYAL_RANKS = frozenset({10, 11, 12, 13, 14})


def classify_natural(cards: Sequence[Card]) -> Category:
    """Комбинация без джокеров. Пять карт, дубликаты допускаются (джокеры)."""
    rank_counts = Counter(card.rank for card in cards)
    counts = sorted(rank_counts.values(), reverse=True)

    if counts[0] == 5:
        return Category.FIVE_KIND

    flush = len({card.suit for card in cards}) == 1
    distinct = sorted(rank_counts)
    straight = len(distinct) == 5 and (
        distinct[4] - distinct[0] == 4 or distinct == [2, 3, 4, 5, 14]
    )

    if flush and set(distinct) == _ROYAL_RANKS:
        return Category.ROYAL_FLUSH
    if flush and straight:
        return Category.STRAIGHT_FLUSH
    if counts[0] == 4:
        return Category.FOUR_KIND
    if counts[0] == 3 and counts[1] == 2:
        return Category.FULL_HOUSE
    if flush:
        return Category.FLUSH
    if straight:
        return Category.STRAIGHT
    if counts[0] == 3:
        return Category.THREE_KIND
    if counts[0] == 2 and counts[1] == 2:
        return Category.TWO_PAIR
    if counts[0] == 2:
        pair_rank = max(rank for rank, n in rank_counts.items() if n == 2)
        return Category.JACKS_OR_BETTER if pair_rank >= 11 else Category.PAIR
    return Category.NOTHING


def classify_wild(cards: Sequence[Card]) -> Category:
    """Комбинация в режиме Deuces Wild: любая двойка — дикая карта."""
    naturals = tuple(sorted(c for c in cards if c.rank != WILD_RANK))
    wilds = len(cards) - len(naturals)

    if wilds == 0:
        return classify_natural(cards)
    if wilds == 4:
        # Четыре двойки + любая карта: выше этого в таблице только натуральный
        # роял, который с двойками в руке недостижим.
        return Category.FOUR_DEUCES
    return _best_wild(naturals, wilds)


@lru_cache(maxsize=1 << 16)
def _best_wild(naturals: tuple[Card, ...], wilds: int) -> Category:
    best = Category.NOTHING

    # 1. Всё, кроме флешей, — чистый перебор рангов. Масть подставляем такую,
    #    чтобы в руке заведомо встречались минимум две масти (флеш исключён).
    off_suit = next(s for s in range(4) if s != naturals[0].suit)
    for ranks in combinations_with_replacement(RANKS, wilds):
        hand = list(naturals) + [Card(rank, off_suit) for rank in ranks]
        category = classify_natural(hand)
        if category > best:
            best = category

    # 2. Семейство флешей возможно только если все натуральные карты одномастны.
    suits = {card.suit for card in naturals}
    if len(suits) == 1:
        suit = suits.pop()
        for ranks in combinations_with_replacement(RANKS, wilds):
            hand = list(naturals) + [Card(rank, suit) for rank in ranks]
            category = classify_natural(hand)
            if category > best:
                best = category

    # Роял, собранный с джокером, стоит дешевле натурального.
    if best == Category.ROYAL_FLUSH:
        best = Category.WILD_ROYAL
    return best


def classify(cards: Sequence[Card], wild: bool) -> Category:
    return classify_wild(cards) if wild else classify_natural(cards)
