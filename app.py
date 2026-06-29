import os, requests, json, io, pytz
from flask import Flask, request, jsonify
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime

app = Flask(__name__)

LINE_TOKEN     = os.environ.get('LINE_TOKEN')
ROOT_FOLDER_ID = os.environ.get('ROOT_FOLDER_ID', '1QAPqQ_OxF5waXoiy4F9ykJkbcV50zvKw')
DRIVE_FOLDER_URL = f'https://drive.google.com/drive/folders/{ROOT_FOLDER_ID}'

# ====== Google Drive (OAuth2) ======
def get_drive_service():
    token_json = os.environ.get('GOOGLE_TOKEN_JSON')
    if not token_json:
        raise Exception('GOOGLE_TOKEN_JSON not set')

    token_data = json.loads(token_json)
    creds = Credentials(
        token=token_data.get('token'),
        refresh_token=token_data.get('refresh_token'),
        token_uri='https://oauth2.googleapis.com/token',
        client_id=token_data.get('client_id'),
        client_secret=token_data.get('client_secret'),
        scopes=['https://www.googleapis.com/auth/drive']
    )

    if creds.expired and creds.refresh_token:
        creds.refresh(Request())

    return build('drive', 'v3', credentials=creds)


def get_or_create_folder(service, name, parent_id):
    q = (f"name='{name}' and "
         f"mimeType='application/vnd.google-apps.folder' and "
         f"'{parent_id}' in parents and trashed=false")
    res = service.files().list(q=q, fields='files(id)').execute()
    files = res.get('files', [])
    if files:
        return files[0]['id']
    meta = {
        'name': name,
        'mimeType': 'application/vnd.google-apps.folder',
        'parents': [parent_id]
    }
    folder = service.files().create(body=meta, fields='id').execute()
    return folder['id']


def save_media(message_id, user_id, media_type, source_type):
    url = f'https://api-data.line.me/v2/bot/message/{message_id}/content'
    res = requests.get(
        url,
        headers={'Authorization': f'Bearer {LINE_TOKEN}'},
        timeout=60
    )
    if res.status_code != 200:
        print(f'Failed to fetch {media_type} messageId={message_id} status={res.status_code}')
        return None

    bkk       = pytz.timezone('Asia/Bangkok')
    now       = datetime.now(bkk)

    # โฟลเดอร์แยกตาม ปี → เดือน → วัน
    year_str  = now.strftime('%Y')
    month_str = now.strftime('%Y-%m')
    day_str   = now.strftime('%Y-%m-%d')
    date_str  = now.strftime('%Y%m%d_%H%M%S')
    uid_short = (user_id or 'unknown')[-4:]

    source_folder = 'group' if source_type in ['group', 'room'] else 'direct'
    ext  = 'jpg' if media_type == 'image' else 'mp4'
    mime = 'image/jpeg' if media_type == 'image' else 'video/mp4'
    filename = f"{media_type.upper()}_{date_str}_{uid_short}.{ext}"

    try:
        service  = get_drive_service()

        # โครงสร้าง: ROOT → group/direct → images/videos → ปี → เดือน → วัน
        src_id   = get_or_create_folder(service, source_folder, ROOT_FOLDER_ID)
        type_id  = get_or_create_folder(service, media_type + 's', src_id)
        year_id  = get_or_create_folder(service, year_str, type_id)
        month_id = get_or_create_folder(service, month_str, year_id)
        day_id   = get_or_create_folder(service, day_str, month_id)

        media = MediaIoBaseUpload(io.BytesIO(res.content), mimetype=mime, resumable=True)
        file = service.files().create(
            body={'name': filename, 'parents': [day_id]},
            media_body=media,
            fields='id, webViewLink'
        ).execute()

        print(f'Saved: {filename} -> {file.get("webViewLink")}')
        return file.get('webViewLink'), filename

    except Exception as e:
        print(f'Drive error: {e}')
        return None, None


# ====== LINE Reply ======
def send_reply(reply_token, messages):
    try:
        requests.post(
            'https://api.line.me/v2/bot/message/reply',
            headers={
                'Authorization': f'Bearer {LINE_TOKEN}',
                'Content-Type': 'application/json'
            },
            json={'replyToken': reply_token, 'messages': messages},
            timeout=10
        )
    except Exception as e:
        print(f'Reply error: {e}')


# ====== Webhook ======
@app.route('/webhook', methods=['GET', 'POST'])
def webhook():
    if request.method == 'GET':
        return jsonify(status='ok'), 200

    body = request.get_json(silent=True)
    if not body:
        return jsonify(status='ok'), 200

    for event in body.get('events', []):
        if event['type'] != 'message':
            continue

        msg         = event['message']
        uid         = event['source'].get('userId', 'unknown')
        source_type = event['source']['type']
        reply_token = event.get('replyToken')

        if msg['type'] == 'image':
            file_url, filename = save_media(msg['id'], uid, 'image', source_type)
            # แจ้งหลังบันทึกเสร็จเฉพาะแชท 1:1
            if source_type == 'user' and reply_token:
                if file_url:
                    bkk = pytz.timezone('Asia/Bangkok')
                    now = datetime.now(bkk)
                    send_reply(reply_token, [{
                        'type': 'text',
                        'text': (
                            f"✅ บันทึกรูปเสร็จแล้วครับ\n"
                            f"📅 วันที่: {now.strftime('%d/%m/%Y %H:%M:%S')}\n"
                            f"📄 ชื่อไฟล์: {filename}\n"
                            f"📁 ดูได้ที่: {file_url}"
                        )
                    }])
                else:
                    send_reply(reply_token, [{
                        'type': 'text',
                        'text': "⚠️ บันทึกรูปไม่สำเร็จ กรุณาลองใหม่ครับ"
                    }])

        elif msg['type'] == 'video':
            file_url, filename = save_media(msg['id'], uid, 'video', source_type)
            if source_type == 'user' and reply_token:
                if file_url:
                    bkk = pytz.timezone('Asia/Bangkok')
                    now = datetime.now(bkk)
                    send_reply(reply_token, [{
                        'type': 'text',
                        'text': (
                            f"✅ บันทึกวิดีโอเสร็จแล้วครับ\n"
                            f"📅 วันที่: {now.strftime('%d/%m/%Y %H:%M:%S')}\n"
                            f"📄 ชื่อไฟล์: {filename}\n"
                            f"📁 ดูได้ที่: {file_url}"
                        )
                    }])
                else:
                    send_reply(reply_token, [{
                        'type': 'text',
                        'text': "⚠️ บันทึกวิดีโอไม่สำเร็จ กรุณาลองใหม่ครับ"
                    }])

        elif msg['type'] == 'text' and reply_token:
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
                        "→ บันทึกเสร็จแล้วรับลิงก์กลับทันที\n\n"
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
