"""Market-data clients."""

from __future__ import annotations

import json
import urllib.parse
import urllib.request
from datetime import timedelta
from typing import Awaitable, Callable

from websockets.asyncio.client import connect

from models import PricePoint
from utils import parse_alpaca_timestamp, utc_now


class AlpacaMarketDataClient:
    """HTTP + WebSocket client for Alpaca market data."""

    BASE_HTTP_URL = "https://data.alpaca.markets"
    BASE_WS_URL = "wss://stream.data.alpaca.markets"

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        symbol: str,
        feed: str,
        history_bars: int,
        use_test_stream: bool,
    ) -> None:
        self.api_key = api_key
        self.api_secret = api_secret
        self.symbol = symbol.upper()
        self.feed = feed
        self.history_bars = history_bars
        self.use_test_stream = use_test_stream

    def fetch_history(self) -> list[PricePoint]:
        """Fetch historical minute bars to seed the chart and strategy state."""

        end_dt = utc_now() - timedelta(minutes=16)
        start_dt = end_dt - timedelta(minutes=max(30, self.history_bars + 10))

        query = urllib.parse.urlencode(
            {
                "symbols": self.symbol,
                "timeframe": "1Min",
                "start": start_dt.isoformat().replace("+00:00", "Z"),
                "end": end_dt.isoformat().replace("+00:00", "Z"),
                "limit": str(self.history_bars),
                "feed": self.feed,
                "sort": "asc",
            }
        )
        url = f"{self.BASE_HTTP_URL}/v2/stocks/bars?{query}"

        request = urllib.request.Request(
            url,
            headers={
                "APCA-API-KEY-ID": self.api_key,
                "APCA-API-SECRET-KEY": self.api_secret,
            },
        )

        with urllib.request.urlopen(request, timeout=20) as response:
            payload = json.loads(response.read().decode("utf-8"))

        bars = payload.get("bars", {}).get(self.symbol, [])
        history: list[PricePoint] = []

        for bar in bars:
            history.append(
                PricePoint(
                    symbol=self.symbol,
                    event_time=parse_alpaca_timestamp(bar["t"]),
                    price=float(bar["c"]),
                    size=int(bar.get("v", 0)),
                    source="history_bar",
                )
            )

        return history

    async def stream_trades(
        self,
        on_point: Callable[[PricePoint], Awaitable[None]],
    ) -> None:
        """Stream live trades over WebSocket and pass them to a callback."""

        if self.use_test_stream:
            ws_url = f"{self.BASE_WS_URL}/v2/test"
            subscribe_symbol = "FAKEPACA"
        else:
            ws_url = f"{self.BASE_WS_URL}/v2/{self.feed}"
            subscribe_symbol = self.symbol

        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }

        async with connect(ws_url, additional_headers=headers, max_queue=512) as websocket:
            await websocket.recv()

            await websocket.send(
                json.dumps(
                    {
                        "action": "auth",
                        "key": self.api_key,
                        "secret": self.api_secret,
                    }
                )
            )
            await websocket.recv()

            await websocket.send(
                json.dumps(
                    {
                        "action": "subscribe",
                        "trades": [subscribe_symbol],
                    }
                )
            )
            await websocket.recv()

            async for raw_message in websocket:
                messages = json.loads(raw_message)
                for message in messages:
                    if message.get("T") != "t":
                        continue

                    point = PricePoint(
                        symbol=message["S"],
                        event_time=parse_alpaca_timestamp(message["t"]),
                        price=float(message["p"]),
                        size=int(message.get("s", 0)),
                        source="live_trade",
                    )
                    await on_point(point)