"""Paper portfolio logic."""

from __future__ import annotations

from models import OrderDecision, PortfolioSnapshot


class PaperPortfolio:
    """Very small long-only paper portfolio used for signal evaluation."""

    def __init__(self, starting_cash: float = 100_000.0) -> None:
        self.cash = float(starting_cash)
        self.position_qty = 0
        self.avg_cost = 0.0
        self.last_price = 0.0
        self.realized_pnl = 0.0

    def mark_price(self, price: float) -> None:
        """Update the marked price for unrealized PnL calculations."""

        self.last_price = float(price)

    def execute(self, decision: OrderDecision) -> None:
        """Apply a paper-trade decision to the portfolio."""

        self.last_price = decision.price

        if decision.quantity <= 0:
            return

        if decision.side == "BUY":
            cost = decision.quantity * decision.price
            if cost > self.cash:
                affordable_qty = int(self.cash // decision.price)
                if affordable_qty <= 0:
                    return
                cost = affordable_qty * decision.price
                filled_qty = affordable_qty
            else:
                filled_qty = decision.quantity

            new_total_qty = self.position_qty + filled_qty
            if new_total_qty <= 0:
                return

            if self.position_qty == 0:
                self.avg_cost = decision.price
            else:
                weighted_cost = self.avg_cost * self.position_qty + decision.price * filled_qty
                self.avg_cost = weighted_cost / new_total_qty

            self.position_qty = new_total_qty
            self.cash -= cost

        elif decision.side == "SELL":
            filled_qty = min(self.position_qty, decision.quantity)
            if filled_qty <= 0:
                return

            proceeds = filled_qty * decision.price
            self.cash += proceeds
            self.realized_pnl += (decision.price - self.avg_cost) * filled_qty
            self.position_qty -= filled_qty

            if self.position_qty == 0:
                self.avg_cost = 0.0

    def snapshot(self) -> PortfolioSnapshot:
        """Return the current portfolio snapshot."""

        market_value = self.position_qty * self.last_price
        equity = self.cash + market_value
        unrealized_pnl = (self.last_price - self.avg_cost) * self.position_qty

        return PortfolioSnapshot(
            cash=self.cash,
            position_qty=self.position_qty,
            avg_cost=self.avg_cost,
            last_price=self.last_price,
            equity=equity,
            realized_pnl=self.realized_pnl,
            unrealized_pnl=unrealized_pnl,
        )