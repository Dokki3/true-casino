"""Таблицы выплат. Всё, что определяет математику игры, собрано здесь.

Выплата — множитель ставки **за одну руку**, и это полный возврат, а не
доплата к ставке: x1 означает "ставка вернулась, нетто нуль" — так устроен
любой видеопокер.

Менять цифры можно свободно: игра честная ровно настолько, насколько честна
таблица, и обе таблицы здесь — классические эталоны, которые настоящие казино
давно порезали.
"""

from __future__ import annotations

from dataclasses import dataclass

from evaluator import CATEGORY_NAMES, Category


@dataclass(frozen=True)
class PayRow:
    category: Category
    multiplier: int
    name: str = ""
    odds: str = ""

    @property
    def title(self) -> str:
        return self.name or CATEGORY_NAMES[self.category]


@dataclass(frozen=True)
class PayTable:
    key: str
    title: str
    subtitle: str
    wild: bool
    rtp: str
    rows: tuple[PayRow, ...]

    def payout(self, category: Category) -> int:
        for row in self.rows:
            if row.category == category:
                return row.multiplier
        return 0


JACKS_OR_BETTER = PayTable(
    key="JB",
    title="Jacks or Better",
    subtitle="классический видеопокер 9/6",
    wild=False,
    rtp="отдача 99.54%",
    rows=(
        PayRow(Category.ROYAL_FLUSH, 800, odds="1 : 40 000"),
        PayRow(Category.STRAIGHT_FLUSH, 50, odds="1 : 9 150"),
        PayRow(Category.FOUR_KIND, 25, odds="1 : 423"),
        PayRow(Category.FULL_HOUSE, 9, odds="1 : 86"),
        PayRow(Category.FLUSH, 6, odds="1 : 91"),
        PayRow(Category.STRAIGHT, 4, odds="1 : 89"),
        PayRow(Category.THREE_KIND, 3, odds="1 : 13.5"),
        PayRow(Category.TWO_PAIR, 2, odds="1 : 7.7"),
        PayRow(Category.JACKS_OR_BETTER, 1, name="Пара J, Q, K, A", odds="1 : 4.7"),
    ),
)

DEUCES_WILD = PayTable(
    key="DW",
    title="Deuces Wild",
    subtitle="двойка заменяет любую карту, пары не платят",
    wild=True,
    rtp="отдача 100.76%",
    rows=(
        PayRow(Category.ROYAL_FLUSH, 800, name="Натуральный роял-флеш", odds="1 : 45 000"),
        PayRow(Category.FOUR_DEUCES, 200, odds="1 : 4 900"),
        PayRow(Category.WILD_ROYAL, 25, odds="1 : 550"),
        PayRow(Category.FIVE_KIND, 15, odds="1 : 320"),
        PayRow(Category.STRAIGHT_FLUSH, 9, odds="1 : 250"),
        PayRow(Category.FOUR_KIND, 5, odds="1 : 15"),
        PayRow(Category.FULL_HOUSE, 3, odds="1 : 48"),
        PayRow(Category.FLUSH, 2, odds="1 : 60"),
        PayRow(Category.STRAIGHT, 2, odds="1 : 18"),
        PayRow(Category.THREE_KIND, 1, name="Сет", odds="1 : 3.5"),
    ),
)

MODES: tuple[PayTable, ...] = (JACKS_OR_BETTER, DEUCES_WILD)

# Проверка согласованности с порядком Category: выплата не должна убывать при
# росте силы комбинации, иначе "лучшая категория" перестанет означать
# "лучшая выплата" в эвалюаторе.
for _table in MODES:
    _ordered = sorted(_table.rows, key=lambda row: row.category)
    _mults = [row.multiplier for row in _ordered]
    assert _mults == sorted(_mults), "таблица нарушает порядок категорий: " + _table.key
