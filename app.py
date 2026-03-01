import os
import json
from datetime import datetime
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
print(f"DEBUG: Key starts with {os.getenv('NOTION_API_KEY')[:5]}...")
print(f"DEBUG: DB ID is {os.getenv('NOTION_DATABASE_ID')}")

app = Flask(__name__)
configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# 一時的にデータを保存するメモリ（※サーバー再起動で消えますが、個人利用ならOK）
user_sessions = {}

def save_to_notion(data):
    notion.pages.create(
        parent={"database_id": DATABASE_ID},
        properties={
            "名前": {"title": [{"text": {"content": data.get("name", "不明")}}]},
            "日付": {"date": {"start": data.get("date", datetime.now().strftime('%Y-%m-%d'))}},
            "時間帯": {"select": {"name": data.get("period", "間食")}},
            "カロリー": {"number": data.get("calories", 0)},
            "タンパク質": {"number": data.get("protein", 0)},
            "脂質": {"number": data.get("fat", 0)},
            "炭水化物": {"number": data.get("carbs", 0)},
            "メモ": {"rich_text": [{"text": {"content": data.get("memo", "")}}]}
        }
    )

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# テキストメッセージが来た時
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    user_id = event.source.user_id
    user_text = event.message.text

    # もし「画像解析済み」のデータがメモリにあれば
    if user_id in user_sessions:
        pending_data = user_sessions[user_id]
        
        # Geminiに「いつの食事か」を解釈させる
        now = datetime.now().strftime('%Y-%m-%d')
        prompt = f"今日は {now} です。ユーザーの入力「{user_text}」から、日付(YYYY-MM-DD形式)と時間帯(朝食, 昼食, 夕食, 間食のいずれか)を抽出し、以下のJSONで返して。 \n" + '{"date": "YYYY-MM-DD", "period": "時間帯"}'
        
        response = client.models.generate_content(model='gemini-2.5-flash', contents=prompt)
        time_data = json.loads(response.text.replace('```json', '').replace('```', '').strip())
        
        # データを合体させてNotion保存
        pending_data.update(time_data)
        save_to_notion(pending_data)
        
        reply_text = f"✅ {time_data['date']}の{time_data['period']}として保存しました！"
        del user_sessions[user_id] # メモリを空にする
    
    else:
        # メモリがない場合は、通常のAIチャット（または説明）を返す
        reply_text = "まず食事の写真を送ってください。その後に「今日の昼ごはん」などと教えていただければ保存します！"

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply_text)]
        ))

# 画像が来た時
@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    user_id = event.source.user_id
    
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        message_content = line_bot_blob_api.get_message_content(message_id=event.message.id)
        image_part = types.Part.from_bytes(data=message_content, mime_type='image/jpeg')
        
        # 画像だけ解析してメモリに置く
        prompt = """この食事を解析して以下のJSONで答えて。
        {"name": "料理名", "calories": 数値, "protein": 数値, "fat": 数値, "carbs": 数値, "memo": "栄養アドバイス"}"""
        
        response = client.models.generate_content(model='gemini-2.5-flash', contents=[prompt, image_part])
        data = json.loads(response.text.replace('```json', '').replace('```', '').strip())
        
        # 解析結果を一時保存
        user_sessions[user_id] = data
        
        reply_text = f"🍴 {data['name']} ですね！\nこれは「いつ」のご飯ですか？\n（例：今日の昼ごはん、昨日の夕食）"
        
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply_text)]
        ))

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)