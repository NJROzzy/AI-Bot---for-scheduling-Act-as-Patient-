import os
from twilio.rest import Client
from dotenv import load_dotenv

load_dotenv()

account_sid = os.getenv("TWILIO_ACCOUNT_SID")
auth_token = os.getenv("TWILIO_AUTH_TOKEN")
twilio_number = os.getenv("TWILIO_PHONE_NUMBER")

client = Client(account_sid, auth_token)

YOUR_NUMBER = "+13513451190"  # <-- put your real number

twiml = """
<Response>
  <Say voice="alice">
    Hello Nitish. This is your AI voice bot test. Please speak after the beep.
  </Say>
  <Pause length="1"/>
  <Say voice="alice">Beep.</Say>
  <Record maxLength="20"/>
  <Say voice="alice">
    Thank you. Goodbye.
  </Say>
</Response>
"""

call = client.calls.create(
    twiml=twiml,
    to=YOUR_NUMBER,
    from_=twilio_number
)

print("Call SID:", call.sid)