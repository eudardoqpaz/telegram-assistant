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

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
FIREBASE_CREDS = os.environ.get("FIREBASE_CREDS", "")

db = None
if FIREBASE_CREDS:
    creds_dict = json.loads(FIREBASE_CREDS)
    cred = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()

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
        except:
            pass
    else:
        data = load_data(user_id, collection)
        data.append(item)
        user_dir = DATA_DIR / str(user_id)
        user_dir.mkdir(exist_ok=True)
        (user_dir / f"{collection}.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

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

def parse_reminder(text):
    msg = text.lower().strip()
    
    patterns = [
        r'recu[eé]rdame\s+(.+?)\s+(?:el|el d[ií]a)\s+(\d{1,2})\s+de\s+(\w+)\s+(?:a las|a la)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',
        r'recu[eé]rdame\s+(.+?)\s+(?:el)\s+(\d{1,2})[/-](\d{1,2})(?:[/-](\d{2,4}))?\s+(?:a las|a la)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',
        r'recu[eé]rdame\s+(.+?)\s+en\s+(\d+)\s+(minuto|hora|d[ií]a|semana)s?',
        r'recu[eé]rdame\s+(.+?)\s+(?:ma[nñ]ana|hoy)\s+(?:a las|a la)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',
        r'recu[eé]rdame\s+(.+?)\s+(?:el)\s+(lunes|martes|mi[eé]rcoles|jueves|viernes|s[aá]bado|domingo)\s+(?:a las|a la)\s+(\d{1,2})(?::(\d{2}))?\s*(am|pm)?',
        r'recu[eé]rdame\s+(.+)',
    ]
    
    for pattern in patterns:
        match = re.search(pattern, msg)
        if match:
            return match, pattern
    return None, None

def get_reminder_datetime(match, pattern):
    now = datetime.now()
    
    if 'en \\d+' in pattern:
        text = match.group(1)
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
        return text, dt
    
    elif 'mañana' in pattern or 'hoy' in pattern:
        text = match.group(1)
        hour = int(match.group(2))
        minute = int(match.group(3) or 0)
        ampm = match.group(4)
        
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        
        if 'mañana' in pattern:
            dt = now + timedelta(days=1)
        else:
            dt = now
        
        dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return text, dt
    
    elif 'lunes' in pattern or 'martes' in pattern:
        text = match.group(1)
        day_name = match.group(2)
        hour = int(match.group(3))
        minute = int(match.group(4) or 0)
        ampm = match.group(5)
        
        if ampm == 'pm' and hour < 12:
            hour += 12
        elif ampm == 'am' and hour == 12:
            hour = 0
        
        days_map = {'lunes': 0, 'martes': 1, 'miércoles': 2, 'miercoles': 2, 'jueves': 3, 'viernes': 4, 'sábado': 5, 'sabado': 5, 'domingo': 6}
        target_day = days_map.get(day_name, 0)
        current_day = now.weekday()
        days_ahead = target_day - current_day
        if days_ahead <= 0:
            days_ahead += 7
        
        dt = now + timedelta(days=days_ahead)
        dt = dt.replace(hour=hour, minute=minute, second=0, microsecond=0)
        return text, dt
    
    elif '\\d{1,2}\\s+de\\s+\\w+' in pattern:
        text = match.group(1)
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
            dt = datetime(now.year, month, day, hour, minute)
            if dt < now:
                dt = dt.replace(year=now.year + 1)
        except:
            dt = now + timedelta(hours=1)
        return text, dt
    
    else:
        text = match.group(1)
        return text, now + timedelta(hours=1)

def get_user_context(user_id):
    todos = load_data(user_id, "todos")
    reminders = load_data(user_id, "reminders")
    ctx = ""
    pending_todos = [t for t in todos if not t.get('done')]
    pending_rems = [r for r in reminders if not r.get('done')]
    if pending_todos:
        ctx += f"Tareas pendientes: {', '.join(t['text'] for t in pending_todos[-5:])}\n"
    if pending_rems:
        ctx += f"Recordatorios: {', '.join(r['text'] + ' (' + r['when'] + ')' for r in pending_rems[-3:])}\n"
    return ctx

def chat_with_ai(user_message, user_context=""):
    system = f"""Eres JoseBot, el asistente personal de Jose. Eres su amigo virtual, cercano y útil.

PERSONALIDAD:
- Habla natural, como un amigo, no como un robot
- Usa emojis moderadamente
- Sé breve pero cálido
- Si detecta que quiere agregar una tarea, hazlo tú mismo
- Si menciona un recordatorio, créalo automáticamente
- Responde siempre en español

CONTEXTO ACTUAL:
{user_context}"""

    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama3-8b-8192", "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message}
            ], "max_tokens": 500}, timeout=20)
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
    except:
        pass
    return None

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Ver tareas", callback_data="t"), InlineKeyboardButton("⏰ Recordatorios", callback_data="r")],
        [InlineKeyboardButton("📅 Eventos", callback_data="e"), InlineKeyboardButton("📊 Resumen", callback_data="s")]
    ]
    await update.message.reply_text(
        "¡Hola Jose! 👋 Soy tu asistente personal.\n\n"
        "Háblame normal, como a un amigo. Puedo:\n\n"
        "📝 Guardar tareas\n"
        "⏰ Crear recordatorios\n"
        "📅 Agendar eventos\n\n"
        "Ejemplos:\n"
        "💬 \"Tengo que comprar leche\"\n"
        "💬 \"Recuérdame llamar al doctor mañana a las 3pm\"\n"
        "💬 \"Recuérdame pagar la luz el 15 de julio a las 10am\"\n"
        "💬 \"Qué tengo pendiente?\"\n\n"
        "🎤 También entiendo audio!",
        reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **Ejemplos de lo que puedes decirme:**\n\n"
        "**Tareas:**\n"
        "• \"Tengo que hacer X\"\n"
        "• \"Necesito comprar X\"\n"
        "• \"No olvides pagar X\"\n\n"
        "**Recordatorios:**\n"
        "• \"Recuérdame llamar en 2 horas\"\n"
        "• \"Recuérdame pagar mañana a las 3pm\"\n"
        "• \"Recuérdame el 15 de julio a las 10am\"\n"
        "• \"Recuérdame el viernes a las 5pm\"\n\n"
        "**Consultas:**\n"
        "• \"Qué tengo pendiente?\"\n"
        "• \"Mis tareas\"\n"
        "• \"Cuáles son mis recordatorios?\"\n\n"
        "🎤 También puedes enviarme audio!",
        parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message.text
    user_context = get_user_context(uid)
    
    msg_lower = msg.lower()
    
    match, pattern = parse_reminder(msg)
    if match:
        text, dt = get_reminder_datetime(match, pattern)
        when_str = dt.strftime("%Y-%m-%d %H:%M")
        
        add_item(uid, "reminders", {
            "text": text,
            "when": when_str,
            "done": False,
            "created": datetime.now().isoformat()
        })
        
        time_display = dt.strftime("%H:%M")
        date_display = dt.strftime("%d/%m/%Y")
        
        await update.message.reply_text(
            f"⏰ ¡Listo! Te recuerdo:\n\n"
            f"📝 {text}\n"
            f"🕐 {time_display} del {date_display}"
        )
        return
    
    task_triggers = ['tengo que', 'necesito', 'no olvides', 'anota que', 'apunta que', 'pendiente de']
    for trigger in task_triggers:
        if trigger in msg_lower:
            task_text = msg
            for t in task_triggers:
                task_text = task_text.replace(t, '').strip()
            if task_text:
                add_item(uid, "todos", {"text": task_text, "done": False, "created": datetime.now().isoformat()})
                await update.message.reply_text(f"✅ Tarea guardada: {task_text}")
                return
    
    status_triggers = ['qué tengo', 'mis tareas', 'pendientes', 'resumen', 'qué hay', 'cuáles son mis']
    for trigger in status_triggers:
        if trigger in msg_lower:
            todos = [t for t in load_data(uid, "todos") if not t.get('done')]
            rems = [r for r in load_data(uid, "reminders") if not r.get('done')]
            
            txt = "📊 **Tu resumen:**\n\n"
            if todos:
                txt += "📋 **Tareas:**\n"
                for i, t in enumerate(todos[:5]):
                    txt += f"  {i+1}. {t['text']}\n"
            if rems:
                txt += "\n⏰ **Recordatorios:**\n"
                for i, r in enumerate(rems[:5]):
                    txt += f"  {i+1}. {r['text']} ({r['when']})\n"
            if not todos and not rems:
                txt += "¡No tienes nada pendiente! 🎉"
            
            await update.message.reply_text(txt, parse_mode='Markdown')
            return
    
    done_triggers = ['hecha', 'completada', 'terminé', 'listo', 'ya hice', 'marcar']
    for trigger in done_triggers:
        if trigger in msg_lower:
            nums = re.findall(r'\d+', msg)
            if nums:
                idx = int(nums[0]) - 1
                todos = load_data(uid, "todos")
                if 0 <= idx < len(todos):
                    todos[idx]['done'] = True
                    if db and '_id' in todos[idx]:
                        update_item(uid, "todos", todos[idx]['_id'], {'done': True})
                    else:
                        save_all(uid, "todos", todos)
                    await update.message.reply_text(f"✅ ¡Listo! \"{todos[idx]['text']}\" completada.")
                    return
            await update.message.reply_text("¿Cuál tarea? Dime el número.")
            return
    
    await update.message.reply_chat_action("typing")
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
    await update.message.reply_text(txt, parse_mode='Markdown')

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "t": await list_todos(update, context)
    elif q.data == "r": await list_reminders(update, context)
    elif q.data == "s": await summary(update, context)

async def check_reminders(app: Application):
    while True:
        try:
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
                                if datetime.now() >= reminder_time:
                                    await app.bot.send_message(int(uid), f"⏰ ¡RECORDATORIO!\n\n📝 {r['text']}")
                                    rems_ref.document(r['_id']).update({'done': True})
                            except:
                                pass
            else:
                for d in DATA_DIR.iterdir():
                    if d.is_dir():
                        uid = d.name
                        rems = load_data(uid, "reminders")
                        changed = False
                        for r in rems:
                            if not r.get('done') and r.get('when'):
                                try:
                                    if datetime.now() >= datetime.strptime(r['when'], "%Y-%m-%d %H:%M"):
                                        await app.bot.send_message(int(uid), f"⏰ ¡RECORDATORIO!\n\n📝 {r['text']}")
                                        r['done'] = True
                                        changed = True
                                except:
                                    pass
                        if changed:
                            save_all(uid, "reminders", rems)
        except Exception as e:
            logger.error(f"Error checking reminders: {e}")
        await asyncio.sleep(30)

def main():
    if not TOKEN:
        print("ERROR: Configura TELEGRAM_TOKEN")
        return
    
    logger.info("Bot iniciado!")
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
