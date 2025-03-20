# 📌 Aptitude Telegram Bot  

A Telegram bot that sends aptitude-based quiz polls every hour to users and groups. It fetches random aptitude questions from an external API and provides an explanation after each poll.  

## 🚀 Features  

- 📌 Sends a multiple-choice quiz poll every hour.  
- 🔄 Fetches random aptitude questions from an API.  
- 📖 Provides an explanation for each question.  
- 🏢 Supports both group and private chats.  
- ✅ Users can start and stop receiving polls using `/start` and `/stop` commands.  
- ⚡ Uses **FastAPI** to handle webhooks.  
- 🔁 Includes a **self-ping mechanism** to keep the bot alive on **Render**.  

## 🛠 Tech Stack  

- **Python** (async programming with `asyncio`)  
- **FastAPI** (for webhook handling)  
- **httpx** (for making API requests)  
- **python-telegram-bot** (for Telegram bot integration)  
- **Uvicorn** (to run the FastAPI application)  

## 🛠 Installation  

### Prerequisites  

- Python **3.8+**  
- **Telegram Bot Token** (from [BotFather](https://t.me/BotFather))  
- **Webhook URL** (Render or any other hosting service)  

### ⚙️ Steps  

1️⃣ **Clone the repository:**  
   ```sh
   git clone https://github.com/klu-2200031955/Aptitude-Telegram-Bot.git
   cd Aptitude-Telegram-Bot
   ```

2️⃣ **Create a virtual environment and activate it:**  
   ```sh
   python -m venv venv
   source venv/bin/activate  # On Windows use: venv\Scripts\activate
   ```

3️⃣ **Install dependencies:**  
   ```sh
   pip install -r requirements.txt
   ```

4️⃣ **Set environment variables:**  
   ```sh
   export BOT_TOKEN="your-telegram-bot-token"
   export WEBHOOK_URL="your-webhook-url"
   export API_URL="your-api-url"
   ```

5️⃣ **Run the bot locally:**  
   ```sh
   uvicorn main:app --host 0.0.0.0 --port 2032
   ```

## 🔗 API Endpoints  

| Method | Endpoint       | Description                         |
| ------ | -------------- | ----------------------------------- |
| GET    | `/`            | Checks if the bot is running       |
| GET    | `/set_webhook` | Sets the webhook for Telegram      |
| POST   | `/webhook`     | Processes incoming Telegram updates |
| GET    | `/ping`        | Self-ping to keep the bot alive    |

## 📌 Usage  

### Start Receiving Polls  
Send the command `/start` in a private chat or a group where the bot is added.  

### Stop Receiving Polls  
Send the command `/stop` to stop receiving polls.  

## 🚀 Deployment on Render  

1️⃣ **Create a new Web Service** on [Render](https://render.com/).  
2️⃣ Set the **environment variables** `BOT_TOKEN`, `WEBHOOK_URL`, and `API_URL`.  
3️⃣ Deploy the repository.  
4️⃣ Configure **UptimeRobot** to ping the `/ping` endpoint every 5 minutes to keep the bot active.  

## 🤖 Telegram Bot  

Try the bot here: [Aptitude Telegram Bot](https://t.me/Aptitude_Questions_Bot)  

## 📜 License  

This project is licensed under the **MIT License**.  

## 👨‍💻 Author  

[Samudrala Venkata Pavan Tarun Kumar](https://github.com/klu-2200031955) 
