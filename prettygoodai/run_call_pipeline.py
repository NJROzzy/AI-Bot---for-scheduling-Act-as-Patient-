import os
import time
import json
import pathlib
from typing import Optional, Tuple

import requests
from dotenv import load_dotenv
from twilio.rest import Client
from openai import OpenAI


# ----------------------------
# Setup / Config
# ----------------------------

load_dotenv()

TWILIO_ACCOUNT_SID = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
TWILIO_AUTH_TOKEN = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
TWILIO_PHONE_NUMBER = os.getenv("TWILIO_PHONE_NUMBER", "").strip()
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()

YOUR_NUMBER = os.getenv("YOUR_NUMBER", "").strip()
WEBHOOK_BASE = os.getenv("WEBHOOK_BASE", "").strip().rstrip("/")

# NOTE: NO CALL TIME LIMIT.
# Twilio will not cap the call duration from your side.
# The call ends only if the callee/clinic hangs up, or Twilio/other side ends it.

CALL_WAIT_TIMEOUT_S = int(os.getenv("CALL_WAIT_TIMEOUT_S", "3600"))          # wait up to 1 hour for call to complete
RECORDING_SID_TIMEOUT_S = int(os.getenv("RECORDING_SID_TIMEOUT_S", "600"))   # wait up to 10 min for recording SID
RECORDING_READY_TIMEOUT_S = int(os.getenv("RECORDING_READY_TIMEOUT_S", "900"))  # wait up to 15 min for processing
DOWNLOAD_TIMEOUT_S = int(os.getenv("DOWNLOAD_TIMEOUT_S", "900"))             # total download retry time

POLL_S = float(os.getenv("POLL_S", "2.5"))

RECORDINGS_DIR = pathlib.Path("recordings")
TRANSCRIPTS_DIR = pathlib.Path("transcripts")
REPORTS_DIR = pathlib.Path("reports")

for d in (RECORDINGS_DIR, TRANSCRIPTS_DIR, REPORTS_DIR):
    d.mkdir(parents=True, exist_ok=True)

missing = [k for k, v in {
    "TWILIO_ACCOUNT_SID": TWILIO_ACCOUNT_SID,
    "TWILIO_AUTH_TOKEN": TWILIO_AUTH_TOKEN,
    "TWILIO_PHONE_NUMBER": TWILIO_PHONE_NUMBER,
    "OPENAI_API_KEY": OPENAI_API_KEY,
    "YOUR_NUMBER": YOUR_NUMBER,
    "WEBHOOK_BASE": WEBHOOK_BASE,
}.items() if not v]

if missing:
    raise RuntimeError(f"Missing env vars in .env: {', '.join(missing)}")

if not WEBHOOK_BASE.startswith("https://"):
    raise RuntimeError("WEBHOOK_BASE must be an https ngrok URL, e.g. https://abc123.ngrok-free.app")

twilio = Client(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN)
openai_client = OpenAI(api_key=OPENAI_API_KEY)


# ----------------------------
# Twilio Helpers
# ----------------------------

FINAL_CALL_STATUSES = {"completed", "busy", "failed", "no-answer", "canceled"}


def wait_for_call_complete(call_sid: str, timeout_s: int, poll_s: float) -> str:
    """Wait until Twilio call is completed (or fails). Returns final status."""
    start = time.time()
    while True:
        call = twilio.calls(call_sid).fetch()
        status = call.status  # queued/ringing/in-progress/completed/busy/failed/no-answer/canceled
        if status in FINAL_CALL_STATUSES:
            return status
        if time.time() - start > timeout_s:
            return "timeout"
        time.sleep(poll_s)


def wait_for_recording_sid(call_sid: str, timeout_s: int, poll_s: float) -> str:
    """Wait for a recording to appear for a call. Returns Recording SID."""
    start = time.time()
    while True:
        recs = twilio.recordings.list(call_sid=call_sid, limit=20)
        if recs:
            # usually only one recording; if multiple, you could select latest by date_created
            return recs[0].sid
        if time.time() - start > timeout_s:
            raise TimeoutError("No recording SID found for call within timeout.")
        time.sleep(poll_s)


def wait_for_recording_ready(recording_sid: str, timeout_s: int, poll_s: float) -> None:
    """
    Twilio may create Recording resource before audio is downloadable.
    We wait until status is 'completed' and duration exists (or is non-empty).
    """
    start = time.time()
    while True:
        rec = twilio.recordings(recording_sid).fetch()
        status = getattr(rec, "status", None)      # 'processing' -> 'completed'
        duration = getattr(rec, "duration", None)  # sometimes string/None until ready

        if status == "completed" and duration not in (None, "", "0"):
            return

        if time.time() - start > timeout_s:
            raise TimeoutError(f"Recording not ready after {timeout_s}s. status={status}, duration={duration}")

        time.sleep(poll_s)


def _try_download(url: str) -> Tuple[int, bytes]:
    r = requests.get(
        url,
        auth=(TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN),
        timeout=60,
        allow_redirects=True,
    )
    return r.status_code, r.content


def download_recording(recording_sid: str, out_basepath: pathlib.Path, max_wait_s: int, poll_s: float) -> pathlib.Path:
    """
    Download the recording audio from Twilio with retries.

    Tries these URLs (some become available earlier than others):
    1) /Media
    2) .mp3
    3) .wav
    """
    rec = twilio.recordings(recording_sid).fetch()

    # rec.uri example: /2010-04-01/Accounts/AC.../Recordings/RE....json
    uri_no_json = rec.uri.replace(".json", "")
    media_url = f"https://api.twilio.com{uri_no_json}/Media"
    mp3_url = f"https://api.twilio.com{uri_no_json}.mp3"
    wav_url = f"https://api.twilio.com{uri_no_json}.wav"

    start = time.time()
    attempt = 0
    last_status: Optional[Tuple[str, int]] = None

    while time.time() - start < max_wait_s:
        attempt += 1

        for url, ext in ((media_url, ".bin"), (mp3_url, ".mp3"), (wav_url, ".wav")):
            status, content = _try_download(url)
            last_status = (url, status)

            # 404 often means "not ready yet"
            if status == 200 and content:
                out_path = out_basepath.with_suffix(ext)
                out_path.write_bytes(content)
                return out_path

        time.sleep(min(poll_s * attempt, 12))

    raise RuntimeError(f"Recording media not available after {max_wait_s}s. Last status={last_status}")


# ----------------------------
# OpenAI Helpers
# ----------------------------

def whisper_transcribe(audio_path: pathlib.Path) -> str:
    with audio_path.open("rb") as f:
        result = openai_client.audio.transcriptions.create(
            model="whisper-1",
            file=f,
        )
    return result.text


def bug_analyze(transcript_text: str) -> dict:
    prompt = f"""
You are an AI QA analyst. Analyze this phone call transcript and produce a concise bug/quality report.

Return JSON with:
- summary: 2-3 sentences
- issues: list of objects {{
    severity: "low"|"med"|"high",
    category: "...",
    evidence: "...",
    why_it_matters: "...",
    suggestion: "..."
  }}
- overall_score: 1-10

Transcript:
{transcript_text}
""".strip()

    resp = openai_client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
        temperature=0.2,
    )
    return json.loads(resp.choices[0].message.content)


# ----------------------------
# Main Pipeline
# ----------------------------

def main():
    # 1) Place call (NO TIME LIMIT)
    call = twilio.calls.create(
        url=f"{WEBHOOK_BASE}/voice",
        to=YOUR_NUMBER,
        from_=TWILIO_PHONE_NUMBER,
        record=True,
    )
    call_sid = call.sid
    print(f"[1/7] Call started: {call_sid}")

    # 2) Wait for completion
    final_status = wait_for_call_complete(call_sid, timeout_s=CALL_WAIT_TIMEOUT_S, poll_s=POLL_S)
    print(f"[2/7] Call finished with status: {final_status}")

    if final_status != "completed":
        print("Call did not complete; skipping recording/transcription/report.")
        return

    # 3) Wait for recording SID
    rec_sid = wait_for_recording_sid(call_sid, timeout_s=RECORDING_SID_TIMEOUT_S, poll_s=POLL_S)
    print(f"[3/7] Recording SID: {rec_sid}")

    # 4) Wait for recording to become ready
    print("[4/7] Waiting for recording to be ready...")
    wait_for_recording_ready(rec_sid, timeout_s=RECORDING_READY_TIMEOUT_S, poll_s=POLL_S)
    print("[4/7] Recording ready.")

    # 5) Download recording
    out_base = RECORDINGS_DIR / call_sid
    audio_path = download_recording(rec_sid, out_basepath=out_base, max_wait_s=DOWNLOAD_TIMEOUT_S, poll_s=POLL_S)
    print(f"[5/7] Downloaded recording -> {audio_path}")

    # 6) Whisper transcription
    transcript_text = whisper_transcribe(audio_path)
    transcript_path = TRANSCRIPTS_DIR / f"{call_sid}.txt"
    transcript_path.write_text(transcript_text, encoding="utf-8")
    print(f"[6/7] Whisper transcript -> {transcript_path}")

    # 7) QA report
    report = bug_analyze(transcript_text)
    report_path = REPORTS_DIR / f"{call_sid}.json"
    report_path.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"[7/7] Bug/quality report -> {report_path}")


if __name__ == "__main__":
    main()