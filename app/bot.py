import asyncio
import functools
import io
import logging
from datetime import date, datetime, time as dt_time

from telegram import BotCommand, InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from app.frigate import FrigateClient, FrigateEvent

logger = logging.getLogger(__name__)


def authorized_only(func):
    @functools.wraps(func)
    async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not self._authorized(update):
            return
        return await func(self, update, context, *args, **kwargs)
    return wrapper


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

    def _authorized(self, update: Update) -> bool:
        if update.effective_chat and update.effective_chat.id != self.chat_id:
            logger.warning("Ignoring message from chat %d (expected %d)", update.effective_chat.id, self.chat_id)
            return False
        return True

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
        self.application.add_handler(CallbackQueryHandler(self._handle_menu_callback, pattern="^menu:"))
        self.application.add_handler(CallbackQueryHandler(self._handle_clip_callback, pattern="^clip:"))
        self.application.add_handler(CallbackQueryHandler(self._handle_snapall_callback, pattern="^snapall$"))
        self.application.add_handler(CallbackQueryHandler(self._handle_event_callback, pattern="^event:"))
        self.application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self._handle_text_input))

    async def _reply(self, update: Update, text: str, reply_markup=None):
        if update.message:
            await update.message.reply_text(text, reply_markup=reply_markup)

    @authorized_only
    async def cmd_start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cameras = await self.frigate.get_cameras()
        keyboard = []
        for cam in cameras:
            name = cam['name']
            keyboard.append([
                InlineKeyboardButton(f"📷 {name}", callback_data=f"menu:cam:{name}"),
                InlineKeyboardButton(f"🎥 {name}", callback_data=f"menu:clip:{name}"),
            ])
        keyboard += [
            [InlineKeyboardButton("📸 Снимки со всех камер", callback_data="menu:snapall")],
            [InlineKeyboardButton("🎥 Видео со всех камер", callback_data="menu:recordall")],
            [InlineKeyboardButton("🔔 Подписки", callback_data="menu:subs"),
             InlineKeyboardButton("🔇 Тихие часы", callback_data="menu:quiet")],
            [InlineKeyboardButton("✅ Вкл уведомления", callback_data="menu:unmute"),
             InlineKeyboardButton("❌ Выкл уведомления", callback_data="menu:mute")],
            [InlineKeyboardButton("📊 Статистика", callback_data="menu:stats"),
             InlineKeyboardButton("🖥 Здоровье", callback_data="menu:health")],
            [InlineKeyboardButton("⏱ Аптайм", callback_data="menu:uptime"),
             InlineKeyboardButton("🎥 Запись", callback_data="menu:record")],
            [InlineKeyboardButton("🏷 Подписаться", callback_data="menu:subscribe"),
             InlineKeyboardButton("🗑 Отписаться", callback_data="menu:unsubscribe")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await self._reply(update, "Привет! Я бот уведомлений Frigate.\n\n👇 Выбери действие:", reply_markup=reply_markup)

    @authorized_only
    async def cmd_help(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        await self.cmd_start(update, context)

    @authorized_only
    async def cmd_mute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._muted = True
        await self._reply(update, "Уведомления отключены.")

    @authorized_only
    async def cmd_unmute(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        self._muted = False
        await self._reply(update, "Уведомления включены.")

    @authorized_only
    async def cmd_cameras(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        cameras = await self.frigate.get_cameras()
        if not cameras:
            await self._reply(update, "Не удалось получить список камер.")
            return
        lines = ["Список камер:"]
        for cam in cameras:
            status = "✅" if cam.get("enabled", True) else "❌"
            lines.append(f"{status} {cam['name']}")
        await self._reply(update, "\n".join(lines))

    @authorized_only
    async def cmd_subscriptions(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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

    @authorized_only
    async def cmd_subscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.monitor:
            await self._reply(update, "Монитор не подключён.")
            return
        if not context.args:
            await self._reply(update, "Укажи: /subscribe <метка> или /subscribe camera <камера>")
            return
        
        if context.args[0] == "camera":
            if len(context.args) < 2:
                await self._reply(update, "Укажи название камеры: /subscribe camera <имя>")
                return
            camera_name = context.args[1]
            if not camera_name or not isinstance(camera_name, str):
                await self._reply(update, "❌ Неверное имя камеры")
                return
            result = self.monitor.add_include_camera(camera_name)
            await self._reply(update, f"📷 Камера {camera_name}: {result}")
        else:
            label = context.args[0]
            if not label or not isinstance(label, str):
                await self._reply(update, "❌ Неверная метка")
                return
            result = self.monitor.add_include_label(label)
            await self._reply(update, f"🏷 Метка {label}: {result}")

    @authorized_only
    async def cmd_unsubscribe(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not self.monitor:
            await self._reply(update, "Монитор не подключён.")
            return
        if not context.args:
            await self._reply(update, "Укажи: /unsubscribe <метка> или /unsubscribe camera <камера>")
            return
        
        if context.args[0] == "camera":
            if len(context.args) < 2:
                await self._reply(update, "Укажи название камеры: /unsubscribe camera <имя>")
                return
            camera_name = context.args[1]
            if not camera_name or not isinstance(camera_name, str):
                await self._reply(update, "❌ Неверное имя камеры")
                return
            result = self.monitor.remove_include_camera(camera_name)
            await self._reply(update, f"📷 Камера {camera_name}: {result}")
        else:
            label = context.args[0]
            if not label or not isinstance(label, str):
                await self._reply(update, "❌ Неверная метка")
                return
            result = self.monitor.remove_include_label(label)
            await self._reply(update, f"🏷 Метка {label}: {result}")

    @authorized_only
    async def cmd_event(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        if not context.args:
            await self._reply(update, "Укажи ID события: /event <id>")
            return
        
        event_id = context.args[0]
        # Валидация event_id (должен быть непустой строкой)
        if not event_id or not isinstance(event_id, str) or len(event_id) == 0:
            await self._reply(update, "❌ Неверный ID события")
            return
        
        msg = await update.message.reply_text("Запрашиваю событие...")
        event = await self.frigate.get_event(event_id)
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
        thumbnail = await self.frigate.get_thumbnail(event.id)
        if thumbnail:
            await msg.delete()
            await update.message.reply_photo(
                io.BytesIO(thumbnail),
                filename=f"{event.id[:8]}.jpg",
                caption=caption,
            )
            if event.has_clip:
                clip = await self.frigate.get_clip(event.id)
                if clip:
                    await update.message.reply_video(
                        io.BytesIO(clip),
                        filename=f"{event.id[:8]}.mp4",
                        read_timeout=120,
                        write_timeout=120,
                        connect_timeout=30,
                    )
        else:
            await msg.edit_text(caption)

    @authorized_only
    async def cmd_record(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        if not context.args:
            await self._reply(update, "Укажи камеру: /record <камера> [on|off]")
            return
        
        camera = context.args[0]
        # Валидация названия камеры
        if not camera or not isinstance(camera, str) or len(camera) == 0:
            await self._reply(update, "❌ Неверное имя камеры")
            return
        
        if len(context.args) > 1:
            action = context.args[1].lower()
            if action in ("on", "start", "вкл"):
                ok = await self.frigate.recording_start(camera)
                await self._reply(update, f"✅ Запись на {camera} включена" if ok else f"❌ Ошибка включения записи на {camera}")
            elif action in ("off", "stop", "выкл"):
                ok = await self.frigate.recording_stop(camera)
                await self._reply(update, f"✅ Запись на {camera} выключена" if ok else f"❌ Ошибка выключения записи на {camera}")
            else:
                await self._reply(update, "Используй: on/off")
        else:
            stats = await self.frigate.get_stats()
            if stats and "cameras" in stats:
                c = stats["cameras"].get(camera, {})
                rec = "🔴 вкл" if c.get("recording_enabled") else "⚫ выкл"
                det = "🎯 вкл" if c.get("detection_enabled") else "🎯 выкл"
                await self._reply(update, f"📷 {camera}\nЗапись: {rec}\nДетекция: {det}")
            else:
                await self._reply(update, f"Не удалось получить статус {camera}")

    @authorized_only
    async def cmd_quiet(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
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
            time_arg = context.args[0].strip()
            # Валидация формата времени
            if "-" not in time_arg:
                raise ValueError("Format must be HH:MM-HH:MM")
            
            times = time_arg.split("-")
            if len(times) != 2:
                raise ValueError("Expected exactly 2 times")
            
            sh, sm = times[0].strip().split(":")
            eh, em = times[1].strip().split(":")
            
            # Валидация часов и минут
            start_h, start_m = int(sh), int(sm)
            end_h, end_m = int(eh), int(em)
            
            if not (0 <= start_h <= 23 and 0 <= start_m <= 59):
                raise ValueError("Invalid start time")
            if not (0 <= end_h <= 23 and 0 <= end_m <= 59):
                raise ValueError("Invalid end time")
            
            self._quiet_start = dt_time(start_h, start_m)
            self._quiet_end = dt_time(end_h, end_m)
            await self._reply(update, f"🔇 Тихие часы: {self._quiet_start.strftime('%H:%M')} — {self._quiet_end.strftime('%H:%M')}")
        except (ValueError, IndexError) as e:
            logger.warning("Invalid quiet time format: %s", e)
            await self._reply(update, "❌ Неверный формат. Используй: /quiet 23:00-07:00")

    @authorized_only
    async def cmd_health(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        msg = await update.message.reply_text("Проверяю состояние Frigate...")
        await self._send_health_to_msg(msg)

    async def _send_health_to_msg(self, msg):
        version = await self.frigate.get_version()
        stats = await self.frigate.get_stats()
        cameras = await self.frigate.get_cameras()
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

    async def _send_health(self, query):
        msg = await query.message.reply_text("Проверяю состояние Frigate...")
        await self._send_health_to_msg(msg)

    @authorized_only
    async def cmd_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        msg = await update.message.reply_text("Собираю статистику...")
        await self._send_stats_to_msg(msg)

    async def _send_stats_to_msg(self, msg):
        today_start = datetime.combine(date.today(), dt_time.min).timestamp()
        events = await self.frigate.get_events(limit=500, after=today_start)
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

    async def _send_stats(self, query):
        msg = await query.message.reply_text("Собираю статистику...")
        await self._send_stats_to_msg(msg)

    @authorized_only
    async def cmd_uptime(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        await self._send_uptime_to_msg(update.message)

    async def _send_uptime_to_msg(self, msg):
        delta = datetime.now() - self._start_time
        d, h, m = delta.days, delta.seconds // 3600, (delta.seconds % 3600) // 60
        lines = [
            "⏱ Uptime",
            f"🤖 Бот запущен: {self._start_time.strftime('%d.%m.%Y %H:%M:%S')}",
            f"⏳ Работает: {d}д {h}ч {m}м",
        ]
        stats = await self.frigate.get_stats()
        if stats and "service_uptime" in stats:
            s = stats["service_uptime"]
            fd, fh, fm = int(s // 86400), int((s % 86400) // 3600), int((s % 3600) // 60)
            lines.append(f"🖥 Frigate работает: {fd}д {fh}ч {fm}м")
        await msg.reply_text("\n".join(lines))

    async def _send_uptime(self, query):
        await self._send_uptime_to_msg(query.message)

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
            caption = (f"🚨 Обнаружен {event.label}\n"
                       f"📷 Камера: {event.camera}\n"
                       f"⏱ {ts}{score}\n"
                       f"🆔 `{event.id[:12]}…`")

            keyboard = [[InlineKeyboardButton("ℹ️ Подробнее", callback_data=f"event:{event.id}")]]
            if event.has_clip:
                keyboard.append([InlineKeyboardButton("🎥 Смотреть видео", callback_data=f"clip:{event.id}")])
            reply_markup = InlineKeyboardMarkup(keyboard)

            if self.send_snapshot:
                thumbnail = await self.frigate.get_thumbnail(event.id)
                if thumbnail:
                    await self.application.bot.send_photo(
                        chat_id=self.chat_id,
                        photo=io.BytesIO(thumbnail),
                        filename=f"{event.camera}_{event.id[:8]}.jpg",
                        caption=caption,
                        reply_markup=reply_markup,
                    )
                    return

            if self.send_video and event.has_clip:
                clip = await self.frigate.get_clip(event.id)
                if clip:
                    await self.application.bot.send_video(
                        chat_id=self.chat_id,
                        video=io.BytesIO(clip),
                        caption=caption,
                        reply_markup=reply_markup,
                        read_timeout=120,
                        write_timeout=120,
                        connect_timeout=30,
                    )
                    return

            await self.application.bot.send_message(
                chat_id=self.chat_id,
                text=caption,
                reply_markup=reply_markup,
            )
        except Exception as exc:
            logger.error("Failed to send notification for event %s: %s", event.id, exc, exc_info=True)

    async def _handle_event_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        event_id = query.data.split(":", 1)[1]

        msg = await query.message.reply_text("Запрашиваю событие...")
        event = await self.frigate.get_event(event_id)
        if not event:
            await msg.edit_text("Событие не найдено.")
            return

        ts = event.start_time_dt.strftime('%d.%m.%Y %H:%M:%S')
        score = f" (уверенность {event.top_score:.0%})" if event.top_score else ""
        caption = (
            f"🚨 Событие {event.id[:12]}…\n"
            f"🏷 {event.label}{score}\n"
            f"📷 {event.camera}\n"
            f"⏱ {ts}"
        )

        thumbnail = await self.frigate.get_thumbnail(event.id)
        if thumbnail:
            await msg.delete()
            await query.message.reply_photo(
                io.BytesIO(thumbnail),
                filename=f"{event.id[:8]}.jpg",
                caption=caption,
            )
        else:
            await msg.edit_text(caption)

    async def _handle_clip_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer("🎥 Загружаю видео...")
        event_id = query.data.split(":", 1)[1]

        msg = await query.message.reply_text("Загружаю видео...")
        clip_data = await self.frigate.get_clip(event_id)
        if clip_data:
            await msg.delete()
            await query.message.reply_video(
                io.BytesIO(clip_data),
                filename=f"{event_id[:8]}.mp4",
                caption="🎥 Видео события",
                read_timeout=120,
                write_timeout=120,
                connect_timeout=30,
            )
        else:
            await msg.edit_text("❌ Не удалось загрузить видео.")

    async def _handle_menu_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        await query.answer()
        action = query.data.split(":", 1)[1] if ":" in query.data else query.data

        if action == "snapall":
            await self._send_snapall(query.message)

        elif action == "recordall":
            await self._send_recordall(query.message)

        elif action == "subs":
            filters = self.monitor.get_filters() if self.monitor else {}
            lines = ["📋 Текущие подписки:"]
            if filters:
                lines.append(f"🏷 Метки: {'все' if not filters['include_labels'] else ', '.join(filters['include_labels'])}")
                lines.append(f"📷 Камеры: {'все' if not filters['include_cameras'] else ', '.join(filters['include_cameras'])}")
            await query.message.reply_text("\n".join(lines))

        elif action.startswith("cam:"):
            camera_name = action.split(":", 1)[1]
            msg = await query.message.reply_text("Запрашиваю снимок...")
            data = await self.frigate.get_latest_snapshot(camera_name)
            if data:
                await msg.delete()
                await query.message.reply_photo(
                    io.BytesIO(data),
                    filename=f"{camera_name}.jpg",
                    caption=f"📷 {camera_name}\n{datetime.now().strftime('%H:%M:%S')}",
                )
            else:
                await msg.edit_text(f"Не удалось получить снимок с камеры {camera_name}")

        elif action.startswith("clip:"):
            camera_name = action.split(":", 1)[1]
            msg = await query.message.reply_text(f"🎥 Запрашиваю видео с {camera_name}...")
            clip_data = await self.frigate.get_last_clip(camera_name)
            if clip_data:
                await msg.delete()
                await query.message.reply_video(
                    io.BytesIO(clip_data),
                    filename=f"{camera_name}_{int(datetime.now().timestamp())}.mp4",
                    caption=f"🎥 {camera_name}\n{datetime.now().strftime('%H:%M:%S')}",
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                )
            else:
                await msg.edit_text(f"❌ Нет видео для камеры {camera_name}")

        elif action == "quiet":
            context.user_data["pending_action"] = "quiet"
            await query.message.reply_text(
                "🔇 Введи время тихих часов в формате HH:MM-HH:MM\n"
                "Например: 23:00-07:00\n"
                "Или отправь «off» чтобы выключить."
            )

        elif action == "mute":
            self._muted = True
            await query.message.reply_text("❌ Уведомления отключены.")

        elif action == "unmute":
            self._muted = False
            await query.message.reply_text("✅ Уведомления включены.")

        elif action == "stats":
            await self._send_stats(query)

        elif action == "health":
            await self._send_health(query)

        elif action == "uptime":
            await self._send_uptime(query)

        elif action == "record":
            cameras = await self.frigate.get_cameras()
            lines = ["🎥 Управление записью\n", "Напиши: /record <камера> on или /record <камера> off"]
            for cam in cameras:
                lines.append(f"  📷 {cam['name']}")
            await query.message.reply_text("\n".join(lines))

        elif action == "subscribe":
            context.user_data["pending_action"] = "subscribe"
            await query.message.reply_text(
                "🏷 Напиши метку для подписки (например: person, car)\n"
                "Или: camera <имя_камеры>"
            )

        elif action == "unsubscribe":
            context.user_data["pending_action"] = "unsubscribe"
            await query.message.reply_text(
                "🗑 Напиши метку для отписки (например: person, car)\n"
                "Или: camera <имя_камеры>"
            )

        else:
            await query.message.reply_text(f"Неизвестная команда: {action}")

    async def _handle_text_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message or not update.message.text:
            return
        pending = context.user_data.pop("pending_action", None)
        if not pending:
            return
        text = update.message.text.strip()

        if pending == "quiet":
            if text.lower() in ("off", "выкл", "disable"):
                self._quiet_start = self._quiet_end = None
                await self._reply(update, "🔇 Тихие часы отключены.")
                return
            try:
                if "-" not in text:
                    raise ValueError
                times = text.split("-")
                sh, sm = times[0].strip().split(":")
                eh, em = times[1].strip().split(":")
                sh_i, sm_i = int(sh), int(sm)
                eh_i, em_i = int(eh), int(em)
                if not (0 <= sh_i <= 23 and 0 <= sm_i <= 59 and 0 <= eh_i <= 23 and 0 <= em_i <= 59):
                    raise ValueError
                self._quiet_start = dt_time(sh_i, sm_i)
                self._quiet_end = dt_time(eh_i, em_i)
                await self._reply(update, f"🔇 Тихие часы: {self._quiet_start.strftime('%H:%M')} — {self._quiet_end.strftime('%H:%M')}")
            except (ValueError, IndexError):
                await self._reply(update, "❌ Неверный формат. Используй: HH:MM-HH:MM")

        elif pending == "subscribe":
            if not self.monitor:
                await self._reply(update, "Монитор не подключён.")
                return
            if text.startswith("camera "):
                name = text[7:].strip()
                result = self.monitor.add_include_camera(name) if name else "не указана"
                await self._reply(update, f"📷 Камера {name}: {result}")
            else:
                result = self.monitor.add_include_label(text)
                await self._reply(update, f"🏷 Метка {text}: {result}")

        elif pending == "unsubscribe":
            if not self.monitor:
                await self._reply(update, "Монитор не подключён.")
                return
            if text.startswith("camera "):
                name = text[7:].strip()
                result = self.monitor.remove_include_camera(name) if name else "не указана"
                await self._reply(update, f"📷 Камера {name}: {result}")
            else:
                result = self.monitor.remove_include_label(text)
                await self._reply(update, f"🏷 Метка {text}: {result}")

    def _make_cam_handler(self, camera_name: str):
        async def handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not self._authorized(update):
                return
            if not update.message:
                return
            msg = await update.message.reply_text("Запрашиваю снимок...")
            data = await self.frigate.get_latest_snapshot(camera_name)
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
        cameras = await self.frigate.get_cameras()
        for cam in cameras:
            name = cam["name"]
            self.application.add_handler(CommandHandler(name, self._make_cam_handler(name)))

    async def _send_snapall(self, msg_target):
        cameras = await self.frigate.get_cameras()
        if not cameras:
            await msg_target.reply_text("Не удалось получить список камер.")
            return

        msg = await msg_target.reply_text("Запрашиваю снимки со всех камер...")
        tasks = [self.frigate.get_latest_snapshot(cam["name"]) for cam in cameras]
        results = await asyncio.gather(*tasks)
        snapshots = dict(zip([cam["name"] for cam in cameras], results))
        await msg.delete()

        sent = 0
        for name, data in snapshots.items():
            if data:
                await msg_target.reply_photo(
                    io.BytesIO(data),
                    filename=f"{name}.jpg",
                    caption=f"📷 {name}\n{datetime.now().strftime('%H:%M:%S')}",
                )
                sent += 1

        if sent == 0:
            await msg_target.reply_text("Не удалось получить снимки ни с одной камеры.")
        elif sent < len(cameras):
            await msg_target.reply_text(f"Получено {sent} из {len(cameras)} снимков.")

    async def _send_recordall(self, msg_target):
        cameras = await self.frigate.get_cameras()
        if not cameras:
            await msg_target.reply_text("Не удалось получить список камер.")
            return

        status = await msg_target.reply_text("🎥 Запрашиваю видео со всех камер...")
        sent = 0
        for cam in cameras:
            name = cam["name"]
            await status.edit_text(f"🎥 {name}: загружаю...")
            data = await self.frigate.get_recording_clip(name, 10)
            if not data:
                data = await self.frigate.get_last_clip(name)
            if data:
                await msg_target.reply_video(
                    io.BytesIO(data),
                    filename=f"{name}_{int(datetime.now().timestamp())}.mp4",
                    caption=f"🎥 {name} (10с)\n{datetime.now().strftime('%H:%M:%S')}",
                    read_timeout=120,
                    write_timeout=120,
                    connect_timeout=30,
                )
                sent += 1

        if sent == 0:
            await status.edit_text("❌ Не удалось получить видео ни с одной камеры.")
        else:
            await status.edit_text(f"✅ Отправлено {sent} из {len(cameras)} видео.")

    @authorized_only
    async def cmd_snapall(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.message:
            return
        await self._send_snapall(update.message)

    async def _handle_snapall_callback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.callback_query
        logger.info("SNAPALL CALLBACK: data=%s, from=%s", query.data, query.from_user.id if query.from_user else None)
        await query.answer("Запрашиваю снимки...")
        await self._send_snapall(query.message)

    async def set_commands(self):
        cameras = await self.frigate.get_cameras()
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
            await self.application.updater.start_polling(allowed_updates=["message", "callback_query"])
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
