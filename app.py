import os, requests, json, io, pytz, threading
from flask import Flask, request, jsonify
from google.oauth2.credentials import Credentials
from google.auth.transport.requests import Request
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime
from collections import defaultdict

app = Flask(__name__)

LINE_TOKEN       = os.environ.get('LINE_TOKEN')
ROOT_FOLDER_ID   = os.environ.get('ROOT_FOLDER_ID', '1QAPqQ_OxF5waXoiy4F9ykJkbcV50zvKw')
DRIVE_FOLDER_URL = f'https://drive.google.com/drive/folders/{ROOT_FOLDER_ID}'

# ====== Session tracking ======
pending      = defaultdict(lambda: {'messages': [], 'timer': None, 'reply_token': None})
pending_lock = threading.Lock()
BATCH_WAIT   = 5  # วินาที

session_counter = {}
session_lock    = threading.Lock()

def get_session_number(uid, day_str):
    key = f"{uid}_{day_str}"
    with session_lock:
        session_counter[key] = session_counter.get(key, 0) + 1
        return session_counter[key]


# ====== Google Drive ======
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


# ====== ประมวลผล batch ======
def process_batch(uid, source_type, reply_token):
    with pending_lock:
        messages = pending[uid]['messages'].copy()
        pending[uid]['messages'] = []
        pending[uid]['timer']    = None

    if not messages:
        return

    bkk        = pytz.timezone('Asia/Bangkok')
    now        = datetime.now(bkk)
    year_str   = now.strftime('%Y')
    month_str  = now.strftime('%Y-%m')
    day_str    = now.strftime('%Y-%m-%d')
    date_str   = now.strftime('%Y%m%d_%H%M%S')
    uid_short  = (uid or 'unknown')[-4:]

    session_num    = get_session_number(uid, day_str)
    session_folder = f"{day_str}_ครั้งที่{session_num}"
    source_folder  = 'group' if source_type in ['group', 'room'] else 'direct'

    saved_count  = 0
    failed_count = 0

    try:
        service    = get_drive_service()
        src_id     = get_or_create_folder(service, source_folder, ROOT_FOLDER_ID)
        year_id    = get_or_create_folder(service, year_str, src_id)
        month_id   = get_or_create_folder(service, month_str, year_id)
        session_id = get_or_create_folder(service, session_folder, month_id)

        for i, msg in enumerate(messages, 1):
            media_type = msg['type']
            ext  = 'jpg' if media_type == 'image' else 'mp4'
            mime = 'image/jpeg' if media_type == 'image' else 'video/mp4'
            filename = f"{media_type.upper()}_{date_str}_{uid_short}_{i:03d}.{ext}"

            res = requests.get(
                f'https://api-data.line.me/v2/bot/message/{msg["id"]}/content',
                headers={'Authorization': f'Bearer {LINE_TOKEN}'},
                timeout=60
            )
            if res.status_code != 200:
                failed_count += 1
                continue

            try:
                media = MediaIoBaseUpload(io.BytesIO(res.content), mimetype=mime, resumable=True)
                service.files().create(
                    body={'name': filename, 'parents': [session_id]},
                    media_body=media,
                    fields='id'
                ).execute()
                saved_count += 1
                print(f'Saved: {filename}')
            except Exception as e:
                print(f'Upload error: {e}')
                failed_count += 1

    except Exception as e:
        print(f'Batch error: {e}')

    # แจ้งผลเฉพาะแชท 1:1
    if source_type == 'user' and reply_token:
        if saved_count > 0:
            msg_text = (
                f"✅ บันทึกเสร็จแล้วครับ\n"
                f"📅 {now.strftime('%d/%m/%Y %H:%M:%S')}\n"
                f"📂 โฟลเดอร์: {session_folder}\n"
                f"📊 บันทึกสำเร็จ: {saved_count} ไฟล์\n"
                f"{'⚠️ ล้มเหลว: ' + str(failed_count) + ' ไฟล์' + chr(10) if failed_count else ''}"
                f"─────────────────\n"
                f"📁 ดูทั้งหมด: {DRIVE_FOLDER_URL}"
            )
        else:
            msg_text = "⚠️ บันทึกไม่สำเร็จทุกไฟล์ กรุณาลองใหม่ครับ"

        send_reply(reply_token, [{'type': 'text', 'text': msg_text}])


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

        if msg['type'] in ['image', 'video']:
            with pending_lock:
                if pending[uid]['timer']:
                    pending[uid]['timer'].cancel()

                pending[uid]['messages'].append(msg)
                pending[uid]['reply_token'] = reply_token

                t = threading.Timer(
                    BATCH_WAIT,
                    process_batch,
                    args=[uid, source_type, reply_token]
                )
                pending[uid]['timer'] = t
                t.start()

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
                        "→ บันทึกเสร็จแล้วรายงานทีเดียวครับ\n\n"
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
