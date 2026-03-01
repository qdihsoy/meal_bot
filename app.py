import os
import json
from google import genai
from google.genai import types
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, MessagingApiBlob
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent
from notion_client import Client as NotionClient
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
notion = NotionClient(auth=os.getenv('NOTION_API_KEY'))
DATABASE_ID = os.getenv('NOTION_DATABASE_ID')

app = Flask(__name__)
configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

def save_to_notion(data):
    print(f"--- 4. Notionに書き込み開始: {data['name']} ---")
    try:
        notion.pages.create(
            parent={"database_id": DATABASE_ID},
            properties={
                "名前": {"title": [{"text": {"content": data.get("name", "不明")}}]},
                "カロリー": {"number": data.get("calories", 0)},
                "タンパク質": {"number": data.get("protein", 0)},
                "脂質": {"number": data.get("fat", 0)},
                "炭水化物": {"number": data.get("carbs", 0)},
                "メモ": {"rich_text": [{"text": {"content": data.get("memo", "")}}]}
            }
        )
        print("--- 5. Notion書き込み成功！ ---")
    except Exception as e:
        print(f"--- Notionエラー発生: {e} ---")
        raise e

@app.route("/callback", methods=['POST'])
def callback():
    print("--- 1. LINEからWebhookを受信しました ---")
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        print("署名エラーです")
        abort(400)
    return 'OK'

def analyze_and_save(contents, event):
    print("--- 2. Geminiで解析中... ---")
    prompt = """食事を解析して以下のJSON形式で答えて。
    {"name": "料理名", "calories": 数値, "protein": 数値, "fat": 数値, "carbs": 数値, "memo": "アドバイス"}"""
    
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=[prompt] + contents
        )
        print(f"--- 3. Geminiの回答を受信: {response.text[:50]}... ---")
        
        json_str = response.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(json_str)
        
        # Notion保存実行
        save_to_notion(data)
        reply_text = f"✅ Notionに保存しました！\n\n🍴{data['name']}\n🔥{data['calories']}kcal"

    except Exception as e:
        print(f"--- 解析・保存中にエラー: {e} ---")
        reply_text = f"エラーが発生しました: {str(e)}"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply_text)]
        ))

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    analyze_and_save([event.message.text], event)

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        message_content = line_bot_blob_api.get_message_content(message_id=event.message.id)
        image_part = types.Part.from_bytes(data=message_content, mime_type='image/jpeg')
        analyze_and_save([image_part], event)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)