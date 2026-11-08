# RFC: Live In-Place Single-Message Master Synthesizer (Gemini + Shelby + Third Arbiter)

* **Author:** wewe00220033
* **Date:** 2026-11-09
* **Project:** Gemini Voice Transcriber (Branch: `A-test`)
* **Status:** Draft / Ideation (EGM Brainstorm)

---

## 1. Overview

This RFC proposes transforming the dual-polish execution model into an **In-Place Live-Updating Single-Message Master Synthesizer Architecture**. Instead of presenting the user with two side-by-side or separate outputs from Gemini Flash-Lite and local Shelby ChatGPT, the system runs both models asynchronously in the background. A third agent (The Master Synthesizer / السبّاك) ingests the original raw transcript, the 12-section editorial prompt, and both candidates' outputs to forge a single unified, authoritative result. The entire progression is tracked and rendered in-place within a single Telegram message that dynamically updates its state.

---

## 2. Technical State Machine & Lifecycle

The message lifecycle consists of 4 distinct sequential states:

```
[Voice Uploaded]
       │
       ▼
[State 0: Raw Audio Transcription via Gemini Transcriber]
       │
       ▼
[State 1: Send Telegram Message with Live Status Header + Raw Text]
       │
  ┌────┴────────────────────────┐
  ▼                             ▼
[Thread A: Gemini Polish]    [Thread B: Shelby Polish (Timeout: 180s)]
  │                             │
  ▼                             ▼
[Gemini Finishes ~1.5s]       [Shelby Status: Running / Done / Timeout]
  │                             │
  └──────────────┬──────────────┘
                 ▼
[State 2: In-Place Status Update]
   - If Shelby Succeeds: Launch Third Agent (Synthesizer) with Raw + Prompt + Both Drafts
   - If Shelby Times Out (>180s): Bypass Synthesizer, finalize directly with Gemini draft
                 │
                 ▼
[State 3: Master Golden Output Replaces Body in Message]
   - Full 12-Section formatted transcript inside <pre> for one-touch copy
   - Live telemetry footer: Gemini time, Shelby time, Synthesizer time, Total time
```

---

## 3. Telegram API Rate Limits & SRE Constraints

1. **Telegram EditMessageText Rate Limit:**
   - Telegram enforces a limit of approximately **1 edit per second per chat** (and max 20-30 edits per minute globally across chats).
   - If we edit the message too frequently (e.g. every 100ms or for every tick), Telegram returns `429 Too Many Requests (Retry-After)`.
   - **SRE Safeguard:** Status transitions must be event-driven, not tick-driven:
     - Transition 1: When Gemini completes.
     - Transition 2: When Shelby completes or times out.
     - Transition 3: When the final synthesized output is delivered.
     - Maximum 3 API calls per voice note, completely immune to rate limiting.

2. **Payload Size & Truncation:**
   - Total text per edit must strictly remain `<= 4096 characters`.
   - If the raw text or candidate drafts are large, the status message must only display the status tickers and summary lines, reserving full `<pre>` block delivery for the final state.

3. **Fallback Invariant:**
   - If the local Shelby process exceeds 180.0 seconds, `subprocess.run(timeout=180.0)` raises `TimeoutExpired`.
   - The thread automatically kills the hung process, flags `shelby_status = "TIMEOUT"`, and hands off Gemini's output as the final winner immediately.

---

## 4. Goals vs. Non-Goals

### In-Scope Goals:
- Single Telegram message updated in-place (no chat clutter).
- Background dual-run (Gemini Flash-Lite + Shelby ChatGPT).
- Hard 180s timeout ceiling for Shelby.
- Third synthesis agent taking raw text + prompt + both drafts to output one master text.
- Full telemetry footer showing exact seconds per stage.

### Out-of-Scope Non-Goals:
- Storing intermediate drafts permanently in database or MD archive if user only requested the final master (unless debug flag enabled).
- Creating custom Telegram webapps; the UI remains strictly native Telegram messages.

