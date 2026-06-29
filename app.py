import os, requests
from flask import Flask, request, jsonify
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseUpload
from datetime import datetime
import io, pytz
import json

app = Flask(__name__)

LINE_TOKEN = os.environ.get('LINE_TOKEN')
FOLDER_NAME = 'LINE_Media'

def get_drive_service():
    creds_json = os.environ.get('GOOGLE_CREDENTIALS')
    creds_dict = json.loads(creds_json)
    creds = service_account.Credentials.from_service_account_info(
        creds_dict,
        scopes=['https://www.googleapis.com/auth/drive']
    )
    return build('drive', 'v3', credentials=creds)

def get_or_create_folder(service, name, parent_id=None):
    q = f"name='{name}' and mimeType='application/vnd.google-apps.folder' and trashed=false"
    if parent_id:
        q += f" and '{parent_id}' in parents"
    res = service.files().list(q=q, fields='files(id)').execute()
    files = res.get('files', [])
    if files:
        return files[0]['id']
    meta = {'name': name, 'mimeType': 'application/vnd.google-apps.folder'}
    if parent_id:
        meta['parents'] = [parent_id]
    folder = service.files().create(body=meta, fields='id').execute()
    return folder['id']

def save_media(message_id, user_id, media_type):
    url = f'https://api-data.line.me/v2/bot/message/{message_id}/content'
    res = requests.get(url, headers={'Authorization': f'Bearer {LINE_TOKEN}'})
    if res.status_code != 200:
        return

    bkk = pytz.timezone('Asia/Bangkok')
    now = datetime.now(bkk)
    month_str = now.strftime('%Y-%m')
    date_str  = now.strftime('%Y%m%d_%H%M%S')
    uid_short = (user_id or 'unknown')[-4:]

    ext  = 'jpg' if media_type == 'image' else 'mp4'
    mime = 'image/jpeg' if media_type == 'image' else 'video/mp4'
    filename = f"{media_type.upper()}_{date_str}_{uid_short}.{ext}"

    service = get_drive_service()
    root_id    = get_or_create_folder(service, FOLDER_NAME)
    type_id    = get_or_create_folder(service, media_type + 's', root_id)
    month_id   = get_or_create_folder(service, month_str, type_id)

    media = MediaIoBaseUpload(io.BytesIO(res.content), mimetype=mime)
    service.files().create(
        body={'name': filename, 'parents': [month_id]},
        media_body=media
    ).execute()
    print(f'Saved: {filename}')

@app.route('/webhook', methods=['POST'])
def webhook():
    body = request.get_json()
    for event in body.get('events', []):
        if event['type'] == 'message':
            msg  = event['message']
            uid  = event['source'].get('userId', 'unknown')
            if msg['type'] == 'image':
                save_media(msg['id'], uid, 'image')
            elif msg['type'] == 'video':
                save_media(msg['id'], uid, 'video')
    return jsonify(status='ok'), 200

@app.route('/')
def index():
    return jsonify(status='running'), 200

if __name__ == '__main__':
    app.run(host='0.0.0.0', port=int(os.environ.get('PORT', 5000)))
