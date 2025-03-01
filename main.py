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

async def check_and_send_polls(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for chat_id, details in list(active_users.items()):
        last_poll_time = datetime.fromisoformat(details["last_poll_time"])
        if now - last_poll_time >= POLL_INTERVAL:
            await send_poll_to_user(chat_id, context)

async def poll_scheduler(context: ContextTypes.DEFAULT_TYPE):
    await check_and_send_polls(context)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_name = update.message.from_user.full_name
    user_id = update.message.from_user.id
    active_users[chat_id] = {"user_id": user_id, "user_name": user_name, "last_poll_time": datetime.now().isoformat()}
    await update.message.reply_text("Polls will be sent every hour. Use /stop to stop receiving them.")
    await send_poll_to_user(chat_id, context)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    active_users.pop(chat_id, None)
    await update.message.reply_text("You will no longer receive polls.")

async def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    async with httpx.AsyncClient() as client:
        response = await client.post(url, json={"url": WEBHOOK_URL})
        logging.info(f"Webhook response: {response.json()}")

@app.on_event("startup")
async def startup_event():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))

    job_queue = application.job_queue
    job_queue.run_repeating(poll_scheduler, interval=60, first=10)
    job_queue.run_repeating(self_ping, interval=300, first=20)

    await application.initialize()
    await application.start()
    await set_webhook()
    logging.info("Bot started and webhook set successfully.")

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

@app.get("/", response_class=HTMLResponse)
async def root():
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Bot Status</title>
        <style>
            body {
                font-family: Arial, sans-serif;
                text-align: center;
                padding: 50px;
            }
            h1 {
                color: #4CAF50;
            }
        </style>
    </head>
    <body>
        <h1>Bot is Alive</h1>
        <p>The Telegram bot is running and active.</p>
    </body>
    </html>
    """

@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.get("/active_users")
async def get_active_users():
    return active_users

@app.post("/webhook")
async def receive_update(request: Request):
    update = await request.json()
    if application:
        await application.update_queue.put(Update.de_json(update, application.bot))
    return Response(content="OK", status_code=200)

@app.get("/ping")
async def ping():
    return {"status": "ok"}

@app.post("/broadcast")
async def broadcast_message(request: Request):
    data = await request.json()
    message = data.get("message")
    if not message:
        raise HTTPException(status_code=400, detail="Message field is required.")
    
    for chat_id in active_users.keys():
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
        except Exception as e:
            logging.error(f"Failed to send message to {chat_id}: {e}")
    
    return {"status": "Message sent to all active users."}
    

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=2032)
