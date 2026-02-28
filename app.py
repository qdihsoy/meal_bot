import os
from google import genai
from google.genai import types
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, MessagingApiBlob
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
app = Flask(__name__)

configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    prompt = f"あなたはプロの栄養士です。短くアドバイスして：{event.message.text}"
    response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
    
    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=response.text)]
        ))

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    print("--- 画像を受信しました。解析を開始します ---")
    
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        message_content = line_bot_blob_api.get_message_content(message_id=event.message.id)
        image_part = types.Part.from_bytes(
            data=message_content,
            mime_type='image/jpeg'
        )

        prompt = "この写真の料理名を当てて、カロリーと栄養素を推測し、健康アドバイスを150文字以内で教えて。"
        
        try:
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=[prompt, image_part]
            )

            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text=response.text)]
            ))
            print("--- 画像の解析・返信完了！ ---")
        except Exception as e:
            print(f"解析エラー: {e}")
            line_bot_api = MessagingApi(api_client)
            line_bot_api.reply_message(ReplyMessageRequest(
                reply_token=event.reply_token,
                messages=[TextMessage(text="ごめんなさい、写真がうまく読み込めませんでした。")]
            ))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)