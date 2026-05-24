import asyncio
import logging
import signal
import sys

from app.bot import TelegramNotifier
from app.config import Config
from app.frigate import FrigateClient
from app.monitor import EventMonitor

logger = logging.getLogger(__name__)


async def run(config: Config):
    frigate = FrigateClient(config.frigate_url)
    notifier = TelegramNotifier(
        token=config.telegram_token,
        chat_id=config.telegram_chat_id,
        frigate=frigate,
        send_snapshot=config.send_snapshot,
        send_video=config.send_video,
    )
    loop = asyncio.get_event_loop()

    monitor = EventMonitor(
        client=frigate,
        state_file=config.state_file,
        poll_interval=config.poll_interval,
        event_limit=config.event_limit,
        include_labels=config.include_labels,
        exclude_labels=config.exclude_labels,
        include_cameras=config.include_cameras,
        exclude_cameras=config.exclude_cameras,
        loop=loop,
    )

    monitor.on_event(notifier.send_event_notification)

    stop_event = asyncio.Event()

    def shutdown(sig):
        logger.info("Received signal %s, shutting down...", sig)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda s=sig: shutdown(s))
        except NotImplementedError:
            pass

    await notifier.start()
    monitor.start()
    logger.info("Bot started. Press Ctrl+C to stop.")

    try:
        await stop_event.wait()
    except asyncio.CancelledError:
        pass
    finally:
        logger.info("Stopping...")
        monitor.stop()
        await notifier.stop()
        frigate.close()
        logger.info("Stopped.")


def main():
    config_path = sys.argv[1] if len(sys.argv) > 1 else "/config/config.yml"
    config = Config.load(config_path)

    log_level = logging.DEBUG if config.debug else logging.INFO
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    asyncio.run(run(config))


if __name__ == "__main__":
    main()
