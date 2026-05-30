import os, logging, asyncio
from datetime import date, timedelta, datetime
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pg8000.native
from urllib.parse import urlparse

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
API_KEY = os.environ.get("API_KEY", "changeme")
DATABASE_URL = os.environ["DATABASE_URL"]
PORT = int(os.environ.get("PORT", 8000))


def parse_db_url(url):
    r = urlparse(url)
    return {
        "host": r.hostname,
        "port": r.port or 5432,
        "database": r.path.lstrip("/"),
        "user": r.username or "postgres",
        "password": r.password,
        "ssl_context": True
    }
    r = urlparse(url)
    return {"host": r.hostname, "port": r.port or 5432, "database": r.path[1:],
            "user": r.username, "password": r.password, "ssl_context": True}


def get_db():
    p = parse_db_url(DATABASE_URL)
    return pg8000.native.Connection(**p)


def init_db():
    conn = get_db()
    conn.run("""CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL,
        time TEXT DEFAULT '', description TEXT DEFAULT '',
        category TEXT DEFAULT '', priority TEXT DEFAULT 'Средний',
        contact TEXT DEFAULT '', reminder_time TEXT DEFAULT '09:00')""")
    conn.run("""CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, value TEXT)""")
    conn.close()
    logging.info("Database initialized")


def get_reminder_time():
    try:
        conn = get_db()
        rows = conn.run("SELECT value FROM config WHERE key='reminderTime'")
        conn.close()
        return rows[0][0] if rows else "09:00"
    except:
        return "09:00"


def row_to_dict(row, keys):
    return dict(zip(keys, row))


EVENT_KEYS = ["id","title","date","time","description","category","priority","contact","reminder_time"]


def pri_emoji(p):
    return {"Высокий": "🔺", "Средний": "🔸", "Низкий": "🔹"}.get(p, "•")


# ── FastAPI ──────────────────────────────────────────────

api = FastAPI(title="Event Tracker API")
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def auth(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")


class EventIn(BaseModel):
    id: str; title: str; date: str; time: str = ""; description: str = ""
    category: str = ""; priority: str = "Средний"; contact: str = ""; reminderTime: str = "09:00"


class ConfigIn(BaseModel):
    reminderTime: str = "09:00"


@api.get("/api/events")
def list_events(_=Depends(auth)):
    conn = get_db()
    rows = conn.run("SELECT * FROM events ORDER BY date, time")
    conn.close()
    return [row_to_dict(r, EVENT_KEYS) for r in rows]


@api.post("/api/events")
def create_event(ev: EventIn, _=Depends(auth)):
    conn = get_db()
    conn.run("""INSERT INTO events (id,title,date,time,description,category,priority,contact,reminder_time)
               VALUES (:id,:title,:date,:time,:desc,:cat,:pri,:contact,:rt)
               ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title,date=EXCLUDED.date,
               time=EXCLUDED.time,description=EXCLUDED.description,category=EXCLUDED.category,
               priority=EXCLUDED.priority,contact=EXCLUDED.contact,reminder_time=EXCLUDED.reminder_time""",
               id=ev.id,title=ev.title,date=ev.date,time=ev.time,desc=ev.description,
               cat=ev.category,pri=ev.priority,contact=ev.contact,rt=ev.reminderTime)
    conn.close()
    return ev


@api.put("/api/events/{eid}")
def update_event(eid: str, ev: EventIn, _=Depends(auth)):
    conn = get_db()
    conn.run("""INSERT INTO events (id,title,date,time,description,category,priority,contact,reminder_time)
               VALUES (:id,:title,:date,:time,:desc,:cat,:pri,:contact,:rt)
               ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title,date=EXCLUDED.date,
               time=EXCLUDED.time,description=EXCLUDED.description,category=EXCLUDED.category,
               priority=EXCLUDED.priority,contact=EXCLUDED.contact,reminder_time=EXCLUDED.reminder_time""",
               id=eid,title=ev.title,date=ev.date,time=ev.time,desc=ev.description,
               cat=ev.category,pri=ev.priority,contact=ev.contact,rt=ev.reminderTime)
    conn.close()
    return ev


@api.delete("/api/events/{eid}")
def delete_event(eid: str, _=Depends(auth)):
    conn = get_db()
    conn.run("DELETE FROM events WHERE id=:id", id=eid)
    conn.close()
    return {"deleted": eid}


@api.get("/api/config")
def get_config(_=Depends(auth)):
    return {"reminderTime": get_reminder_time()}


@api.put("/api/config")
def set_config(cfg: ConfigIn, _=Depends(auth)):
    conn = get_db()
    conn.run("INSERT INTO config (key,value) VALUES ('reminderTime',:v) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", v=cfg.reminderTime)
    conn.close()
    return cfg


@api.get("/health")
def health():
    return {"status": "ok"}


# ── Telegram bot ─────────────────────────────────────────

async def cmd_start(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 *Event Tracker Bot*\n\nКоманды:\n/today — события сегодня\n/tomorrow — события завтра\n/upcoming — ближайшие 7 дней\n/list — все предстоящие\n\nДобавляй события через приложение.",
        parse_mode="Markdown")


async def cmd_today(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.run("SELECT * FROM events WHERE date=:d ORDER BY time", d=date.today().isoformat())
    conn.close()
    if not rows:
        await update.message.reply_text("✅ На сегодня событий нет"); return
    evs = [row_to_dict(r, EVENT_KEYS) for r in rows]
    lines = [f"🔴 *Сегодня ({date.today().strftime('%d.%m')}):*\n"]
    for r in evs:
        line = f"{pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]: line += f" · {r['time']}"
        if r["contact"]: line += f"\n   👤 {r['contact']}"
        if r["description"]: line += f"\n   📝 {r['description']}"
        lines.append(line)
    await update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


async def cmd_tomorrow(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    tom = (date.today() + timedelta(1)).isoformat()
    conn = get_db()
    rows = conn.run("SELECT * FROM events WHERE date=:d ORDER BY time", d=tom)
    conn.close()
    if not rows:
        await update.message.reply_text("✅ На завтра событий нет"); return
    evs = [row_to_dict(r, EVENT_KEYS) for r in rows]
    lines = [f"⚠️ *Завтра ({(date.today()+timedelta(1)).strftime('%d.%m')}):*\n"]
    for r in evs:
        line = f"{pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]: line += f" · {r['time']}"
        if r["contact"]: line += f" · {r['contact']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_upcoming(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    start, end = date.today().isoformat(), (date.today() + timedelta(7)).isoformat()
    conn = get_db()
    rows = conn.run("SELECT * FROM events WHERE date BETWEEN :s AND :e ORDER BY date,time", s=start, e=end)
    conn.close()
    if not rows:
        await update.message.reply_text("📭 Нет событий на ближайшие 7 дней"); return
    evs = [row_to_dict(r, EVENT_KEYS) for r in rows]
    lines = ["📅 *Ближайшие 7 дней:*\n"]
    for r in evs:
        days = (date.fromisoformat(r["date"]) - date.today()).days
        label = "Сегодня" if days == 0 else ("Завтра" if days == 1 else f"+{days} дн.")
        line = f"[{label}] {pri_emoji(r['priority'])} *{r['title']}*"
        if r["time"]: line += f" · {r['time']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def cmd_list(update: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = get_db()
    rows = conn.run("SELECT * FROM events WHERE date>=:d ORDER BY date,time", d=date.today().isoformat())
    conn.close()
    if not rows:
        await update.message.reply_text("📭 Нет предстоящих событий"); return
    evs = [row_to_dict(r, EVENT_KEYS) for r in rows]
    lines = [f"📋 *Все предстоящие ({len(evs)}):*\n"]
    for r in evs:
        days = (date.fromisoformat(r["date"]) - date.today()).days
        label = "сегодня" if days == 0 else ("завтра" if days == 1 else f"через {days} дн.")
        line = f"{pri_emoji(r['priority'])} *{r['title']}* — {label}"
        if r["time"]: line += f" в {r['time']}"
        lines.append(line)
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def send_daily_reminders(app):
    today_str, tom_str = date.today().isoformat(), (date.today() + timedelta(1)).isoformat()
    conn = get_db()
    today_rows = conn.run("SELECT * FROM events WHERE date=:d ORDER BY time", d=today_str)
    tom_rows = conn.run("SELECT * FROM events WHERE date=:d ORDER BY time", d=tom_str)
    conn.close()
    today_evs = [row_to_dict(r, EVENT_KEYS) for r in today_rows]
    tom_evs = [row_to_dict(r, EVENT_KEYS) for r in tom_rows]
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
    from zoneinfo import ZoneInfo
    current = datetime.now(ZoneInfo("Europe/Moscow")).strftime("%H:%M")
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
