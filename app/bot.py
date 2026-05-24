import asyncio
import io
import logging
from datetime import datetime

from telegram import BotCommand, Update
from telegram.ext import Application, CommandHandler, ContextTypes

from app.frigate import FrigateClient, FrigateEvent

logger = logging.getLogger(__name__)


class TelegramNotifier:
    def __init__(
        self,
        token: str,
        chat_id: int,
        frigate: FrigateClient,
        send_snapshot: bool = True,
        send_video: bool = False,
    ):
        self.chat_id = chat_id
        self.frigate = frigate
        self.send_snapshot = send_snapshot
        self.send_video = send_video
        self.application = Application.builder().token(token).build()
        self._register_handlers()
        self._muted = False

    def _register_handlers(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CommandHandler("cam", self.cmd_cam))
        self.application.add_handler(CommandHandler("cameras", self.cmd_cameras))
        self.application.add_handler(CommandHandler("mute", self.cmd_mute))
        self.application.add_handler(CommandHandler("unmute", self.cmd_unmute))

    async def _reply(self, update: Update, text: str):
        if update.effective_chat:
            if update.effective_chat.id != self.chat_id:
                logger.warning("Ignoring message from chat %d (expected %d)", update.effective_chat.id, self.chat_id)
                return
        logger.info("Handling command from chat %d", update.effective_chat.id if update.effective_chat else 0)
        if update.message:
            await update.message.reply_text(text)

    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self._reply(
            update,
            "Привет! Я бот уведомлений Frigate.\n\n"
            "Команды:\n"
            "/cam <name> — snapshot с камеры\n"
            "/cameras — список камер\n"
            "/mute — отключить уведомления\n"
            "/unmute — включить уведомления\n"
            "/help — справка",
        )

    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.cmd_start(update, context)

    async def cmd_mute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        self._muted = True
        await self._reply(update, "Уведомления отключены.")

    async def cmd_unmute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        self._muted = False
        await self._reply(update, "Уведомления включены.")

    async def cmd_cameras(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        cameras = await asyncio.to_thread(self.frigate.get_cameras)
        if not cameras:
            await self._reply(update, "Не удалось получить список камер.")
            return
        lines = ["Список камер:"]
        for cam in cameras:
            status = "✅" if cam.get("enabled", True) else "❌"
            lines.append(f"{status} {cam['name']}")
        await self._reply(update, "\n".join(lines))

    async def cmd_cam(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not context.args:
            await self._reply(update, "Укажи имя камеры: /cam parking")
            return
        camera_name = context.args[0]
        msg = await update.message.reply_text("Запрашиваю снимок...")
        data = await asyncio.to_thread(self.frigate.get_latest_snapshot, camera_name)
        if data:
            await msg.delete()
            await update.message.reply_photo(
                io.BytesIO(data),
                filename=f"{camera_name}.jpg",
                caption=f"📷 {camera_name}\n{datetime.now().strftime('%H:%M:%S')}",
            )
        else:
            await msg.edit_text(f"Не удалось получить снимок с камеры {camera_name}")

    async def send_event_notification(self, event: FrigateEvent):
        if self._muted:
            logger.info("Muted, skipping notification for %s", event.id)
            return
        try:
            ts = event.start_time_dt.strftime('%d.%m.%Y %H:%M:%S')
            score = f"\n📊 Уверенность: {event.top_score:.0%}" if event.top_score else ""
            caption = f"🚨 Обнаружен {event.label}\n📷 Камера: {event.camera}\n⏱ {ts}{score}"

            thumbnail = await asyncio.to_thread(self.frigate.get_thumbnail, event.id)
            if thumbnail:
                await self.application.bot.send_photo(
                    chat_id=self.chat_id,
                    photo=io.BytesIO(thumbnail),
                    filename=f"{event.camera}_{event.id[:8]}.jpg",
                    caption=caption,
                )
                return

            if self.send_video and event.has_clip:
                clip = await asyncio.to_thread(self.frigate.get_clip, event.id)
                if clip:
                    await self.application.bot.send_video(
                        chat_id=self.chat_id,
                        video=io.BytesIO(clip),
                        caption=caption,
                    )
                    return

            await self.application.bot.send_message(
                chat_id=self.chat_id,
                text=caption,
            )
        except Exception as exc:
            logger.error("Failed to send notification: %s", exc)

    async def set_commands(self):
        commands = [
            BotCommand("start", "Показать справку"),
            BotCommand("cam", "Снимок с камеры: /cam <name>"),
            BotCommand("cameras", "Список камер"),
            BotCommand("mute", "Отключить уведомления"),
            BotCommand("unmute", "Включить уведомления"),
        ]
        await self.application.bot.set_my_commands(commands)

    async def start(self):
        for attempt in range(5):
            try:
                await self.application.initialize()
                await self.application.start()
                await self.set_commands()
                logger.info("Telegram bot started")
                return
            except Exception as exc:
                logger.warning("Telegram connect attempt %d/5 failed: %s", attempt + 1, exc)
                if attempt < 4:
                    await asyncio.sleep(5)
        logger.warning("Telegram bot started with delayed connection (will retry in background)")

    async def stop(self):
        await self.application.stop()
        await self.application.shutdown()
        logger.info("Telegram bot stopped")
