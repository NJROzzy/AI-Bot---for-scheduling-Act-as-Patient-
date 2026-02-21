## AI Voice Patient – Autonomous Appointment Booking Agent

## Overview

This project implements an AI-powered voice patient agent that autonomously calls a clinic scheduling system and books a physical therapy appointment.
(Please refer to the Best-recording folder for the selected MP3, transcript, and report.)
The agent:
	•	Verifies identity
	•	Negotiates availability constraints
	•	Selects appropriate appointment slots
	•	Provides required visit details
	•	Declines optional services (e.g., SMS reminders)
	•	Exits the call gracefully after completion

The system integrates OpenAI (LLM + TTS), Twilio Voice, and FastAPI to simulate a fully autonomous patient interacting with an IVR scheduling system.

⸻

## Primary Objective

Book a physical therapy appointment, ideally on Wednesday afternoon.
If unavailable, accept the next reasonable available option and complete the booking.

⸻
System Architecture

Patient AI (GPT-4o)
        ↓
FastAPI Webhook Server (server.py)
        ↓
Twilio Voice (Speech ↔ TwiML)
        ↓
Clinic Scheduling Bot

Components
	•	OpenAI GPT-4o → Conversational reasoning
	•	OpenAI TTS → Speech generation
	•	Twilio Voice → Call handling + speech recognition
	•	FastAPI → Webhook + call state management
	•	Whisper (OpenAI STT) → Post-call transcription
	•	Pipeline Automation → Recording download + reporting

⸻

## Project Structure

prettygoodai/
│
├── server.py              # Main Twilio webhook + AI logic
├── run_call_pipeline.py   # End-to-end call automation pipeline
├── call_live.py           # Initiate live call
├── call_test.py           # Local test utilities
├── transcribe.py          # Recording transcription utility
│
├── recordings/            # Downloaded call audio files
├── transcripts/           # Structured conversation logs
├── reports/               # Post-call evaluation reports
├── tts_audio/             # Cached TTS responses
│
└── .env                   # Configuration + API keys


⸻

## Core Design Principles

1. Hybrid Control (LLM + Deterministic Logic)

The agent does not rely purely on LLM output.

Deterministic heuristics handle:
	•	Informational clinic messages (“Let me check availability”)
	•	Post-booking wrap-up detection
	•	SMS reminder decline logic
	•	“Are you still there?” handling

LLM is used only for:
	•	Appointment negotiation
	•	Slot selection
	•	Reason explanation

This prevents conversational drift and infinite loops.

⸻

2. Structured Output Enforcement

The model returns strict JSON:

{
  "say": "string",
  "done": true/false
}

	•	say → What the patient speaks
	•	done → Whether the call should terminate

This allows deterministic Twilio control via TwiML.

⸻

3. Post-Booking State Management

Once an appointment is confirmed:
	•	The agent switches to wrap-up mode
	•	Responds to optional questions
	•	Terminates cleanly when asked “Anything else?”

This prevents looping or redundant dialogue.

⸻

4. TTS Caching

Generated speech is hashed and cached:
	•	Reduces latency
	•	Prevents regeneration of common phrases
	•	Improves natural pacing

⸻

## End-to-End Pipeline

run_call_pipeline.py automates:
	1.	Initiating the call
	2.	Waiting for completion
	3.	Downloading the recording
	4.	Transcribing audio
	5.	Generating structured reports

This allows reproducible evaluation of conversation quality.

⸻
## Configuration (.env)

Example:

OPENAI_API_KEY=your_key_here
PATIENT_NAME=Nitish John Rawat
PATIENT_DOB_SPOKEN=
APPT_TYPE=physical therapy
PREFERRED_DAY_TIME=Wednesday afternoon

TTS_MODEL=gpt-4o-mini-tts
TTS_VOICE=aria
CHAT_MODEL=gpt-4o

GATHER_TIMEOUT_S=8
MAX_SILENT_RETRIES=999999

Behavior can be modified without changing code.

⸻

Example Capabilities Demonstrated
	•	Identity verification
	•	Availability rejection handling
	•	Multi-option slot selection
	•	Deterministic reasoning under constraints
	•	Graceful exit logic
	•	Structured conversational state management

⸻

Safeguards
	•	Silence during clinic processing statements
	•	Anti-repeat response guard
	•	Wrap-up detection
	•	Post-booking conversation limiter
	•	Strict JSON enforcement
	•	No hallucinated providers or time slots

⸻

Key Technical Highlights
	•	Stateful per-call session memory
	•	Heuristic gating before LLM invocation
	•	Structured JSON response control
	•	Voice loop stabilization
	•	Deterministic post-booking termination
	•	Automated recording + transcription pipeline

⸻

Demo

A Loom walkthrough demonstrates:
	•	Live call recording
	•	Booking negotiation
	•	Clean wrap-up
	•	Code architecture overview
	•	Pipeline execution

⸻

Future Improvements
	•	Full structured output via JSON schema enforcement
	•	Multi-appointment scheduling
	•	Dynamic patient personas
	•	Cost guard / max-turn limiter
	•	Real-time analytics dashboard

⸻

## Conclusion

This project demonstrates a robust, state-controlled AI voice agent capable of autonomous task completion in a real-world IVR environment.

It combines conversational AI with deterministic safeguards to ensure reliability, control, and graceful termination.



---

## Acknowledgment

I appreciate the opportunity to build and showcase this autonomous AI voice agent as part of the Good AI assessment.
