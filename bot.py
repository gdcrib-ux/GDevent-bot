import os
import json
import sqlite3
import logging
from datetime import date, timedelta
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
DB_PATH = os.environ.get("DB_PATH", "events.db")


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                date TEXT NOT NULL,
                time TEXT DEFAULT '',
                description TEXT DEFAULT '',
                category TEXT DEFAULT '',
                priority TEXT DEFAULT 'Средний',
                contact TEXT DEFAULT '',
                reminder_time TEXT DEFAULT '09:00'
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS config (
                key TEXT PRIMARY KEY,
                value TEXT
            )
        """)


def get_reminder_time():
    with get_db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key='reminderTime'").fetchone()
        return row["value"] if row else "09:00"


def pri_emoji(p):
    return {"Высокий": "🔺", "Средний": "🔸", "Низкий": "🔹"}.get(p, "•")


async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = (
        "👋 *Event Tracker Bot*\n\n"
        "Команды:\n"
        "/today — события сегодня\n"
        "/tomorrow — события завтра\n"
        "/upcoming — ближайшие 7 дней\n"
        "/list — все предстоящие события\n"
        "/sync \\<JSON\\> — синхронизация из веб-приложения\n\n"
        "Уведомления приходят автоматически: за день до события и в день события."
    )
    await update.message.reply_text(text, parse_mode="Markdown")


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    today_str = date.today().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE date = ? ORDER BY time", (today_str,)
        ).fetchall()
    if not rows:
        await update.message.reply_text("✅ На сегодня событий нет")
        return
    lines = [f"🔴 *События на сегодня ({date.today().strftime('%d.%m.%Y')}):*\n"]
    for r in rows:
        line = f"{pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]:
            line += f" · {r['time']}"
        if r["category"]:
            line += f" \\[{r['category']}\\]"
        if r["contact"]:
            line += f"\n   👤 {r['contact']}"
        if r["description"]:
            line += f"\n   📝 {r['description']}"
        lines.append(line)
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def cmd_tomorrow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tom_str = (date.today() + timedelta(days=1)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE date = ? ORDER BY time", (tom_str,)
        ).fetchall()
    if not rows:
        await update.message.reply_text("✅ На завтра событий нет")
        return
    lines = [f"⚠️ *События завтра ({(date.today()+timedelta(1)).strftime('%d.%m.%Y')}):*\n"]
    for r in rows:
        line = f"{pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]:
            line += f" · {r['time']}"
        if r["contact"]:
            line += f" · {r['contact']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_upcoming(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    start = date.today().isoformat()
    end = (date.today() + timedelta(days=7)).isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE date BETWEEN ? AND ? ORDER BY date, time",
            (start, end),
        ).fetchall()
    if not rows:
        await update.message.reply_text("📭 Нет событий на ближайшие 7 дней")
        return
    lines = ["📅 *Ближайшие 7 дней:*\n"]
    for r in rows:
        d = date.fromisoformat(r["date"])
        days = (d - date.today()).days
        label = "Сегодня" if days == 0 else ("Завтра" if days == 1 else f"+{days} дн.")
        line = f"[{label}] {pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]:
            line += f" · {r['time']}"
        if r["contact"]:
            line += f" · {r['contact']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    today_str = date.today().isoformat()
    with get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM events WHERE date >= ? ORDER BY date, time", (today_str,)
        ).fetchall()
    if not rows:
        await update.message.reply_text("📭 Нет предстоящих событий")
        return
    lines = [f"📋 *Все предстоящие события ({len(rows)}):*\n"]
    for r in rows:
        days = (date.fromisoformat(r["date"]) - date.today()).days
        d_label = "сегодня" if days == 0 else ("завтра" if days == 1 else f"через {days} дн.")
        line = f"{pri_emoji(r['priority'])} *{r['title']}* — {d_label}"
        if r["time"]:
            line += f" в {r['time']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_sync(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    try:
        json_str = text[len("/sync"):].strip()
        data = json.loads(json_str)
        events = data.get("events", [])
        reminder_time = data.get("reminderTime", "09:00")

        with get_db() as conn:
            conn.execute("DELETE FROM events")
            for ev in events:
                conn.execute(
                    """INSERT OR REPLACE INTO events
                       (id, title, date, time, description, category, priority, contact, reminder_time)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ev.get("id", ""),
                        ev["title"],
                        ev["date"],
                        ev.get("time", ""),
                        ev.get("description", ""),
                        ev.get("category", ""),
                        ev.get("priority", "Средний"),
                        ev.get("contact", ""),
                        ev.get("reminderTime", reminder_time),
                    ),
                )
            conn.execute(
                "INSERT OR REPLACE INTO config VALUES ('reminderTime', ?)", (reminder_time,)
            )

        upcoming = sum(1 for e in events if e["date"] >= date.today().isoformat())
        await update.message.reply_text(
            f"✅ Синхронизировано {len(events)} событий ({upcoming} предстоящих)\n"
            f"⏰ Время напоминаний: {reminder_time}"
        )
        logging.info(f"Sync: {len(events)} events, reminder time: {reminder_time}")

    except (json.JSONDecodeError, KeyError) as e:
        await update.message.reply_text(
            f"❌ Ошибка синхронизации: {e}\n\n"
            "Используйте кнопку 'Синк с ботом' в веб-приложении — она скопирует правильный формат."
        )


async def send_daily_reminders(app):
    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(days=1)).isoformat()

    with get_db() as conn:
        today_evs = conn.execute(
            "SELECT * FROM events WHERE date = ? ORDER BY time", (today_str,)
        ).fetchall()
        tomorrow_evs = conn.execute(
            "SELECT * FROM events WHERE date = ? ORDER BY time", (tomorrow_str,)
        ).fetchall()

    # Reminder for tomorrow
    if tomorrow_evs:
        lines = [f"⚠️ *Завтра ({(date.today()+timedelta(1)).strftime('%d.%m')}):*\n"]
        for r in tomorrow_evs:
            line = f"{pri_emoji(r['priority'])} *{r['title']}*"
            if r["time"]:
                line += f" · {r['time']}"
            if r["contact"]:
                line += f" · {r['contact']}"
            if r["category"]:
                line += f" \\[{r['category']}\\]"
            lines.append(line)
        await app.bot.send_message(
            chat_id=CHAT_ID, text="\n".join(lines), parse_mode="Markdown"
        )
        logging.info(f"Sent tomorrow reminder: {len(tomorrow_evs)} events")

    # Reminder for today
    if today_evs:
        lines = [f"🔴 *Сегодня ({date.today().strftime('%d.%m')}):*\n"]
        for r in today_evs:
            line = f"{pri_emoji(r['priority'])} *{r['title']}*"
            if r["time"]:
                line += f" · {r['time']}"
            if r["contact"]:
                line += f" · {r['contact']}"
            if r["description"]:
                line += f"\n   📝 {r['description']}"
            lines.append(line)
        await app.bot.send_message(
            chat_id=CHAT_ID, text="\n\n".join(lines), parse_mode="Markdown"
        )
        logging.info(f"Sent today reminder: {len(today_evs)} events")


def main():
    init_db()

    reminder_time = get_reminder_time()
    hour, minute = map(int, reminder_time.split(":"))
    logging.info(f"Starting bot. Reminder time: {hour:02d}:{minute:02d}")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("today", cmd_today))
    app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    app.add_handler(CommandHandler("list", cmd_list))
    app.add_handler(CommandHandler("sync", cmd_sync))

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(
        send_daily_reminders,
        "cron",
        hour=hour,
        minute=minute,
        args=[app],
        id="daily_reminder",
    )
    scheduler.start()

    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
