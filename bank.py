"""Общий кошелёк казино: один баланс на все столы.

Вы приходите с $10 000 и таскаете их между покером, рулеткой, блэкджеком и
костями. Банк знает только про деньги: сколько поставлено, сколько вернулось,
что лежит в джекпоте и какая история у сессии. Правила игр его не касаются.

Ничего не сохраняется на диск: закрыли окно - сессия кончилась.
"""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

START_BALANCE = 10_000
MIN_BET = 100
HISTORY_LIMIT = 400

JACKPOT_SEED = 50_000     # заведение кладёт стартовую сумму
JACKPOT_RATE = 0.01       # доля каждой покерной ставки, уходящая в джекпот


@dataclass
class Entry:
    game: str          # ключ стола
    tag: str           # короткая пометка: что именно выпало
    bet: int
    win: int
    balance_after: int

    @property
    def net(self) -> int:
        return self.win - self.bet


@dataclass
class Stats:
    rounds: int = 0
    wagered: int = 0
    returned: int = 0

    @property
    def net(self) -> int:
        return self.returned - self.wagered

    @property
    def rtp(self) -> float | None:
        return self.returned / self.wagered if self.wagered else None


@dataclass
class Bank:
    balance: int = START_BALANCE
    jackpot: int = JACKPOT_SEED
    jackpot_paid_in: int = 0
    history: deque = field(default_factory=lambda: deque(maxlen=HISTORY_LIMIT))
    stats: dict = field(default_factory=dict)

    # --- деньги -------------------------------------------------------------

    def can_afford(self, amount: int) -> bool:
        return 0 < amount <= self.balance

    def take(self, amount: int) -> bool:
        """Списать ставку. Возвращает False, если денег не хватает."""
        if not self.can_afford(amount):
            return False
        self.balance -= amount
        return True

    def give(self, amount: int) -> None:
        self.balance += amount

    def record(self, game: str, tag: str, bet: int, win: int) -> Entry:
        """Записать итог раунда. Деньги к этому моменту уже перешли."""
        stats = self.stats.setdefault(game, Stats())
        stats.rounds += 1
        stats.wagered += bet
        stats.returned += win
        entry = Entry(game=game, tag=tag, bet=bet, win=win, balance_after=self.balance)
        self.history.appendleft(entry)
        return entry

    # --- джекпот ------------------------------------------------------------

    def contribute(self, bet: int) -> int:
        """Отложить долю ставки в джекпот. Возвращает отложенную сумму."""
        share = int(bet * JACKPOT_RATE)
        if share > 0:
            self.jackpot += share
            self.jackpot_paid_in += share
        return share

    def claim_jackpot(self) -> int:
        """Забрать джекпот и начать копить заново с сида.

        Деньги не начисляются здесь: стол сам решает, что выплатить, потому
        что роял-флеш платит максимум из джекпота и обычной выплаты x800.
        """
        prize = self.jackpot
        self.jackpot = JACKPOT_SEED
        self.jackpot_paid_in = 0
        return prize

    # --- сессия -------------------------------------------------------------

    @property
    def session_net(self) -> int:
        return self.balance - START_BALANCE

    @property
    def wagered(self) -> int:
        return sum(stats.wagered for stats in self.stats.values())

    @property
    def returned(self) -> int:
        return sum(stats.returned for stats in self.stats.values())

    @property
    def rounds(self) -> int:
        return sum(stats.rounds for stats in self.stats.values())

    @property
    def is_broke(self) -> bool:
        return self.balance < MIN_BET

    def reset(self) -> None:
        self.balance = START_BALANCE
        self.jackpot = JACKPOT_SEED
        self.jackpot_paid_in = 0
        self.history.clear()
        self.stats.clear()
