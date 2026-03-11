"""CLI parser and runtime bootstrap."""

from __future__ import annotations

import argparse
import asyncio
import queue
import sys
import threading

from engine import PlatformEngine
from models import ChartState

try:
    import uvloop
except ImportError:  # pragma: no cover - optional dependency
    uvloop = None


def build_arg_parser() -> argparse.ArgumentParser:
    """Create the CLI argument parser."""

    parser = argparse.ArgumentParser(description="Single-file investing platform MVP")
    parser.add_argument("--symbol", default="AAPL", help="Ticker symbol, e.g. AAPL")
    parser.add_argument(
        "--feed",
        default="iex",
        choices=["iex", "test"],
        help="Use 'iex' for Alpaca's free live IEX feed or 'test' for FAKEPACA.",
    )
    parser.add_argument(
        "--strategy",
        default="mean_reversion_3sigma",
        choices=["mean_reversion_3sigma"],
        help="Strategy block to run.",
    )
    parser.add_argument(
        "--position-sizing",
        default="fixed_fraction",
        choices=["fixed_fraction", "fractional_kelly_proxy"],
        help="Position sizing rule.",
    )
    parser.add_argument(
        "--history-bars",
        type=int,
        default=240,
        help="Number of warm-start minute bars.",
    )
    parser.add_argument(
        "--mean-window",
        type=int,
        default=120,
        help="Rolling window length for mean and stdev.",
    )
    parser.add_argument("--entry-z", type=float, default=3.0, help="Entry threshold in sigmas.")
    parser.add_argument("--exit-z", type=float, default=0.5, help="Re-arm threshold in sigmas.")
    parser.add_argument(
        "--fixed-allocation",
        type=float,
        default=0.02,
        help="Fixed-fraction allocation.",
    )
    parser.add_argument(
        "--fractional-kelly",
        type=float,
        default=0.25,
        help="Fractional Kelly multiplier.",
    )
    parser.add_argument(
        "--max-allocation",
        type=float,
        default=0.10,
        help="Maximum Kelly-style allocation.",
    )
    parser.add_argument(
        "--payoff-ratio",
        type=float,
        default=1.0,
        help="Assumed payoff ratio for Kelly proxy.",
    )
    parser.add_argument(
        "--starting-cash",
        type=float,
        default=100_000.0,
        help="Paper portfolio starting cash.",
    )
    parser.add_argument(
        "--max-points",
        type=int,
        default=1500,
        help="Maximum points kept on the live chart.",
    )

    return parser


def start_runtime_thread(
    args: argparse.Namespace,
    gui_queue: queue.Queue[ChartState],
) -> tuple[threading.Thread, PlatformEngine, asyncio.AbstractEventLoop]:
    """Start the asyncio runtime on a dedicated thread."""

    if uvloop is not None and sys.platform != "win32":
        asyncio.set_event_loop_policy(uvloop.EventLoopPolicy())

    loop_ready = threading.Event()
    holder: dict[str, object] = {}

    def runner() -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        engine = PlatformEngine(args=args, gui_queue=gui_queue)

        holder["loop"] = loop
        holder["engine"] = engine
        loop_ready.set()

        task = loop.create_task(engine.run())
        try:
            loop.run_until_complete(task)
        finally:
            pending = asyncio.all_tasks(loop)
            for pending_task in pending:
                pending_task.cancel()
            if pending:
                loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            loop.close()

    thread = threading.Thread(target=runner, name="platform-runtime", daemon=True)
    thread.start()
    loop_ready.wait()

    return thread, holder["engine"], holder["loop"]