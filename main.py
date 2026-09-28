import os
import re
import time
import queue
import threading
from concurrent.futures import ThreadPoolExecutor

import requests
import telebot
from telebot import types

import config
import db
from downloader import download, extract_info, human_bytes, human_time, cancel

BOT = telebot.TeleBot(
    config.BOT_TOKEN,
    parse_mode="HTML",
    threaded=True,
    num_threads=8,
)

db.init_db()

URL_RE = re.compile(r"https?://[^\s<>\"]+", re.I)

# user_id -> preferences
PREFS = {}
PREF_LOCK = threading.RLock()

EXECUTOR = ThreadPoolExecutor(max_workers=config.MAX_CONCURRENT_JOBS)
QUEUES = {}
QUEUE_LOCK = threading.RLock()

# job_id -> dict
JOBS = {}
JOB_LOCK = threading.RLock()

def pref(user_id):
    with PREF_LOCK:
        return PREFS.setdefault(user_id, {
            "height": 1080,
            "fps": 30,
            "kind": "video",
        })

def first_url(text):
    if not text:
        return None
    m = URL_RE.search(text)
    if not m:
        return None
    return m.group(0).rstrip(").,!?]}>")

def esc(s):
    return (str(s or "")
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;"))

def progress_bar(pct):
    pct = max(0, min(100, int(pct)))
    filled = pct // 10
    return "█" * filled + "░" * (10 - filled)

def format_progress(d):
    total = d.get("total")
    got = d.get("downloaded", 0)
    pct = (got / total * 100) if total else 0
    speed = human_bytes(d.get("speed")) + "/s" if d.get("speed") else "?"
    eta = human_time(d.get("eta"))
    return (
        f"<b>PROCESSING</b>\n"
        f"<code>[{progress_bar(pct)}] {pct:5.1f}%</code>\n"
        f"📦 {human_bytes(got)} / {human_bytes(total)}\n"
        f"⚡ {speed}  •  ⏱ {eta}"
    )

def main_menu():
    kb = types.InlineKeyboardMarkup(row_width=2)
    kb.add(
        types.InlineKeyboardButton("🎬 Video", callback_data="kind:video"),
        types.InlineKeyboardButton("🎵 MP3", callback_data="kind:audio"),
    )
    kb.add(
        types.InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
        types.InlineKeyboardButton("📜 History", callback_data="history"),
    )
    kb.add(
        types.InlineKeyboardButton("❓ Help", callback_data="help"),
        types.InlineKeyboardButton("🏓 Ping", callback_data="ping"),
    )
    return kb

@BOT.message_handler(commands=["start"])
def start(message):
    db.touch_user(message.from_user)
    BOT.send_message(
        message.chat.id,
        "<b>ABSOLUTE CINEMA</b> 🎬\n\n"
        "Скинь ссылку на видео — я определю платформу и покажу доступные качества.\n\n"
        "Поддержка зависит от yt-dlp и конкретного сайта. "
        "Премиум не нужен.",
        reply_markup=main_menu()
    )

@BOT.message_handler(commands=["help"])
def help_cmd(message):
    BOT.send_message(
        message.chat.id,
        "<b>ABSOLUTE CINEMA — команды</b>\n\n"
        "/start — главное меню\n"
        "/settings — качество/FPS\n"
        "/history — история\n"
        "/cancel — остановить загрузку\n"
        "/stats — статистика (admin)\n"
        "/ping — состояние бота\n\n"
        "Просто отправь URL."
    )

@BOT.message_handler(commands=["ping"])
def ping_cmd(message):
    BOT.send_message(message.chat.id, "🏓 <b>PONG</b>\nРаботаю.")

@BOT.message_handler(commands=["cancel"])
def cancel_cmd(message):
    if cancel(message.from_user.id):
        BOT.send_message(message.chat.id, "🛑 Остановка текущей загрузки…")
    else:
        BOT.send_message(message.chat.id, "ℹ️ Активной загрузки нет.")

@BOT.message_handler(commands=["settings"])
def settings_cmd(message):
    send_settings(message.chat.id, message.from_user.id)

@BOT.message_handler(commands=["history"])
def history_cmd(message):
    rows = db.get_history(message.from_user.id, 10)
    if not rows:
        BOT.send_message(message.chat.id, "📜 История пока пустая.")
        return
    text = ["<b>📜 Последние загрузки</b>"]
    for r in rows:
        text.append(
            f"\n• {esc(r['title'] or 'Без названия')}\n"
            f"  {esc(r['kind'])} • {esc(r['quality'])} • {esc(r['status'])}"
        )
    BOT.send_message(message.chat.id, "\n".join(text))

@BOT.message_handler(commands=["stats"])
def stats_cmd(message):
    if message.from_user.id not in config.ADMIN_IDS:
        return
    users, jobs = db.stats()
    BOT.send_message(message.chat.id, f"📊 Users: <b>{users}</b>\nJobs: <b>{jobs}</b>")

def send_settings(chat_id, user_id):
    p = pref(user_id)
    kb = types.InlineKeyboardMarkup(row_width=3)
    for h in (360, 480, 720, 1080, 1440, 2160):
        mark = "✅" if p["height"] == h else ""
        kb.add(types.InlineKeyboardButton(f"{mark}{h}p", callback_data=f"h:{h}"))
    kb.add(
        types.InlineKeyboardButton(
            ("✅ " if p["fps"] == 30 else "") + "30 FPS",
            callback_data="fps:30"
        ),
        types.InlineKeyboardButton(
            ("✅ " if p["fps"] == 60 else "") + "60 FPS",
            callback_data="fps:60"
        )
    )
    BOT.send_message(
        chat_id,
        f"<b>⚙️ Settings</b>\n"
        f"Quality: <b>{p['height']}p</b>\n"
        f"FPS preference: <b>{p['fps']}</b>",
        reply_markup=kb
    )

@BOT.callback_query_handler(func=lambda c: True)
def callbacks(call):
    uid = call.from_user.id
    data = call.data
    try:
        if data == "settings":
            BOT.answer_callback_query(call.id)
            send_settings(call.message.chat.id, uid)
        elif data == "help":
            BOT.answer_callback_query(call.id)
            help_cmd(call.message)
        elif data == "ping":
            BOT.answer_callback_query(call.id, "PONG")
        elif data == "history":
            BOT.answer_callback_query(call.id)
            history_cmd(call.message)
        elif data.startswith("h:"):
            pref(uid)["height"] = int(data.split(":")[1])
            BOT.answer_callback_query(call.id, "Quality updated")
            send_settings(call.message.chat.id, uid)
        elif data.startswith("fps:"):
            pref(uid)["fps"] = int(data.split(":")[1])
            BOT.answer_callback_query(call.id, "FPS updated")
            send_settings(call.message.chat.id, uid)
        elif data.startswith("kind:"):
            pref(uid)["kind"] = data.split(":")[1]
            BOT.answer_callback_query(call.id, "Mode updated")
        elif data.startswith("dl:"):
            key = int(data.split(":", 1)[1])
            with JOB_LOCK:
                job = JOBS.get(key)
            if not job:
                BOT.answer_callback_query(call.id, "Задача устарела.")
                return
            if job["user_id"] != uid:
                BOT.answer_callback_query(call.id, "Эта задача принадлежит другому пользователю.")
                return
            BOT.answer_callback_query(call.id, "Добавлено в очередь…")
            EXECUTOR.submit(run_download, job)
    except Exception as e:
        BOT.answer_callback_query(call.id, str(e)[:150])

@BOT.message_handler(func=lambda m: bool(first_url(m.text or "")))
def url_handler(message):
    db.touch_user(message.from_user)
    url = first_url(message.text)

    # Basic queue guard
    with QUEUE_LOCK:
        q = QUEUES.setdefault(message.from_user.id, [])
        if len(q) >= config.MAX_QUEUE_PER_USER:
            BOT.reply_to(message, "⏳ Слишком много задач в очереди. Дождись завершения.")
            return
        q.append(time.time())

    msg = BOT.reply_to(message, "🔎 <b>Анализирую ссылку…</b>")

    def worker():
        try:
            info = extract_info(url)
            title = info.get("title") or "Без названия"
            duration = info.get("duration")
            uploader = info.get("uploader") or info.get("channel") or "Unknown"
            extractor = info.get("extractor_key") or info.get("extractor") or "unknown"

            kb = types.InlineKeyboardMarkup(row_width=2)
            kb.add(
                types.InlineKeyboardButton("🚀 Скачать", callback_data=f"dl:{hash(url)}"),
                types.InlineKeyboardButton("⚙️ Settings", callback_data="settings"),
            )

            with JOB_LOCK:
                JOBS[hash(url)] = {
                    "url": url,
                    "info": info,
                    "user_id": message.from_user.id,
                    "chat_id": message.chat.id,
                    "source_message": msg.message_id,
                }

            BOT.edit_message_text(
                f"🎬 <b>{esc(title)}</b>\n\n"
                f"👤 {esc(uploader)}\n"
                f"🌐 {esc(extractor)}\n"
                f"⏱ {human_time(duration)}\n\n"
                f"Выбрано: <b>{pref(message.from_user.id)['height']}p / "
                f"{pref(message.from_user.id)['fps']} FPS</b>",
                message.chat.id,
                msg.message_id,
                reply_markup=kb
            )
        except Exception as e:
            BOT.edit_message_text(
                f"❌ Не удалось прочитать ссылку.\n<code>{esc(str(e))}</code>",
                message.chat.id,
                msg.message_id
            )
        finally:
            with QUEUE_LOCK:
                q = QUEUES.get(message.from_user.id, [])
                if q:
                    q.pop(0)

    EXECUTOR.submit(worker)

@BOT.message_handler(func=lambda m: True)
def fallback(message):
    db.touch_user(message.from_user)
    BOT.send_message(
        message.chat.id,
        "🔗 Отправь ссылку на видео/пост из поддерживаемого сервиса."
    )

def run_download(job):
    uid = job["user_id"]
    chat_id = job["chat_id"]
    url = job["url"]
    p = pref(uid)

    status_msg = BOT.send_message(chat_id, "⏬ <b>Подготовка загрузки…</b>")
    last_edit = [0.0]

    def progress(data):
        now = time.time()
        if now - last_edit[0] < config.PROGRESS_INTERVAL and data.get("status") != "finished":
            return
        last_edit[0] = now
        try:
            BOT.edit_message_text(
                format_progress(data),
                chat_id,
                status_msg.message_id
            )
        except Exception:
            pass

    try:
        info, path = download(
            url, uid,
            kind=p["kind"],
            height=p["height"],
            fps=p["fps"],
            progress_cb=progress
        )

        db.increment_jobs(uid)
        db.add_history(uid, url, info.get("title"), p["kind"], f"{p['height']}p/{p['fps']}fps", "OK")

        BOT.edit_message_text(
            f"✅ <b>Готово</b>\n"
            f"{esc(info.get('title') or path.name)}\n"
            f"📦 {human_bytes(path.stat().st_size)}",
            chat_id,
            status_msg.message_id
        )

        with open(path, "rb") as f:
            if p["kind"] == "audio":
                BOT.send_audio(
                    chat_id, f,
                    caption="🎵 <b>ABSOLUTE CINEMA</b>"
                )
            else:
                BOT.send_document(
                    chat_id, f,
                    caption="🎬 <b>ABSOLUTE CINEMA</b>"
                )

        try:
            path.unlink()
        except OSError:
            pass

    except Exception as e:
        db.add_history(uid, url, job["info"].get("title"), p["kind"], f"{p['height']}p/{p['fps']}fps", "ERROR")
        BOT.edit_message_text(
            f"❌ <b>Ошибка</b>\n<code>{esc(str(e))}</code>",
            chat_id,
            status_msg.message_id
        )

def run():
    if not config.BOT_TOKEN or "PASTE_" in config.BOT_TOKEN:
        raise SystemExit("Set BOT_TOKEN in config.py first.")

    print("ABSOLUTE CINEMA started.")
    while True:
        try:
            BOT.infinity_polling(
                skip_pending=True,
                timeout=30,
                long_polling_timeout=30
            )
        except KeyboardInterrupt:
            EXECUTOR.shutdown(wait=False, cancel_futures=True)
            break
        except Exception as e:
            print("Polling error:", repr(e))
            time.sleep(5)

if __name__ == "__main__":
    run()
