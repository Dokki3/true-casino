"""Карты и честная колода.

Перетасовка идёт через `random.SystemRandom` — это системный источник энтропии
ОС (CryptGenRandom/urandom), а не Mersenne Twister с воспроизводимым seed'ом.

Важное свойство, ради которого всё это и писалось: ни в одной функции этого
модуля (и всего проекта) нет доступа к балансу, размеру ставки или истории
раздач. Карты физически не могут "знать", сколько вы поставили. Раздача — это
просто снятие карт с верха перетасованного списка.
"""

from __future__ import annotations

import random
from typing import Iterable, NamedTuple

# Ранги: 2..14, где 11=J, 12=Q, 13=K, 14=A.
RANKS: tuple[int, ...] = tuple(range(2, 15))

RANK_LABELS: dict[int, str] = {
    2: "2", 3: "3", 4: "4", 5: "5", 6: "6", 7: "7", 8: "8", 9: "9",
    10: "10", 11: "J", 12: "Q", 13: "K", 14: "A",
}

# Порядок мастей: пики, червы, бубны, трефы.
SUIT_SYMBOLS: tuple[str, ...] = ("♠", "♥", "♦", "♣")
SUIT_LETTERS: tuple[str, ...] = ("S", "H", "D", "C")
RED_SUITS: frozenset[int] = frozenset({1, 2})

WILD_RANK = 2  # двойка — джокер в режиме Deuces Wild


class Card(NamedTuple):
    rank: int  # 2..14
    suit: int  # 0..3

    @property
    def rank_label(self) -> str:
        return RANK_LABELS[self.rank]

    @property
    def is_red(self) -> bool:
        return self.suit in RED_SUITS

    def label(self, use_symbols: bool = True) -> str:
        suit = SUIT_SYMBOLS[self.suit] if use_symbols else SUIT_LETTERS[self.suit]
        return f"{self.rank_label}{suit}"

    def __str__(self) -> str:  # pragma: no cover - для отладки
        return self.label()


FULL_DECK: tuple[Card, ...] = tuple(
    Card(rank, suit) for suit in range(4) for rank in RANKS
)
assert len(FULL_DECK) == 52

_rng = random.SystemRandom()


class Deck:
    """Перетасованная колода. `exclude` убирает карты, которые уже на руках."""

    def __init__(self, exclude: Iterable[Card] = ()) -> None:
        removed = set(exclude)
        self._cards: list[Card] = [c for c in FULL_DECK if c not in removed]
        _rng.shuffle(self._cards)

    def deal(self, count: int = 1) -> list[Card]:
        if count > len(self._cards):
            raise ValueError("в колоде не осталось столько карт")
        drawn = self._cards[-count:]
        del self._cards[-count:]
        return drawn

    def deal_one(self) -> Card:
        return self.deal(1)[0]

    def __len__(self) -> int:
        return len(self._cards)


def parse_hand(text: str) -> list[Card]:
    """`"As Kh 4c"` -> список карт. Нужно для тестов и отладки."""
    letters = {s: i for i, s in enumerate(SUIT_LETTERS)}
    labels = {v.upper(): k for k, v in RANK_LABELS.items()}
    labels["T"] = 10
    hand: list[Card] = []
    for token in text.split():
        rank_part, suit_part = token[:-1].upper(), token[-1].upper()
        hand.append(Card(labels[rank_part], letters[suit_part]))
    return hand
