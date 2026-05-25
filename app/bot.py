import asyncio
import io
import logging
from datetime import date, datetime, time as dt_time

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
        monitor=None,
        send_snapshot: bool = True,
        send_video: bool = False,
    ):
        self.chat_id = chat_id
        self.frigate = frigate
        self.monitor = monitor
        self.send_snapshot = send_snapshot
        self.send_video = send_video
        self.application = Application.builder().token(token).build()
        self._register_handlers()
        self._muted = False
        self._start_time = datetime.now()
        self._quiet_start: dt_time | None = None
        self._quiet_end: dt_time | None = None

    def _register_handlers(self):
        self.application.add_handler(CommandHandler("start", self.cmd_start))
        self.application.add_handler(CommandHandler("help", self.cmd_help))
        self.application.add_handler(CommandHandler("sub", self.cmd_subscriptions))
        self.application.add_handler(CommandHandler("subscriptions", self.cmd_subscriptions))
        self.application.add_handler(CommandHandler("subscribe", self.cmd_subscribe))
        self.application.add_handler(CommandHandler("unsubscribe", self.cmd_unsubscribe))
        self.application.add_handler(CommandHandler("cameras", self.cmd_cameras))
        self.application.add_handler(CommandHandler("snapall", self.cmd_snapall))
        self.application.add_handler(CommandHandler("event", self.cmd_event))
        self.application.add_handler(CommandHandler("record", self.cmd_record))
        self.application.add_handler(CommandHandler("quiet", self.cmd_quiet))
        self.application.add_handler(CommandHandler("health", self.cmd_health))
        self.application.add_handler(CommandHandler("stats", self.cmd_stats))
        self.application.add_handler(CommandHandler("uptime", self.cmd_uptime))
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
        cameras = await asyncio.to_thread(self.frigate.get_cameras)
        lines = [
            "Привет! Я бот уведомлений Frigate.\n",
            "Команды:",
            "/sub — управление подписками",
            "/cameras — список камер",
            "/snapall — снимки со всех камер",
            "/event <id> — информация о событии",
            "/record <камера> [on|off] — запись",
            "/quiet [время|off] — тихие часы",
            "/health — состояние Frigate",
            "/stats — статистика",
            "/uptime — аптайм",
            "/mute /unmute — уведомления",
            "/help — справка",
            "",
            "📷 Камеры:",
        ]
        for cam in cameras:
            lines.append(f"/{cam['name']} — снимок с камеры")
        await self._reply(update, "\n".join(lines))

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

    async def cmd_subscriptions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not self.monitor:
            await self._reply(update, "Монитор не подключён.")
            return
        filters = self.monitor.get_filters()
        lines = ["📋 Текущие подписки:"]
        il = filters["include_labels"]
        lines.append(f"🏷 Метки: {'все' if not il else ', '.join(il)}")
        ic = filters["include_cameras"]
        lines.append(f"📷 Камеры: {'все' if not ic else ', '.join(ic)}")
        await self._reply(update, "\n".join(lines))

    async def cmd_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not self.monitor:
            await self._reply(update, "Монитор не подключён.")
            return
        if not context.args:
            await self._reply(update, "Укажи: /subscribe <метка> или /subscribe camera <камера>")
            return
        if context.args[0] == "camera" and len(context.args) > 1:
            result = self.monitor.add_include_camera(context.args[1])
            await self._reply(update, f"📷 Камера {context.args[1]}: {result}")
        else:
            result = self.monitor.add_include_label(context.args[0])
            await self._reply(update, f"🏷 Метка {context.args[0]}: {result}")

    async def cmd_unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not self.monitor:
            await self._reply(update, "Монитор не подключён.")
            return
        if not context.args:
            await self._reply(update, "Укажи: /unsubscribe <метка> или /unsubscribe camera <камера>")
            return
        if context.args[0] == "camera" and len(context.args) > 1:
            result = self.monitor.remove_include_camera(context.args[1])
            await self._reply(update, f"📷 Камера {context.args[1]}: {result}")
        else:
            result = self.monitor.remove_include_label(context.args[0])
            await self._reply(update, f"🏷 Метка {context.args[0]}: {result}")

    async def cmd_event(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not update.message:
            return
        if not context.args:
            await self._reply(update, "Укажи ID события: /event <id>")
            return
        event_id = context.args[0]
        msg = await update.message.reply_text("Запрашиваю событие...")
        event = await asyncio.to_thread(self.frigate.get_event, event_id)
        if not event:
            await msg.edit_text(f"Событие {event_id} не найдено.")
            return
        ts = event.start_time_dt.strftime('%d.%m.%Y %H:%M:%S')
        score = f" (уверенность {event.top_score:.0%})" if event.top_score else ""
        caption = (
            f"🚨 Событие {event.id[:12]}…\n"
            f"🏷 {event.label}{score}\n"
            f"📷 {event.camera}\n"
            f"⏱ {ts}"
        )
        thumbnail = await asyncio.to_thread(self.frigate.get_thumbnail, event.id)
        if thumbnail:
            await msg.delete()
            await update.message.reply_photo(
                io.BytesIO(thumbnail),
                filename=f"{event.id[:8]}.jpg",
                caption=caption,
            )
            if event.has_clip:
                clip = await asyncio.to_thread(self.frigate.get_clip, event.id)
                if clip:
                    await update.message.reply_video(
                        io.BytesIO(clip),
                        filename=f"{event.id[:8]}.mp4",
                    )
        else:
            await msg.edit_text(caption)

    async def cmd_record(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not update.message:
            return
        if not context.args:
            await self._reply(update, "Укажи камеру: /record <камера> [on|off]")
            return
        camera = context.args[0]
        if len(context.args) > 1:
            action = context.args[1].lower()
            if action in ("on", "start", "вкл"):
                ok = await asyncio.to_thread(self.frigate.recording_start, camera)
                await self._reply(update, f"✅ Запись на {camera} включена" if ok else f"❌ Ошибка включения записи на {camera}")
            elif action in ("off", "stop", "выкл"):
                ok = await asyncio.to_thread(self.frigate.recording_stop, camera)
                await self._reply(update, f"✅ Запись на {camera} выключена" if ok else f"❌ Ошибка выключения записи на {camera}")
            else:
                await self._reply(update, "Используй: on/off")
        else:
            stats = await asyncio.to_thread(self.frigate.get_stats)
            if stats and "cameras" in stats:
                c = stats["cameras"].get(camera, {})
                rec = "🔴 вкл" if c.get("recording_enabled") else "⚫ выкл"
                det = "🎯 вкл" if c.get("detection_enabled") else "🎯 выкл"
                await self._reply(update, f"📷 {camera}\nЗапись: {rec}\nДетекция: {det}")
            else:
                await self._reply(update, f"Не удалось получить статус {camera}")

    async def cmd_quiet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not context.args:
            if self._quiet_start and self._quiet_end:
                await self._reply(update, f"🔇 Тихие часы: {self._quiet_start.strftime('%H:%M')} — {self._quiet_end.strftime('%H:%M')}")
            else:
                await self._reply(update, "🔇 Тихие часы не установлены. Используй: /quiet 23:00-07:00")
            return
        if context.args[0].lower() in ("off", "выкл", "disable"):
            self._quiet_start = self._quiet_end = None
            await self._reply(update, "🔇 Тихие часы отключены.")
            return
        try:
            times = context.args[0].split("-")
            if len(times) != 2:
                raise ValueError
            sh, sm = times[0].strip().split(":")
            eh, em = times[1].strip().split(":")
            self._quiet_start = dt_time(int(sh), int(sm))
            self._quiet_end = dt_time(int(eh), int(em))
            await self._reply(update, f"🔇 Тихие часы: {self._quiet_start.strftime('%H:%M')} — {self._quiet_end.strftime('%H:%M')}")
        except (ValueError, IndexError):
            await self._reply(update, "Неверный формат. Используй: /quiet 23:00-07:00")

    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not update.message:
            return
        msg = await update.message.reply_text("Проверяю состояние Frigate...")
        version = await asyncio.to_thread(self.frigate.get_version)
        stats = await asyncio.to_thread(self.frigate.get_stats)
        cameras = await asyncio.to_thread(self.frigate.get_cameras)
        lines = ["🖥 Frigate Health"]
        lines.append(f"Версия: {version or '❌ недоступна'}")
        if cameras:
            enabled = sum(1 for c in cameras if c.get("enabled", True))
            lines.append(f"📷 Камеры: {enabled}/{len(cameras)} активны")
        if stats:
            lines.append(f"🎯 FPS детекции: {stats.get('detection_fps', 0):.1f}")
            uptime_sec = stats.get("service_uptime", 0)
            d, h, m = int(uptime_sec // 86400), int((uptime_sec % 86400) // 3600), int((uptime_sec % 3600) // 60)
            lines.append(f"⏱ Frigate работает: {d}д {h}ч {m}м")
        await msg.edit_text("\n".join(lines))

    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not update.message:
            return
        msg = await update.message.reply_text("Собираю статистику...")
        today_start = datetime.combine(date.today(), dt_time.min).timestamp()
        events = await asyncio.to_thread(self.frigate.get_events, limit=500, after=today_start)
        if not events:
            await msg.edit_text("📊 За сегодня событий нет.")
            return
        by_label, by_camera = {}, {}
        for e in events:
            by_label[e.label] = by_label.get(e.label, 0) + 1
            by_camera[e.camera] = by_camera.get(e.camera, 0) + 1
        top_labels = sorted(by_label.items(), key=lambda x: -x[1])[:5]
        top_cameras = sorted(by_camera.items(), key=lambda x: -x[1])[:5]
        lines = [f"📊 Статистика за {datetime.now().strftime('%d.%m.%Y')}", f"Всего событий: {len(events)}", ""]
        lines.append("🏷 Топ меток:")
        for label, count in top_labels:
            lines.append(f"  {label}: {count}")
        lines.append("")
        lines.append("📷 Топ камер:")
        for cam, count in top_cameras:
            lines.append(f"  {cam}: {count}")
        await msg.edit_text("\n".join(lines))

    async def cmd_uptime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            return
        if not update.message:
            return
        delta = datetime.now() - self._start_time
        d, h, m = delta.days, delta.seconds // 3600, (delta.seconds % 3600) // 60
        lines = [
            "⏱ Uptime",
            f"🤖 Бот запущен: {self._start_time.strftime('%d.%m.%Y %H:%M:%S')}",
            f"⏳ Работает: {d}д {h}ч {m}м",
        ]
        stats = await asyncio.to_thread(self.frigate.get_stats)
        if stats and "service_uptime" in stats:
            s = stats["service_uptime"]
            fd, fh, fm = int(s // 86400), int((s % 86400) // 3600), int((s % 3600) // 60)
            lines.append(f"🖥 Frigate работает: {fd}д {fh}ч {fm}м")
        await update.message.reply_text("\n".join(lines))

    async def send_event_notification(self, event: FrigateEvent):
        if self._muted:
            logger.info("Muted, skipping notification for %s", event.id)
            return

        if self._quiet_start and self._quiet_end:
            now = datetime.now().time()
            if self._quiet_start <= self._quiet_end:
                if self._quiet_start <= now <= self._quiet_end:
                    logger.info("Quiet hours, skipping notification for %s", event.id)
                    return
            else:
                if now >= self._quiet_start or now <= self._quiet_end:
                    logger.info("Quiet hours, skipping notification for %s", event.id)
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
            BotCommand("sub", "Управление подписками"),
            BotCommand("cameras", "Список камер"),
            BotCommand("snapall", "Снимки со всех камер"),
            BotCommand("event", "Инфо о событии"),
            BotCommand("record", "Управление записью"),
            BotCommand("quiet", "Тихие часы"),
            BotCommand("health", "Состояние Frigate"),
            BotCommand("stats", "Статистика"),
            BotCommand("uptime", "Аптайм"),
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
