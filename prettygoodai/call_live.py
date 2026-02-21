import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

client = Client(account_sid, auth_token)

YOUR_NUMBER = "+13513451190"  # <-- your real number (E.164 format)
WEBHOOK_BASE = "https://leonida-unformative-anh.ngrok-free.dev"  # <-- HTTPS ngrok URL

call = client.calls.create(
    url=f"{WEBHOOK_BASE}/voice",
    to=YOUR_NUMBER,
    from_=twilio_number,
    record=True
)

print("Call SID:", call.sid)