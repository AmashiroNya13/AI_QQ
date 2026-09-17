import asyncio
import unittest
from types import SimpleNamespace

from astrbot.core.pipeline.scheduler import PipelineScheduler


class BlockingStage:
    def __init__(self):
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.closed = False

    async def process(self, event):
        self.started.set()
        await self.release.wait()

    async def close(self):
        self.closed = True


class FakeEvent:
    def __init__(self):
        self.cleaned = False

    def is_stopped(self):
        return False

    def cleanup_temporary_local_files(self):
        self.cleaned = True


class PipelineSchedulerTests(unittest.IsolatedAsyncioTestCase):
    async def test_close_waits_for_active_event(self):
        scheduler = object.__new__(PipelineScheduler)
        stage = BlockingStage()
        scheduler.ctx = SimpleNamespace()
        scheduler.stages = [stage]
        scheduler._active_executions = 0
        scheduler._idle = asyncio.Event()
        scheduler._idle.set()
        scheduler._closing = False
        event = FakeEvent()

        with unittest.mock.patch(
            "astrbot.core.pipeline.scheduler.active_event_registry"
        ):
            execution = asyncio.create_task(scheduler.execute(event))
            await stage.started.wait()
            closing = asyncio.create_task(scheduler.close())
            await asyncio.sleep(0)
            self.assertFalse(closing.done())
            self.assertFalse(stage.closed)

            stage.release.set()
            await execution
            await closing

        self.assertTrue(stage.closed)
        self.assertTrue(event.cleaned)
