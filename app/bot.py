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
        self.application.add_handler(CommandHandler("cameras", self.cmd_cameras))
        self.application.add_handler(CommandHandler("snapall", self.cmd_snapall))
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
            "/cameras — список камер\n"
            "/<камера> — снимок с камеры\n"
            "/snapall — снимки со всех камер\n"
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

    def _make_cam_handler(self, camera_name: str):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if update.effective_chat and update.effective_chat.id != self.chat_id:
                return
            if not update.message:
                return
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
        return handler

    async def _register_camera_commands(self):
        cameras = await asyncio.to_thread(self.frigate.get_cameras)
        for cam in cameras:
            name = cam["name"]
            self.application.add_handler(CommandHandler(name, self._make_cam_handler(name)))

    async def cmd_snapall(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not update.message:
            return
        cameras = await asyncio.to_thread(self.frigate.get_cameras)
        if not cameras:
            await self._reply(update, "Не удалось получить список камер.")
            return

        msg = await update.message.reply_text("Запрашиваю снимки со всех камер...")
        tasks = [asyncio.to_thread(self.frigate.get_latest_snapshot, cam["name"]) for cam in cameras]
        results = await asyncio.gather(*tasks)
        snapshots = dict(zip([cam["name"] for cam in cameras], results))
        await msg.delete()

        sent = 0
        for name, data in snapshots.items():
            if data:
                await update.message.reply_photo(
                    io.BytesIO(data),
                    filename=f"{name}.jpg",
                    caption=f"📷 {name}\n{datetime.now().strftime('%H:%M:%S')}",
                )
                sent += 1

        if sent == 0:
            await update.message.reply_text("Не удалось получить снимки ни с одной камеры.")
        elif sent < len(cameras):
            await update.message.reply_text(f"Получено {sent} из {len(cameras)} снимков.")

    async def set_commands(self):
        cameras = await asyncio.to_thread(self.frigate.get_cameras)
        commands = [
            BotCommand("start", "Показать справку"),
            BotCommand("cameras", "Список камер"),
            BotCommand("snapall", "Снимки со всех камер"),
            BotCommand("mute", "Отключить уведомления"),
            BotCommand("unmute", "Включить уведомления"),
        ]
        for cam in cameras:
            commands.append(BotCommand(cam["name"], f"Снимок с {cam['name']}"))
        await self.application.bot.set_my_commands(commands)

    async def start(self):
        await self.application.initialize()
        await self._register_camera_commands()
        logger.info("Application initialized")
        if self.application.updater:
            logger.info("Starting updater polling...")
            await self.application.updater.start_polling()
            logger.info("Updater polling started")
        else:
            logger.error("No updater available!")
        await self.application.start()
        await self.set_commands()
        logger.info("Telegram bot started")

    async def stop(self):
        await self.application.stop()
        await self.application.shutdown()
        logger.info("Telegram bot stopped")
