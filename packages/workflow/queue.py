import asyncio
import os
from abc import ABC, abstractmethod
from typing import Optional
from packages.observability.logger import get_logger

logger = get_logger("queue")


class QueueAdapter(ABC):
    @abstractmethod
    async def enqueue(self, enquiry_id: str) -> None:
        pass

    @abstractmethod
    async def dequeue(self, timeout_seconds: float = 2.0) -> Optional[str]:
        pass


class MemoryQueueAdapter(QueueAdapter):
    def __init__(self):
        self._queue: asyncio.Queue[str] = asyncio.Queue()

    async def enqueue(self, enquiry_id: str) -> None:
        await self._queue.put(enquiry_id)

    async def dequeue(self, timeout_seconds: float = 2.0) -> Optional[str]:
        try:
            return await asyncio.wait_for(self._queue.get(), timeout=timeout_seconds)
        except asyncio.TimeoutError:
            return None


class RedisStreamsQueueAdapter(QueueAdapter):
    def __init__(self, redis_url: str, stream_name: str = "enquiry_stream"):
        import redis.asyncio as aioredis
        self.redis_url = redis_url
        self.stream_name = stream_name
        self.client = aioredis.from_url(redis_url, decode_responses=True)

    async def enqueue(self, enquiry_id: str) -> None:
        await self.client.xadd(self.stream_name, {"enquiry_id": enquiry_id})

    async def dequeue(self, timeout_seconds: float = 2.0) -> Optional[str]:
        try:
            # Simple read
            block_ms = int(timeout_seconds * 1000)
            res = await self.client.xread({self.stream_name: "$"}, count=1, block=block_ms)
            if res:
                stream, messages = res[0]
                if messages:
                    msg_id, data = messages[0]
                    return data.get("enquiry_id")
        except Exception as e:
            logger.error(f"Redis dequeue error: {e}")
        return None


def get_queue() -> QueueAdapter:
    redis_url = os.getenv("REDIS_URL", "").strip()
    if redis_url and redis_url != "memory":
        try:
            return RedisStreamsQueueAdapter(redis_url)
        except Exception as e:
            logger.warning(f"Could not connect to Redis at {redis_url}: {e}. Falling back to MemoryQueueAdapter.")
    return _memory_queue_singleton


_memory_queue_singleton = MemoryQueueAdapter()
