import os

from dotenv import load_dotenv
from fastapi import FastAPI, Request

from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError

from linebot.v3.messaging import (
    Configuration,
    ApiClient,
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
# LINE CONFIGURATION
# =========================================================

CHANNEL_ACCESS_TOKEN = os.getenv(
    "LINE_CHANNEL_ACCESS_TOKEN"
)

CHANNEL_SECRET = os.getenv(
    "LINE_CHANNEL_SECRET"
)

if not CHANNEL_ACCESS_TOKEN:
    raise RuntimeError(
        "LINE_CHANNEL_ACCESS_TOKEN is missing from .env"
    )

if not CHANNEL_SECRET:
    raise RuntimeError(
        "LINE_CHANNEL_SECRET is missing from .env"
    )


configuration = Configuration(
    access_token=CHANNEL_ACCESS_TOKEN
)

handler = WebhookHandler(
    CHANNEL_SECRET
)


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
GEMINI_KEYS = [
    key for key in GEMINI_KEYS
    if key
]

if not GEMINI_KEYS:
    raise RuntimeError(
        "No GEMINI_API_KEY_1...5 found in .env"
    )


# Gemini model
MODEL = "gemini-2.5-flash-lite"


# =========================================================
# TRANSLATION FUNCTION
# =========================================================

def translate_with_gemini(text):

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

    # =====================================================
    # TRY GEMINI KEYS
    # =====================================================

    for index, api_key in enumerate(
        GEMINI_KEYS,
        start=1
    ):

        try:

            print(
                f"Trying Gemini API key {index}..."
            )

            client = genai.Client(
                api_key=api_key
            )

            response = client.models.generate_content(
                model=MODEL,
                contents=prompt
            )

            if not response.text:
                raise RuntimeError(
                    "Gemini returned an empty response"
                )

            translated_text = (
                response.text.strip()
            )

            print(
                f"Gemini API key {index} succeeded."
            )

            return translated_text

        except Exception as e:

            last_error = e

            print(
                f"Gemini API key {index} failed:"
            )

            print(e)

            # Try next key
            continue


    # =====================================================
    # ALL KEYS FAILED
    # =====================================================

    raise RuntimeError(
        f"All Gemini API keys failed. "
        f"Last error: {last_error}"
    )


# =========================================================
# LINE WEBHOOK
# =========================================================

@app.post("/webhook")
async def webhook(request: Request):

    signature = request.headers.get(
        "x-line-signature"
    )

    body = await request.body()

    try:

        handler.handle(
            body.decode("utf-8"),
            signature
        )

    except InvalidSignatureError:

        print(
            "Invalid LINE signature"
        )

        return {
            "error": "Invalid signature"
        }

    return "OK"


# =========================================================
# HANDLE TEXT MESSAGE
# =========================================================

@handler.add(
    MessageEvent,
    message=TextMessageContent
)
def handle_message(event):

    # =====================================================
    # GET MESSAGE
    # =====================================================

    text = event.message.text.strip()

    print(
        f"Received message: {text}"
    )


    # =====================================================
    # IGNORE EMPTY MESSAGE
    # =====================================================

    if not text:
        return


    # =====================================================
    # TRANSLATE
    # =====================================================

    try:

        translated_text = (
            translate_with_gemini(text)
        )

        print(
            f"Translation: {translated_text}"
        )

    except Exception as e:

        print(
            "Translation error:"
        )

        print(e)

        translated_text = (
            "เกิดข้อผิดพลาดในการแปล "
            "กรุณาลองใหม่อีกครั้ง"
        )


    # =====================================================
    # REPLY TO LINE
    # =====================================================

    try:

        with ApiClient(
            configuration
        ) as api_client:

            line_bot_api = MessagingApi(
                api_client
            )

            # =================================================
            # REPLY WITH QUOTE
            # =================================================

            line_bot_api.reply_message(
                ReplyMessageRequest(
                    reply_token=event.reply_token,

                    messages=[
                        TextMessage(
                            text=translated_text,

                            # Quote the original message
                            quote_token=event.message.quote_token
                        )
                    ]
                )
            )

            print(
                "Reply sent successfully."
            )


    except Exception as e:

        print(
            "LINE reply error:"
        )

        print(e)