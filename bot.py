import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import requests
import re
import firebase_admin
from firebase_admin import credentials, firestore
from zoneinfo import ZoneInfo

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
FIREBASE_CREDS = os.environ.get("FIREBASE_CREDS", "")
TIMEZONE = os.environ.get("TIMEZONE", "America/New_York")

db = None
if FIREBASE_CREDS:
    try:
        creds_dict = json.loads(FIREBASE_CREDS)
        cred = credentials.Certificate(creds_dict)
        firebase_admin.initialize_app(cred)
        db = firestore.client()
        logger.info("Firestore connected")
    except Exception as e:
        logger.error(f"Firestore error: {e}")

DATA_DIR = __import__('pathlib').Path("data")
DATA_DIR.mkdir(exist_ok=True)

def load_data(user_id, collection):
    if db:
        try:
            docs = db.collection('users').document(str(user_id)).collection(collection).stream()
            return [doc.to_dict() | {'_id': doc.id} for doc in docs]
        except:
            return []
    else:
        path = DATA_DIR / str(user_id) / f"{collection}.json"
        if path.exists():
            return json.loads(path.read_text(encoding='utf-8'))
        return []

def add_item(user_id, collection, item):
    if db:
        try:
            db.collection('users').document(str(user_id)).collection(collection).add(item)
            logger.info(f"Added to {collection}: {item}")
        except Exception as e:
            logger.error(f"Firestore add error: {e}")
    else:
        data = load_data(user_id, collection)
        data.append(item)
        user_dir = DATA_DIR / str(user_id)
        user_dir.mkdir(exist_ok=True)
        (user_dir / f"{collection}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        logger.info(f"Added to local {collection}: {item}")

def update_item(user_id, collection, item_id, data):
    if db:
        try:
            db.collection('users').document(str(user_id)).collection(collection).document(item_id).update(data)
        except:
            pass

def save_all(user_id, collection, data):
    if db:
        try:
            col_ref = db.collection('users').document(str(user_id)).collection(collection)
            for doc in col_ref.stream():
                doc.reference.delete()
            for item in data:
                col_ref.add(item)
        except:
            pass
    else:
        user_dir = DATA_DIR / str(user_id)
        user_dir.mkdir(exist_ok=True)
        (user_dir / f"{collection}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def get_now():
    return datetime.now(ZoneInfo(TIMEZONE))

def parse_reminder_regex(text):
    msg = text.lower().strip()
    msg = msg.replace('ó', 'o').replace('á', 'a').replace('é', 'e').replace('í', 'i').replace('ú', 'u')
    now = get_now()
    
    # Pattern: recuerdame en X minutos/horas/dias [texto]
    match = re.search(r'recu[eé]rdame\s+en\s+(\d+)\s+(minutos?|horas?|d[ií]as?|semanas?)\s+(.+)', msg)
    if match:
        amount = int(match.group(1))
        unit = match.group(2)
        task_text = match.group(3).strip()
        
        if 'minuto' in unit:
            dt = now + timedelta(minutes=amount)
        elif 'hora' in unit:
            dt = now + timedelta(hours=amount)
        elif 'd' in unit:
            dt = now + timedelta(days=amount)
        elif 'semana' in unit:
            dt = now + timedelta(weeks=amount)
        else:
            dt = now + timedelta(hours=1)
        
        return 'reminder', task_text, dt
    
    # Pattern: recuerdame [texto] en X minutos/horas/dias
    match = re.search(r'recu[eé]rdame\s+(.+?)\s+en\s+(\d+)\s+(minutos?|horas?|d[ií]as?|semanas?)', msg)
    if match:
        task_text = match.group(1).strip()
        amount = int(match.group(2))
        unit = match.group(3)
        
        if 'minuto' in unit:
            dt = now + timedelta(minutes=amount)
        elif 'hora' in unit:
            dt = now + timedelta(hours=amount)
        elif 'd' in unit:
            dt = now + timedelta(days=amount)
        elif 'semana' in unit:
            dt = now + timedelta(weeks=amount)
        else:
            dt = now + timedelta(hours=1)
        
        return 'reminder', task_text, dt
    
    # Pattern: recuerdame mañana a las X
    match = re.search(r'recu[eé]rdame\s+(.+?)\s+ma[nñ]ana\s+(?:a las?|a la)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', msg)
    if match:
        task_text = match.group(1).strip()
        hour = int(match.group(2))
        minute = int(match.group(3) or 0)
        ampm = match.group(4)
        
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        
        dt = now + timedelta(days=1)
        dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return 'reminder', task_text, dt
    
    # Pattern: recuerdame hoy a las X
    match = re.search(r'recu[eé]rdame\s+(.+?)\s+hoy\s+(?:a las?|a la)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', msg)
    if match:
        task_text = match.group(1).strip()
        hour = int(match.group(2))
        minute = int(match.group(3) or 0)
        ampm = match.group(4)
        
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        
        dt = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return 'reminder', task_text, dt
    
    # Pattern: recuerdame el dia X de mes a las Y
    match = re.search(r'recu[eé]rdame\s+(.+?)\s+(?:el|el dia)\s+(\d{1,2})\s+de\s+(\w+)\s+(?:a las?|a la)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', msg)
    if match:
        task_text = match.group(1).strip()
        day = int(match.group(2))
        month_name = match.group(3)
        hour = int(match.group(4))
        minute = int(match.group(5) or 0)
        ampm = match.group(6)
        
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        
        months = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}
        month = months.get(month_name, now.month)
        
        try:
            dt = now.replace(month=month, day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if dt < now:
                dt = dt.replace(year=now.year + 1)
        except:
            dt = now + timedelta(hours=1)
        return 'reminder', task_text, dt
    
    # Pattern: recuerdame el dia X a las Y
    match = re.search(r'recu[eé]rdame\s+(.+?)\s+(?:el|el dia)\s+(\d{1,2})\s+(?:a las?|a la)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?', msg)
    if match:
        task_text = match.group(1).strip()
        day = int(match.group(2))
        hour = int(match.group(3))
        minute = int(match.group(4) or 0)
        ampm = match.group(5)
        
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        
        try:
            dt = now.replace(day=day, hour=hour, minute=minute, second=0, microsecond=0)
            if dt < now:
                if now.month == 12:
                    dt = dt.replace(year=now.year + 1, month=1)
                else:
                    dt = dt.replace(month=now.month + 1)
        except:
            dt = now + timedelta(hours=1)
        return 'reminder', task_text, dt
    
    # Pattern: recuerdame el dia X
    match = re.search(r'recu[eé]rdame\s+(.+?)\s+(?:el|el dia)\s+(\d{1,2})(?:\s+de\s+(\w+))?', msg)
    if match:
        task_text = match.group(1).strip()
        day = int(match.group(2))
        month_name = match.group(3)
        
        months = {'enero': 1, 'febrero': 2, 'marzo': 3, 'abril': 4, 'mayo': 5, 'junio': 6, 'julio': 7, 'agosto': 8, 'septiembre': 9, 'octubre': 10, 'noviembre': 11, 'diciembre': 12}
        month = months.get(month_name, now.month) if month_name else now.month
        
        try:
            dt = now.replace(month=month, day=day, hour=10, minute=0, second=0, microsecond=0)
            if dt < now:
                if month == 12:
                    dt = dt.replace(year=now.year + 1, month=1)
                else:
                    dt = dt.replace(month=month + 1)
        except:
            dt = now + timedelta(days=1)
        return 'reminder', task_text, dt
    
    # Pattern: recuerdame [texto] (default: 1 hour)
    match = re.search(r'recu[eé]rdame\s+(.+)', msg)
    if match:
        task_text = match.group(1).strip()
        return 'reminder', task_text, now + timedelta(hours=1)
    
    # Pattern: no olvides [texto]
    match = re.search(r'no\s+olvides?\s+(.+)', msg)
    if match:
        task_text = match.group(1).strip()
        return 'reminder', task_text, now + timedelta(hours=1)
    
    # Pattern: tengo que [texto]
    match = re.search(r'tengo\s+que\s+(.+)', msg)
    if match:
        task_text = match.group(1).strip()
        return 'task', task_text, None
    
    # Pattern: necesito [texto]
    match = re.search(r'necesito\s+(.+)', msg)
    if match:
        task_text = match.group(1).strip()
        return 'task', task_text, None
    
    # Pattern: anota [texto]
    match = re.search(r'anota\s+(.+)', msg)
    if match:
        task_text = match.group(1).strip()
        return 'task', task_text, None
    
    # Pattern: que tengo / mis tareas / pendientes
    if re.search(r'(que tengo|mis tareas|pendientes|resumen|que hay)', msg):
        return 'summary', None, None
    
    # Pattern: hecha/completada [numero]
    match = re.search(r'(hecha|completada|terminé|listo|ya hice)\s+(?:la\s+)?(?:tarea\s+)?(\d+)', msg)
    if match:
        return 'done', int(match.group(2)), None
    
    return None, None, None

def get_user_context(user_id):
    todos = load_data(user_id, "todos")
    reminders = load_data(user_id, "reminders")
    ctx = ""
    pending_todos = [t for t in todos if not t.get('done')]
    pending_rems = [r for r in reminders if not r.get('done')]
    if pending_todos:
        ctx += f"Tareas pendientes: {', '.join(t['text'] for t in pending_todos[-5:])}\n"
    if pending_rems:
        ctx += f"Recordatorios activos: {', '.join(r['text'] + ' (' + r['when'] + ')' for r in pending_rems[-3:])}\n"
    return ctx

def chat_with_ai(user_message, user_context=""):
    if not GROQ_KEY:
        return None
    
    now = get_now()
    system = f"""Eres JoseBot, el asistente personal de Jose. Eres su amigo virtual, cercano y útil.

PERSONALIDAD:
- Habla natural, como un amigo, no como un robot
- Usa emojis moderadamente
- Sé breve pero cálido
- Responde siempre en español

FECHA ACTUAL: {now.strftime("%Y-%m-%d %H:%M")} ({TIMEZONE})

CONTEXTO ACTUAL:
{user_context}"""

    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama3-8b-8192", "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message}
            ], "max_tokens": 300, "temperature": 0.7}, timeout=15)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"AI error: {e}")
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Ver tareas", callback_data="t"), InlineKeyboardButton("⏰ Recordatorios", callback_data="r")],
        [InlineKeyboardButton("📊 Resumen", callback_data="s"), InlineKeyboardButton("❓ Ayuda", callback_data="h")]
    ]
    await update.message.reply_text(
        "¡Hola Jose! 👋 Soy tu asistente personal.\n\n"
        "Dime lo que necesites:\n\n"
        "💬 \"Recuérdame en 5 minutos buscar el cargador\"\n"
        "💬 \"Tengo que comprar leche\"\n"
        "💬 \"Qué tengo pendiente?\"\n\n"
        "🎤 También entiendo audio!",
        reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **Ejemplos:**\n\n"
        "**Recordatorios:**\n"
        "• \"Recuérdame en 5 minutos buscar el cargador\"\n"
        "• \"Recuérdame mañana a las 3pm llamar al doctor\"\n"
        "• \"No olvides pagar la luz el viernes\"\n\n"
        "**Tareas:**\n"
        "• \"Tengo que comprar leche\"\n"
        "• \"Necesito hacer X\"\n\n"
        "**Consultas:**\n"
        "• \"Qué tengo pendiente?\"\n"
        "• \"Mis recordatorios\"\n\n"
        "🎤 ¡También puedes enviarme audio!",
        parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message.text
    user_context = get_user_context(uid)
    
    await update.message.reply_chat_action("typing")
    logger.info(f"Processing message: {msg}")
    
    # First try regex patterns
    action, data, dt = parse_reminder_regex(msg)
    logger.info(f"Regex result: action={action}, data={data}, dt={dt}")
    
    if action == "reminder":
        text = data
        when_str = dt.strftime("%Y-%m-%d %H:%M")
        
        add_item(uid, "reminders", {
            "text": text,
            "when": when_str,
            "done": False,
            "created": get_now().isoformat()
        })
        
        time_display = dt.strftime("%H:%M")
        date_display = dt.strftime("%d/%m/%Y")
        await update.message.reply_text(
            f"⏰ ¡Listo! Te recuerdo:\n\n"
            f"📝 {text}\n"
            f"🕐 {time_display} del {date_display}"
        )
        return
    
    elif action == "task":
        text = data
        add_item(uid, "todos", {"text": text, "done": False, "created": get_now().isoformat()})
        await update.message.reply_text(f"✅ Tarea guardada: {text}")
        return
    
    elif action == "summary":
        todos = [t for t in load_data(uid, "todos") if not t.get('done')]
        rems = [r for r in load_data(uid, "reminders") if not r.get('done')]
        txt = f"📊 **Resumen:**\n\n📋 Tareas: {len(todos)}\n⏰ Recordatorios: {len(rems)}\n\n"
        if todos:
            txt += "**Tareas:**\n" + "".join(f"  • {t['text']}\n" for t in todos[:5])
        if rems:
            txt += "\n**Recordatorios:**\n" + "".join(f"  • {r['text']} ({r['when']})\n" for r in rems[:3])
        if not todos and not rems:
            txt += "¡No tienes nada pendiente! 🎉"
        await update.message.reply_text(txt, parse_mode='Markdown')
        return
    
    elif action == "done":
        idx = data - 1
        todos = load_data(uid, "todos")
        if 0 <= idx < len(todos):
            todos[idx]['done'] = True
            if db and '_id' in todos[idx]:
                update_item(uid, "todos", todos[idx]['_id'], {'done': True})
            else:
                save_all(uid, "todos", todos)
            await update.message.reply_text(f"✅ ¡Listo! \"{todos[idx]['text']}\" completada.")
        else:
            await update.message.reply_text("¿Cuál tarea? Dime el número.")
        return
    
    # If no regex match, try AI
    response = await asyncio.to_thread(chat_with_ai, msg, user_context)
    if response:
        await update.message.reply_text(response)
    else:
        await update.message.reply_text("No entendí bien. ¿Puedes repetir?")

async def handle_audio(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    await update.message.reply_text("🎤 Escuchando...")
    
    try:
        file = await update.message.voice.get_file() if update.message.voice else await update.message.audio.get_file()
        audio_bytes = await file.download_as_bytearray()
        
        import speech_recognition as sr
        from pydub import AudioSegment
        import io
        
        audio = AudioSegment.from_file(io.BytesIO(audio_bytes))
        wav_io = io.BytesIO()
        audio.export(wav_io, format="wav")
        wav_io.seek(0)
        
        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_io) as source:
            audio_data = recognizer.record(source)
            text = recognizer.recognize_google(audio_data, language='es-ES')
        
        await update.message.reply_text(f"📝 \"{text}\"")
        
        update.message.text = text
        await handle_message(update, context)
        
    except Exception as e:
        logger.error(f"Audio error: {e}")
        await update.message.reply_text("No pude entender el audio. ¿Puedes repetir o escribirlo?")

async def list_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    todos = load_data(update.effective_user.id, "todos")
    if not todos:
        await update.message.reply_text("📋 No tienes tareas pendientes. ¡Estás al día! 🎉")
        return
    txt = "📋 **Tus tareas:**\n\n"
    for i, t in enumerate(todos):
        txt += f"{i+1}. {'✅' if t.get('done') else '⬜'} {t['text']}\n"
    txt += "\nDime \"hecha la tarea X\" para completarla"
    await update.message.reply_text(txt, parse_mode='Markdown')

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rems = [r for r in load_data(update.effective_user.id, "reminders") if not r.get('done')]
    if not rems:
        await update.message.reply_text("⏰ No tienes recordatorios pendientes.")
        return
    txt = "⏰ **Recordatorios:**\n\n"
    for i, r in enumerate(rems):
        txt += f"{i+1}. {r['text']} - {r['when']}\n"
    await update.message.reply_text(txt, parse_mode='Markdown')

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    todos = [t for t in load_data(uid, "todos") if not t.get('done')]
    rems = [r for r in load_data(uid, "reminders") if not r.get('done')]
    txt = f"📊 **Resumen:**\n\n📋 Tareas: {len(todos)}\n⏰ Recordatorios: {len(rems)}\n\n"
    if todos:
        txt += "**Tareas:**\n" + "".join(f"  • {t['text']}\n" for t in todos[:5])
    if rems:
        txt += "\n**Recordatorios:**\n" + "".join(f"  • {r['text']} ({r['when']})\n" for r in rems[:3])
    if not todos and not rems:
        txt += "¡No tienes nada pendiente! 🎉"
    await update.message.reply_text(txt, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "t": await list_todos(update, context)
    elif q.data == "r": await list_reminders(update, context)
    elif q.data == "s": await summary(update, context)
    elif q.data == "h": await help_cmd(update, context)

async def check_reminders(app: Application):
    logger.info(f"Reminder checker started - timezone: {TIMEZONE}")
    while True:
        try:
            now = get_now()
            logger.info(f"Checking reminders at {now.strftime('%Y-%m-%d %H:%M:%S')}")
            
            if db:
                users = db.collection('users').stream()
                for user_doc in users:
                    uid = user_doc.id
                    rems_ref = user_doc.reference.collection('reminders')
                    rems = [doc.to_dict() | {'_id': doc.id} for doc in rems_ref.stream()]
                    for r in rems:
                        if not r.get('done') and r.get('when'):
                            try:
                                reminder_time = datetime.strptime(r['when'], "%Y-%m-%d %H:%M")
                                reminder_time = reminder_time.replace(tzinfo=ZoneInfo(TIMEZONE))
                                if now >= reminder_time:
                                    logger.info(f"Sending reminder to {uid}: {r['text']}")
                                    await app.bot.send_message(int(uid), f"⏰ ¡RECORDATORIO!\n\n📝 {r['text']}")
                                    rems_ref.document(r['_id']).update({'done': True})
                            except Exception as e:
                                logger.error(f"Reminder error: {e}")
            else:
                for d in DATA_DIR.iterdir():
                    if d.is_dir():
                        uid = d.name
                        rems = load_data(uid, "reminders")
                        changed = False
                        for r in rems:
                            if not r.get('done') and r.get('when'):
                                try:
                                    reminder_time = datetime.strptime(r['when'], "%Y-%m-%d %H:%M")
                                    reminder_time = reminder_time.replace(tzinfo=ZoneInfo(TIMEZONE))
                                    if now >= reminder_time:
                                        logger.info(f"Sending reminder to {uid}: {r['text']}")
                                        await app.bot.send_message(int(uid), f"⏰ ¡RECORDATORIO!\n\n📝 {r['text']}")
                                        r['done'] = True
                                        changed = True
                                except Exception as e:
                                    logger.error(f"Reminder error: {e}")
                        if changed:
                            save_all(uid, "reminders", rems)
        except Exception as e:
            logger.error(f"Error checking reminders: {e}")
        await asyncio.sleep(15)

def main():
    if not TOKEN:
        print("ERROR: Configura TELEGRAM_TOKEN")
        return
    
    logger.info(f"Bot iniciado! Timezone: {TIMEZONE}")
    app = Application.builder().token(TOKEN).build()
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("tareas", list_todos))
    app.add_handler(CommandHandler("recordatorios", list_reminders))
    app.add_handler(CommandHandler("resumen", summary))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, handle_audio))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    
    async def post_init(application):
        asyncio.create_task(check_reminders(application))
    app.post_init = post_init
    
    logger.info("Esperando mensajes...")
    app.run_polling()

if __name__ == '__main__':
    main()
