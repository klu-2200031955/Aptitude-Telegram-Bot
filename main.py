import os
import logging
import asyncio
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SELF_PING_URL = f"{WEBHOOK_URL}/ping" 
API_URL = 'https://aptitude-api-one.vercel.app/api/random'
RESET_URL = 'https://aptitude-api-one.vercel.app/api/reset'

app = FastAPI()
application = None

POLL_INTERVAL = timedelta(hours=1)
active_users = {}

RETRY_ATTEMPTS = 3
RETRY_DELAY = 5

def escape_markdown(text):
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

async def reset_questions():
    async with httpx.AsyncClient() as client:
        try:
            response = await client.post(RESET_URL)
            if response.status_code == 200:
                logging.info("Questions reset successfully.")
            else:
                logging.error(f"Failed to reset questions: {response.text}")
        except httpx.RequestError as e:
            logging.error(f"Error resetting questions: {e}")

async def fetch_question():
    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(API_URL)
                response.raise_for_status()
                data = response.json()
                if data.get("message") == "No more questions available! Reset needed.":
                    logging.warning("No more questions. Triggering reset.")
                    await reset_questions()
                    return None
                return data
        except httpx.RequestError as e:
            logging.error(f"Attempt {attempt + 1}: Error fetching question: {e}")
            await asyncio.sleep(RETRY_DELAY)
    return None

async def send_poll_to_user(chat_id, context: ContextTypes.DEFAULT_TYPE):
    question_data = await fetch_question()
    if not question_data:
        return

    question = question_data['question']
    options = question_data['options']
    explanation = question_data['explanation']
    correct_answer = question_data['answer']
    correct_option_id = options.index(correct_answer)

    await context.bot.send_poll(
        chat_id=chat_id,
        question=question,
        options=options,
        is_anonymous=True,
        type='quiz',
        correct_option_id=correct_option_id,
    )

    safe_explanation = escape_markdown(explanation)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Poll Explanation:\n\n||{safe_explanation}||",
        parse_mode="MarkdownV2"
    )

    active_users[chat_id] = datetime.now().isoformat()

async def check_and_send_polls(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for chat_id, last_poll_time_str in list(active_users.items()):
        last_poll_time = datetime.fromisoformat(last_poll_time_str)
        if now - last_poll_time >= POLL_INTERVAL:
            await send_poll_to_user(chat_id, context)

async def poll_scheduler(context: ContextTypes.DEFAULT_TYPE):
    await check_and_send_polls(context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    active_users[chat_id] = datetime.now().isoformat()
    await update.message.reply_text("Polls will be sent every hour. Use /stop to stop receiving them.")
    await send_poll_to_user(chat_id, context)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    active_users.pop(chat_id, None)
    await update.message.reply_text("You will no longer receive polls.")

@app.on_event("startup")
async def startup_event():
    initialize_application()
    await application.initialize()
    await application.start()
    await set_webhook()
    logging.info("Bot started and webhook set successfully.")

def initialize_application():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))
    job_queue = application.job_queue
    job_queue.run_repeating(poll_scheduler, interval=60, first=10)  
    job_queue.run_repeating(self_ping, interval=300, first=20)  

async def self_ping(context: ContextTypes.DEFAULT_TYPE):
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(SELF_PING_URL)
            if response.status_code == 200:
                logging.info("Self-ping successful.")
            else:
                logging.warning(f"Self-ping failed: {response.text}")
        except httpx.RequestError as e:
            logging.error(f"Self-ping error: {e}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=2032)
