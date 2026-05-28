import os, json, sqlite3, logging, asyncio
from datetime import date, timedelta, datetime
from contextlib import asynccontextmanager
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import Optional
import uvicorn
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
API_KEY = os.environ.get("API_KEY", "changeme")
DB_PATH = os.environ.get("DB_PATH", "events.db")
PORT = int(os.environ.get("PORT", 8000))


def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with get_db() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS events (
            id TEXT PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL,
            time TEXT DEFAULT '', description TEXT DEFAULT '',
            category TEXT DEFAULT '', priority TEXT DEFAULT 'Средний',
            contact TEXT DEFAULT '', reminder_time TEXT DEFAULT '09:00')""")
        conn.execute("""CREATE TABLE IF NOT EXISTS config (
            key TEXT PRIMARY KEY, value TEXT)""")


def get_reminder_time():
    with get_db() as conn:
        row = conn.execute("SELECT value FROM config WHERE key='reminderTime'").fetchone()
        return row["value"] if row else "09:00"


def pri_emoji(p):
    return {"Высокий": "🔺", "Средний": "🔸", "Низкий": "🔹"}.get(p, "•")


# ── FastAPI ──────────────────────────────────────────────

api = FastAPI(title="Event Tracker API")
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def auth(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")


class EventIn(BaseModel):
    id: str
    title: str
    date: str
    time: str = ""
    description: str = ""
    category: str = ""
    priority: str = "Средний"
    contact: str = ""
    reminderTime: str = "09:00"


class ConfigIn(BaseModel):
    reminderTime: str = "09:00"


@api.get("/api/events")
def list_events(_=Depends(auth)):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events ORDER BY date, time").fetchall()
    return [dict(r) for r in rows]


@api.post("/api/events")
def create_event(ev: EventIn, _=Depends(auth)):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?)",
            (ev.id, ev.title, ev.date, ev.time, ev.description,
             ev.category, ev.priority, ev.contact, ev.reminderTime))
    return ev


@api.put("/api/events/{eid}")
def update_event(eid: str, ev: EventIn, _=Depends(auth)):
    with get_db() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO events VALUES (?,?,?,?,?,?,?,?,?)",
            (eid, ev.title, ev.date, ev.time, ev.description,
             ev.category, ev.priority, ev.contact, ev.reminderTime))
    return ev


@api.delete("/api/events/{eid}")
def delete_event(eid: str, _=Depends(auth)):
    with get_db() as conn:
        conn.execute("DELETE FROM events WHERE id=?", (eid,))
    return {"deleted": eid}


@api.get("/api/config")
def get_config(_=Depends(auth)):
    return {"reminderTime": get_reminder_time()}


@api.put("/api/config")
def set_config(cfg: ConfigIn, _=Depends(auth)):
    with get_db() as conn:
        conn.execute("INSERT OR REPLACE INTO config VALUES ('reminderTime',?)", (cfg.reminderTime,))
    return cfg


@api.get("/health")
def health():
    return {"status": "ok"}


# ── Telegram bot ─────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Event Tracker Bot*\n\n"
        "Команды:\n"
        "/today — события сегодня\n"
        "/tomorrow — события завтра\n"
        "/upcoming — ближайшие 7 дней\n"
        "/list — все предстоящие\n\n"
        "Добавляй события через приложение на телефоне.",
        parse_mode="Markdown")


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    today_str = date.today().isoformat()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE date=? ORDER BY time", (today_str,)).fetchall()
    if not rows:
        await update.message.reply_text("✅ На сегодня событий нет")
        return
    lines = [f"🔴 *Сегодня ({date.today().strftime('%d.%m')}):*\n"]
    for r in rows:
        line = f"{pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]: line += f" · {r['time']}"
        if r["contact"]: line += f"\n   👤 {r['contact']}"
        if r["description"]: line += f"\n   📝 {r['description']}"
        lines.append(line)
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def cmd_tomorrow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tom = (date.today() + timedelta(1)).isoformat()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE date=? ORDER BY time", (tom,)).fetchall()
    if not rows:
        await update.message.reply_text("✅ На завтра событий нет"); return
    lines = [f"⚠️ *Завтра ({(date.today()+timedelta(1)).strftime('%d.%m')}):*\n"]
    for r in rows:
        line = f"{pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]: line += f" · {r['time']}"
        if r["contact"]: line += f" · {r['contact']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_upcoming(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    start, end = date.today().isoformat(), (date.today() + timedelta(7)).isoformat()
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE date BETWEEN ? AND ? ORDER BY date,time", (start, end)).fetchall()
    if not rows:
        await update.message.reply_text("📭 Нет событий на ближайшие 7 дней"); return
    lines = ["📅 *Ближайшие 7 дней:*\n"]
    for r in rows:
        days = (date.fromisoformat(r["date"]) - date.today()).days
        label = "Сегодня" if days == 0 else ("Завтра" if days == 1 else f"+{days} дн.")
        line = f"[{label}] {pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]: line += f" · {r['time']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    with get_db() as conn:
        rows = conn.execute("SELECT * FROM events WHERE date>=? ORDER BY date,time", (date.today().isoformat(),)).fetchall()
    if not rows:
        await update.message.reply_text("📭 Нет предстоящих событий"); return
    lines = [f"📋 *Все предстоящие ({len(rows)}):*\n"]
    for r in rows:
        days = (date.fromisoformat(r["date"]) - date.today()).days
        label = "сегодня" if days == 0 else ("завтра" if days == 1 else f"через {days} дн.")
        line = f"{pri_emoji(r['priority'])} *{r['title']}* — {label}"
        if r["time"]: line += f" в {r['time']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def send_daily_reminders(app):
    today_str = date.today().isoformat()
    tomorrow_str = (date.today() + timedelta(1)).isoformat()
    with get_db() as conn:
        today_evs = conn.execute("SELECT * FROM events WHERE date=? ORDER BY time", (today_str,)).fetchall()
        tom_evs = conn.execute("SELECT * FROM events WHERE date=? ORDER BY time", (tomorrow_str,)).fetchall()
    if tom_evs:
        lines = [f"⚠️ *Завтра ({(date.today()+timedelta(1)).strftime('%d.%m')}):*\n"]
        for r in tom_evs:
            line = f"{pri_emoji(r['priority'])} *{r['title']}*"
            if r["time"]: line += f" · {r['time']}"
            if r["contact"]: line += f" · {r['contact']}"
            lines.append(line)
        await app.bot.send_message(chat_id=CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
    if today_evs:
        lines = [f"🔴 *Сегодня ({date.today().strftime('%d.%m')}):*\n"]
        for r in today_evs:
            line = f"{pri_emoji(r['priority'])} *{r['title']}*"
            if r["time"]: line += f" · {r['time']}"
            if r["contact"]: line += f" · {r['contact']}"
            if r["description"]: line += f"\n   📝 {r['description']}"
            lines.append(line)
        await app.bot.send_message(chat_id=CHAT_ID, text="\n\n".join(lines), parse_mode="Markdown")


async def run_minute_check(app):
    """Runs every minute, sends reminders when time matches config."""
    current = datetime.now().strftime("%H:%M")
    if current == get_reminder_time():
        await send_daily_reminders(app)


async def main():
    init_db()
    logging.info(f"Starting on port {PORT}")

    tg_app = Application.builder().token(BOT_TOKEN).build()
    tg_app.add_handler(CommandHandler("start", cmd_start))
    tg_app.add_handler(CommandHandler("today", cmd_today))
    tg_app.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    tg_app.add_handler(CommandHandler("upcoming", cmd_upcoming))
    tg_app.add_handler(CommandHandler("list", cmd_list))

    scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    scheduler.add_job(run_minute_check, "cron", minute="*", args=[tg_app])
    scheduler.start()

    server = uvicorn.Server(uvicorn.Config(api, host="0.0.0.0", port=PORT, log_level="warning"))

    async with tg_app:
        await tg_app.start()
        await tg_app.updater.start_polling(allowed_updates=Update.ALL_TYPES)
        logging.info("Bot started. API starting...")
        await server.serve()
        await tg_app.updater.stop()
        await tg_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
