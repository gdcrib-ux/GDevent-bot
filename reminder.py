import os
import requests
from datetime import date, timedelta

BOT_TOKEN = os.environ['BOT_TOKEN']
CHAT_ID = os.environ['CHAT_ID']
API_URL = os.environ['API_URL'].rstrip('/')
API_KEY = os.environ['API_KEY']

def get_events():
    try:
        r = requests.get(f'{API_URL}/api/events', 
                        headers={'X-API-Key': API_KEY},
                        timeout=60)
        data = r.json()
        if isinstance(data, list):
            return data
        return []
    except Exception as e:
        print(f'Error getting events: {e}')
        return []
def send(text):
    requests.post(f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage',
        json={'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'Markdown'})

def pri_emoji(p):
    return {'Высокий': '🔺', 'Средний': '🔸', 'Низкий': '🔹'}.get(p, '•')

def main():
    events = get_events()

def send(text):
    url = f'https://api.telegram.org/bot{BOT_TOKEN}/sendMessage'
    payload = {'chat_id': CHAT_ID, 'text': text, 'parse_mode': 'Markdown'}
    print(f'Sending to {CHAT_ID}...')
    r = requests.post(url, json=payload)
    print(f'Response: {r.status_code} - {r.text}')
    if tom_evs:
        lines = [f"⚠️ *Завтра ({tomorrow.strftime('%d.%m')}):*\n"]
        for e in tom_evs:
            line = f"{pri_emoji(e['priority'])} *{e['title']}*"
            if e['time']: line += f" · {e['time']}"
            if e['contact']: line += f" · {e['contact']}"
            lines.append(line)
        send('\n'.join(lines))

    if today_evs:
        lines = [f"🔴 *Сегодня ({today.strftime('%d.%m')}):*\n"]
        for e in today_evs:
            line = f"{pri_emoji(e['priority'])} *{e['title']}*"
            if e['time']: line += f"\n   🕐 {e['time']}"
            if e['contact']: line += f"\n   👤 {e['contact']}"
            if e['description']: line += f"\n   📝 {e['description']}"
            lines.append(line)
        send('\n\n'.join(lines))

if __name__ == '__main__':
    main()
