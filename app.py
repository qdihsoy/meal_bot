import os
import json
from google import genai
from google.genai import types
from flask import Flask, request, abort
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.messaging import Configuration, ApiClient, MessagingApi, ReplyMessageRequest, TextMessage, MessagingApiBlob
from linebot.v3.webhooks import MessageEvent, TextMessageContent, ImageMessageContent
# from notion_client import Client
from dotenv import load_dotenv

load_dotenv()
client = genai.Client(api_key=os.getenv('GEMINI_API_KEY'))
notion = Client(auth=os.getenv('NOTION_API_KEY'))
DATABASE_ID = os.getenv('NOTION_DATABASE_ID')

app = Flask(__name__)
configuration = Configuration(access_token=os.getenv('LINE_CHANNEL_ACCESS_TOKEN'))
handler = WebhookHandler(os.getenv('LINE_CHANNEL_SECRET'))

# Notionにデータを保存する関数
def save_to_notion(data):
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

@app.route("/callback", methods=['POST'])
def callback():
    signature = request.headers['X-Line-Signature']
    body = request.get_data(as_text=True)
    try:
        handler.handle(body, signature)
    except InvalidSignatureError:
        abort(400)
    return 'OK'

# AIに解析させて保存する共通処理
def analyze_and_save(contents, event):
    # AIにJSON形式で答えるよう指示
    prompt = """
    食事を解析して以下のJSON形式で答えてください。
    {
        "name": "料理名",
        "calories": 数値,
        "protein": 数値,
        "fat": 数値,
        "carbs": 数値,
        "memo": "栄養士としてのアドバイス(100文字以内)"
    }
    """
    
    response = client.models.generate_content(
        model='gemini-2.5-flash',
        contents=[prompt] + contents
    )
    
    try:
        # 返ってきたテキストからJSON部分だけを取り出す
        json_str = response.text.replace('```json', '').replace('```', '').strip()
        data = json.loads(json_str)
        
        # Notionに保存
        save_to_notion(data)
        
        reply_text = f"✅ 保存しました！\n\n🍴{data['name']}\n🔥{data['calories']}kcal\n📝{data['memo']}"
    except Exception as e:
        print(f"Error: {e}")
        reply_text = response.text # パース失敗時はそのまま返す

    with ApiClient(configuration) as api_client:
        line_bot_api = MessagingApi(api_client)
        line_bot_api.reply_message(ReplyMessageRequest(
            reply_token=event.reply_token,
            messages=[TextMessage(text=reply_text)]
        ))

@handler.add(MessageEvent, message=TextMessageContent)
def handle_text_message(event):
    # テキストが届いた場合
    analyze_and_save([event.message.text], event)

@handler.add(MessageEvent, message=ImageMessageContent)
def handle_image_message(event):
    # 画像が届いた場合
    with ApiClient(configuration) as api_client:
        line_bot_blob_api = MessagingApiBlob(api_client)
        message_content = line_bot_blob_api.get_message_content(message_id=event.message.id)
        image_part = types.Part.from_bytes(data=message_content, mime_type='image/jpeg')
        analyze_and_save([image_part], event)

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 8000))
    app.run(host="0.0.0.0", port=port)