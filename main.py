import os
import logging
import asyncio
import httpx
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.responses import HTMLResponse
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes, JobQueue
from telegram.error import BadRequest

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler('bot.log')
    ]
)
logger = logging.getLogger(__name__)

# Environment variables
BOT_TOKEN = os.getenv("BOT_TOKEN")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
SELF_PING_URL = f"{WEBHOOK_URL}/ping"
API_BASE_URL = os.getenv("API_URL")
API_URL = f"{API_BASE_URL}/api/random"
RESET_URL = f"{API_BASE_URL}/api/reset"

# App initialization
app = FastAPI()
application = None

# Constants
POLL_INTERVAL = timedelta(hours=1)
RETRY_ATTEMPTS = 3
RETRY_DELAY = 5
REQUEST_TIMEOUT = 30.0

# Data stores
users = {}
active_users = {}

def escape_markdown(text):
    escape_chars = ['_', '*', '[', ']', '(', ')', '~', '`', '>', '#', '+', '-', '=', '|', '{', '}', '.', '!']
    for char in escape_chars:
        text = text.replace(char, f'\\{char}')
    return text

async def reset_questions():
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.post(RESET_URL)
            if response.status_code == 200:
                logger.info("Questions reset successfully.")
            else:
                logger.error(f"Failed to reset questions: {response.status_code} - {response.text}")
        except httpx.HTTPStatusError as e:
            logger.error(f"HTTP error resetting questions: {e.response.status_code} - {e.response.text}")
        except httpx.RequestError as e:
            logger.error(f"Connection error resetting questions: {str(e)}")
        except Exception as e:
            logger.error(f"Unexpected error resetting questions: {str(e)}")

async def fetch_question(chat_id):
    for attempt in range(RETRY_ATTEMPTS):
        try:
            async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
                response = await client.get(API_URL)
                response.raise_for_status()
                data = response.json()

                if data.get("message") == "No more questions available! Reset needed.":
                    logger.warning("No more questions. Triggering reset.")
                    await reset_questions()
                    return None

                question_id = data.get("_id")
                if chat_id in active_users:
                    if question_id in active_users[chat_id]["asked_questions"]:
                        logger.info(f"Question {question_id} already sent to {chat_id}, fetching a new one.")
                        continue
                    active_users[chat_id]["asked_questions"].append(question_id)

                return data
        except httpx.HTTPStatusError as e:
            logger.error(f"Attempt {attempt + 1}: HTTP error fetching question: {e.response.status_code} - {e.response.text}")
            await asyncio.sleep(RETRY_DELAY)
        except httpx.RequestError as e:
            logger.error(f"Attempt {attempt + 1}: Connection error fetching question: {str(e)}")
            await asyncio.sleep(RETRY_DELAY)
        except Exception as e:
            logger.error(f"Attempt {attempt + 1}: Unexpected error fetching question: {str(e)}")
            await asyncio.sleep(RETRY_DELAY)

    if chat_id in active_users:
        active_users[chat_id]["asked_questions"].clear()
        return await fetch_question(chat_id)

    return None

async def send_poll_to_user(chat_id, context: ContextTypes.DEFAULT_TYPE):
    try:
        question_data = await fetch_question(chat_id)
        if not question_data:
            await context.bot.send_message(
                chat_id=chat_id,
                text="Temporarily unable to fetch questions. Please try again later."
            )
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
            explanation=explanation[:200] if explanation else None,
            explanation_parse_mode="MarkdownV2"
        )

        if explanation:
            safe_explanation = escape_markdown(explanation)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"Poll Explanation:\n\n||{safe_explanation}||",
                parse_mode="MarkdownV2"
            )
    except BadRequest as e:
        logger.error(f"Failed to send poll to {chat_id}: {str(e)}")
        await context.bot.send_message(
            chat_id=chat_id,
            text="An error occurred while sending the poll. Please try again."
        )
    except Exception as e:
        logger.error(f"Unexpected error sending poll to {chat_id}: {str(e)}")

async def poll_scheduler(context: ContextTypes.DEFAULT_TYPE):
    now = datetime.now()
    for chat_id, details in list(active_users.items()):
        try:
            last_poll_time = datetime.fromisoformat(details["last_poll_time"])
            if now - last_poll_time >= POLL_INTERVAL:
                await send_poll_to_user(chat_id, context)
                active_users[chat_id]["last_poll_time"] = now.isoformat()
        except Exception as e:
            logger.error(f"Error in scheduler for {chat_id}: {str(e)}")

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        user = update.effective_user
        user_name = user.username or "Unknown"
        full_name = user.full_name
        user_id = user.id

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

        await context.bot.send_message(
            chat_id=chat_id,
            text="Polls will be sent every hour. Use /stop to stop receiving them."
        )
        await send_poll_to_user(chat_id, context)
    except Exception as e:
        logger.error(f"Error in start command: {str(e)}")
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="An error occurred. Please try again."
            )

async def stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        chat_id = update.effective_chat.id
        if chat_id in active_users:
            asked_questions = active_users[chat_id]["asked_questions"]
            del active_users[chat_id]
            users[chat_id]["asked_questions"] = asked_questions
            await context.bot.send_message(
                chat_id=chat_id,
                text="You will no longer receive polls, but your progress is saved."
            )
        else:
            await context.bot.send_message(
                chat_id=chat_id,
                text="You are not currently receiving polls."
            )
    except Exception as e:
        logger.error(f"Error in stop command: {str(e)}")
        if update.effective_chat:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="An error occurred. Please try again."
            )

async def set_webhook():
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/setWebhook"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT) as client:
        try:
            response = await client.post(url, json={"url": WEBHOOK_URL})
            logger.info(f"Webhook response: {response.json()}")
        except Exception as e:
            logger.error(f"Error setting webhook: {str(e)}")

async def self_ping(context: ContextTypes.DEFAULT_TYPE):
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            response = await client.get(SELF_PING_URL)
            if response.status_code == 200:
                logger.info("Self-ping successful.")
            else:
                logger.warning(f"Self-ping failed: {response.status_code} - {response.text}")
        except Exception as e:
            logger.error(f"Self-ping error: {str(e)}")

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error(f"Exception while handling an update: {context.error}", exc_info=context.error)
    if update and isinstance(update, Update) and update.effective_chat:
        try:
            await context.bot.send_message(
                chat_id=update.effective_chat.id,
                text="An error occurred. Please try again later."
            )
        except Exception as e:
            logger.error(f"Error sending error message: {str(e)}")

@app.on_event("startup")
async def startup_event():
    global application
    try:
        application = Application.builder().token(BOT_TOKEN).build()
        
        # Add handlers
        application.add_handler(CommandHandler("start", start))
        application.add_handler(CommandHandler("stop", stop))
        application.add_error_handler(error_handler)

        # Set up job queue
        job_queue = application.job_queue
        if job_queue:
            job_queue.run_repeating(poll_scheduler, interval=60.0, first=10.0)
            job_queue.run_repeating(self_ping, interval=300.0, first=20.0)

        await application.initialize()
        await application.start()
        await set_webhook()
        logger.info("Bot started successfully.")
    except Exception as e:
        logger.error(f"Startup failed: {str(e)}")
        raise

@app.on_event("shutdown")
async def shutdown_event():
    try:
        if application:
            await application.stop()
            await application.shutdown()
        logger.info("Bot shutdown complete.")
    except Exception as e:
        logger.error(f"Shutdown error: {str(e)}")

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
            .status { margin: 20px; padding: 20px; border-radius: 5px; background: #f5f5f5; }
        </style>
    </head>
    <body>
        <h1>Bot is Running</h1>
        <div class="status">
            <p>Active users: {active}</p>
            <p>Total users: {total}</p>
            <p>Last updated: {time}</p>
        </div>
    </body>
    </html>
    """.format(
        active=len(active_users),
        total=len(users),
        time=datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S UTC")
    )

@app.head("/")
async def head_root():
    return Response(status_code=200)

@app.get("/ping")
async def ping():
    try:
        # Check external API connectivity
        async with httpx.AsyncClient(timeout=5.0) as client:
            await client.get(API_URL)
        
        # Check bot status
        if application and application.updater and application.updater.running:
            return {
                "status": "ok",
                "details": {
                    "active_users": len(active_users),
                    "total_users": len(users),
                    "api_status": "reachable",
                    "bot_status": "running"
                }
            }
        return {
            "status": "degraded",
            "details": {
                "active_users": len(active_users),
                "total_users": len(users),
                "api_status": "reachable",
                "bot_status": "not running"
            }
        }
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        raise HTTPException(
            status_code=500,
            detail={
                "status": "unhealthy",
                "error": str(e)
            }
        )

@app.post("/webhook")
async def receive_update(request: Request):
    try:
        update = await request.json()
        if application:
            await application.update_queue.put(Update.de_json(update, application.bot))
        return Response(content="OK", status_code=200)
    except Exception as e:
        logger.error(f"Error processing webhook update: {str(e)}")
        return Response(content="Error", status_code=500)

@app.post("/broadcast")
async def broadcast_message(request: Request):
    data = await request.json()
    message = data.get("message")
    chat_id = data.get("chat_id")

    if not message:
        raise HTTPException(status_code=400, detail="Message field is required.")

    if not application or not application.bot:
        raise HTTPException(status_code=503, detail="Bot not initialized")

    try:
        if chat_id:
            await application.bot.send_message(chat_id=chat_id, text=message)
            return {"status": f"Message sent to user {chat_id}."}

        results = {"success": 0, "failed": 0}
        for user_chat_id in users.keys():
            try:
                await application.bot.send_message(chat_id=user_chat_id, text=message)
                results["success"] += 1
            except Exception as e:
                logger.error(f"Failed to send message to {user_chat_id}: {str(e)}")
                results["failed"] += 1

        return {
            "status": "Broadcast completed",
            "results": results
        }
    except Exception as e:
        logger.error(f"Broadcast failed: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/users", response_class=HTMLResponse)
async def get_all_users():
    if not users:
        return """
        <h2>No users found</h2>
        <p>The bot has no registered users yet.</p>
        """

    table_html = """
    <h2>Registered Users</h2>
    <table border='1' style='width:100%; border-collapse: collapse;'>
        <tr style='background-color: #f2f2f2;'>
            <th>Chat ID</th>
            <th>User ID</th>
            <th>Username</th>
            <th>Full Name</th>
            <th>Questions Asked</th>
        </tr>
    """
    for chat_id, data in users.items():
        asked_questions = len(data['asked_questions'])
        table_html += f"""
        <tr>
            <td>{chat_id}</td>
            <td>{data['user_id']}</td>
            <td>{data['user_name']}</td>
            <td>{data['full_name']}</td>
            <td>{asked_questions}</td>
        </tr>
        """
    table_html += "</table>"
    return table_html

@app.get("/active_users", response_class=HTMLResponse)
async def get_active_users():
    if not active_users:
        return """
        <h2>No active users</h2>
        <p>There are currently no active users receiving polls.</p>
        """

    table_html = """
    <h2>Active Users</h2>
    <table border='1' style='width:100%; border-collapse: collapse;'>
        <tr style='background-color: #f2f2f2;'>
            <th>Chat ID</th>
            <th>User ID</th>
            <th>Username</th>
            <th>Full Name</th>
            <th>Last Poll Time</th>
            <th>Questions Asked</th>
        </tr>
    """
    for chat_id, data in active_users.items():
        asked_questions = len(data['asked_questions'])
        last_poll_time = datetime.fromisoformat(data['last_poll_time']).strftime("%Y-%m-%d %H:%M")
        table_html += f"""
        <tr>
            <td>{chat_id}</td>
            <td>{data['user_id']}</td>
            <td>{data['user_name']}</td>
            <td>{data['full_name']}</td>
            <td>{last_poll_time}</td>
            <td>{asked_questions}</td>
        </tr>
        """
    table_html += "</table>"
    return table_html

@app.post("/reset_questions")
async def reset_user_questions(request: Request):
    data = await request.json()
    chat_id = data.get("chat_id")

    try:
        if chat_id:
            if chat_id in users:
                users[chat_id]["asked_questions"] = []
                if chat_id in active_users:
                    active_users[chat_id]["asked_questions"] = []
                return {"status": f"Questions reset for chat ID {chat_id}."}
            raise HTTPException(status_code=404, detail=f"Chat ID {chat_id} not found.")

        for user_data in users.values():
            user_data["asked_questions"] = []
        for active_data in active_users.values():
            active_data["asked_questions"] = []
        
        return {"status": "Questions reset for all users."}
    except Exception as e:
        logger.error(f"Error resetting questions: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app:app",
        host="0.0.0.0",
        port=2032,
        reload=True,
        log_config=None,
        access_log=False
    )
