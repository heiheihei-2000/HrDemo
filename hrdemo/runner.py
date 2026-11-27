"""Bridge: run one async agent turn in a worker thread and hand events to Streamlit's sync loop."""

import asyncio
import queue
import threading
from collections.abc import Iterator

from .graph import GRAPH, Ctx, StepAborted, run_step
from .llm import LLM
from .mcp_client import MCPHub


async def _turn(emit, user_text: str, history: list[dict], shortlist: dict, decisions: list[dict] | None) -> None:
    llm, hub = LLM(), MCPHub()
    ctx = Ctx(llm=llm, hub=hub, emit=emit)
    try:
        try:
            await run_step(ctx, "连接 MCP 服务", hub.connect, quiet=True)
        except StepAborted as e:
            emit({"type": "block", "text": e.as_markdown()})
            return
        out = await GRAPH.ainvoke(
            {"user_text": user_text, "history": history, "shortlist": shortlist, "decisions": decisions or []},
            config={"configurable": {"ctx": ctx}},
        )
        emit({"type": "final", "shortlist": out.get("shortlist", shortlist), "shortlist_rows": out.get("shortlist_rows")})
    finally:
        await hub.close()
        await llm.aclose()


def stream_turn(user_text: str, history: list[dict], shortlist: dict, decisions: list[dict] | None = None) -> Iterator[dict]:
    q: queue.Queue = queue.Queue()

    def worker():
        try:
            asyncio.run(_turn(q.put, user_text, history, shortlist, decisions))
        except BaseException as e:  # noqa: BLE001 - surface anything to the UI instead of dying silently
            q.put({"type": "fatal", "text": f"{type(e).__name__}: {e}"})
        finally:
            q.put(None)

    threading.Thread(target=worker, daemon=True).start()
    while (ev := q.get()) is not None:
        yield ev
