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
import firebase_admin
from firebase_admin import credentials, firestore

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
FIREBASE_CREDS = os.environ.get("FIREBASE_CREDS", "")

if FIREBASE_CREDS:
    creds_dict = json.loads(FIREBASE_CREDS)
    cred = credentials.Certificate(creds_dict)
    firebase_admin.initialize_app(cred)
    db = firestore.client()
else:
    try:
        cred = credentials.ApplicationDefault()
        firebase_admin.initialize_app(cred)
        db = firestore.client()
    except:
        db = None

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

def get_user_context(user_id):
    todos = load_data(user_id, "todos")
    reminders = load_data(user_id, "reminders")
    events = load_data(user_id, "events")
    ctx = ""
    pending_todos = [t for t in todos if not t.get('done')]
    pending_rems = [r for r in reminders if not r.get('done')]
    if pending_todos:
        ctx += f"Tareas pendientes: {', '.join(t['text'] for t in pending_todos[-5:])}\n"
    if pending_rems:
        ctx += f"Recordatorios: {', '.join(r['text'] + ' (' + r['when'] + ')' for r in pending_rems[-3:])}\n"
    if events:
        ctx += f"Eventos: {', '.join(e['title'] + ' el ' + e['date'] for e in events[-3:])}\n"
    return ctx

def chat_with_ai(user_message, user_context=""):
    system = f"""Eres JoseBot, el asistente personal de Jose. Eres su amigo virtual, cercano y útil.

PERSONALIDAD:
- Habla natural, como un amigo, no como un robot
- Usa emojis moderadamente
- Sé breve pero cálido
- Si detecta que quiere agregar una tarea o recordatorio, hazlo tú mismo sin pedirle que use comandos
- Si te envía algo que suena como tarea, agrégala automáticamente
- Si menciona una fecha/hora, crea el recordatorio automáticamente
- Responde siempre en español

HERRAMIENTAS INTERNAS:
Si el usuario dice algo como:
- "tengo que...", "necesito...", "no olvides...", "recuérdame..." → Agrégalo como tarea
- "recuérdame en X horas/minutos/días..." → Crea recordatorio
- "tengo una reunión/cita/evento el..." → Crea evento
- "qué tengo pendiente", "mis tareas" → Muestra resumen
- "marca como hecha la tarea X" → Marca como completada

CONTEXTO ACTUAL:
{user_context}

Si el usuario pregunta qué puedes hacer, dile que puede hablarte naturalmente como a un amigo y tú le ayudas con tareas, recordatorios y eventos."""

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

def extract_action(message):
    msg = message.lower().strip()
    
    task_triggers = ['tengo que', 'necesito', 'no olvides', 'recuérdame', 'pendiente', 'agregar tarea', 'anota que', 'apunta que']
    reminder_triggers = ['recuérdame en', 'alarma en', 'avísame en', 'en minutos', 'en horas', 'en días']
    event_triggers = ['reunión', 'cita', 'evento', 'meeting', 'junta']
    status_triggers = ['qué tengo', 'mis tareas', 'pendientes', 'resumen', 'qué hay']
    done_triggers = ['hecha', 'completada', 'terminé', 'listo', 'ya hice']
    
    for trigger in task_triggers:
        if trigger in msg:
            task_text = message
            for t in task_triggers:
                task_text = task_text.replace(t, '').strip()
            if task_text:
                return ('add_todo', task_text)
    
    for trigger in reminder_triggers:
        if trigger in msg:
            return ('add_reminder', message)
    
    for trigger in event_triggers:
        if trigger in msg:
            return ('add_event', message)
    
    for trigger in status_triggers:
        if trigger in msg:
            return ('summary', None)
    
    for trigger in done_triggers:
        if trigger in msg:
            return ('done_hint', message)
    
    return (None, None)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Ver tareas", callback_data="t"), InlineKeyboardButton("⏰ Recordatorios", callback_data="r")],
        [InlineKeyboardButton("📅 Eventos", callback_data="e"), InlineKeyboardButton("📊 Resumen", callback_data="s")]
    ]
    await update.message.reply_text(
        "¡Hola Jose! 👋 Soy tu asistente personal.\n\n"
        "Puedes hablarme naturalmente, como a un amigo. Por ejemplo:\n\n"
        "💬 \"Tengo que comprar leche\"\n"
        "💬 \"Recuérdame llamar al doctor en 2 horas\"\n"
        "💬 \"Tengo una reunión el viernes\"\n"
        "💬 \"Qué tengo pendiente?\"\n\n"
        "También puedes enviarme audio y lo entiendo 🎤",
        reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **Puedes hablarme así:**\n\n"
        "💬 \"Tengo que hacer X\" → Creo la tarea\n"
        "💬 \"Recuérdame X en 2 horas\" → Creo recordatorio\n"
        "💬 \"Reunión el viernes\" → Creo evento\n"
        "💬 \"Qué tengo pendiente?\" → Te doy resumen\n"
        "💬 \"Marcar tarea 1 como hecha\" → La completo\n\n"
        "🎤 También puedes enviarme audio!",
        parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message.text
    user_context = get_user_context(uid)
    
    action, data = extract_action(msg)
    
    if action == 'add_todo':
        add_item(uid, "todos", {"text": data, "done": False, "created": datetime.now().isoformat()})
        response = await asyncio.to_thread(chat_with_ai, f"El usuario acaba de agregar la tarea: {data}. Confirma de forma natural y breve.", user_context)
        await update.message.reply_text(response or f"✅ Tarea agregada: {data}")
        return
    
    elif action == 'add_reminder':
        response = await asyncio.to_thread(chat_with_ai, f"El usuario quiere un recordatorio: {msg}. Extrae el texto y el tiempo, y responde confirmando el recordatorio de forma natural.", user_context)
        await update.message.reply_text(response or "⏰ Recordatorio creado")
        return
    
    elif action == 'add_event':
        response = await asyncio.to_thread(chat_with_ai, f"El usuario menciona un evento: {msg}. Responde confirmando de forma natural.", user_context)
        await update.message.reply_text(response or "📅 Evento guardado")
        return
    
    elif action == 'summary':
        todos = [t for t in load_data(uid, "todos") if not t.get('done')]
        rems = [r for r in load_data(uid, "reminders") if not r.get('done')]
        evts = load_data(uid, "events")
        
        summary_text = f"📋 Tareas: {len(todos)} | ⏰ Recordatorios: {len(rems)} | 📅 Eventos: {len(evts)}\n\n"
        if todos:
            summary_text += "**Tus tareas:**\n" + "".join(f"  • {t['text']}\n" for t in todos[:5])
        if rems:
            summary_text += "\n**Recordatorios:**\n" + "".join(f"  • {r['text']} ({r['when']})\n" for r in rems[:3])
        
        await update.message.reply_text(summary_text, parse_mode='Markdown')
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
        
        await update.message.reply_text(f"📝 Dijiste: \"{text}\"")
        
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

async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    evts = load_data(update.effective_user.id, "events")
    if not evts:
        await update.message.reply_text("📅 No tienes eventos próximos.")
        return
    txt = "📅 **Eventos:**\n\n"
    for i, e in enumerate(evts):
        txt += f"{i+1}. {e['title']} - {e['date']}\n"
    await update.message.reply_text(txt, parse_mode='Markdown')

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    todos = [t for t in load_data(uid, "todos") if not t.get('done')]
    rems = [r for r in load_data(uid, "reminders") if not r.get('done')]
    evts = load_data(uid, "events")
    txt = f"📊 **Resumen:**\n\n📋 Tareas: {len(todos)}\n⏰ Recordatorios: {len(rems)}\n📅 Eventos: {len(evts)}\n\n"
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
    elif q.data == "e": await list_events(update, context)
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
                            if datetime.now() >= datetime.strptime(r['when'], "%Y-%m-%d %H:%M"):
                                try:
                                    await app.bot.send_message(int(uid), f"⏰ ¡Recordatorio!\n\n📝 {r['text']}")
                                    rems_ref.document(r['_id']).update({'done': True})
                                except: pass
        except Exception as e:
            logger.error(f"Error: {e}")
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
    app.add_handler(CommandHandler("eventos", list_events))
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
