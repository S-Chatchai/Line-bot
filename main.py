import os
import threading
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, HTTPException, Request

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
from google.genai import types


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


# =========================================================
# GEMINI CONFIGURATION
# =========================================================

GEMINI_KEYS = [
    os.getenv("GEMINI_API_KEY_1"),
    os.getenv("GEMINI_API_KEY_2"),
    os.getenv("GEMINI_API_KEY_3"),
    os.getenv("GEMINI_API_KEY_4"),
    os.getenv("GEMINI_API_KEY_5"),
]

# Remove empty keys
GEMINI_KEYS = [key for key in GEMINI_KEYS if key]

if not GEMINI_KEYS:
    raise RuntimeError("No GEMINI_API_KEY_1...5 found in .env")

# Pre-initialize clients เพื่อลดเวลาทำ Handshake ซ้ำๆ
GEMINI_CLIENTS = [genai.Client(api_key=key) for key in GEMINI_KEYS]

# ตัวชี้ลำดับ Key ที่ใช้งาน เพื่อไม่ให้เริ่มต้นที่ Key 1 ใหม่ตลอดเวลา
current_key_index = 0
key_lock = threading.Lock()

# Gemini model
MODEL = "gemini-2.5-flash-lite"


# =========================================================
# TRANSLATION FUNCTION
# =========================================================

def translate_with_gemini(text: str) -> str:
    global current_key_index

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
    total_keys = len(GEMINI_CLIENTS)

    with key_lock:
        start_index = current_key_index

    # =====================================================
    # TRY GEMINI KEYS (Start from current available key)
    # =====================================================

    for attempt in range(total_keys):
        idx = (start_index + attempt) % total_keys
        client = GEMINI_CLIENTS[idx]

        try:
            print(f"Trying Gemini API key index {idx + 1}...")

            # ตั้ง Timeout ไว้ 3500ms (3.5 วินาที) ต่อ Key
            # เพื่อให้ถ้าค้างหรือติด Rate Limit จะตัดข้ามทันที LINE Token จะได้ไม่หมดอายุ
            response = client.models.generate_content(
                model=MODEL,
                contents=prompt,
                config=types.GenerateContentConfig(
                    http_options={"timeout": 3500}
                ),
            )

            if not response.text:
                raise RuntimeError("Gemini returned an empty response")

            translated_text = response.text.strip()
            print(f"Gemini API key index {idx + 1} succeeded.")

            # บันทึก Index ที่ทำงานสำเร็จไว้ใช้ต่อสำหรับรอบหน้า
            with key_lock:
                current_key_index = idx

            return translated_text

        except Exception as e:
            last_error = e
            print(f"Gemini API key index {idx + 1} failed: {e}")
            continue

    # =====================================================
    # ALL KEYS FAILED
    # =====================================================

    raise RuntimeError(
        f"All Gemini API keys failed. Last error: {last_error}"
    )


# =========================================================
# ASYNC WORKER (HANDLE PROCESSING IN BACKGROUND)
# =========================================================

def process_message_event(text: str, reply_token: str, quote_token: str | None):
    # ข้ามข้อความว่าง
    clean_text = text.strip()
    if not clean_text:
        return

    # แปลข้อความ
    try:
        translated_text = translate_with_gemini(clean_text)
        print(f"Translation: {translated_text}")
    except Exception as e:
        print(f"Translation error: {e}")
        # กรณีแปลไม่ได้ทั้งหมด จะไม่ตอบกลับขยะ/ข้อความ Error เข้าไปรบกวนในกลุ่ม
        return

    # ตอบกลับทาง LINE
    try:
        with ApiClient(configuration) as api_client:
            line_bot_api = MessagingApi(api_client)

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=reply_token,
                    messages=[
                        TextMessage(
                            text=translated_text,
                            quote_token=quote_token,
                        )
                    ],
                )
            )
            print("Reply sent successfully.")

    except Exception as e:
        print(f"LINE reply error: {e}")


# =========================================================
# LINE WEBHOOK
# =========================================================

@app.post("/webhook")
async def webhook(request: Request, background_tasks: BackgroundTasks):

    signature = request.headers.get("x-line-signature", "")
    body = (await request.body()).decode("utf-8")

    try:
        # ใช้ parse เพื่อดึง event ออกมา แล้วส่งต่อไปรันใน background_tasks ทันที
        # Endpoint นี้จะได้ return "OK" ภายในไม่กี่ millisecond ไม่โดน LINE ตัด connection
        events = handler.parser.parse(body, signature)
    except InvalidSignatureError:
        print("Invalid LINE signature")
        raise HTTPException(status_code=400, detail="Invalid signature")

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(event.message, TextMessageContent):
            background_tasks.add_task(
                process_message_event,
                text=event.message.text,
                reply_token=event.reply_token,
                quote_token=getattr(event.message, "quote_token", None),
            )

    return "OK"
