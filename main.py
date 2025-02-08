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


async def fetch_question():
    API_URL = 'https://aptitude-api.vercel.app/Random'
    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with httpx.AsyncClient() as client:
                response = await client.get(API_URL)
                response.raise_for_status()
                return response.json()
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
    chat_type = update.message.chat.type
    logging.info(f"Chat type: {chat_type}, Chat ID: {chat_id}")

    if chat_type in ["private", "supergroup", "group"]:
        await update.message.reply_text("Polls will be sent every hour. Use /stop to stop receiving them.")
        active_users[chat_id] = datetime.now().isoformat()
        await send_poll_to_user(chat_id, context)
    else:
        await update.message.reply_text("This bot doesn't support this type of chat.")


async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id

    if chat_id in active_users:
        del active_users[chat_id]
        await update.message.reply_text("You will no longer receive polls.")
    else:
        await update.message.reply_text("You are not subscribed to polls.")


@app.post("/webhook")
async def webhook(request: Request):
    try:
        update = await request.json()

        if application is None:
            return Response(content="Bot is not initialized", status_code=500)

        tg_update = Update.de_json(update, application.bot)
        await application.process_update(tg_update)

        return Response(content="OK", status_code=200)
    except Exception as e:
        logging.error(f"Error processing webhook: {e}")
        return Response(content=f"Error: {e}", status_code=400)


@app.get("/set_webhook")
async def set_webhook():
    webhook_url = f"{WEBHOOK_URL}/webhook"
    async with httpx.AsyncClient() as client:
        response = await client.post(f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook", json={"url": webhook_url})
        if response.status_code == 200:
            logging.info(f"Webhook successfully set to {webhook_url}")
            return {"message": f"Webhook set to {webhook_url}"}
        else:
            logging.error(f"Failed to set webhook: {response.text}")
            return {"error": response.text}


@app.get("/")
async def root():
    html_content = """
    <html>
        <head>
            <title>Telegram Bot Status</title>
        </head>
        <body>
            <h1>Bot is Alive!</h1>
            <p>The Telegram bot is running and responsive.</p>
        </body>
    </html>
    """
    return HTMLResponse(content=html_content)


@app.get("/ping")
async def ping():
    """Self-ping endpoint to keep the bot alive on Render."""
    logging.info("Received self-ping request.")
    return {"status": "Bot is alive"}


@app.head("/")
async def head():
    return HTMLResponse(status_code=200)


def initialize_application():
    global application
    application = Application.builder().token(BOT_TOKEN).build()
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("stop", stop))

    job_queue = application.job_queue
    job_queue.run_repeating(poll_scheduler, interval=60, first=10)  
    job_queue.run_repeating(self_ping, interval=300, first=20)  


async def self_ping(context: ContextTypes.DEFAULT_TYPE):
    """Pings the bot's own server to prevent Render from sleeping."""
    async with httpx.AsyncClient() as client:
        try:
            response = await client.get(SELF_PING_URL)
            if response.status_code == 200:
                logging.info("Self-ping successful.")
            else:
                logging.warning(f"Self-ping failed: {response.text}")
        except httpx.RequestError as e:
            logging.error(f"Self-ping error: {e}")


@app.on_event("startup")
async def startup_event():
    initialize_application()
    await application.initialize()
    await application.start()

    await set_webhook()

    logging.info("Bot started and webhook set successfully.")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=2032)
