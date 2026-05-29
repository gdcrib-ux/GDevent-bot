import os, json, logging, asyncio
from datetime import date, timedelta, datetime
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from psycopg2cffi import compat
compat.register()
import psycopg2
from psycopg2.extras import RealDictCursor
from psycopg2.extras import RealDictCursor

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
API_KEY = os.environ.get("API_KEY", "changeme")
DATABASE_URL = os.environ["DATABASE_URL"]
PORT = int(os.environ.get("PORT", 8000))


def get_db():
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor, sslmode='require')


def init_db():
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("""
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
                )""")
            cur.execute("""
                CREATE TABLE IF NOT EXISTS config (
                    key TEXT PRIMARY KEY,
                    value TEXT
                )""")
        conn.commit()
    logging.info("Database initialized")


def get_reminder_time():
    try:
        with get_db() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT value FROM config WHERE key='reminderTime'")
                row = cur.fetchone()
                return row["value"] if row else "09:00"
    except:
        return "09:00"


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
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM events ORDER BY date, time")
            rows = cur.fetchall()
    return [dict(r) for r in rows]


@api.post("/api/events")
def create_event(ev: EventIn, _=Depends(auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO events (id,title,date,time,description,category,priority,contact,reminder_time)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                   title=EXCLUDED.title, date=EXCLUDED.date, time=EXCLUDED.time,
                   description=EXCLUDED.description, category=EXCLUDED.category,
                   priority=EXCLUDED.priority, contact=EXCLUDED.contact,
                   reminder_time=EXCLUDED.reminder_time""",
                (ev.id, ev.title, ev.date, ev.time, ev.description,
                 ev.category, ev.priority, ev.contact, ev.reminderTime))
        conn.commit()
    return ev


@api.put("/api/events/{eid}")
def update_event(eid: str, ev: EventIn, _=Depends(auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """INSERT INTO events (id,title,date,time,description,category,priority,contact,reminder_time)
                   VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
                   ON CONFLICT (id) DO UPDATE SET
                   title=EXCLUDED.title, date=EXCLUDED.date, time=EXCLUDED.time,
                   description=EXCLUDED.description, category=EXCLUDED.category,
                   priority=EXCLUDED.priority, contact=EXCLUDED.contact,
                   reminder_time=EXCLUDED.reminder_time""",
                (eid, ev.title, ev.date, ev.time, ev.description,
                 ev.category, ev.priority, ev.contact, ev.reminderTime))
        conn.commit()
    return ev


@api.delete("/api/events/{eid}")
def delete_event(eid: str, _=Depends(auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM events WHERE id=%s", (eid,))
        conn.commit()
    return {"deleted": eid}


@api.get("/api/config")
def get_config(_=Depends(auth)):
    return {"reminderTime": get_reminder_time()}


@api.put("/api/config")
def set_config(cfg: ConfigIn, _=Depends(auth)):
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO config (key,value) VALUES ('reminderTime',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value",
                (cfg.reminderTime,))
        conn.commit()
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
        "Добавляй события через приложение.",
        parse_mode="Markdown")


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    today_str = date.today().isoformat()
    with get_db() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM events WHERE date=%s ORDER BY time", (today_str,))
            rows = cur.fetchall()
    if not rows:
        await update.message.reply_text("✅ На сегодня событий нет"); return
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
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM events WHERE date=%s ORDER BY time", (tom,))
            rows = cur.fetchall()
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
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM events WHERE date BETWEEN %s AND %s ORDER BY date,time", (start, end))
            rows = cur.fetchall()
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
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM events WHERE date>=%s ORDER BY date,time", (date.today().isoformat(),))
            rows = cur.fetchall()
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
        with conn.cursor() as cur:
            cur.execute("SELECT * FROM events WHERE date=%s ORDER BY time", (today_str,))
            today_evs = cur.fetchall()
            cur.execute("SELECT * FROM events WHERE date=%s ORDER BY time", (tomorrow_str,))
            tom_evs = cur.fetchall()
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
        await server.serve()
        await tg_app.updater.stop()
        await tg_app.stop()


if __name__ == "__main__":
    asyncio.run(main())
