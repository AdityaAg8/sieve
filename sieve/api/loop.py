"""The event loop the API server runs on.

uvicorn 0.36 replaced `Config.setup_event_loop` with `Config.get_loop_factory` and builds the
loop itself, so a policy installed by `create_app` is simply ignored. Worse, uvicorn's own
`asyncio_loop_factory` returns `ProactorEventLoop` on win32 *unless* it is running under the
reloader's subprocess -- which is exactly why `make serve --reload` worked on Windows while a
plain `uvicorn sieve.api.main:app` reported the database down: psycopg-async refuses the
Proactor loop.

So the loop is chosen the uvicorn way, with `--loop <module>:<function>` (or `loop=` in
`Config`), pointing at `new_event_loop` below.

The shape matters: for a **custom** loop path uvicorn returns the imported object directly as
the factory (`Config.get_loop_factory` only calls `loop_factory(use_subprocess=...)` for its
own built-in names), so what we register has to be a zero-argument callable returning a *new*
loop -- the same contract as the documented `--loop winloop:new_event_loop`.
"""

from __future__ import annotations

import asyncio
import sys

from uvicorn.loops.auto import auto_loop_factory

# The string `--loop` and `Config(loop=...)` both take. Kept beside the function so the two
# cannot drift apart.
LOOP_FACTORY_PATH = "sieve.api.loop:new_event_loop"


def new_event_loop() -> asyncio.AbstractEventLoop:
    """A fresh event loop psycopg can use, on every platform.

    Off Windows there is nothing to fix, so defer to uvicorn's own auto factory and keep
    uvloop in production.
    """
    if sys.platform == "win32":
        return asyncio.SelectorEventLoop()
    return auto_loop_factory()()
