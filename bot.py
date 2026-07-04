import os
import json
import logging
import asyncio
from datetime import datetime, timedelta
from pathlib import Path
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler,
    ContextTypes, filters
)
import requests

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
GROQ_KEY = os.environ.get("GROQ_API_KEY", "")
DATA_DIR = Path("data")
DATA_DIR.mkdir(exist_ok=True)

def load_data(user_id, filename):
    path = DATA_DIR / str(user_id) / filename
    if path.exists():
        return json.loads(path.read_text(encoding='utf-8'))
    return []

def save_data(user_id, filename, data):
    user_dir = DATA_DIR / str(user_id)
    user_dir.mkdir(exist_ok=True)
    (user_dir / filename).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')

def get_user_context(user_id):
    todos = load_data(user_id, "todos.json")
    reminders = load_data(user_id, "reminders.json")
    events = load_data(user_id, "events.json")
    ctx = ""
    if todos:
        pending = [t for t in todos if not t.get('done')]
        if pending:
            ctx += f"Tareas pendientes: {', '.join(t['text'] for t in pending[-5:])}\n"
    if reminders:
        pending = [r for r in reminders if not r.get('done')]
        if pending:
            ctx += f"Recordatorios: {', '.join(r['text'] for r in pending[-3:])}\n"
    if events:
        ctx += f"Eventos: {', '.join(e['title'] for e in events[-3:])}\n"
    return ctx

def chat_with_ai(user_message, user_context=""):
    if not GROQ_KEY:
        return "💬 Chat IA no configurado. Usa los comandos:\n/tarea /tareas /recordar /evento /resumen"

    system = f"""Eres un asistente personal amigable. Responde en español, breve y útil.
Si el usuario quiere tareas o recordatorios, sugiere usar /tarea o /recordar.
{user_context}"""

    try:
        r = requests.post("https://api.groq.com/openai/v1/chat/completions",
            headers={"Authorization": f"Bearer {GROQ_KEY}", "Content-Type": "application/json"},
            json={"model": "llama3-8b-8192", "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_message}
            ], "max_tokens": 400},
            timeout=20
        )
        if r.status_code == 200:
            return r.json()["choices"][0]["message"]["content"]
        return "No pude procesar tu mensaje."
    except:
        return "IA no disponible. Usa los comandos para gestionar tareas."

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [
        [InlineKeyboardButton("📋 Tareas", callback_data="t"), InlineKeyboardButton("⏰ Recordatorios", callback_data="r")],
        [InlineKeyboardButton("📅 Eventos", callback_data="e"), InlineKeyboardButton("📊 Resumen", callback_data="s")],
        [InlineKeyboardButton("❓ Ayuda", callback_data="h")]
    ]
    await update.message.reply_text(
        "👋 ¡Hola! Soy tu asistente personal.\n\n"
        "📝 Enviarme texto para chatear\n"
        "📋 /tarea - Gestionar tareas\n"
        "⏰ /recordar - Crear recordatorios\n"
        "📅 /evento - Agregar eventos\n"
        "📊 /resumen - Ver pendientes",
        reply_markup=InlineKeyboardMarkup(kb)
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📚 **COMANDOS:**\n\n"
        "📋 /tarea [texto] - Agregar tarea\n"
        "📋 /tareas - Ver tareas\n"
        "✅ /hecha [n] - Completar tarea\n\n"
        "⏰ /recordar [texto] en [tiempo]\n"
        "   Ej: /recordar comprar leche en 2 horas\n\n"
        "📅 /evento [titulo] [fecha]\n"
        "   Ej: /evento Reunion 2025-01-15\n\n"
        "📊 /resumen - Ver todo\n"
        "🧹 /limpiar - Borrar completadas",
        parse_mode='Markdown'
    )

async def add_todo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = ' '.join(context.args) if context.args else None
    if not text:
        await update.message.reply_text("Usa: /tarea comprar leche")
        return
    todos = load_data(uid, "todos.json")
    todos.append({"text": text, "done": False, "created": datetime.now().isoformat()})
    save_data(uid, "todos.json", todos)
    await update.message.reply_text(f"✅ Tarea: {text}")

async def list_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    todos = load_data(update.effective_user.id, "todos.json")
    if not todos:
        await update.message.reply_text("📋 Sin tareas.")
        return
    txt = "📋 **TAREAS:**\n\n"
    for i, t in enumerate(todos):
        txt += f"{i+1}. {'✅' if t.get('done') else '⬜'} {t['text']}\n"
    txt += "\n/hecha [n] para completar"
    await update.message.reply_text(txt, parse_mode='Markdown')

async def done_todo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not context.args:
        await update.message.reply_text("Usa: /hecha 1")
        return
    try:
        idx = int(context.args[0]) - 1
        todos = load_data(uid, "todos.json")
        if 0 <= idx < len(todos):
            todos[idx]['done'] = True
            save_data(uid, "todos.json", todos)
            await update.message.reply_text(f"✅ {todos[idx]['text']}")
        else:
            await update.message.reply_text("Numero invalido.")
    except:
        await update.message.reply_text("Usa: /hecha 1")

async def add_reminder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    text = ' '.join(context.args) if context.args else ""
    if not text:
        await update.message.reply_text("Usa: /recordar comprar leche en 2 horas")
        return

    time_map = {'minuto': 1, 'minutos': 1, 'hora': 60, 'horas': 60, 'dia': 1440, 'dias': 1440}
    parts = text.lower().split(' en ')
    if len(parts) < 2:
        await update.message.reply_text("Formato: /recordar [texto] en [tiempo]")
        return

    rtext = parts[0].strip()
    tstr = parts[1].strip()
    minutes = 0
    for w, m in time_map.items():
        if w in tstr:
            nums = [int(s) for s in tstr.split() if s.isdigit()]
            if nums:
                minutes = nums[0] * m
                break
    if minutes == 0:
        try: minutes = int(tstr.split()[0]) * 60
        except: minutes = 60

    rtime = datetime.now() + timedelta(minutes=minutes)
    rems = load_data(uid, "reminders.json")
    rems.append({"text": rtext, "when": rtime.strftime("%Y-%m-%d %H:%M"), "done": False})
    save_data(uid, "reminders.json", rems)
    await update.message.reply_text(f"⏰ Recordatorio: {rtext}\n🕐 {rtime.strftime('%H:%M %d/%m')}")

async def list_reminders(update: Update, context: ContextTypes.DEFAULT_TYPE):
    rems = [r for r in load_data(update.effective_user.id, "reminders.json") if not r.get('done')]
    if not rems:
        await update.message.reply_text("⏰ Sin recordatorios.")
        return
    txt = "⏰ **RECORDATORIOS:**\n\n"
    for i, r in enumerate(rems):
        txt += f"{i+1}. {r['text']} - {r['when']}\n"
    await update.message.reply_text(txt, parse_mode='Markdown')

async def add_event(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if len(context.args) < 2:
        await update.message.reply_text("Usa: /evento Reunion 2025-01-15")
        return
    title = context.args[0]
    date = context.args[1] if len(context.args) > 1 else datetime.now().strftime("%Y-%m-%d")
    time = context.args[2] if len(context.args) > 2 else "00:00"
    evts = load_data(uid, "events.json")
    evts.append({"title": title, "date": f"{date} {time}", "created": datetime.now().isoformat()})
    save_data(uid, "events.json", evts)
    await update.message.reply_text(f"📅 {title} - {date} {time}")

async def list_events(update: Update, context: ContextTypes.DEFAULT_TYPE):
    evts = load_data(update.effective_user.id, "events.json")
    if not evts:
        await update.message.reply_text("📅 Sin eventos.")
        return
    txt = "📅 **EVENTOS:**\n\n"
    for i, e in enumerate(evts):
        txt += f"{i+1}. {e['title']} - {e['date']}\n"
    await update.message.reply_text(txt, parse_mode='Markdown')

async def summary(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    todos = [t for t in load_data(uid, "todos.json") if not t.get('done')]
    rems = [r for r in load_data(uid, "reminders.json") if not r.get('done')]
    evts = load_data(uid, "events.json")
    txt = f"📊 **RESUMEN:**\n\n📋 Tareas: {len(todos)}\n⏰ Recordatorios: {len(rems)}\n📅 Eventos: {len(evts)}\n\n"
    if todos:
        txt += "**Tareas:**\n" + "".join(f"  • {t['text']}\n" for t in todos[:3])
    if rems:
        txt += "**Recordatorios:**\n" + "".join(f"  • {r['text']} ({r['when']})\n" for r in rems[:3])
    await update.message.reply_text(txt, parse_mode='Markdown')

async def clean_todos(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    todos = load_data(uid, "todos.json")
    remaining = [t for t in todos if not t.get('done')]
    save_data(uid, "todos.json", remaining)
    await update.message.reply_text(f"🧹 {len(todos) - len(remaining)} eliminadas.")

async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    msg = update.message.text
    ctx = get_user_context(uid)
    await update.message.reply_chat_action("typing")
    resp = await asyncio.to_thread(chat_with_ai, msg, ctx)
    await update.message.reply_text(resp)

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "t": await list_todos(update, context)
    elif q.data == "r": await list_reminders(update, context)
    elif q.data == "e": await list_events(update, context)
    elif q.data == "s": await summary(update, context)
    elif q.data == "h": await help_cmd(update, context)

async def check_reminders(app: Application):
    while True:
        try:
            for d in DATA_DIR.iterdir():
                if d.is_dir():
                    uid = d.name
                    rems = load_data(uid, "reminders.json")
                    changed = False
                    for r in rems:
                        if not r.get('done') and r.get('when'):
                            if datetime.now() >= datetime.strptime(r['when'], "%Y-%m-%d %H:%M"):
                                try:
                                    await app.bot.send_message(int(uid), f"⏰ **RECORDATORIO:**\n\n📝 {r['text']}", parse_mode='Markdown')
                                    r['done'] = True
                                    changed = True
                                except: pass
                    if changed: save_data(uid, "reminders.json", rems)
        except Exception as e:
            logger.error(f"Error: {e}")
        await asyncio.sleep(30)

def main():
    if not TOKEN:
        print("ERROR: Configura TELEGRAM_TOKEN")
        return
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("tarea", add_todo))
    app.add_handler(CommandHandler("tareas", list_todos))
    app.add_handler(CommandHandler("hecha", done_todo))
    app.add_handler(CommandHandler("recordar", add_reminder))
    app.add_handler(CommandHandler("recordatorios", list_reminders))
    app.add_handler(CommandHandler("evento", add_event))
    app.add_handler(CommandHandler("eventos", list_events))
    app.add_handler(CommandHandler("resumen", summary))
    app.add_handler(CommandHandler("limpiar", clean_todos))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))

    async def post_init(application):
        asyncio.create_task(check_reminders(application))
    app.post_init = post_init

    print("🤖 Bot iniciado!")
    app.run_polling()

if __name__ == '__main__':
    main()
