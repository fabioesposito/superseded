import asyncio

from superseded.models import AgentEvent, Stage
from superseded.pipeline.events import PipelineEventManager


async def test_start_and_stop():
    manager = PipelineEventManager()
    manager.start("SUP-001")
    assert "SUP-001" in manager._queues
    manager.stop("SUP-001")
    assert "SUP-001" not in manager._queues


async def test_publish_and_subscribe():
    manager = PipelineEventManager()
    manager.start("SUP-001")

    event = AgentEvent(event_type="stdout", content="hello", stage=Stage.BUILD)
    await manager.publish("SUP-001", event)

    received = []

    async def collect():
        async for evt in manager.subscribe("SUP-001"):
            received.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.01)
    manager.stop("SUP-001")
    await asyncio.wait_for(task, timeout=2)

    assert len(received) == 1
    assert received[0].content == "hello"


async def test_publish_to_nonexistent_raises():
    manager = PipelineEventManager()
    event = AgentEvent(event_type="stdout", content="hello", stage=Stage.BUILD)
    try:
        await manager.publish("SUP-999", event)
        raise AssertionError("Should have raised")
    except KeyError:
        pass


async def test_multiple_events_in_order():
    manager = PipelineEventManager()
    manager.start("SUP-001")

    for i in range(3):
        await manager.publish(
            "SUP-001",
            AgentEvent(event_type="stdout", content=f"line {i}", stage=Stage.BUILD),
        )

    received = []

    async def collect():
        async for evt in manager.subscribe("SUP-001"):
            received.append(evt)

    task = asyncio.create_task(collect())
    await asyncio.sleep(0.01)
    manager.stop("SUP-001")
    await asyncio.wait_for(task, timeout=2)

    assert len(received) == 3
    assert received[0].content == "line 0"
    assert received[2].content == "line 2"
