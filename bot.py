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

def analyze_message(user_message, user_context=""):
    now = datetime.now()
    current_date = now.strftime("%Y-%m-%d")
    current_time = now.strftime("%H:%M")
    current_day = now.strftime("%A")
    
    system = f"""Eres JoseBot, el asistente personal de Jose. Tu trabajo es entender lo que quiere y ejecutar acciones.

FECHA ACTUAL: {current_date} ({current_day})
HORA ACTUAL: {current_time}

CONTEXTO DEL USUARIO:
{user_context}

Analiza el mensaje del usuario y responde SOLO con un JSON en este formato:

Si es un RECORDATORIO:
{{"action": "reminder", "text": "texto del recordatorio", "when": "YYYY-MM-DD HH:MM"}}

Si es una TAREA:
{{"action": "task", "text": "texto de la tarea"}}

Si quiere VER TAREAS:
{{"action": "show_tasks"}}

Si quiere VER RECORDATORIOS:
{{"action": "show_reminders"}}

Si quiere RESUMEN:
{{"action": "summary"}}

Si es una CONVERSACIÓN normal (saludos, preguntas, charla):
{{"action": "chat", "response": "tu respuesta natural y amigable"}}

REGLAS IMPORTANTES:
- Si dice "en X minutos/horas/dias", calcula la fecha exacta
- Si dice "mañana", usa la fecha de mañana
- Si dice "el lunes/martes/etc", calcula el próximo día de la semana
- Si dice "el dia X", usa ese día del mes actual (o el siguiente si ya pasó)
- Si dice "a las X", usa esa hora
- Si no especifica hora para recordatorio, usa 1 hora después de ahora
- Si no entiendes bien, responde como chat pidiendo aclaración
- Responde SIEMPRE en español
- Sé breve y natural en las respuestas de chat
- Usa emojis moderadamente"""

    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama3-8b-8192", "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message}
            ], "max_tokens": 300, "temperature": 0.1}, timeout=20)
        if r.status_code == 200:
            response = r.json()["choices"][0]["message"]["content"]
            # Extract JSON from response
            json_match = re.search(r'\{[^{}]*\}', response)
            if json_match:
                return json.loads(json_match.group())
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
        "Puedes hablarme como quieras, te entiendo. Por ejemplo:\n\n"
        "💬 \"Recuérdame en 5 minutos buscar el cargador\"\n"
        "💬 \"Tengo que comprar leche\"\n"
        "💬 \"No olvides llamar al doctor mañana\"\n"
        "💬 \"Qué tengo pendiente?\"\n\n"
        "🎤 También entiendo audio!",
        reply_markup=InlineKeyboardMarkup(kb))

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **Puedes decirme lo que sea:**\n\n"
        "**Recordatorios:**\n"
        "• \"Recuérdame en 5 minutos...\"\n"
        "• \"Recuérdame mañana a las 3pm...\"\n"
        "• \"No olvides que tengo reunión el viernes\"\n\n"
        "**Tareas:**\n"
        "• \"Tengo que hacer X\"\n"
        "• \"Necesito comprar X\"\n"
        "• \"Anota que debo pagar X\"\n\n"
        "**Consultas:**\n"
        "• \"Qué tengo pendiente?\"\n"
        "• \"Mis tareas\"\n"
        "• \"Cuándo es mi próximo recordatorio?\"\n\n"
        "**Charla:**\n"
        "• \"Hola, ¿cómo estás?\"\n"
        "• \"¿Qué puedes hacer?\"\n\n"
        "🎤 ¡También puedes enviarme audio!",
        parse_mode='Markdown')

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message.text
    user_context = get_user_context(uid)
    
    await update.message.reply_chat_action("typing")
    
    result = await asyncio.to_thread(analyze_message, msg, user_context)
    
    if not result:
        await update.message.reply_text("No entendí bien. ¿Puedes repetir?")
        return
    
    action = result.get("action")
    
    if action == "reminder":
        text = result.get("text", "")
        when = result.get("when", "")
        
        if text and when:
            add_item(uid, "reminders", {
                "text": text,
                "when": when,
                "done": False,
                "created": datetime.now().isoformat()
            })
            
            try:
                dt = datetime.strptime(when, "%Y-%m-%d %H:%M")
                time_display = dt.strftime("%H:%M")
                date_display = dt.strftime("%d/%m/%Y")
                await update.message.reply_text(
                    f"⏰ ¡Listo! Te recuerdo:\n\n"
                    f"📝 {text}\n"
                    f"🕐 {time_display} del {date_display}"
                )
            except:
                await update.message.reply_text(f"⏰ Recordatorio guardado: {text}")
        else:
            await update.message.reply_text("⏰ No entendí bien el recordatorio. ¿Puedes repetir?")
    
    elif action == "task":
        text = result.get("text", "")
        if text:
            add_item(uid, "todos", {"text": text, "done": False, "created": datetime.now().isoformat()})
            await update.message.reply_text(f"✅ Tarea guardada: {text}")
        else:
            await update.message.reply_text("✅ No entendí la tarea. ¿Puedes repetir?")
    
    elif action == "show_tasks":
        todos = load_data(uid, "todos")
        if not todos:
            await update.message.reply_text("📋 No tienes tareas pendientes. ¡Estás al día! 🎉")
            return
        txt = "📋 **Tus tareas:**\n\n"
        for i, t in enumerate(todos):
            txt += f"{i+1}. {'✅' if t.get('done') else '⬜'} {t['text']}\n"
        txt += "\nDime \"hecha la tarea X\" para completarla"
        await update.message.reply_text(txt, parse_mode='Markdown')
    
    elif action == "show_reminders":
        rems = [r for r in load_data(uid, "reminders") if not r.get('done')]
        if not rems:
            await update.message.reply_text("⏰ No tienes recordatorios pendientes.")
            return
        txt = "⏰ **Recordatorios:**\n\n"
        for i, r in enumerate(rems):
            txt += f"{i+1}. {r['text']} - {r['when']}\n"
        await update.message.reply_text(txt, parse_mode='Markdown')
    
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
    
    elif action == "chat":
        response = result.get("response", "No entendí bien. ¿Puedes repetir?")
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
