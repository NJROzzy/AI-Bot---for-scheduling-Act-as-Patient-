# server.py
import os
import re
import json
import hashlib
from datetime import datetime
from typing import Dict, List, Tuple, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from openai import OpenAI
from twilio.twiml.voice_response import VoiceResponse, Gather

load_dotenv()

# -----------------------
# Required env
# -----------------------
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
if not OPENAI_API_KEY:
    raise RuntimeError("Missing OPENAI_API_KEY in .env")

# -----------------------
# Patient facts / intent
# -----------------------
PATIENT_NAME = os.getenv("PATIENT_NAME", "Nitish John Rawat").strip()
PATIENT_DOB_SPOKEN = os.getenv("PATIENT_DOB_SPOKEN", "").strip()
APPT_TYPE = os.getenv("APPT_TYPE", "physical therapy").strip()
PREFERRED_DAY_TIME = os.getenv("PREFERRED_DAY_TIME", "Wednesday afternoon").strip()
DEFAULT_REASON = os.getenv("DEFAULT_REASON", "Rehabilitation and mobility improvement.").strip()

# -----------------------
# Runtime tuning
# -----------------------
GATHER_TIMEOUT_S = int(os.getenv("GATHER_TIMEOUT_S", "8"))
MAX_SILENT_RETRIES = int(os.getenv("MAX_SILENT_RETRIES", "999999"))

TTS_MODEL = os.getenv("TTS_MODEL", "gpt-4o-mini-tts").strip()
TTS_VOICE = os.getenv("TTS_VOICE", "aria").strip()
CHAT_MODEL = os.getenv("CHAT_MODEL", "gpt-4o").strip()

# -----------------------
# App init
# -----------------------
app = FastAPI()
client = OpenAI(api_key=OPENAI_API_KEY)

os.makedirs("tts_audio", exist_ok=True)
os.makedirs("transcripts", exist_ok=True)
app.mount("/tts", StaticFiles(directory="tts_audio"), name="tts")

CALL_HISTORY: Dict[str, List[dict]] = {}
CALL_META: Dict[str, dict] = {}

# -----------------------
# Utilities
# -----------------------
def norm(s: str) -> str:
    return " ".join((s or "").lower().split())


def log_turn(call_sid: str, speaker: str, text: str) -> None:
    entry = {
        "ts_utc": datetime.utcnow().isoformat(),
        "call_sid": call_sid,
        "speaker": speaker,  # "other_bot" | "patient"
        "text": text,
    }
    with open(f"transcripts/{call_sid}.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


# -----------------------
# OpenAI TTS (cached)
# -----------------------
def generate_tts_cached(text: str, voice: str = TTS_VOICE) -> str:
    text_norm = " ".join(text.strip().split())
    key = hashlib.sha256(f"{TTS_MODEL}:{voice}:{text_norm}".encode("utf-8")).hexdigest()[:16]
    filename = f"{key}.mp3"
    path = os.path.join("tts_audio", filename)

    if os.path.exists(path):
        return filename

    audio = client.audio.speech.create(model=TTS_MODEL, voice=voice, input=text_norm)
    data = audio.read() if callable(getattr(audio, "read", None)) else audio.content

    with open(path, "wb") as f:
        f.write(data)

    return filename


def speak(vr: VoiceResponse, base: str, text: str) -> None:
    text = (text or "").strip()
    if not text:
        return
    mp3 = generate_tts_cached(text, voice=TTS_VOICE)
    vr.play(f"{base}/tts/{mp3}")


def prewarm_tts() -> None:
    common = [
        "Yes.",
        "Yes, that's correct.",
        "No, thank you.",
        "Thank you.",
        "No, that's all. Thank you. Goodbye.",
        DEFAULT_REASON,
    ]
    for s in common:
        try:
            generate_tts_cached(s)
        except Exception:
            pass


# -----------------------
# TwiML helpers
# -----------------------
def gather_only(base: str) -> VoiceResponse:
    vr = VoiceResponse()
    gather = Gather(
        input="speech",
        action=f"{base}/handle_speech",
        method="POST",
        timeout=GATHER_TIMEOUT_S,
        speechTimeout="auto",
        language="en-US",
    )
    vr.append(gather)
    vr.redirect(f"{base}/voice")
    return vr


def gather_after_speaking(vr: VoiceResponse, base: str) -> None:
    gather = Gather(
        input="speech",
        action=f"{base}/handle_speech",
        method="POST",
        timeout=GATHER_TIMEOUT_S,
        speechTimeout="auto",
        language="en-US",
    )
    vr.append(gather)
    vr.redirect(f"{base}/voice")


# -----------------------
# Heuristics
# -----------------------
def is_help_prompt(other_bot_said: str) -> bool:
    t = norm(other_bot_said)
    return any(
        x in t
        for x in [
            "how can i help",
            "how may i help",
            "how can we help",
            "what can i help you with",
            "what can i do for you",
            "how can i assist",
            "reason for your call",
            "reason you're calling",
            "reason you are calling",
            "what are you calling about",
            "what would you like to do",
            "what can i assist you with",
            "how can i help you today",
        ]
    )


def is_informational_only(other_bot_said: str) -> bool:
    t = norm(other_bot_said)

    if any(x in t for x in ["this call may be recorded", "quality and training purposes"]):
        return True

    checking_phrases = [
        "got it",
        "one moment",
        "hold on",
        "let me check",
        "checking",
        "while i fetch",
        "while i check",
        "while i look",
        "let me look",
        "let me find",
        "find the soonest",
        "check for the next available",
        "checking for the next available",
        "searching",
        "please hold",
        "let me see",
        "i'll check",
        "i will check",
    ]

    if "?" not in other_bot_said and any(x in t for x in checking_phrases):
        return True

    return False


def direct_question_heuristic(other_bot_said: str) -> bool:
    t = norm(other_bot_said)
    if "?" in other_bot_said:
        return True
    return any(
        x in t
        for x in [
            "would you like",
            "do you have",
            "do you prefer",
            "can you",
            "could you",
            "is that correct",
            "is that right",
            "are you still there",
            "text reminder",
            "send you a text",
            "sms reminder",
            "reason for your visit",
            "reason for your appointment",
        ]
    )


def detect_appointment_set(other_bot_said: str) -> bool:
    t = norm(other_bot_said)
    return any(
        x in t
        for x in [
            "appointment is set",
            "appointment is confirmed",
            "your appointment is confirmed",
            "your physical therapy appointment is set",
            "is confirmed for",
            "is set for",
            "all set for",
            "your appointment is all set",
        ]
    )


def detect_hangup(other_bot_said: str) -> bool:
    t = norm(other_bot_said)
    return any(x in t for x in ["goodbye", "i am going to end the call", "ending the call"])


def detect_text_reminder_question(other_bot_said: str) -> bool:
    t = norm(other_bot_said)
    return any(x in t for x in ["text reminder", "send you a text", "sms reminder"]) and (
        "?" in other_bot_said or "would you like" in t
    )


def detect_still_there(other_bot_said: str) -> bool:
    return "are you still there" in norm(other_bot_said)


def detect_wrapup_question(other_bot_said: str) -> bool:
    t = norm(other_bot_said)
    return any(
        x in t
        for x in [
            "anything else",
            "any other questions",
            "any other question",
            "need anything else",
            "need help with anything else",
            "can i help with anything else",
            "can i assist with anything else",
            "do you need anything else",
            "is there anything else",
            "if you need anything else",
            "if you have any other questions",
            "make any changes",
            "your appointment is all set",
            "your appointment is confirmed if you need anything else",
            "great day",
            "have a great day",
        ]
    )


# -----------------------
# Option extraction
# -----------------------
DOW_PAT = r"(monday|tuesday|wednesday|thursday|friday|saturday|sunday)"
TIME_PAT = r"(\b\d{1,2}(:\d{2})?\s*(a\.?m\.?|p\.?m\.?)\b)"
MONTHDATE_PAT = (
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december)\b\s+\d{1,2}(st|nd|rd|th)?"
)
PROVIDER_PAT = r"(with\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+))"


def extract_available_options(other_bot_said: str) -> List[dict]:
    text = other_bot_said or ""
    provider = None
    pm = re.search(PROVIDER_PAT, text)
    if pm:
        provider = pm.group(2).strip()

    dows = re.findall(DOW_PAT, text, flags=re.IGNORECASE)
    dows = [d.lower() for d in dows] if dows else []

    times = re.findall(TIME_PAT, text, flags=re.IGNORECASE)
    times = [tup[0] for tup in times] if times else []

    dm = re.search(MONTHDATE_PAT, text, flags=re.IGNORECASE)
    date_str = dm.group(0) if dm else None

    options: List[dict] = []
    if not times:
        return options

    if not dows:
        for tm in times:
            options.append({"dow": None, "date": date_str, "time": tm, "provider": provider, "raw": text})
        return options

    if len(dows) == 1:
        for tm in times:
            options.append({"dow": dows[0], "date": date_str, "time": tm, "provider": provider, "raw": text})
        return options

    for i, tm in enumerate(times):
        options.append(
            {"dow": dows[min(i, len(dows) - 1)], "date": date_str, "time": tm, "provider": provider, "raw": text}
        )
    return options


# -----------------------
# GPT patient
# -----------------------
SYSTEM_PROMPT = f"""
You are a patient on a phone call with a clinic scheduling system.

Identity:
- Name: {PATIENT_NAME}
- DOB: {PATIENT_DOB_SPOKEN}

Primary Goal:
- Book a {APPT_TYPE} appointment, ideally {PREFERRED_DAY_TIME}.
- If unavailable, accept the next available reasonable option and complete booking.

Behavior:
- Keep responses SHORT (1 sentence).
- Do NOT repeat yourself.
- Stay silent while they are checking unless they asked a question.
- If the clinic offers exactly one time, confirm with date+time in your reply.
- If the clinic offers multiple options, choose ONE immediately.
- If asked “Is that correct?” answer exactly: "Yes, that's correct."
- If asked for reason: reply with "{DEFAULT_REASON}"
- If asked about text reminders: answer exactly: "No, thank you."
- If asked “Are you still there?” answer exactly: "Yes."
- If the clinic asks wrap-up like “anything else?” answer exactly: "No, that's all. Thank you. Goodbye." and set done=true.
- NEVER output "..." or filler. If you should be silent, set "say" to "" (empty string).

Never:
- Mention being an AI.
- Invent times/providers/medical details beyond the reason phrase above.

Return JSON only:
{{"say":"...","done":true/false}}
""".strip()


def _llm(
    call_sid: str,
    other_bot_said: str,
    meta: dict,
    allowed_to_initiate: bool,
    available_options: List[dict],
) -> dict:
    hist = CALL_HISTORY.setdefault(call_sid, [])

    clinic_text = (other_bot_said or "").strip()
    if len(clinic_text) > 800:
        clinic_text = clinic_text[:800] + " ..."

    hist.append({"role": "user", "content": f"Clinic said: {clinic_text}"})
    trimmed = hist[-10:]

    context = {
        "allowed_to_initiate": bool(allowed_to_initiate),
        "available_options": available_options,
        "appointment_booked": bool(meta.get("appointment_booked", False)),
        "reminder": "Return ONLY JSON with keys say/done. say must be 1 short sentence.",
    }

    msgs = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"Context JSON: {json.dumps(context)}"},
        *trimmed,
    ]

    def _call_openai(extra_instruction: Optional[str] = None) -> str:
        call_msgs = msgs if not extra_instruction else msgs + [{"role": "user", "content": extra_instruction}]
        resp = client.chat.completions.create(
            model=CHAT_MODEL,
            messages=call_msgs,
            response_format={"type": "json_object"},
            temperature=0.35,
            max_tokens=140,
        )
        return resp.choices[0].message.content or ""

    raw = _call_openai()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        raw2 = _call_openai('Output ONLY valid JSON like: {"say":"...", "done":false}. No extra text.')
        try:
            data = json.loads(raw2)
        except json.JSONDecodeError:
            data = {"say": "", "done": False}

    say = (data.get("say") or "").strip()
    hist.append({"role": "assistant", "content": say})

    if not isinstance(data.get("done", False), bool):
        data["done"] = False
    if not isinstance(say, str):
        data["say"] = ""

    return data


def gpt_reply(call_sid: str, other_bot_said: str, allowed_to_initiate: bool, meta: dict) -> Tuple[str, bool]:
    # -------------------------
    # AFTER BOOKING
    # -------------------------
    if meta.get("appointment_booked"):
        # If wrap-up is asked -> say bye and end.
        if detect_wrapup_question(other_bot_said):
            return "No, that's all. Thank you. Goodbye.", True

        # Still-there -> answer once only, then keep listening.
        if detect_still_there(other_bot_said):
            meta["post_book_still_there_count"] = int(meta.get("post_book_still_there_count", 0)) + 1
            if meta["post_book_still_there_count"] <= 1:
                return "Yes.", False
            return "", False

        # Text reminder -> no.
        if detect_text_reminder_question(other_bot_said):
            return "No, thank you.", False

        # Otherwise: don’t engage post-book.
        return "", False

    # -------------------------
    # BEFORE BOOKING
    # -------------------------
    if is_informational_only(other_bot_said) and not direct_question_heuristic(other_bot_said) and not allowed_to_initiate:
        return "", False

    if not allowed_to_initiate and not direct_question_heuristic(other_bot_said):
        return "", False

    options = extract_available_options(other_bot_said)
    data = _llm(
        call_sid=call_sid,
        other_bot_said=other_bot_said,
        meta=meta,
        allowed_to_initiate=allowed_to_initiate,
        available_options=options,
    )

    say = (data.get("say") or "").strip()
    done = bool(data.get("done", False))

    # Anti-repeat
    last = meta.get("last_say", "")
    if last and norm(last) == norm(say):
        return "", False
    meta["last_say"] = say

    return say, done


# -----------------------
# Routes
# -----------------------
@app.on_event("startup")
def _startup():
    prewarm_tts()


@app.post("/voice")
async def voice_entry(request: Request):
    form = await request.form()
    call_sid = str(form.get("CallSid") or "unknown")
    base = str(request.base_url).rstrip("/")

    CALL_HISTORY.setdefault(call_sid, [])
    CALL_META.setdefault(
        call_sid,
        {
            "silent_retries": 0,
            "allowed_to_initiate": False,
            "appointment_booked": False,
            "last_say": "",
            "post_book_still_there_count": 0,
        },
    )

    return Response(content=str(gather_only(base)), media_type="application/xml")


@app.post("/handle_speech")
async def handle_speech(request: Request):
    form = await request.form()
    call_sid = str(form.get("CallSid") or "unknown")
    other_bot_said = (form.get("SpeechResult") or "").strip()
    base = str(request.base_url).rstrip("/")

    meta = CALL_META.setdefault(
        call_sid,
        {
            "silent_retries": 0,
            "allowed_to_initiate": False,
            "appointment_booked": False,
            "last_say": "",
            "post_book_still_there_count": 0,
        },
    )

    # Nothing captured -> keep listening forever
    if not other_bot_said:
        meta["silent_retries"] += 1
        if meta["silent_retries"] >= MAX_SILENT_RETRIES:
            meta["silent_retries"] = 0
        return Response(content=str(gather_only(base)), media_type="application/xml")

    meta["silent_retries"] = 0
    log_turn(call_sid, "other_bot", other_bot_said)

    if is_help_prompt(other_bot_said):
        meta["allowed_to_initiate"] = True

    if detect_appointment_set(other_bot_said):
        meta["appointment_booked"] = True

    if detect_hangup(other_bot_said):
        return Response(content=str(gather_only(base)), media_type="application/xml")

    say, done = gpt_reply(
        call_sid=call_sid,
        other_bot_said=other_bot_said,
        allowed_to_initiate=bool(meta.get("allowed_to_initiate")),
        meta=meta,
    )

    if not say:
        return Response(content=str(gather_only(base)), media_type="application/xml")

    log_turn(call_sid, "patient", say)

    vr = VoiceResponse()
    speak(vr, base, say)
    vr.pause(length=1)

    if done:
        vr.hangup()
        return Response(content=str(vr), media_type="application/xml")

    gather_after_speaking(vr, base)
    return Response(content=str(vr), media_type="application/xml")