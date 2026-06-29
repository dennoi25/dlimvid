import os, requests, json, io, pytz
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime

app = Flask(__name__)

LINE_TOKEN = os.environ.get('LINE_TOKEN')
FOLDER_NAME = 'LINE_Media'
ROOT_FOLDER_ID = '1ZiIzxPVRCuXTSDw9ufgRh5XyZyfxqDgE'  # ← ใส่ Folder ID จริงตรงนี้
DRIVE_FOLDER_URL = f'https://drive.google.com/drive/folders/{ROOT_FOLDER_ID}'

# ====== Google Drive ======
def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(
        '/etc/secrets/credentials.json',
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def get_or_create_folder(service, name, parent_id):
    q = (f"name='{name}' and "
         f"mimeType='application/vnd.google-apps.folder' and "
         f"'{parent_id}' in parents and trashed=false")
    res = service.files().list(q=q, fields='files(id)',
                               supportsAllDrives=True,
                               includeItemsFromAllDrives=True).execute()
    files = res.get('files', [])
    if files:
        return files[0]['id']
    meta = {
        'name': name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    folder = service.files().create(body=meta, fields='id',
                                    supportsAllDrives=True).execute()
    return folder['id']

def save_media(message_id, user_id, media_type, source_type):
    url = f'https://api-data.line.me/v2/bot/message/{message_id}/content'
    res = requests.get(url, headers={'Authorization': f'Bearer {LINE_TOKEN}'}, timeout=30)
    if res.status_code != 200:
        print(f'Failed to fetch {media_type} messageId={message_id}')
        return None

    bkk = pytz.timezone('Asia/Bangkok')
    now = datetime.now(bkk)
    month_str = now.strftime('%Y-%m')
    date_str  = now.strftime('%Y%m%d_%H%M%S')
    uid_short = (user_id or 'unknown')[-4:]

    source_folder = 'group' if source_type in ['group', 'room'] else 'direct'

    ext  = 'jpg' if media_type == 'image' else 'mp4'
    mime = 'image/jpeg' if media_type == 'image' else 'video/mp4'
    filename = f"{media_type.upper()}_{date_str}_{uid_short}.{ext}"

    service = get_drive_service()
    src_id   = get_or_create_folder(service, source_folder, ROOT_FOLDER_ID)
    type_id  = get_or_create_folder(service, media_type + 's', src_id)
    month_id = get_or_create_folder(service, month_str, type_id)

    media = MediaIoBaseUpload(io.BytesIO(res.content), mimetype=mime)
    file = service.files().create(
        body={'name': filename, 'parents': [month_id]},
        media_body=media,
        fields='id, webViewLink',
        supportsAllDrives=True
    ).execute()

    print(f'Saved: {filename}')
    return file.get('webViewLink')

# ====== LINE Reply ======
def send_reply(reply_token, messages):
    requests.post(
        'https://api.line.me/v2/bot/message/reply',
        headers={
            'Authorization': f'Bearer {LINE_TOKEN}',
            'Content-Type': 'application/json'
        },
        json={'replyToken': reply_token, 'messages': messages},
        timeout=10
    )

# ====== Webhook ======
@app.route('/webhook', methods=['POST'])
def webhook():
    body = request.get_json()
    for event in body.get('events', []):
        if event['type'] != 'message':
            continue

        msg         = event['message']
        uid         = event['source'].get('userId', 'unknown')
        source_type = event['source']['type']
        reply_token = event.get('replyToken')

        if msg['type'] == 'image':
            file_url = save_media(msg['id'], uid, 'image', source_type)
            if source_type == 'user' and reply_token and file_url:
                send_reply(reply_token, [{
                    'type': 'text',
                    'text': f"✅ บันทึกรูปแล้วครับ\n📁 ดูได้ที่: {file_url}"
                }])

        elif msg['type'] == 'video':
            file_url = save_media(msg['id'], uid, 'video', source_type)
            if source_type == 'user' and reply_token and file_url:
                send_reply(reply_token, [{
                    'type': 'text',
                    'text': f"✅ บันทึกวิดีโอแล้วครับ\n📁 ดูได้ที่: {file_url}"
                }])

        elif msg['type'] == 'text':
            text = msg['text'].strip()
            if text == '/album':
                send_reply(reply_token, [{
                    'type': 'text',
                    'text': f"📁 Album กลุ่มทั้งหมดอยู่ที่นี่ครับ\n{DRIVE_FOLDER_URL}"
                }])
            elif text == '/help':
                send_reply(reply_token, [{
                    'type': 'text',
                    'text': (
                        "📌 คำสั่งที่ใช้ได้\n\n"
                        "ส่งรูป/วิดีโอในกลุ่ม\n"
                        "→ บันทึกขึ้น Drive อัตโนมัติ\n\n"
                        "แชท 1:1 กับ Bot แล้วส่งรูป/วิดีโอ\n"
                        "→ บันทึกและรับลิงก์กลับทันที\n\n"
                        "/album → ลิงก์ Google Drive\n"
                        "/help  → ดูคำสั่งทั้งหมด"
                    )
                }])

    return jsonify(status='ok'), 200

@app.route('/')
def index():
    return jsonify(status='running'), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
