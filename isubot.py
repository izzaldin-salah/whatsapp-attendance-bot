import json
import requests
from flask import Flask, request
import gspread
from oauth2client.service_account import ServiceAccountCredentials
from datetime import datetime
import schedule
import time
import threading
import logging

# Setup logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)

# -----------------------------
#   GOOGLE SHEETS SETUP
# -----------------------------
scope = ["https://spreadsheets.google.com/feeds",
         "https://www.googleapis.com/auth/drive"]

creds = ServiceAccountCredentials.from_json_keyfile_name("creds.json", scope)
client = gspread.authorize(creds)

sheet = client.open("ISU_Attendance").sheet1


# -----------------------------
#   WHATSAPP API SETUP
# -----------------------------
VERIFY_TOKEN = "ISU_VERIFY"
WHATSAPP_TOKEN = "EAAQvu2KhWQoBPZCKvHC4Bg6VBTdYhzWxaA3yOhwXM7sDYp1bFc3jr1gGLflEKKrI1yupmihnpWnJM6wBcZCXjwmPyc1wnbYmM7K5xUUAVkZBfTDW1zQirSYlrNHFtDKyMB9ZBZBZAjs6qYr8oxkHCSxmwV6isrkjN1pEBuQMWBiWIU2aCNJqF3biEPUdzRlCTBelcJf2bZCx1Aht6U0bNFawrr74mxpJ8R4PPWoWVZAnsNQa5eAZD"
PHONE_NUMBER_ID = "882386174957956"

app = Flask(__name__)


# -----------------------------
# Helper: Send WhatsApp Message
# -----------------------------
def send_message(phone, text):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "text",
        "text": {"body": text}
    }
    response = requests.post(url, json=data, headers=headers)
    logging.info(f"Send message response: {response.status_code} - {response.text}")
    return response


# -----------------------------
# Send Button Message (Days)
# -----------------------------
def send_day_buttons(phone):
    url = f"https://graph.facebook.com/v20.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }
    data = {
        "messaging_product": "whatsapp",
        "to": phone,
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "Please choose your attendance day:"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "sat", "title": "Saturday"}},
                    {"type": "reply", "reply": {"id": "mon", "title": "Monday"}},
                    {"type": "reply", "reply": {"id": "wed", "title": "Wednesday"}}
                ]
            }
        }
    }
    response = requests.post(url, json=data, headers=headers)
    logging.info(f"Send buttons response: {response.status_code} - {response.text}")
    return response


# -----------------------------
# Users database (local JSON)
# -----------------------------
def load_users():
    try:
        with open("users.json", "r") as f:
            return json.load(f)
    except:
        return {}


def save_users(users):
    with open("users.json", "w") as f:
        json.dump(users, f, indent=4)


# -----------------------------
# Webhook Verify
# -----------------------------
@app.route("/webhook", methods=["GET"])
def verify():
    if request.args.get("hub.verify_token") == VERIFY_TOKEN:
        return request.args.get("hub.challenge")
    return "Invalid verification token"


# -----------------------------
# Main Webhook Handler
# -----------------------------
@app.route("/webhook", methods=["POST"])
def webhook():
    data = request.json
    
    # Log the incoming webhook data
    logging.info(f"Received webhook data: {json.dumps(data, indent=2)}")

    try:
        # Check if message exists in the webhook
        if "entry" not in data:
            logging.warning("No 'entry' in webhook data")
            return "ok"
            
        entry = data["entry"][0]
        
        if "changes" not in entry:
            logging.warning("No 'changes' in entry")
            return "ok"
            
        changes = entry["changes"][0]
        value = changes.get("value", {})
        
        # Check for status updates (message sent/delivered/read) - ignore these
        if "statuses" in value:
            logging.info("Received status update, ignoring")
            return "ok"
        
        # Check for messages
        if "messages" not in value:
            logging.warning("No 'messages' in value")
            return "ok"
            
        message = value["messages"][0]
        phone = message["from"]
        
        logging.info(f"Processing message from {phone}, type: {message.get('type')}")
        
        users = load_users()

        # FIRST MESSAGE OR UNKNOWN USER
        if phone not in users:
            # ask for name
            if message["type"] != "text":
                logging.info(f"New user {phone} sent non-text message, asking for name")
                send_message(phone, "Welcome! Please type your full name:")
                return "ok"

            text = message["text"]["body"].strip()
            logging.info(f"New user {phone} provided name: {text}")

            # save the name
            users[phone] = {"name": text}
            save_users(users)

            send_message(phone, f"Thank you {text}! Now choose your attendance:")
            time.sleep(1)  # Small delay between messages
            send_day_buttons(phone)
            return "ok"

        # KNOWN USER = Handle buttons
        if message["type"] == "interactive":
            day_id = message["interactive"]["button_reply"]["id"]
            logging.info(f"User {phone} selected day: {day_id}")

            day_map = {"sat": "Saturday", "mon": "Monday", "wed": "Wednesday"}
            day = day_map.get(day_id)

            now = datetime.now()
            date_str = now.strftime("%Y-%m-%d")
            timestamp = now.strftime("%Y-%m-%d %H:%M:%S")

            # write to Google Sheet
            sheet.append_row([timestamp, phone, users[phone]["name"], day, date_str])
            logging.info(f"Saved attendance for {users[phone]['name']} on {day}")

            send_message(phone, f"Attendance saved for {day}. Thank you!")
            return "ok"

        # If text from known user
        logging.info(f"Known user {phone} sent text, showing day buttons")
        send_day_buttons(phone)
        return "ok"

    except Exception as e:
        logging.error(f"Error processing webhook: {str(e)}", exc_info=True)
        return "ok"


# -----------------------------
# DAILY SUMMARY SCHEDULER
# -----------------------------
GROUP_ID = "WHATSAPP_GROUP_ID"

def send_daily_summary():
    records = sheet.get_all_records()

    today = datetime.now().strftime("%Y-%m-%d")
    today_rows = [r for r in records if r["Date"] == today]

    if not today_rows:
        return

    summary = f"📌 Attendance Summary — {today}\n\n"

    for r in today_rows:
        summary += f"- {r['Name']}: Present ({r['Day']})\n"

    # send to group
    send_message(GROUP_ID, summary)


def schedule_runner():
    while True:
        schedule.run_pending()
        time.sleep(1)


# Run summary at 9 PM every day
schedule.every().day.at("21:00").do(send_daily_summary)


# Start scheduler thread
threading.Thread(target=schedule_runner, daemon=True).start()


# -----------------------------
# RUN SERVER
# -----------------------------
if __name__ == "__main__":
    import os
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
