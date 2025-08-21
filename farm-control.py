# PHIÊN BẢN ĐIỀU KHIỂN FARM - NÂNG CẤP TOÀN DIỆN VÀ HOÀN CHỈNH
import discum
import threading
import time
import os
import random
import re
import requests
import json
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv

load_dotenv()

# --- CẤU HÌNH ---
main_token_alpha = os.getenv("MAIN_TOKEN")
other_main_tokens = os.getenv("MAIN_TOKENS").split(",") if os.getenv("MAIN_TOKENS") else []
sub_tokens = os.getenv("TOKENS").split(",") if os.getenv("TOKENS") else []
acc_names_str = os.getenv("SUB_ACC_NAMES")
sub_acc_names = [name.strip() for name in acc_names_str.split(',')] if acc_names_str else []

karuta_id = "646937666251915264"
yoru_bot_id = "1311684840462225440"

GREEK_LETTERS = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta', 'Iota', 'Kappa', 
                 'Lambda', 'Mu', 'Nu', 'Xi', 'Omicron', 'Pi', 'Rho', 'Sigma', 'Tau', 'Upsilon', 
                 'Phi', 'Chi', 'Psi', 'Omega']

# --- BIẾN TRẠNG THÁI ---
main_bots = []
sub_bots = []
event_grab_enabled = False
auto_reboot_enabled = False
auto_reboot_delay = 3600
last_reboot_cycle_time = 0
auto_reboot_stop_event = threading.Event()
auto_reboot_thread = None
bots_lock = threading.Lock()
server_start_time = time.time()
bot_active_states = {}
farm_servers = []
farm_groups = []

main_panel = {
    'auto_grab_enabled_alpha': False, 'heart_threshold_alpha': 15,
    'auto_grab_enabled_main': False, 'heart_threshold_main': 50,
    'auto_grab_enabled_sub': False, 'heart_threshold_sub': 50,
    'spam_message': 'kcf', 'spam_delay': 10
}

# --- HÀM LƯU VÀ TẢI CÀI ĐẶT ---
def save_farm_settings():
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("FARM_JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    data = {'farm_servers': farm_servers, 'farm_groups': farm_groups, 'main_panel': main_panel}
    try:
        req = requests.put(url, json=data, headers=headers, timeout=10)
        if req.status_code == 200: print("[Settings] Đã lưu cài đặt farm.", flush=True)
    except Exception as e: print(f"[Settings] Lỗi khi lưu cài đặt farm: {e}", flush=True)

def load_farm_settings():
    global farm_servers, farm_groups, main_panel
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("FARM_JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    headers = {'X-Master-Key': api_key, 'X-Bin-Meta': 'false'}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
    try:
        req = requests.get(url, headers=headers, timeout=10)
        if req.status_code == 200:
            data = req.json()
            farm_servers = data.get('farm_servers', [])
            farm_groups = data.get('farm_groups', [])
            main_panel.update(data.get('main_panel', {}))
            print(f"[Settings] Đã tải {len(farm_servers)} farm và {len(farm_groups)} nhóm.", flush=True)
    except Exception: farm_servers, farm_groups = [], []

def save_main_settings():
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    settings = {
        'event_grab_enabled': event_grab_enabled, 'auto_reboot_enabled': auto_reboot_enabled, 
        'auto_reboot_delay': auto_reboot_delay, 'bot_active_states': bot_active_states,
        'last_reboot_cycle_time': last_reboot_cycle_time
    }
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    try:
        req = requests.put(url, json=settings, headers=headers, timeout=10)
        if req.status_code == 200: print("[Settings] Đã lưu cài đặt chính.", flush=True)
    except Exception as e: print(f"[Settings] Lỗi khi lưu cài đặt chính: {e}", flush=True)

def load_main_settings():
    api_key, bin_id = os.getenv("JSONBIN_API_KEY"), os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    headers = {'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
    try:
        req = requests.get(url, headers=headers, timeout=10)
        if req.status_code == 200 and req.json().get("record"):
            globals().update(req.json()["record"])
            print("[Settings] Đã tải cài đặt chính.", flush=True)
        else:
            save_main_settings()
    except Exception as e: print(f"[Settings] Lỗi khi tải cài đặt chính: {e}", flush=True)

# --- LOGIC BOT FARM ---
def get_grab_settings_for_bot(target_server, bot_type, bot_index):
    if bot_type == 'main' and bot_index == 0:  # Alpha
        return (target_server.get('auto_grab_enabled_alpha', False),
                target_server.get('heart_threshold_alpha', 15),
                {0: 0.2, 1: 1.2, 2: 2.0})
    elif bot_type == 'main':  # Các bot main khác (Beta, Gamma...)
        return (target_server.get('auto_grab_enabled_main', False),
                target_server.get('heart_threshold_main', 50),
                {0: 1.0, 1: 2.0, 2: 2.8})
    else:  # Sub bots
        return (target_server.get('auto_grab_enabled_sub', False),
                target_server.get('heart_threshold_sub', 50),
                {0: 0.7, 1: 1.7, 2: 2.4})

def handle_farm_grab(bot, msg, bot_type, bot_index):
    channel_id = msg.get("channel_id")
    target_server = next((s for s in farm_servers if s.get('main_channel_id') == channel_id), None)
    if not target_server: return

    if msg.get("author", {}).get("id") == karuta_id and 'dropping 3' in msg.get("content", ""):
        last_drop_msg_id = msg["id"]

        is_grab_enabled, heart_threshold, delays = get_grab_settings_for_bot(target_server, bot_type, bot_index)
        ktb_channel_id = target_server.get('ktb_channel_id')

        if is_grab_enabled and ktb_channel_id:
            def read_yoru_bot():
                time.sleep(0.6)
                try:
                    messages = bot.getMessages(channel_id, num=5).json()
                    for msg_item in messages:
                        if msg_item.get("author", {}).get("id") == yoru_bot_id and msg_item.get("embeds"):
                            desc = msg_item["embeds"][0].get("description", "")
                            hearts = [int(m.group(1)) if (m := re.search(r'♡(\d+)', l)) else 0 for l in desc.split('\n')[:3]]
                            if not any(hearts): break
                            max_heart = max(hearts)
                            if max_heart >= heart_threshold:
                                index = hearts.index(max_heart)
                                emoji = ["1️⃣", "2️⃣", "3️⃣"][index]
                                delay = delays.get(index, 1.5)
                                bot_name = f"{GREEK_LETTERS[bot_index] if bot_type == 'main' and bot_index < len(GREEK_LETTERS) else bot_type.capitalize() + str(bot_index)}"
                                print(f"[FARM GRAB] {target_server['name']} | {bot_name} | Tim: {max_heart} | Delay: {delay}s", flush=True)
                                threading.Timer(delay, lambda: (
                                    bot.addReaction(channel_id, last_drop_msg_id, emoji),
                                    time.sleep(2),
                                    bot.sendMessage(ktb_channel_id, "kt b")
                                )).start()
                            break
                except Exception as e:
                    print(f"[FARM ERROR] Lỗi đọc Yoru: {e}", flush=True)
            threading.Thread(target=read_yoru_bot).start()

        if event_grab_enabled and bot_type == 'main' and bot_index == 0: # Chỉ Alpha bot nhặt event
            def check_event():
                try:
                    time.sleep(5)
                    full_msg = bot.getMessage(channel_id, last_drop_msg_id).json()[0]
                    if 'reactions' in full_msg and any(r['emoji']['name'] == '🍉' for r in full_msg['reactions']):
                        print(f"[EVENT GRAB] {target_server['name']} | Phát hiện event! Alpha bot nhặt.", flush=True)
                        bot.addReaction(channel_id, last_drop_msg_id, "🍉")
                except Exception as e:
                    print(f"[EVENT ERROR] Lỗi kiểm tra event: {e}", flush=True)
            threading.Thread(target=check_event).start()

def create_bot(token, bot_type, bot_index):
    bot = discum.Client(token=token, log=False)
    @bot.gateway.command
    def on_ready(resp):
        if resp.event.ready:
            user = resp.raw.get('user', {})
            print(f"Bot '{bot_type.capitalize()}{bot_index}' đã đăng nhập: {user.get('username')}", flush=True)

    @bot.gateway.command
    def on_message(resp):
        if not (resp.event.message or (resp.raw and resp.raw.get('t') == 'MESSAGE_UPDATE')): return
        msg = resp.parsed.auto()
        handle_farm_grab(bot, msg, bot_type, bot_index)

    threading.Thread(target=bot.gateway.run, daemon=True).start()
    return bot

# --- CÁC VÒNG LẶP NỀN ---
spam_tasks_running = set()
def spam_loop():
    while True:
        try:
            now = time.time()
            for group in farm_groups:
                if not group.get('spam_enabled'): continue
                
                group_id = group.get('id')
                last_spam = group.get('last_spam_time', 0)
                delay = group.get('spam_delay', 10)

                if (now - last_spam) >= delay and group_id not in spam_tasks_running:
                    selected_bot_ids = group.get('selected_bots', [])
                    bots_to_use = []
                    with bots_lock:
                        for bot_id in selected_bot_ids:
                            if bot_active_states.get(bot_id, False):
                                type, index_str = bot_id.split('_')
                                index = int(index_str)
                                if type == 'main' and index < len(main_bots):
                                    bots_to_use.append(main_bots[index])
                                elif type == 'sub' and index < len(sub_bots):
                                    bots_to_use.append(sub_bots[index])
                    
                    if not bots_to_use: continue

                    farms_in_group = [s for s in farm_servers if s.get('group_id') == group_id]
                    if not farms_in_group: continue

                    spam_tasks_running.add(group_id)
                    group['last_spam_time'] = now
                    
                    def group_spam_task(g_id, farms, bots_list):
                        print(f"[SPAM] Bắt đầu spam cho nhóm {group['name']} với {len(bots_list)} bot.", flush=True)
                        for server in farms:
                            msg = server.get('spam_message')
                            chan_id = server.get('spam_channel_id')
                            if not msg or not chan_id: continue
                            for i, bot in enumerate(bots_list):
                                try:
                                    bot.sendMessage(chan_id, msg)
                                    time.sleep(2) # Delay giữa các bot
                                except Exception: pass
                        spam_tasks_running.remove(g_id)
                        print(f"[SPAM] Hoàn thành spam cho nhóm {group['name']}.", flush=True)

                    threading.Thread(target=group_spam_task, args=(group_id, farms_in_group, bots_to_use)).start()
            time.sleep(1)
        except Exception as e:
            print(f"[SPAM ERROR] Lỗi trong vòng lặp spam: {e}", flush=True)
            time.sleep(5)

def reboot_bot(target_id):
    with bots_lock:
        bot_type, index_str = target_id.split('_')
        index = int(index_str)
        
        if bot_type == 'main':
            if index < len(main_bots):
                try: main_bots[index].gateway.close()
                except: pass
                token = main_token_alpha if index == 0 else other_main_tokens[index - 1]
                main_bots[index] = create_bot(token, 'main', index)
                print(f"[Reboot] Main Bot {index} đã khởi động lại.", flush=True)
        elif bot_type == 'sub':
            if index < len(sub_bots):
                try: sub_bots[index].gateway.close()
                except: pass
                sub_bots[index] = create_bot(sub_tokens[index], 'sub', index)
                print(f"[Reboot] Sub Bot {index} đã khởi động lại.", flush=True)

def auto_reboot_loop():
    while not auto_reboot_stop_event.is_set():
        try:
            if auto_reboot_enabled and (time.time() - last_reboot_cycle_time) >= auto_reboot_delay:
                print("[Reboot] Bắt đầu chu kỳ reboot tự động...", flush=True)
                all_bot_ids = list(bot_active_states.keys())
                for bot_id in all_bot_ids:
                    if bot_active_states.get(bot_id, False):
                        reboot_bot(bot_id)
                        time.sleep(5)
                last_reboot_cycle_time = time.time()
            if auto_reboot_stop_event.wait(timeout=60): break
        except Exception as e:
            print(f"[REBOOT ERROR] Lỗi trong vòng lặp reboot: {e}", flush=True)
            time.sleep(60)
    print("[Reboot] Luồng tự động reboot đã dừng.", flush=True)

def periodic_save_loop():
    while True:
        time.sleep(300)
        print("[Settings] Bắt đầu lưu định kỳ...", flush=True)
        save_farm_settings()
        save_main_settings()

app = Flask(__name__)

# --- GIAO DIỆN WEB ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Enhanced Karuta Farm Control</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Courier+Prime:wght@400;700&family=Nosifer&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-bg: #0a0a0a; --secondary-bg: #1a1a1a; --panel-bg: #111111; --border-color: #333333;
            --blood-red: #8b0000; --necro-green: #228b22; --shadow-cyan: #008b8b; --text-primary: #f0f0f0;
            --text-secondary: #cccccc; --hot-pink: #FF69B4; --gold: #FFD700; --purple: #9932CC;
        }
        body { font-family: 'Courier Prime', monospace; background: var(--primary-bg); color: var(--text-primary); margin: 0; padding: 20px; }
        .container { max-width: 1800px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; }
        .title { font-family: 'Nosifer', cursive; font-size: 2.5rem; color: var(--hot-pink); text-shadow: 0 0 15px var(--hot-pink);}
        .panel { background: #111; border: 1px solid var(--border-color); border-radius: 10px; padding: 20px; margin-bottom: 20px; position: relative; }
        .panel h2, .panel h3, .panel h4 { font-family: 'Orbitron', monospace; color: var(--text-secondary); border-bottom: 1px solid var(--border-color); padding-bottom: 10px; margin-top: 0;}
        .main-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }
        .btn { background: var(--secondary-bg); border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px 12px; border-radius: 4px; cursor: pointer; font-family: 'Orbitron'; }
        .btn:hover { filter: brightness(1.2); }
        .btn-danger { border-color: var(--blood-red); color: var(--blood-red); } .btn-danger:hover { background: var(--blood-red); color: var(--primary-bg); }
        .btn-success { border-color: var(--necro-green); color: var(--necro-green); } .btn-success:hover { background: var(--necro-green); color: var(--primary-bg); }
        .input-group { display: flex; align-items: stretch; gap: 5px; margin-bottom: 10px; }
        .input-group label { padding: 8px; background: #222; border: 1px solid var(--border-color); border-radius: 5px 0 0 5px; white-space: nowrap; }
        .input-group input, .input-group textarea, .input-group select { width: 100%; background: #000; border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px; border-radius: 0 5px 5px 0; }
        .bot-status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
        .bot-status-item { display: flex; justify-content: space-between; align-items: center; padding: 5px 8px; background: #222; border-radius: 4px; }
        .btn-toggle-state { cursor: pointer; background: transparent; border: none; font-weight: 700; }
        .btn-rise { color: var(--necro-green); } .btn-rest { color: var(--blood-red); }
        .group-panel { border: 1px solid var(--purple); box-shadow: 0 0 10px rgba(153, 50, 204, 0.4); }
        .farm-panel { border-left: 5px solid var(--hot-pink); }
        .main-panel { border: 2px solid var(--gold); box-shadow: 0 0 15px rgba(255, 215, 0, 0.6); }
        #add-farm-btn, #add-group-btn { display: flex; flex-direction: column; align-items: center; justify-content: center; min-height:150px; border-style: dashed; cursor: pointer; }
        .msg-status { text-align: center; color: var(--shadow-cyan); padding: 12px; border: 1px dashed var(--border-color); margin-bottom: 20px; background: rgba(0, 139, 139, 0.1); display: none; position: fixed; top: 10px; left: 50%; transform: translateX(-50%); z-index: 1000; border-radius: 8px;}
        .delete-btn { background: var(--blood-red); color: white; border:none; border-radius: 50%; width: 25px; height: 25px; cursor: pointer; position: absolute; top: 10px; right: 10px; line-height: 25px; text-align: center; }
        .bot-selector { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 5px; margin-top: 10px; max-height: 120px; overflow-y: auto; background: #000; padding: 5px; border-radius: 4px;}
        .bot-selector-item label { display: flex; align-items: center; width: 100%; font-size: 0.9em; }
        .sync-section { border-top: 2px solid var(--gold); margin-top: 20px; padding-top: 15px; }
        .timer-display { font-family: 'Courier Prime', monospace; font-size: 1.1em; font-weight: 700; color: var(--gold); }
    </style>
</head>
<body>
    <div class="container">
        <div class="header"><h1 class="title">ENHANCED FARM CONTROL PANEL</h1></div>
        <div id="msg-status-container" class="msg-status"></div>

        <div class="panel">
            <h2><i class="fas fa-server"></i> System Status & Global Controls</h2>
            <div class="main-grid">
                <div id="bot-status-list"></div>
                <div>
                    <div class="input-group">
                        <label>Auto Reboot Delay (s)</label>
                        <input type="number" id="auto-reboot-delay" value="{{ auto_reboot_delay }}">
                        <button id="auto-reboot-toggle-btn" class="btn {{ reboot_button_class }}">{{ reboot_action }}</button>
                    </div>
                    <button id="event-grab-toggle-btn" class="btn {{ event_grab_button_class }}" style="width: 100%;">{{ event_grab_action }}</button>
                </div>
            </div>
        </div>
        
        <div class="panel main-panel">
            <h2><i class="fas fa-crown"></i> Master Control Panel</h2>
            <div class="main-grid">
                <div>
                    <h4>Harvest Settings</h4>
                    <div class="input-group"><label>ALPHA</label><input type="number" class="main-panel-input" data-field="heart_threshold_alpha" value="{{ main_panel.heart_threshold_alpha }}"><button class="btn main-panel-toggle" data-field="auto_grab_enabled_alpha">{{ 'TẮT' if main_panel.auto_grab_enabled_alpha else 'BẬT' }}</button></div>
                    <div class="input-group"><label>BETA+</label><input type="number" class="main-panel-input" data-field="heart_threshold_main" value="{{ main_panel.heart_threshold_main }}"><button class="btn main-panel-toggle" data-field="auto_grab_enabled_main">{{ 'TẮT' if main_panel.auto_grab_enabled_main else 'BẬT' }}</button></div>
                    <div class="input-group"><label>SUBS</label><input type="number" class="main-panel-input" data-field="heart_threshold_sub" value="{{ main_panel.heart_threshold_sub }}"><button class="btn main-panel-toggle" data-field="auto_grab_enabled_sub">{{ 'TẮT' if main_panel.auto_grab_enabled_sub else 'BẬT' }}</button></div>
                </div>
                 <div>
                    <h4>Broadcast Settings</h4>
                    <div class="input-group"><label>Message</label><textarea class="main-panel-input" data-field="spam_message" rows="2">{{ main_panel.spam_message }}</textarea></div>
                    <div class="input-group"><label>Delay</label><input type="number" class="main-panel-input" data-field="spam_delay" value="{{ main_panel.spam_delay }}"></div>
                </div>
                <div class="sync-section">
                    <h4><i class="fas fa-sync-alt"></i> Đồng Bộ Hóa</h4>
                    <button id="sync-all-btn" class="btn btn-success" style="width: 100%; font-size: 1.1em; padding: 12px;">ĐỒNG BỘ CÀI ĐẶT NÀY VỚI TẤT CẢ FARM</button>
                </div>
            </div>
        </div>

        <div class="panel">
            <h2><i class="fas fa-layer-group"></i> Group Management</h2>
            <div id="group-grid" class="main-grid">
                {% for group in farm_groups %}
                <div class="panel group-panel" data-group-id="{{ group.id }}">
                    <button class="delete-btn delete-group-btn"><i class="fas fa-times"></i></button>
                    <h3>{{ group.name }}</h3>
                    <div class="input-group">
                        <label>Spam</label>
                        <button class="btn group-spam-toggle {{ 'btn-danger' if group.spam_enabled else 'btn-success' }}">{{ 'TẮT' if group.spam_enabled else 'BẬT' }}</button>
                        <input type="number" class="group-input" data-field="spam_delay" value="{{ group.spam_delay or 10 }}" placeholder="Delay (s)">
                        <span class="timer-display group-spam-timer">--:--:--</span>
                    </div>
                    <h4>Select Bots for this Group:</h4>
                    <div class="bot-selector">
                        {% for bot in bot_statuses %}
                        <div class="bot-selector-item"><label><input type="checkbox" class="bot-checkbox" value="{{ bot.reboot_id }}" {% if bot.reboot_id in group.get('selected_bots', []) %}checked{% endif %}> {{ bot.name }}</label></div>
                        {% endfor %}
                    </div>
                </div>
                {% endfor %}
                <div id="add-group-btn" class="panel"><i class="fas fa-plus" style="font-size: 3rem;"></i><span>Add New Group</span></div>
            </div>
        </div>
        
        <div class="panel">
            <h2><i class="fas fa-network-wired"></i> Farm Management</h2>
            <div id="farm-grid" class="main-grid">
                {% for server in farm_servers %}
                <div class="panel farm-panel" data-farm-id="{{ server.id }}">
                    <button class="delete-btn delete-farm-btn"><i class="fas fa-times"></i></button>
                    <h3>{{ server.name }}</h3>
                    <div class="input-group">
                        <label>Group</label>
                        <select class="farm-group-select">
                            <option value="">-- No Group --</option>
                            {% for group in farm_groups %}<option value="{{ group.id }}" {{ 'selected' if server.group_id == group.id else '' }}>{{ group.name }}</option>{% endfor %}
                        </select>
                    </div>
                    <div class="input-group"><label>Main CH</label><input type="text" class="farm-input" data-field="main_channel_id" value="{{ server.main_channel_id or '' }}"></div>
                    <div class="input-group"><label>KTB CH</label><input type="text" class="farm-input" data-field="ktb_channel_id" value="{{ server.ktb_channel_id or '' }}"></div>
                    <div class="input-group"><label>Spam CH</label><input type="text" class="farm-input" data-field="spam_channel_id" value="{{ server.spam_channel_id or '' }}"></div>
                    <div class="input-group"><label>Spam Msg</label><textarea class="farm-input" data-field="spam_message" rows="1">{{ server.spam_message or '' }}</textarea></div>
                </div>
                {% endfor %}
                <div id="add-farm-btn" class="panel"><i class="fas fa-plus" style="font-size: 3rem;"></i><span>Add New Farm</span></div>
            </div>
        </div>
    </div>
<script>
document.addEventListener('DOMContentLoaded', function () {
    const showMsg = (msg) => { if (!msg) return; const el = document.getElementById('msg-status-container'); el.textContent = msg; el.style.display = 'block'; setTimeout(() => { el.style.display = 'none'; }, 4000); };
    const postData = async (url, data) => {
        try {
            const res = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
            const result = await res.json();
            showMsg(result.message);
            if (result.reload) setTimeout(() => location.reload(), 500);
            return result;
        } catch (err) { showMsg('Lỗi giao tiếp với server.'); }
    };

    // --- GLOBAL ---
    document.getElementById('auto-reboot-toggle-btn').addEventListener('click', () => postData('/api/reboot_toggle_auto', { delay: document.getElementById('auto-reboot-delay').value }).then(() => location.reload()));
    document.getElementById('event-grab-toggle-btn').addEventListener('click', () => postData('/api/event_grab_toggle', {}).then(() => location.reload()));
    document.getElementById('bot-status-list').addEventListener('click', e => { if (e.target.matches('.btn-toggle-state')) { postData('/api/toggle_bot_state', { target: e.target.dataset.target }); setTimeout(fetchStatus, 500); }});
    
    // --- MAIN PANEL & SYNC ---
    document.querySelector('.main-panel').addEventListener('change', e => { if(e.target.matches('.main-panel-input')) { const data = {}; data[e.target.dataset.field] = e.target.value; postData('/api/main_panel/update', data); } });
    document.querySelector('.main-panel').addEventListener('click', e => { if(e.target.matches('.main-panel-toggle')) { postData('/api/main_panel/update', { [e.target.dataset.field]: 'toggle' }).then(() => location.reload()); } });
    document.getElementById('sync-all-btn').addEventListener('click', () => { if (confirm('Đồng bộ cài đặt này với TẤT CẢ farm? Hành động này không thể hoàn tác.')) { postData('/api/sync_all_farms'); } });

    // --- GROUP MANAGEMENT ---
    document.getElementById('add-group-btn').addEventListener('click', () => { const name = prompt("Nhập tên nhóm mới:"); if (name) postData('/api/group/add', { name }); });
    document.getElementById('group-grid').addEventListener('click', e => {
        const groupEl = e.target.closest('.group-panel');
        if (!groupEl) return;
        const groupId = groupEl.dataset.groupId;
        if (e.target.matches('.delete-group-btn, .delete-group-btn *')) { if (confirm('Xóa nhóm này? Các farm trong nhóm sẽ bị mất nhóm.')) postData('/api/group/delete', { group_id: groupId }); }
        if (e.target.matches('.group-spam-toggle')) { postData('/api/group/spam_toggle', { group_id: groupId }).then(() => location.reload()); }
    });
    document.getElementById('group-grid').addEventListener('change', e => {
        const groupEl = e.target.closest('.group-panel');
        if (!groupEl) return;
        const groupId = groupEl.dataset.groupId;
        if (e.target.matches('.group-input')) { const data = { group_id: groupId }; data[e.target.dataset.field] = e.target.value; postData('/api/group/update', data); }
        if (e.target.matches('.bot-checkbox')) {
            const selected_bots = Array.from(groupEl.querySelectorAll('.bot-checkbox:checked')).map(cb => cb.value);
            postData('/api/group/update', { group_id: groupId, selected_bots });
        }
    });

    // --- FARM MANAGEMENT ---
    document.getElementById('add-farm-btn').addEventListener('click', () => { const name = prompt("Nhập tên farm mới:"); if (name) postData('/api/farm/add', { name }); });
    document.getElementById('farm-grid').addEventListener('click', e => {
        const farmEl = e.target.closest('.farm-panel');
        if (farmEl && e.target.matches('.delete-farm-btn, .delete-farm-btn *')) { if (confirm('Xóa farm này?')) postData('/api/farm/delete', { farm_id: farmEl.dataset.farmId }); }
    });
    document.getElementById('farm-grid').addEventListener('change', e => {
        const farmEl = e.target.closest('.farm-panel');
        if (!farmEl) return;
        const farmId = farmEl.dataset.farmId;
        const data = { farm_id: farmId };
        if (e.target.matches('.farm-input')) { data[e.target.dataset.field] = e.target.value; postData('/api/farm/update', data); }
        if (e.target.matches('.farm-group-select')) { data['group_id'] = e.target.value; postData('/api/farm/update', data).then(() => location.reload()); }
    });

    async function fetchStatus() {
        try {
            const res = await fetch('/status');
            const data = await res.json();
            document.getElementById('reboot-timer').textContent = formatTime(data.reboot_countdown);
            const botList = document.getElementById('bot-status-list');
            botList.innerHTML = '';
            data.bot_statuses.forEach(bot => {
                const item = document.createElement('div');
                item.className = 'bot-status-item';
                const btnClass = bot.is_active ? 'btn-rise' : 'btn-rest';
                const btnText = bot.is_active ? 'ONLINE' : 'OFFLINE';
                item.innerHTML = `<span>${bot.name}</span><button type="button" data-target="${bot.reboot_id}" class="btn-toggle-state ${btnClass}">${btnText}</button>`;
                botList.appendChild(item);
            });
            if (data.farm_groups) {
                data.farm_groups.forEach(g => {
                    const el = document.querySelector(`.group-panel[data-group-id="${g.id}"] .group-spam-timer`);
                    if (el) {
                        let countdown = g.spam_enabled ? (g.last_spam_time + g.spam_delay) - (Date.now() / 1000) : 0;
                        el.textContent = formatTime(countdown);
                    }
                });
            }
        } catch (err) { console.error('Error fetching status:', err); }
    }
    setInterval(fetchStatus, 2000);
    fetchStatus();
});
</script>
</body>
</html>
"""

# --- FLASK ROUTES ---
@app.route("/")
def index():
    reboot_action, reboot_button_class = ("DISABLE REBOOT", "btn-danger") if auto_reboot_enabled else ("ENABLE REBOOT", "btn-success")
    event_grab_action, event_grab_button_class = ("DISABLE EVENT GRAB", "btn-danger") if event_grab_enabled else ("ENABLE EVENT GRAB", "btn-success")
    
    bot_statuses = []
    with bots_lock:
        for i in range(len(main_bots)):
            name = GREEK_LETTERS[i] if i < len(GREEK_LETTERS) else f"Main {i}"
            bot_statuses.append({"name": name, "reboot_id": f"main_{i}", "is_active": bot_active_states.get(f'main_{i}', False)})
        for i in range(len(sub_bots)):
            name = sub_acc_names[i] if i < len(sub_acc_names) else f"Sub {i}"
            bot_statuses.append({"name": name, "reboot_id": f"sub_{i}", "is_active": bot_active_states.get(f'sub_{i}', False)})

    return render_template_string(HTML_TEMPLATE,
        auto_reboot_delay=auto_reboot_delay, reboot_action=reboot_action, reboot_button_class=reboot_button_class,
        event_grab_action=event_grab_action, event_grab_button_class=event_grab_button_class,
        farm_servers=farm_servers, farm_groups=farm_groups, main_panel=main_panel, bot_statuses=bot_statuses
    )

@app.route("/status")
def status():
    now = time.time()
    reboot_countdown = (last_reboot_cycle_time + auto_reboot_delay - now) if auto_reboot_enabled else 0
    bot_statuses = []
    with bots_lock:
        for i in range(len(main_bots)):
            name = GREEK_LETTERS[i] if i < len(GREEK_LETTERS) else f"Main {i}"
            bot_statuses.append({"name": name, "reboot_id": f"main_{i}", "is_active": bot_active_states.get(f'main_{i}', False)})
        for i in range(len(sub_bots)):
            name = sub_acc_names[i] if i < len(sub_acc_names) else f"Sub {i}"
            bot_statuses.append({"name": name, "reboot_id": f"sub_{i}", "is_active": bot_active_states.get(f'sub_{i}', False)})
    
    return jsonify({
        'reboot_enabled': auto_reboot_enabled, 'reboot_countdown': reboot_countdown,
        'bot_statuses': bot_statuses, 'farm_groups': farm_groups
    })

# --- GLOBAL CONTROL API ---
@app.route("/api/reboot_toggle_auto", methods=['POST'])
def api_reboot_toggle_auto():
    global auto_reboot_enabled, auto_reboot_thread, auto_reboot_stop_event
    auto_reboot_enabled = not auto_reboot_enabled
    auto_reboot_delay = int(request.json.get("delay", 3600))
    if auto_reboot_enabled and (auto_reboot_thread is None or not auto_reboot_thread.is_alive()):
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
    elif not auto_reboot_enabled and auto_reboot_stop_event: auto_reboot_stop_event.set(); auto_reboot_thread = None
    save_main_settings()
    return jsonify({'status': 'success', 'message': f'Auto Reboot đã {"BẬT" if auto_reboot_enabled else "TẮT"}.'})

@app.route("/api/event_grab_toggle", methods=['POST'])
def api_event_grab_toggle():
    global event_grab_enabled
    event_grab_enabled = not event_grab_enabled
    save_main_settings()
    return jsonify({'status': 'success', 'message': f"Event Grab đã {'BẬT' if event_grab_enabled else 'TẮT'}"})

@app.route("/api/toggle_bot_state", methods=['POST'])
def api_toggle_bot_state():
    target = request.json.get('target')
    if target in bot_active_states:
        bot_active_states[target] = not bot_active_states.get(target, False)
        state_text = "ONLINE" if bot_active_states[target] else "OFFLINE"
        save_main_settings()
        return jsonify({'status': 'success', 'message': f"Bot {target.upper()} đã được đặt thành {state_text}."})
    return jsonify({'status': 'error', 'message': 'Không tìm thấy bot.'})

# --- MAIN PANEL & SYNC API ---
@app.route("/api/main_panel/update", methods=['POST'])
def api_main_panel_update():
    data = request.json
    for key, value in data.items():
        if key in main_panel:
            if value == 'toggle': main_panel[key] = not main_panel[key]
            else: main_panel[key] = type(main_panel.get(key, ''))(value)
    save_farm_settings()
    return jsonify({'status': 'success', 'message': 'Đã cập nhật Master Panel.'})

@app.route("/api/sync_all_farms", methods=['POST'])
def api_sync_all_farms():
    count = 0
    for server in farm_servers:
        server['auto_grab_enabled_alpha'] = main_panel['auto_grab_enabled_alpha']
        server['heart_threshold_alpha'] = main_panel['heart_threshold_alpha']
        server['auto_grab_enabled_main'] = main_panel['auto_grab_enabled_main']
        server['heart_threshold_main'] = main_panel['heart_threshold_main']
        server['auto_grab_enabled_sub'] = main_panel['auto_grab_enabled_sub']
        server['heart_threshold_sub'] = main_panel['heart_threshold_sub']
        server['spam_message'] = main_panel['spam_message']
        server['spam_delay'] = main_panel['spam_delay']
        count += 1
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Đã đồng bộ cài đặt cho {count} farm.', 'reload': True})

# --- GROUP API ---
@app.route("/api/group/add", methods=['POST'])
def api_group_add():
    name = request.json.get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Tên nhóm là bắt buộc.'}), 400
    new_group = {
        "id": f"group_{int(time.time())}", "name": name, "spam_enabled": False,
        "spam_delay": 10, "last_spam_time": 0, "selected_bots": []
    }
    farm_groups.append(new_group)
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Nhóm "{name}" đã được thêm.', 'reload': True})

@app.route("/api/group/delete", methods=['POST'])
def api_group_delete():
    group_id = request.json.get('group_id')
    global farm_groups
    farm_groups = [g for g in farm_groups if g.get('id') != group_id]
    for server in farm_servers:
        if server.get('group_id') == group_id:
            server['group_id'] = None
    save_farm_settings()
    return jsonify({'status': 'success', 'message': 'Nhóm đã được xóa.', 'reload': True})

@app.route("/api/group/spam_toggle", methods=['POST'])
def api_group_spam_toggle():
    group_id = request.json.get('group_id')
    group = next((g for g in farm_groups if g.get('id') == group_id), None)
    if not group: return jsonify({'status': 'error', 'message': 'Không tìm thấy nhóm.'}), 404
    group['spam_enabled'] = not group.get('spam_enabled', False)
    if group['spam_enabled']: group['last_spam_time'] = time.time()
    save_farm_settings()
    state = "BẬT" if group['spam_enabled'] else "TẮT"
    return jsonify({'status': 'success', 'message': f"Spam nhóm đã {state}."})

@app.route("/api/group/update", methods=['POST'])
def api_group_update():
    data = request.json
    group_id = data.get('group_id')
    group = next((g for g in farm_groups if g.get('id') == group_id), None)
    if not group: return jsonify({'status': 'error', 'message': 'Không tìm thấy nhóm.'}), 404
    if 'spam_delay' in data: group['spam_delay'] = int(data['spam_delay'])
    if 'selected_bots' in data: group['selected_bots'] = data['selected_bots']
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Đã cập nhật nhóm {group["name"]}.'})

# --- FARM API ---
@app.route("/api/farm/add", methods=['POST'])
def api_farm_add():
    name = request.json.get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Tên farm là bắt buộc.'}), 400
    new_server = { "id": f"farm_{int(time.time())}", "name": name, "group_id": None, 
                   **main_panel } # Copy settings from main panel by default
    farm_servers.append(new_server)
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Farm "{name}" đã được thêm.', 'reload': True})

@app.route("/api/farm/delete", methods=['POST'])
def api_farm_delete():
    global farm_servers
    farm_id = request.json.get('farm_id')
    farm_servers = [s for s in farm_servers if s.get('id') != farm_id]
    save_farm_settings()
    return jsonify({'status': 'success', 'message': 'Farm đã được xóa.', 'reload': True})

@app.route("/api/farm/update", methods=['POST'])
def api_farm_update():
    data = request.json
    farm_id = data.get('farm_id')
    server = next((s for s in farm_servers if s.get('id') == farm_id), None)
    if not server: return jsonify({'status': 'error', 'message': 'Không tìm thấy farm.'}), 404
    
    allowed_fields = ['main_channel_id', 'ktb_channel_id', 'spam_channel_id', 'spam_message', 'group_id']
    for key in allowed_fields:
        if key in data: server[key] = data[key]
        
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Đã cập nhật farm {server["name"]}.'})

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    load_farm_settings()
    load_main_settings()
    print("Đang khởi tạo các bot...", flush=True)
    with bots_lock:
        # Main bots
        all_main_tokens = ([main_token_alpha] if main_token_alpha else []) + other_main_tokens
        for i, token in enumerate(all_main_tokens):
            if token.strip():
                bot_id = f'main_{i}'
                main_bots.append(create_bot(token.strip(), 'main', i))
                if bot_id not in bot_active_states: bot_active_states[bot_id] = True

        # Sub Bots
        for i, token in enumerate(sub_tokens):
            if token.strip():
                bot_id = f'sub_{i}'
                sub_bots.append(create_bot(token.strip(), 'sub', i))
                if bot_id not in bot_active_states: bot_active_states[bot_id] = True

    print("Đang khởi tạo các luồng nền...", flush=True)
    threading.Thread(target=spam_loop, daemon=True).start()
    threading.Thread(target=periodic_save_loop, daemon=True).start()

    if auto_reboot_enabled and (auto_reboot_thread is None or not auto_reboot_thread.is_alive()):
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
    
    port = int(os.environ.get("PORT", 10001))
    print(f"Khởi động Farm Control Panel tại http://0.0.0.0:{port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
