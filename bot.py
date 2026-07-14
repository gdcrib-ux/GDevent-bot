import os, logging, asyncio
from datetime import date, timedelta, datetime
from fastapi import FastAPI, HTTPException, Header, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import uvicorn
from telegram import Bot
from telegram.ext import Updater, CommandHandler
from apscheduler.schedulers.background import BackgroundScheduler
import psycopg2
import psycopg2.extras
from zoneinfo import ZoneInfo

logging.basicConfig(format="%(asctime)s - %(levelname)s - %(message)s", level=logging.INFO)

BOT_TOKEN = os.environ["BOT_TOKEN"]
CHAT_ID = int(os.environ["CHAT_ID"])
API_KEY = os.environ.get("API_KEY", "changeme")
DATABASE_URL = os.environ["DATABASE_URL"]
PORT = int(os.environ.get("PORT", 8000))
MOSCOW = ZoneInfo("Europe/Moscow")

bot = Bot(token=BOT_TOKEN)


def get_db():
    return psycopg2.connect(DATABASE_URL)


def init_db():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""CREATE TABLE IF NOT EXISTS events (
        id TEXT PRIMARY KEY, title TEXT NOT NULL, date TEXT NOT NULL,
        time TEXT DEFAULT '', description TEXT DEFAULT '',
        category TEXT DEFAULT '', priority TEXT DEFAULT 'Средний',
        contact TEXT DEFAULT '', reminder_time TEXT DEFAULT '09:00',
        recurrence TEXT DEFAULT 'none')""")
    try:
        cur.execute("ALTER TABLE events ADD COLUMN IF NOT EXISTS recurrence TEXT DEFAULT 'none'")
    except:
        pass
    cur.execute("""CREATE TABLE IF NOT EXISTS config (
        key TEXT PRIMARY KEY, value TEXT)""")
    conn.commit()
    cur.close()
    conn.close()
    logging.info("Database initialized")


def get_reminder_time():
    try:
        conn = get_db()
        cur = conn.cursor()
        cur.execute("SELECT value FROM config WHERE key='reminderTime'")
        row = cur.fetchone()
        cur.close()
        conn.close()
        return row[0] if row else "09:00"
    except:
        return "09:00"


EVENT_KEYS = ["id","title","date","time","description","category","priority","contact","reminder_time","recurrence"]


def pri_emoji(p):
    return {"Высокий": "🔺", "Средний": "🔸", "Низкий": "🔹"}.get(p, "•")


def recurrence_label(r):
    return {"weekly": "🔁 Еженедельно", "monthly": "🔁 Ежемесячно", "yearly": "🔁 Ежегодно"}.get(r, "")


def matches_recurrence(ev_date_str, recurrence, check_date):
    try:
        ev_date = date.fromisoformat(ev_date_str)
    except:
        return False
    if recurrence == "weekly":
        return ev_date.weekday() == check_date.weekday()
    elif recurrence == "monthly":
        return ev_date.day == check_date.day
    elif recurrence == "yearly":
        return ev_date.month == check_date.month and ev_date.day == check_date.day
    return False


def get_events_for_date(check_date):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    date_str = check_date.isoformat()
    cur.execute("SELECT * FROM events WHERE date=%s AND recurrence='none' ORDER BY time", (date_str,))
    one_time = cur.fetchall()
    cur.execute("SELECT * FROM events WHERE recurrence != 'none' ORDER BY time")
    recurring_all = cur.fetchall()
    cur.close()
    conn.close()
    result = [dict(r) for r in one_time]
    for r in recurring_all:
        ev = dict(r)
        if matches_recurrence(ev["date"], ev["recurrence"], check_date):
            result.append(ev)
    return sorted(result, key=lambda x: x["time"] or "")


# ── FastAPI ──────────────────────────────────────────────

api = FastAPI(title="Event Tracker API")
api.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


def auth(x_api_key: str = Header(...)):
    if x_api_key != API_KEY:
        raise HTTPException(401, "Invalid API key")


class EventIn(BaseModel):
    id: str; title: str; date: str; time: str = ""; description: str = ""
    category: str = ""; priority: str = "Средний"; contact: str = ""
    reminderTime: str = "09:00"; recurrence: str = "none"


class ConfigIn(BaseModel):
    reminderTime: str = "09:00"


@api.get("/api/events")
def list_events(_=Depends(auth)):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM events ORDER BY date, time")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    return [dict(r) for r in rows]


@api.post("/api/events")
def create_event(ev: EventIn, _=Depends(auth)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""INSERT INTO events (id,title,date,time,description,category,priority,contact,reminder_time,recurrence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title,date=EXCLUDED.date,
               time=EXCLUDED.time,description=EXCLUDED.description,category=EXCLUDED.category,
               priority=EXCLUDED.priority,contact=EXCLUDED.contact,
               reminder_time=EXCLUDED.reminder_time,recurrence=EXCLUDED.recurrence""",
               (ev.id,ev.title,ev.date,ev.time,ev.description,ev.category,ev.priority,ev.contact,ev.reminderTime,ev.recurrence))
    conn.commit()
    cur.close()
    conn.close()
    return ev


@api.put("/api/events/{eid}")
def update_event(eid: str, ev: EventIn, _=Depends(auth)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""INSERT INTO events (id,title,date,time,description,category,priority,contact,reminder_time,recurrence)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               ON CONFLICT (id) DO UPDATE SET title=EXCLUDED.title,date=EXCLUDED.date,
               time=EXCLUDED.time,description=EXCLUDED.description,category=EXCLUDED.category,
               priority=EXCLUDED.priority,contact=EXCLUDED.contact,
               reminder_time=EXCLUDED.reminder_time,recurrence=EXCLUDED.recurrence""",
               (eid,ev.title,ev.date,ev.time,ev.description,ev.category,ev.priority,ev.contact,ev.reminderTime,ev.recurrence))
    conn.commit()
    cur.close()
    conn.close()
    return ev


@api.delete("/api/events/{eid}")
def delete_event(eid: str, _=Depends(auth)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("DELETE FROM events WHERE id=%s", (eid,))
    conn.commit()
    cur.close()
    conn.close()
    return {"deleted": eid}


@api.get("/api/config")
def get_config(_=Depends(auth)):
    return {"reminderTime": get_reminder_time()}


@api.put("/api/config")
def set_config(cfg: ConfigIn, _=Depends(auth)):
    conn = get_db()
    cur = conn.cursor()
    cur.execute("INSERT INTO config (key,value) VALUES ('reminderTime',%s) ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value", (cfg.reminderTime,))
    conn.commit()
    cur.close()
    conn.close()
    return cfg


@api.get("/health")
def health():
    return {"status": "ok"}


# ── Telegram bot ─────────────────────────────────────────

def format_event_line(r, show_recurrence=True):
    line = f"{pri_emoji(r['priority'])} *{r['title']}*"
    if r["time"]: line += f" · {r['time']}"
    if r.get("recurrence") and r["recurrence"] != "none" and show_recurrence:
        line += f" {recurrence_label(r['recurrence'])}"
    return line


def cmd_start(update, ctx):
    update.message.reply_text(
        "👋 *Event Tracker Bot*\n\nКоманды:\n/today — события сегодня\n/tomorrow — события завтра\n/upcoming — ближайшие 7 дней\n/list — все предстоящие",
        parse_mode="Markdown")


def cmd_today(update, ctx):
    evs = get_events_for_date(date.today())
    if not evs:
        update.message.reply_text("✅ На сегодня событий нет"); return
    lines = [f"🔴 *Сегодня ({date.today().strftime('%d.%m')}):*\n"]
    for r in evs:
        line = format_event_line(r)
        if r["contact"]: line += f"\n   👤 {r['contact']}"
        if r["description"]: line += f"\n   📝 {r['description']}"
        lines.append(line)
    update.message.reply_text("\n\n".join(lines), parse_mode="Markdown")


def cmd_tomorrow(update, ctx):
    tom = date.today() + timedelta(1)
    evs = get_events_for_date(tom)
    if not evs:
        update.message.reply_text("✅ На завтра событий нет"); return
    lines = [f"⚠️ *Завтра ({tom.strftime('%d.%m')}):*\n"]
    for r in evs:
        line = format_event_line(r)
        if r["contact"]: line += f" · {r['contact']}"
        lines.append(line)
    update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def cmd_upcoming(update, ctx):
    lines = ["📅 *Ближайшие 7 дней:*\n"]
    found = False
    for i in range(8):
        d = date.today() + timedelta(i)
        evs = get_events_for_date(d)
        for r in evs:
            found = True
            label = "Сегодня" if i == 0 else ("Завтра" if i == 1 else f"+{i} дн.")
            lines.append(f"[{label}] {format_event_line(r)}")
    if not found:
        update.message.reply_text("📭 Нет событий на ближайшие 7 дней"); return
    update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def cmd_list(update, ctx):
    conn = get_db()
    cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
    cur.execute("SELECT * FROM events ORDER BY date,time")
    rows = cur.fetchall()
    cur.close()
    conn.close()
    evs = [dict(r) for r in rows]
    if not evs:
        update.message.reply_text("📭 Нет событий"); return
    lines = [f"📋 *Все события ({len(evs)}):*\n"]
    for r in evs:
        rec = recurrence_label(r.get("recurrence","none"))
        line = f"{pri_emoji(r['priority'])} *{r['title']}* — {r['date']}"
        if rec: line += f" {rec}"
        lines.append(line)
    update.message.reply_text("\n".join(lines), parse_mode="Markdown")


def send_daily_reminders():
    today = date.today()
    tomorrow = today + timedelta(1)
    today_evs = get_events_for_date(today)
    tom_evs = get_events_for_date(tomorrow)
    if tom_evs:
        lines = [f"⚠️ *Завтра ({tomorrow.strftime('%d.%m')}):*\n"]
        for r in tom_evs:
            line = format_event_line(r)
            if r["contact"]: line += f" · {r['contact']}"
            lines.append(line)
        bot.send_message(chat_id=CHAT_ID, text="\n".join(lines), parse_mode="Markdown")
    if today_evs:
        lines = [f"🔴 *Сегодня ({today.strftime('%d.%m')}):*\n"]
        for r in today_evs:
            line = format_event_line(r)
            if r["contact"]: line += f" · {r['contact']}"
            if r["description"]: line += f"\n   📝 {r['description']}"
            lines.append(line)
        bot.send_message(chat_id=CHAT_ID, text="\n\n".join(lines), parse_mode="Markdown")


def minute_check():
    current = datetime.now(MOSCOW).strftime("%H:%M")
    if current == get_reminder_time():
        send_daily_reminders()


def main():
    init_db()
    logging.info(f"Starting on port {PORT}")

    updater = Updater(token=BOT_TOKEN)
    dp = updater.dispatcher
    dp.add_handler(CommandHandler("start", cmd_start))
    dp.add_handler(CommandHandler("today", cmd_today))
    dp.add_handler(CommandHandler("tomorrow", cmd_tomorrow))
    dp.add_handler(CommandHandler("upcoming", cmd_upcoming))
    dp.add_handler(CommandHandler("list", cmd_list))
    updater.start_polling()

    scheduler = BackgroundScheduler(timezone="Europe/Moscow")
    scheduler.add_job(minute_check, "cron", minute="*")
    scheduler.start()

    uvicorn.run(api, host="0.0.0.0", port=PORT, log_level="warning")


if __name__ == "__main__":
    main()
