import os
import json
from datetime import datetime
import cloudinary
import cloudinary.uploader
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

cloudinary.config(
    cloud_name = os.getenv('CLOUDINARY_CLOUD_NAME'),
    api_key = os.getenv('CLOUDINARY_API_KEY'),
    api_secret = os.getenv('CLOUDINARY_API_SECRET'),
    secure = True
)

app = Flask(__name__)
configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

user_sessions = {}

def save_to_notion(data):
    properties = {
        "名前": {"title": [{"text": {"content": data.get("name", "不明")}}]},
        "日付": {"date": {"start": data.get("date", datetime.now().strftime('%Y-%m-%d'))}},
        "時間帯": {"rich_text": [{"text": {"content": data.get("period", "間食")}}]},
        "カロリー": {"number": data.get("calories", 0)},
        "タンパク質": {"number": data.get("protein", 0)},
        "脂質": {"number": data.get("fat", 0)},
        "炭水化物": {"number": data.get("carbs", 0)},
        "メモ": {"rich_text": [{"text": {"content": data.get("memo", "")}}]}
    }
    
    if data.get("image_url"):
        properties["画像"] = {
            "files": [{"type": "external", "name": "Meal Photo", "external": {"url": data["image_url"]}}]
        }

    print(f"Notion properties: {json.dumps(properties, ensure_ascii=False)}")
    
    notion.pages.create(parent={"database_id": DATABASE_ID}, properties=properties)

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
    user_id = event.source.user_id
    user_text = event.message.text
    now = datetime.now().strftime('%Y-%m-%d')

    try:
        if user_id in user_sessions:
            pending_data = user_sessions[user_id]
            prompt = (
                f"今日は {now} です。入力「{user_text}」から、"
                "日付(YYYY-MM-DD)と時間帯(朝食, 昼食, 夕食, 間食)を抽出しJSONで返して。\n"
                '{"date": "YYYY-MM-DD", "period": "時間帯"}'
            )
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            json_str = response.text.replace('```json', '').replace('```', '').strip()
            time_data = json.loads(json_str)
            
            pending_data.update(time_data)
            save_to_notion(pending_data)
            reply_text = f"✅ {time_data['date']}の{time_data['period']}として、画像付きで保存しました！"
            del user_sessions[user_id]

        else:
            prompt = (
                f"今日は {now} です。入力された食事内容「{user_text}」を解析して、"
                "料理名、栄養素、日付(YYYY-MM-DD)、時間帯(朝食, 昼食, 夕食, 間食)を推定し、"
                "以下のJSON形式だけで答えてください。\n"
                '{"name": "料理名", "calories": 数値, "protein": 数値, "fat": 数値, "carbs": 数値, "memo": "アドバイス", "date": "YYYY-MM-DD", "period": "時間帯"}'
            )
            response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
            json_str = response.text.replace('```json', '').replace('```', '').strip()
            data = json.loads(json_str)
            
            data["image_url"] = None
            save_to_notion(data)
            reply_text = f"📝 テキストから解析して保存しました！\n\n🍴{data['name']}\n📅{data['date']} ({data['period']})"

    except Exception as e:
        print(f"Error: {e}")
        reply_text = f"エラーが発生しました: {str(e)}"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply_text)]
        ))

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    user_id = event.source.user_id
    
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        message_content = line_bot_blob_api.get_message_content(message_id=event.message.id)
        
        upload_result = cloudinary.uploader.upload(message_content)
        image_url = upload_result['secure_url']
        
        image_part = types.Part.from_bytes(data=message_content, mime_type='image/jpeg')
        prompt = """解析してJSONで答えて。{"name": "料理名", "calories": 数値, "protein": 数値, "fat": 数値, "carbs": 数値, "memo": "アドバイス"}"""
        
        response = client.models.generate_content(model='gemini-2.5-flash', contents=[prompt, image_part])
        data = json.loads(response.text.replace('```json', '').replace('```', '').strip())
        
        data["image_url"] = image_url
        user_sessions[user_id] = data
        
        reply_text = f"🍴 {data['name']} ですね！\nこれは「いつ」のご飯ですか？\n（例：今日の昼ごはん）"
        
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(reply_token=event.reply_token, messages=[TextMessage(text=reply_text)]))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)