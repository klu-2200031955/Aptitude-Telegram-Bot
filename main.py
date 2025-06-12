import os
import logging
import asyncio
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue

logging.basicConfig(level=logging.INFO)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SELF_PING_URL = f"{WEBHOOK_URL}/ping"
API_BASE_URL = os.getenv("API_URL")
API_URL = f"{API_BASE_URL}/api/random"
RESET_URL = f"{API_BASE_URL}/api/reset"

app = FastAPI()
application = None

POLL_INTERVAL = timedelta(hours=1)
users = {}
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

async def fetch_question(chat_id):
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

                question_id = data.get("_id")
                if chat_id in active_users:
                    if question_id in active_users[chat_id]["asked_questions"]:
                        logging.info(f"Question {question_id} already sent to {chat_id}, fetching a new one.")
                        continue
                    active_users[chat_id]["asked_questions"].append(question_id)

                return data
        except httpx.RequestError as e:
            logging.error(f"Attempt {attempt + 1}: Error fetching question: {e}")
            await asyncio.sleep(RETRY_DELAY)

    if chat_id in active_users:
        active_users[chat_id]["asked_questions"].clear()
        return await fetch_question(chat_id)

    return None

async def send_poll_to_user(chat_id, context: ContextTypes.DEFAULT_TYPE):
    question_data = await fetch_question(chat_id)
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
        is_anonymous=False,
        type='quiz',
        correct_option_id=correct_option_id,
    )

    safe_explanation = escape_markdown(explanation)
    await context.bot.send_message(
        chat_id=chat_id,
        text=f"Poll Explanation:\n\n||{safe_explanation}||",
        parse_mode="MarkdownV2"
    )

async def poll_scheduler(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for chat_id, details in list(active_users.items()):
        last_poll_time = datetime.fromisoformat(details["last_poll_time"])
        if now - last_poll_time >= POLL_INTERVAL:
            await send_poll_to_user(chat_id, context)
            active_users[chat_id]["last_poll_time"] = now.isoformat()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    user_name = update.message.from_user.username or "Unknown"
    full_name = update.message.from_user.full_name
    user_id = update.message.from_user.id

    if chat_id not in users:
        users[chat_id] = {
            "user_id": user_id,
            "user_name": user_name,
            "full_name": full_name,
            "asked_questions": []
        }

    active_users[chat_id] = {
        "user_id": user_id,
        "user_name": user_name,
        "full_name": full_name,
        "last_poll_time": datetime.now().isoformat(),
        "asked_questions": users[chat_id]["asked_questions"]
    }

    await update.message.reply_text("Polls will be sent every hour. Use /stop to stop receiving them.")
    await send_poll_to_user(chat_id, context)

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    chat_id = update.message.chat_id
    if chat_id in active_users:
        asked_questions = active_users[chat_id]["asked_questions"]
        del active_users[chat_id]
        users[chat_id]["asked_questions"] = asked_questions
        await update.message.reply_text("You will no longer receive polls, but your progress is saved.")
    else:
        await update.message.reply_text("You are not a subscriber to the bot.")

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
            body { font-family: Arial, sans-serif; text-align: center; padding: 50px; }
            h1 { color: #4CAF50; }
        </style>
    </head>
    <body>
        <h1>Bot is Alive</h1>
        <p>The Telegram bot is running and active.</p>
    </body>
    </html>
    """

@app.post("/broadcast")
async def broadcast_message(request: Request):
    data = await request.json()
    message = data.get("message")
    chat_id = data.get("chat_id")

    if not message:
        raise HTTPException(status_code=400, detail="Message field is required.")

    if chat_id:
        try:
            await application.bot.send_message(chat_id=chat_id, text=message)
            return {"status": f"Message sent to user {chat_id}."}
        except Exception as e:
            logging.error(f"Failed to send message to {chat_id}: {e}")
            return {"status": f"Failed to send message to user {chat_id}."}

    for user_chat_id in users.keys():
        try:
            await application.bot.send_message(chat_id=user_chat_id, text=message)
        except Exception as e:
            logging.error(f"Failed to send message to {user_chat_id}: {e}")

    return {"status": "Message sent to all users."}

@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.get("/users", response_class=HTMLResponse)
async def get_all_users():
    if not users:
        return "<h2>No users found.</h2>"

    table_html = "<h2>Users</h2><table border='1'><tr><th>Chat ID</th><th>User ID</th><th>Username</th><th>Full Name</th><th>Questions Asked</th></tr>"
    for chat_id, data in users.items():
        asked_questions = len(data['asked_questions'])
        table_html += f"<tr><td>{chat_id}</td><td>{data['user_id']}</td><td>{data['user_name']}</td><td>{data['full_name']}</td><td>{asked_questions}</td></tr>"
    table_html += "</table>"
    return table_html

@app.get("/active_users", response_class=HTMLResponse)
async def get_active_users():
    if not active_users:
        return "<h2>No active users found.</h2>"

    table_html = "<h2>Active Users</h2><table border='1'><tr><th>Chat ID</th><th>User ID</th><th>Username</th><th>Full Name</th><th>Last Poll Time</th><th>Questions Asked</th></tr>"
    for chat_id, data in active_users.items():
        asked_questions = len(data['asked_questions'])
        table_html += f"<tr><td>{chat_id}</td><td>{data['user_id']}</td><td>{data['user_name']}</td><td>{data['full_name']}</td><td>{data['last_poll_time']}</td><td>{asked_questions}</td></tr>"
    table_html += "</table>"
    return table_html


@app.post("/webhook")
async def receive_update(request: Request):
    update = await request.json()
    if application:
        await application.update_queue.put(Update.de_json(update, application.bot))
    return Response(content="OK", status_code=200)

@app.post("/reset_questions")
async def reset_user_questions(request: Request):
    data = await request.json()
    chat_id = data.get("chat_id")

    if chat_id:
        if chat_id in users:
            users[chat_id]["asked_questions"] = []
            if chat_id in active_users:
                active_users[chat_id]["asked_questions"] = []
            return {"status": f"Asked questions reset for chat ID {chat_id}."}
        else:
            raise HTTPException(status_code=404, detail=f"Chat ID {chat_id} not found.")
    else:
        for chat_id in users:
            users[chat_id]["asked_questions"] = []
        for chat_id in active_users:
            active_users[chat_id]["asked_questions"] = []
        return {"status": "Asked questions reset for all users."}

@app.get("/ping")
async def ping():
    return {"status": "ok"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app", host="0.0.0.0", port=2032, reload=True)
