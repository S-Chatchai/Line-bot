import os
import time

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError

from linebot.v3.messaging import (
    ApiClient,
    Configuration,
    MessagingApi,
    ReplyMessageRequest,
    TextMessage,
)

from linebot.v3.webhooks import (
    MessageEvent,
    TextMessageContent,
)

from google import genai


# =========================================================
# LOAD .ENV
# =========================================================

load_dotenv()


# =========================================================
# FASTAPI
# =========================================================

app = FastAPI()


# =========================================================
# HEALTH CHECK (Handles the cron-job keepalive ping)
# =========================================================

@app.get("/")
def health_check():
    return {"status": "ok", "message": "Bot is alive"}


# =========================================================
# LINE CONFIGURATION
# =========================================================

CHANNEL_ACCESS_TOKEN = os.getenv("LINE_CHANNEL_ACCESS_TOKEN")
CHANNEL_SECRET = os.getenv("LINE_CHANNEL_SECRET")

if not CHANNEL_ACCESS_TOKEN:
    raise RuntimeError("LINE_CHANNEL_ACCESS_TOKEN is missing from .env")

if not CHANNEL_SECRET:
    raise RuntimeError("LINE_CHANNEL_SECRET is missing from .env")

configuration = Configuration(access_token=CHANNEL_ACCESS_TOKEN)
handler = WebhookHandler(CHANNEL_SECRET)

api_client = ApiClient(configuration)
line_bot_api = MessagingApi(api_client)


# =========================================================
# GEMINI CONFIGURATION (รองรับ 15 Keys: 1 ถึง 15)
# =========================================================

GEMINI_KEYS = [
    os.getenv(f"GEMINI_API_KEY_{i}")
    for i in range(1, 16)
]

# กรองเอาเฉพาะ Key ที่มีการประกาศค่าไว้จริงใน .env
GEMINI_KEYS = [key for key in GEMINI_KEYS if key]

if not GEMINI_KEYS:
    raise RuntimeError("No GEMINI_API_KEY found in .env")

MODEL = "gemini-2.5-flash-lite"


# =========================================================
# TRANSLATION FUNCTION
# =========================================================

def translate_with_gemini(text: str) -> str:
    prompt = f"""
You are a professional Chinese-Thai translator.

Your ONLY job is to translate the user's message.

RULES:

1. Detect the input language first.

2. If the input is Chinese:
   Translate Chinese → Thai.

3. If the input is Thai:
   Translate Thai → Chinese.

4. NEVER translate into the same language as the input.

5. Output ONLY the translation.

6. Do NOT explain the translation.

7. Do NOT add comments.

8. Do NOT add quotation marks.

9. Preserve:
   - names
   - numbers
   - technical terms
   - model numbers
   - company names
   - product names
   - abbreviations

10. Make the translation natural and suitable
    for communication between Thai and Chinese speakers.

11. Do not unnecessarily make the translation formal.

12. Keep the original meaning.

Examples:

Chinese:
你好

Thai:
สวัสดี


Chinese:
今天很忙

Thai:
วันนี้ยุ่งมาก


Thai:
วันนี้อากาศร้อนมาก

Chinese:
今天天气很热。


Thai:
คุณกำลังทำอะไร

Chinese:
你在做什么？


Now translate this message:

{text}
"""
    last_error = None

    for index, api_key in enumerate(GEMINI_KEYS, start=1):
        try:
            print(f"Trying Gemini API key {index}...")
            client = genai.Client(api_key=api_key)
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
            )

            if not response.text:
                raise RuntimeError("Gemini returned an empty response")

            translated_text = response.text.strip()
            print(f"Gemini API key {index} succeeded.")
            return translated_text

        except Exception as e:
            last_error = e
            print(f"Gemini API key {index} failed: {e}")
            continue

    raise RuntimeError(
        f"All Gemini API keys failed. Last error: {last_error}"
    )


# =========================================================
# SAFE LINE REPLY HELPER
# =========================================================

def send_line_reply_with_retry(reply_token: str, message: TextMessage, max_retries: int = 3):
    request_payload = ReplyMessageRequest(
        reply_token=reply_token,
        messages=[message],
    )

    for attempt in range(1, max_retries + 1):
        try:
            line_bot_api.reply_message(request_payload)
            print("Reply sent successfully.")
            return
        except Exception as e:
            print(f"LINE reply attempt {attempt} failed: {e}")
            if attempt < max_retries:
                time.sleep(0.5 * attempt)
            else:
                print("Exceeded maximum retries for LINE reply.")


# =========================================================
# LINE WEBHOOK
# =========================================================

@app.post("/webhook")
async def webhook(request: Request):
    signature = request.headers.get("x-line-signature", "")
    body = (await request.body()).decode("utf-8")

    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("Invalid LINE signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"


# =========================================================
# HANDLE TEXT MESSAGE
# =========================================================

@handler.add(MessageEvent, message=TextMessageContent)
def handle_message(event):
    text = event.message.text.strip()
    print(f"Received message: {text}")

    if not text:
        return

    try:
        translated_text = translate_with_gemini(text)
        print(f"Translation: {translated_text}")
    except Exception as e:
        print(f"Translation error: {e}")
        translated_text = "เกิดข้อผิดพลาดในการแปล กรุณาลองใหม่อีกครั้ง"

    reply_msg = TextMessage(
        text=translated_text,
        quote_token=getattr(event.message, "quote_token", None),
    )

    send_line_reply_with_retry(event.reply_token, reply_msg)
