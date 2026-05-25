import asyncio
import logging
import logging.handlers
import signal
import sys

from app.bot import TelegramNotifier
from app.config import Config
from app.frigate import FrigateClient
from app.monitor import EventMonitor

logger = logging.getLogger(__name__)


async def run(config: Config):
    frigate = FrigateClient(config.frigate_url)

    monitor = EventMonitor(
        client=frigate,
        state_file=config.state_file,
        poll_interval=config.poll_interval,
        event_limit=config.event_limit,
        include_labels=config.include_labels,
        exclude_labels=config.exclude_labels,
        include_cameras=config.include_cameras,
        exclude_cameras=config.exclude_cameras,
    )

    notifier = TelegramNotifier(
        token=config.telegram_token,
        chat_id=config.telegram_chat_id,
        frigate=frigate,
        monitor=monitor,
        send_snapshot=config.send_snapshot,
        send_video=config.send_video,
    )

    monitor.on_event(notifier.send_event_notification)

    stop_event = asyncio.Event()

    def shutdown(sig):
        logger.info("Received signal %s, shutting down...", sig)
        stop_event.set()

    loop = asyncio.get_running_loop()
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
        await monitor.stop()
        await notifier.stop()
        await frigate.close()
        logger.info("Stopped.")


def main():
     config_path = sys.argv[1] if len(sys.argv) > 1 else "/config/config.yml"
     config = Config.load(config_path)

     log_level = logging.DEBUG if config.debug else logging.INFO
     
     # Настройка логирования
     log_format = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
     log_date_format = "%Y-%m-%d %H:%M:%S"
     
     # Создание форматера
     formatter = logging.Formatter(log_format, datefmt=log_date_format)
     
     # Логирование в консоль
     console_handler = logging.StreamHandler(sys.stdout)
     console_handler.setLevel(log_level)
     console_handler.setFormatter(formatter)
     
     # Логирование в файл (с ротацией по размеру)
     log_file = "/data/bot.log"
     try:
         file_handler = logging.handlers.RotatingFileHandler(
             log_file,
             maxBytes=10*1024*1024,  # 10 MB
             backupCount=5  # Хранить 5 старых файлов
         )
         file_handler.setLevel(log_level)
         file_handler.setFormatter(formatter)
     except Exception as e:
         print(f"Warning: Could not setup file logging to {log_file}: {e}", file=sys.stderr)
         file_handler = None
     
     # Настройка корневого логгера
     root_logger = logging.getLogger()
     root_logger.setLevel(log_level)
     root_logger.addHandler(console_handler)
     if file_handler:
         root_logger.addHandler(file_handler)

     asyncio.run(run(config))


if __name__ == "__main__":
    main()
