import asyncio
import os
import signal
from dotenv import load_dotenv

load_dotenv()
from packages.observability.logger import get_logger
from packages.workflow.pipeline import WorkflowPipeline
from packages.workflow.queue import get_queue

logger = get_logger("worker")


async def run_worker():
    logger.info("Starting BEDA Worker process...")
    queue = get_queue()
    pipeline = WorkflowPipeline()
    running = True

    def handle_signal():
        nonlocal running
        logger.info("Shutdown signal received. Stopping worker...")
        running = False

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, handle_signal)
        except NotImplementedError:
            pass

    while running:
        try:
            enquiry_id = await queue.dequeue(timeout_seconds=2.0)
            if enquiry_id:
                logger.info(f"Worker picked up enquiry {enquiry_id} from queue")
                await pipeline.execute_workflow(enquiry_id)
            else:
                await asyncio.sleep(0.5)
        except Exception as e:
            logger.error(f"Worker encountered unhandled error: {e}", exc_info=True)
            await asyncio.sleep(2.0)

    logger.info("Worker process terminated gracefully.")


if __name__ == "__main__":
    asyncio.run(run_worker())
