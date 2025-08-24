# PHIÊN BẢN ĐIỀU KHIỂN FARM - FIXED VERSION
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
from concurrent.futures import ThreadPoolExecutor
import queue

load_dotenv()

# --- CẤU HÌNH ---
main_token_alpha = os.getenv("MAIN_TOKEN")
other_main_tokens = os.getenv("MAIN_TOKENS").split(",") if os.getenv("MAIN_TOKENS") else []
sub_tokens = os.getenv("TOKENS").split(",") if os.getenv("TOKENS") else []
acc_names_str = os.getenv("SUB_ACC_NAMES")
sub_acc_names = [name.strip() for name in acc_names_str.split(',')] if acc_names_str else []
GREEK_ALPHABET = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta', 'Iota', 'Kappa', 'Lambda', 'Mu']

karuta_id = "646937666251915264"
yoru_bot_id = "1311684840462225440"

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

# Dữ liệu mới
farm_servers = []
groups = {}
main_panel_settings = {
    "auto_grab_enabled_alpha": False, "heart_threshold_alpha": 15,
    "auto_grab_enabled_main_other": False, "heart_threshold_main_other": 50,
    "spam_message": "kcf", "spam_delay": 10
}

# Biến chia sẻ thông tin tim giữa các bot
heart_sharing_data = {}
heart_data_lock = threading.Lock()

# SPAM OPTIMIZATION
spam_executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="SpamWorker")
spam_queue = queue.Queue()
active_spam_tasks = set()

# --- DEBUG LOGGING ---
def debug_log(message, level="INFO"):
    timestamp = time.strftime("%H:%M:%S")
    print(f"[{timestamp}] [{level}] {message}", flush=True)

# --- HÀM LƯU VÀ TẢI CÀI ĐẶT ---
def save_farm_settings():
    api_key = os.getenv("JSONBIN_API_KEY")
    farm_bin_id = os.getenv("FARM_JSONBIN_BIN_ID")
    if not api_key or not farm_bin_id: 
        debug_log("Thiếu API key hoặc bin ID cho farm settings", "WARNING")
        return
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{farm_bin_id}"
    try:
        req = requests.put(url, json=farm_servers, headers=headers, timeout=10)
        if req.status_code == 200: 
            debug_log("Đã lưu cài đặt farm panels")
    except Exception as e: 
        debug_log(f"Lỗi khi lưu farm panels: {e}", "ERROR")

def load_farm_settings():
    global farm_servers
    api_key = os.getenv("JSONBIN_API_KEY")
    farm_bin_id = os.getenv("FARM_JSONBIN_BIN_ID")
    if not api_key or not farm_bin_id: return
    headers = {'X-Master-Key': api_key, 'X-Bin-Meta': 'false'}
    url = f"https://api.jsonbin.io/v3/b/{farm_bin_id}/latest"
    try:
        req = requests.get(url, headers=headers, timeout=10)
        if req.status_code == 200:
            data = req.json()
            if isinstance(data, list): 
                farm_servers = data
                debug_log(f"Đã tải {len(farm_servers)} cấu hình farm")
    except Exception: 
        farm_servers = []
        debug_log("Khởi tạo farm_servers rỗng", "WARNING")
        
def save_main_settings():
    api_key = os.getenv("JSONBIN_API_KEY")
    bin_id = os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    settings = {
        'event_grab_enabled': event_grab_enabled, 'auto_reboot_enabled': auto_reboot_enabled, 
        'auto_reboot_delay': auto_reboot_delay, 'bot_active_states': bot_active_states,
        'last_reboot_cycle_time': last_reboot_cycle_time,
        'groups': groups,
        'main_panel_settings': main_panel_settings
    }
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    try:
        req = requests.put(url, json=settings, headers=headers, timeout=10)
        if req.status_code == 200: 
            debug_log("Đã lưu cài đặt chính")
    except Exception as e: 
        debug_log(f"Lỗi khi lưu cài đặt chính: {e}", "ERROR")

def load_main_settings():
    global event_grab_enabled, auto_reboot_enabled, auto_reboot_delay, bot_active_states, groups, main_panel_settings
    api_key = os.getenv("JSONBIN_API_KEY")
    bin_id = os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    headers = {'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
    try:
        req = requests.get(url, headers=headers, timeout=10)
        if req.status_code == 200:
            settings = req.json().get("record", {})
            if settings:
                # Load từng biến một cách rõ ràng
                event_grab_enabled = settings.get('event_grab_enabled', False)
                auto_reboot_enabled = settings.get('auto_reboot_enabled', False)
                auto_reboot_delay = settings.get('auto_reboot_delay', 3600)
                bot_active_states = settings.get('bot_active_states', {})
                groups = settings.get('groups', {})
                main_panel_settings = settings.get('main_panel_settings', {
                    "auto_grab_enabled_alpha": False, "heart_threshold_alpha": 15,
                    "auto_grab_enabled_main_other": False, "heart_threshold_main_other": 50,
                    "spam_message": "kcf", "spam_delay": 10
                })
                debug_log("Đã tải cài đặt chính")
            else: 
                save_main_settings()
    except Exception as e: 
        debug_log(f"Lỗi khi tải cài đặt chính: {e}", "ERROR")

# --- LOGIC BOT FARM FIXED ---
def get_grab_settings(target_server, bot_type, bot_index):
    """Lấy cài đặt grab cho bot - FIXED"""
    if bot_type == 'main' and bot_index == 0:  # Alpha Bot
        return (target_server.get('auto_grab_enabled_alpha', False), 
                target_server.get('heart_threshold_alpha', 15), 
                {0: 0.2, 1: 1.2, 2: 2.0})
    elif bot_type == 'main':  # Other Main Bots - FIXED: Loại bỏ điều kiện bot_index > 0
        return (target_server.get('auto_grab_enabled_main_other', False), 
                target_server.get('heart_threshold_main_other', 50), 
                {0: 1.0, 1: 2.0, 2: 2.8})
    else:  # Sub Bots
        return False, 0, {}

def read_yoru_and_share_hearts(bot, channel_id, drop_msg_id, target_server):
    """Alpha bot đọc Yoru và chia sẻ thông tin tim"""
    try:
        time.sleep(0.6)
        messages = bot.getMessages(channel_id, num=5).json()
        debug_log(f"Alpha đọc messages từ channel {channel_id}")
        
        for msg_item in messages:
            if msg_item.get("author", {}).get("id") == yoru_bot_id and msg_item.get("embeds"):
                desc = msg_item["embeds"][0].get("description", "")
                heart_numbers = []
                for line in desc.split('\n')[:3]:
                    match = re.search(r'♡(\d+)', line)
                    heart_numbers.append(int(match.group(1)) if match else 0)
                
                if not any(heart_numbers): 
                    break
                
                # Chia sẻ thông tin tim
                with heart_data_lock:
                    heart_sharing_data[channel_id] = {
                        'hearts': heart_numbers,
                        'timestamp': time.time(),
                        'drop_msg_id': drop_msg_id,
                        'target_server': target_server
                    }
                
                debug_log(f"[ALPHA SHARED HEARTS: {target_server['name']}] Tim: {heart_numbers}")
                break
    except Exception as e: 
        debug_log(f"Lỗi đọc Yoru Bot: {e}", "ERROR")

def process_shared_hearts(bot, bot_type, bot_index):
    """FIXED: Tất cả main bots xử lý thông tin tim được chia sẻ"""
    with heart_data_lock:
        for channel_id, data in list(heart_sharing_data.items()):
            if time.time() - data['timestamp'] > 10:
                del heart_sharing_data[channel_id]
                continue
                
            target_server = data['target_server']
            heart_numbers = data['hearts']
            drop_msg_id = data['drop_msg_id']
            
            is_card_grab_enabled, heart_threshold, delays = get_grab_settings(target_server, bot_type, bot_index)
            ktb_channel_id = target_server.get('ktb_channel_id')

            debug_log(f"Bot {bot_type} {bot_index}: grab_enabled={is_card_grab_enabled}, threshold={heart_threshold}")

            if is_card_grab_enabled and ktb_channel_id and any(heart_numbers):
                max_num = max(heart_numbers)
                if max_num >= heart_threshold:
                    max_index = heart_numbers.index(max_num)
                    emoji = ["1️⃣", "2️⃣", "3️⃣"][max_index]
                    delay = delays.get(max_index, 1.5)
                    
                    debug_log(f"[GRAB: {target_server['name']} | Bot {bot_type.capitalize()} {bot_index}] {max_num} tim -> delay {delay}s")
                    
                    def grab_action():
                        try:
                            bot.addReaction(channel_id, drop_msg_id, emoji)
                            time.sleep(2)
                            bot.sendMessage(ktb_channel_id, "kt b")
                            debug_log(f"Bot {bot_type} {bot_index} đã grab thành công")
                        except Exception as e:
                            debug_log(f"Lỗi khi grab (Bot {bot_type} {bot_index}): {e}", "ERROR")
                    
                    threading.Timer(delay, grab_action).start()

def handle_farm_grab(bot, msg, bot_type, bot_index):
    """FIXED: Xử lý grab cho tất cả main bots"""
    channel_id = msg.get("channel_id")
    target_server = next((s for s in farm_servers if s.get('main_channel_id') == channel_id), None)
    if not target_server: 
        return

    if msg.get("author", {}).get("id") == karuta_id and 'dropping 3' in msg.get("content", ""):
        last_drop_msg_id = msg["id"]
        debug_log(f"Phát hiện drop tại {target_server['name']} - Bot {bot_type} {bot_index}")

        # Alpha Bot đọc và chia sẻ
        if bot_type == 'main' and bot_index == 0:
            threading.Thread(target=read_yoru_and_share_hearts, 
                           args=(bot, channel_id, last_drop_msg_id, target_server)).start()

        # TẤT CẢ main bots (bao gồm Alpha) xử lý grab
        if bot_type == 'main':
            def delayed_process():
                time.sleep(1.2 if bot_index == 0 else 1.5)  # Alpha delay ít hơn
                process_shared_hearts(bot, bot_type, bot_index)
            threading.Thread(target=delayed_process).start()

        # Event grab chỉ cho Alpha
        if event_grab_enabled and bot_type == 'main' and bot_index == 0:
            def check_farm_event():
                try:
                    time.sleep(5)
                    full_msg_obj = bot.getMessage(channel_id, last_drop_msg_id).json()[0]
                    if 'reactions' in full_msg_obj and any(r['emoji']['name'] == '🉐' for r in full_msg_obj['reactions']):
                        debug_log(f"[EVENT GRAB | FARM: {target_server['name']}] Phát hiện dưa hấu!")
                        bot.addReaction(channel_id, last_drop_msg_id, "🉐")
                except Exception as e: 
                    debug_log(f"Lỗi kiểm tra event: {e}", "ERROR")
            threading.Thread(target=check_farm_event).start()

def create_bot(token, bot_type, bot_index):
    bot = discum.Client(token=token, log=False)
    @bot.gateway.command
    def on_ready(resp):
        if resp.event.ready:
            user = resp.raw.get('user', {})
            debug_log(f"Bot '{bot_type.capitalize()} {bot_index}' đã đăng nhập: {user.get('username')}")

    @bot.gateway.command
    def on_message(resp):
        if not (resp.event.message or (resp.raw and resp.raw.get('t') == 'MESSAGE_UPDATE')): 
            return
        msg = resp.parsed.auto()
        handle_farm_grab(bot, msg, bot_type, bot_index)

    threading.Thread(target=bot.gateway.run, daemon=True).start()
    return bot

# --- SPAM LOOP FIXED ---
def execute_spam_task(task_data):
    """Thực thi spam task"""
    try:
        task_id, channel_id, message, bots_to_use, inter_bot_delay = task_data
        debug_log(f"[SPAM EXEC] Bắt đầu task {task_id} với {len(bots_to_use)} bots")
        
        for i, bot in enumerate(bots_to_use):
            try: 
                bot.sendMessage(channel_id, message)
                debug_log(f"[SPAM] Bot {i} đã gửi: '{message}' vào channel {channel_id}")
                if i < len(bots_to_use) - 1:  # Không delay ở bot cuối
                    time.sleep(inter_bot_delay)
            except Exception as e:
                debug_log(f"[SPAM ERROR] Bot {i} spam failed: {e}", "ERROR")
    except Exception as e:
        debug_log(f"[SPAM ERROR] Task execution failed: {e}", "ERROR")
    finally:
        if task_id in active_spam_tasks:
            active_spam_tasks.remove(task_id)

def optimized_spam_loop():
    """FIXED: Loop spam với debug chi tiết"""
    debug_log("Khởi động Optimized Spam Loop với ThreadPool")
    
    while True:
        try:
            now = time.time()
            debug_log(f"Spam loop check - Groups: {len(groups)}, Farms: {len(farm_servers)}")
            
            # Lặp qua các group để điều phối spam
            for group_name, group_data in groups.items():
                spam_enabled = group_data.get('spam_enabled', False)
                debug_log(f"Group '{group_name}': spam_enabled={spam_enabled}")
                
                if not spam_enabled: 
                    continue
                
                # Tìm các farm thuộc group này
                farms_in_group = [s for s in farm_servers if s.get('group') == group_name]
                debug_log(f"Group '{group_name}' có {len(farms_in_group)} farms")
                
                if not farms_in_group: 
                    continue

                # Chọn các bot được chỉ định cho group này
                with bots_lock:
                    account_indices = group_data.get('spam_accounts', [])
                    bots_to_use = []
                    for i in account_indices:
                        if i < len(sub_bots) and bot_active_states.get(f'sub_{i}', False):
                            bots_to_use.append(sub_bots[i])
                
                debug_log(f"Group '{group_name}': {len(bots_to_use)} bots available for spam")
                
                if not bots_to_use: 
                    continue

                # Xử lý spam cho các farm
                for server in farms_in_group:
                    server_id = server.get('id', 'unknown_farm')
                    spam_channel_id = server.get('spam_channel_id')
                    spam_message = server.get('spam_message', group_data.get('spam_message', main_panel_settings.get('spam_message', 'kcf')))
                    spam_delay = server.get('spam_delay', group_data.get('spam_delay', main_panel_settings.get('spam_delay', 10)))
                    
                    if not spam_channel_id or not spam_message:
                        continue
                        
                    last_spam = server.get('last_spam_time', 0)
                    task_id = f"spam_{server_id}_{group_name}"
                    
                    # Kiểm tra điều kiện spam
                    time_since_last = now - last_spam
                    debug_log(f"Farm '{server['name']}': last_spam={time_since_last:.1f}s ago, delay={spam_delay}s, task_active={task_id in active_spam_tasks}")
                    
                    if time_since_last >= spam_delay and task_id not in active_spam_tasks:
                        active_spam_tasks.add(task_id)
                        server['last_spam_time'] = now
                        
                        task_data = (
                            task_id,
                            spam_channel_id, 
                            spam_message,
                            bots_to_use.copy(),
                            2  # inter_bot_delay
                        )
                        
                        spam_executor.submit(execute_spam_task, task_data)
                        debug_log(f"[SPAM SUBMITTED] Task cho farm '{server['name']}'")
                        
            time.sleep(5)  # Check mỗi 5 giây
            
        except Exception as e: 
            debug_log(f"[ERROR in optimized_spam_loop] {e}", "ERROR")
            time.sleep(5)

def reboot_bot(target_id):
    with bots_lock:
        bot_type, index_str = target_id.split('_')
        index = int(index_str)
        if bot_type == 'main':
            if index < len(main_bots):
                try: 
                    main_bots[index].gateway.close()
                except: 
                    pass
                token = main_token_alpha if index == 0 else other_main_tokens[index - 1]
                main_bots[index] = create_bot(token, 'main', index)
                debug_log(f"Main Bot {index} đã khởi động lại")
        elif bot_type == 'sub':
            if index < len(sub_bots):
                try: 
                    sub_bots[index].gateway.close()
                except: 
                    pass
                sub_bots[index] = create_bot(sub_tokens[index], 'sub', index)
                debug_log(f"Sub Bot {index} đã khởi động lại")

def auto_reboot_loop():
    global last_reboot_cycle_time
    while not auto_reboot_stop_event.is_set():
        try:
            if auto_reboot_enabled and (time.time() - last_reboot_cycle_time) >= auto_reboot_delay:
                debug_log("Bắt đầu chu kỳ reboot tự động...")
                with bots_lock:
                    for i in range(len(main_bots)):
                        if bot_active_states.get(f'main_{i}', False): 
                            reboot_bot(f'main_{i}')
                            time.sleep(5)
                    for i in range(len(sub_bots)):
                         if bot_active_states.get(f'sub_{i}', False): 
                             reboot_bot(f'sub_{i}')
                             time.sleep(5)
                last_reboot_cycle_time = time.time()
            if auto_reboot_stop_event.wait(timeout=60): 
                break
        except Exception as e: 
            debug_log(f"[ERROR in auto_reboot_loop] {e}", "ERROR")
            time.sleep(60)
    debug_log("Luồng tự động reboot đã dừng")

def periodic_save_loop():
    while True:
        time.sleep(300)
        debug_log("Bắt đầu lưu định kỳ...")
        save_farm_settings()
        save_main_settings()

app = Flask(__name__)

# --- GIAO DIỆN WEB FIXED ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Karuta Farm Control - Fixed Debug Version</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Courier+Prime:wght@400;700&family=Nosifer&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-bg: #0a0a0a; --secondary-bg: #1a1a1a; --panel-bg: #111111; --border-color: #333333;
            --blood-red: #8b0000; --necro-green: #228b22; --shadow-cyan: #008b8b; --text-primary: #f0f0f0;
            --text-secondary: #cccccc; --hot-pink: #FF69B4; --gold: #FFD700; --main-blue: #00BFFF;
        }
        body { font-family: 'Courier Prime', monospace; background: var(--primary-bg); color: var(--text-primary); margin: 0; padding: 20px; }
        .container { max-width: 1800px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; }
        .title { font-family: 'Nosifer', cursive; font-size: 2.5rem; color: var(--hot-pink); text-shadow: 0 0 15px var(--hot-pink);}
        .panel { background: #111; border: 1px solid var(--border-color); border-radius: 10px; padding: 20px; margin-bottom: 20px; position: relative; }
        .panel h2, .panel h3 { font-family: 'Orbitron', monospace; color: var(--text-secondary); border-bottom: 1px solid var(--border-color); padding-bottom: 10px; margin-top: 0;}
        .main-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(400px, 1fr)); gap: 20px; }
        .btn { background: var(--secondary-bg); border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px 12px; border-radius: 4px; cursor: pointer; font-family: 'Orbitron'; }
        .btn:hover { filter: brightness(1.2); }
        .btn-danger { border-color: var(--blood-red); color: var(--blood-red); } .btn-danger:hover { background: var(--blood-red); color: var(--primary-bg); }
        .btn-success { border-color: var(--necro-green); color: var(--necro-green); } .btn-success:hover { background: var(--necro-green); color: var(--primary-bg); }
        .btn-primary { border-color: var(--main-blue); color: var(--main-blue); } .btn-primary:hover { background: var(--main-blue); color: var(--primary-bg); }
        .input-group { display: flex; align-items: stretch; gap: 5px; margin-bottom: 10px; }
        .input-group label { padding: 8px; background: #222; border: 1px solid var(--border-color); border-radius: 5px 0 0 5px; white-space: nowrap; }
        .input-group input, .input-group textarea, .input-group select { width: 100%; background: #000; border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px; border-radius: 0 5px 5px 0; }
        .bot-status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
        .bot-status-item { display: flex; justify-content: space-between; align-items: center; padding: 5px 8px; background: #222; border-radius: 4px; }
        .btn-toggle-state { cursor: pointer; background: transparent; border: none; font-weight: 700; }
        .btn-rise { color: var(--necro-green); } .btn-rest { color: var(--blood-red); }
        .group-container { border: 1px solid var(--hot-pink); padding: 15px; border-radius: 8px; margin-bottom: 20px; }
        .group-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 15px; }
        .group-header h3 { border: none; padding: 0; margin: 0; color: var(--hot-pink); }
        .farm-in-group { background: #1a1a1a; padding: 10px; border-radius: 5px; margin-bottom: 5px; display: flex; justify-content: space-between; align-items: center; }
        .spam-account-list { display: grid; grid-template-columns: repeat(auto-fill, minmax(150px, 1fr)); gap: 5px; margin-top: 10px; }
        .spam-account-item { background: #222; padding: 5px; border-radius: 4px; }
        .msg-status { text-align: center; color: var(--shadow-cyan); padding: 12px; border: 1px dashed var(--border-color); margin-bottom: 20px; background: rgba(0, 139, 139, 0.1); display: none; }
        .main-panel { border: 2px solid var(--main-blue); box-shadow: 0 0 15px var(--main-blue); }
        .delete-btn { background: var(--blood-red); color: white; border: none; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
        .optimization-notice { background: rgba(34, 139, 34, 0.1); border: 1px solid var(--necro-green); padding: 10px; margin-bottom: 20px; border-radius: 5px; }
        .optimization-notice i { color: var(--necro-green); margin-right: 10px; }
        /* NEW: Debug info styles */
        .debug-info { background: rgba(255, 165, 0, 0.1); border: 1px solid orange; padding: 10px; border-radius: 5px; margin-bottom: 10px; font-size: 0.9em; }
        .spam-status { background: #222; padding: 10px; margin: 10px 0; border-radius: 5px; border-left: 4px solid var(--hot-pink); }
        .spam-status-enabled { border-left-color: var(--necro-green); }
        .spam-status-disabled { border-left-color: var(--blood-red); }
    </style>
</head>
<body>
    <div class="container">
        <div class="header"><h1 class="title">FARM CONTROL PANEL - FIXED DEBUG</h1></div>
        
        <div class="optimization-notice">
            <i class="fas fa-bug"></i><strong>DEBUG VERSION:</strong> 
            Đã sửa lỗi auto grab cho tất cả main bots, spam loop với debug chi tiết, và hiển thị trạng thái spam rõ ràng.
        </div>
        
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
            <h2><i class="fas fa-crown"></i> Main Control Panel (Template)</h2>
            <div class="main-grid">
                <div>
                    <h4><i class="fas fa-crosshairs"></i> Harvest Settings</h4>
                    <div class="input-group"><label>ALPHA</label><input type="number" class="main-panel-input" data-field="heart_threshold_alpha" value="{{ main_panel.heart_threshold_alpha }}"><button class="btn main-panel-toggle" data-field="auto_grab_enabled_alpha">{{ 'TẮT' if main_panel.auto_grab_enabled_alpha else 'BẬT' }}</button></div>
                    <div class="input-group"><label>BETA+</label><input type="number" class="main-panel-input" data-field="heart_threshold_main_other" value="{{ main_panel.heart_threshold_main_other }}"><button class="btn main-panel-toggle" data-field="auto_grab_enabled_main_other">{{ 'TẮT' if main_panel.auto_grab_enabled_main_other else 'BẬT' }}</button></div>
                    <div style="opacity: 0.5;"><small><i class="fas fa-info-circle"></i> Sub Accounts không có auto grab (chỉ spam)</small></div>
                </div>
                 <div>
                    <h4><i class="fas fa-broadcast-tower"></i> Broadcast Settings</h4>
                    <div class="input-group"><label>Message</label><textarea class="main-panel-input" data-field="spam_message" rows="2">{{ main_panel.spam_message }}</textarea></div>
                    <div class="input-group"><label>Delay</label><input type="number" class="main-panel-input" data-field="spam_delay" value="{{ main_panel.spam_delay }}"></div>
                </div>
                <div>
                    <h4><i class="fas fa-sync-alt"></i> Đồng Bộ Hóa</h4>
                    <div class="input-group"><label>Target Groups</label><select id="sync-target-groups" multiple style="height: 100px;">{% for name in groups %}<option value="{{ name }}">{{ name }}</option>{% endfor %}</select></div>
                    <button id="sync-from-main-btn" class="btn btn-primary" style="width:100%;">Đồng Bộ Cài Đặt</button>
                </div>
            </div>
        </div>

        <div class="panel">
            <h2><i class="fas fa-layer-group"></i> Group & Farm Management</h2>
            <div class="input-group" style="width: 50%;">
                <input type="text" id="new-group-name" placeholder="Tên group mới..."><button id="add-group-btn" class="btn btn-success">Thêm Group</button>
            </div>
            <hr style="border-color: var(--border-color);">
            <div id="groups-container">
                {% for name, data in groups.items() %}
                <div class="group-container" data-group-name="{{ name }}">
                    <div class="group-header">
                        <h3>{{ name }}</h3>
                        <div>
                            <button class="btn group-spam-toggle {{ 'btn-danger' if data.spam_enabled else 'btn-success' }}">{{ 'TẮT SPAM' if data.spam_enabled else 'BẬT SPAM' }}</button>
                            <button class="btn btn-danger delete-group-btn">Xóa Group</button>
                        </div>
                    </div>
                    
                    <!-- NEW: Spam Status Display -->
                    <div class="spam-status {{ 'spam-status-enabled' if data.spam_enabled else 'spam-status-disabled' }}">
                        <strong>Trạng thái SPAM:</strong> 
                        <span style="color: {{ '#22ff22' if data.spam_enabled else '#ff4444' }};">
                            {{ 'ĐANG HOẠT ĐỘNG' if data.spam_enabled else 'TẮT' }}
                        </span>
                        <br>
                        <strong>Tin nhắn:</strong> "{{ data.get('spam_message', main_panel.spam_message) }}"
                        <br>
                        <strong>Delay:</strong> {{ data.get('spam_delay', main_panel.spam_delay) }}s
                        <br>
                        <strong>Số bots:</strong> {{ data.get('spam_accounts', [])|length }} accounts được chọn
                    </div>
                    
                    <h4><i class="fas fa-robot"></i> Spam Accounts for this Group</h4>
                    <div class="spam-account-list">
                        {% for i, sub_name in sub_acc_names %}
                        <div class="spam-account-item"><label><input type="checkbox" class="spam-account-checkbox" value="{{ i }}" {% if i in data.get('spam_accounts', []) %}checked{% endif %}> {{ sub_name }}</label></div>
                        {% endfor %}
                    </div>
                    <h4 style="margin-top: 20px;"><i class="fas fa-network-wired"></i> Farms in this Group</h4>
                    <div class="farms-list">
                        {% for server in farm_servers %}{% if server.group == name %}
                        <div class="farm-in-group">
                            <span>{{ server.name }}</span>
                            <select class="farm-group-selector" data-farm-id="{{ server.id }}">
                                {% for g_name in groups %}<option value="{{ g_name }}" {% if g_name == name %}selected{% endif %}>{{ g_name }}</option>{% endfor %}
                            </select>
                        </div>
                        {% endif %}{% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        
        <div class="panel">
            <h2><i class="fas fa-plus-circle"></i> Add & Manage Farm Panels</h2>
            <div class="debug-info">
                <i class="fas fa-info-circle"></i> <strong>DEBUG INFO:</strong> 
                Farms: {{ farm_servers|length }}, Groups: {{ groups|length }}
                <br>Main Panel Settings: Alpha Grab={{ main_panel.auto_grab_enabled_alpha }}, Main Other Grab={{ main_panel.auto_grab_enabled_main_other }}
            </div>
            <div id="farm-grid" class="main-grid">
                {% for server in farm_servers %}
                <div class="panel" style="border-left: 5px solid var(--hot-pink);">
                    <button class="delete-btn delete-farm-btn" data-farm-id="{{ server.id }}" style="position:absolute; top:10px; right: 10px;">XÓA</button>
                    <h3>{{ server.name }} (Group: {{ server.group or 'None' }})</h3>
                    
                    <!-- NEW: Farm-level settings display -->
                    <div class="debug-info" style="margin: 10px 0;">
                        <small>
                            Alpha Grab: {{ server.get('auto_grab_enabled_alpha', 'Not Set') }} ({{ server.get('heart_threshold_alpha', 'No threshold') }} hearts)
                            <br>Main Other Grab: {{ server.get('auto_grab_enabled_main_other', 'Not Set') }} ({{ server.get('heart_threshold_main_other', 'No threshold') }} hearts)
                            <br>Spam: "{{ server.get('spam_message', 'No message') }}" every {{ server.get('spam_delay', 'No delay') }}s
                        </small>
                    </div>
                    
                    <div class="input-group"><label>Main CH</label><input type="text" class="farm-channel-input" data-farm-id="{{ server.id }}" data-field="main_channel_id" value="{{ server.main_channel_id or '' }}"></div>
                    <div class="input-group"><label>KTB CH</label><input type="text" class="farm-channel-input" data-farm-id="{{ server.id }}" data-field="ktb_channel_id" value="{{ server.ktb_channel_id or '' }}"></div>
                    <div class="input-group"><label>Spam CH</label><input type="text" class="farm-channel-input" data-farm-id="{{ server.id }}" data-field="spam_channel_id" value="{{ server.spam_channel_id or '' }}"></div>
                </div>
                {% endfor %}
                <div id="add-farm-btn" style="display:flex; align-items:center; justify-content:center; min-height:150px; border-style:dashed; cursor:pointer;"><i class="fas fa-plus" style="font-size: 3rem;"></i></div>
            </div>
        </div>
    </div>
<script>
document.addEventListener('DOMContentLoaded', function () {
    // Helper functions
    const msgContainer = document.getElementById('msg-status-container');
    const showMsg = (msg) => { if (!msg) return; msgContainer.textContent = msg; msgContainer.style.display = 'block'; setTimeout(() => { msgContainer.style.display = 'none'; }, 4000); };
    const postData = async (url, data) => {
        try {
            const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
            const result = await response.json();
            showMsg(result.message);
            if (result.reload) setTimeout(() => location.reload(), 500);
            return result;
        } catch (error) { showMsg('Lỗi giao tiếp với server.'); }
    };

    // --- Event Listeners ---
    // Global controls
    document.getElementById('auto-reboot-toggle-btn').addEventListener('click', () => postData('/api/reboot_toggle_auto', { delay: document.getElementById('auto-reboot-delay').value }).then(r => r && r.reload ? null : location.reload()));
    document.getElementById('auto-reboot-delay').addEventListener('change', () => postData('/api/reboot_update_delay', { delay: document.getElementById('auto-reboot-delay').value }));
    document.getElementById('event-grab-toggle-btn').addEventListener('click', () => postData('/api/event_grab_toggle', {}).then(r => r && r.reload ? null : location.reload()));
    document.getElementById('bot-status-list').addEventListener('click', e => { if (e.target.matches('.btn-toggle-state')) { postData('/api/toggle_bot_state', { target: e.target.dataset.target }); setTimeout(fetchStatus, 500); }});

    // Main panel & Sync
    const mainPanel = document.querySelector('.main-panel');
    mainPanel.addEventListener('change', e => { if (e.target.matches('.main-panel-input')) { const data = {}; data[e.target.dataset.field] = e.target.value; postData('/api/main_panel/update', data); } });
    mainPanel.addEventListener('click', e => { if (e.target.matches('.main-panel-toggle')) { const data = {}; data[e.target.dataset.field] = 'toggle'; postData('/api/main_panel/update', data).then(() => location.reload()); } });
    document.getElementById('sync-from-main-btn').addEventListener('click', () => {
        const targets = Array.from(document.getElementById('sync-target-groups').selectedOptions).map(opt => opt.value);
        if (targets.length === 0) { showMsg('Vui lòng chọn ít nhất một group để đồng bộ.'); return; }
        postData('/api/main_panel/sync', { target_groups: targets });
    });

    // Group Management
    document.getElementById('add-group-btn').addEventListener('click', () => { const name = document.getElementById('new-group-name').value; if (name) postData('/api/groups/add', { name }); });
    const groupsContainer = document.getElementById('groups-container');
    groupsContainer.addEventListener('click', e => {
        const groupDiv = e.target.closest('.group-container');
        if (!groupDiv) return;
        const groupName = groupDiv.dataset.groupName;
        if (e.target.matches('.delete-group-btn')) { if (confirm(`Xóa group "${groupName}"? Các farm trong group sẽ bị mất group.`)) postData('/api/groups/delete', { name: groupName }); }
        if (e.target.matches('.group-spam-toggle')) { postData('/api/groups/update', { name: groupName, spam_enabled: 'toggle' }).then(() => location.reload()); }
    });
    groupsContainer.addEventListener('change', e => {
        const groupDiv = e.target.closest('.group-container');
        if (!groupDiv) return;
        const groupName = groupDiv.dataset.groupName;
        if (e.target.matches('.spam-account-checkbox')) {
            const checked = Array.from(groupDiv.querySelectorAll('.spam-account-checkbox:checked')).map(cb => parseInt(cb.value));
            postData('/api/groups/update', { name: groupName, spam_accounts: checked });
        }
        if (e.target.matches('.farm-group-selector')) { postData('/api/farm/update', { farm_id: e.target.dataset.farmId, group: e.target.value }); }
    });

    // Farm Management
    document.getElementById('add-farm-btn').addEventListener('click', () => { const name = prompt("Nhập tên farm mới:"); if (name) postData('/api/farm/add', { name }); });
    const farmGrid = document.getElementById('farm-grid');
    farmGrid.addEventListener('click', e => { if (e.target.matches('.delete-farm-btn')) { if (confirm('Xóa farm này?')) postData('/api/farm/delete', { farm_id: e.target.dataset.farmId }); } });
    farmGrid.addEventListener('change', e => { if (e.target.matches('.farm-channel-input')) { const data = { farm_id: e.target.dataset.farmId }; data[e.target.dataset.field] = e.target.value; postData('/api/farm/update', data); } });

    // Initial Status Fetch
    async function fetchStatus() { 
        try {
            const response = await fetch('/status');
            const data = await response.json();
            const statusList = document.getElementById('bot-status-list');
            if (statusList && data.bot_statuses) {
                statusList.innerHTML = '<div class="bot-status-grid">' + data.bot_statuses.map(bot => 
                    `<div class="bot-status-item">
                        <span>${bot.name}</span>
                        <button class="btn-toggle-state ${bot.is_active ? 'btn-rise' : 'btn-rest'}" data-target="${bot.reboot_id}">
                            ${bot.is_active ? 'RISE' : 'REST'}
                        </button>
                    </div>`
                ).join('') + '</div>';
            }
        } catch (error) { console.error('Error fetching status:', error); }
    }
    
    fetchStatus();
    setInterval(fetchStatus, 10000); // Update every 10 seconds
});
</script>
</body>
</html>
"""

# --- FLASK ROUTES FIXED ---
@app.route("/")
def index():
    reboot_action, reboot_button_class = ("DISABLE REBOOT", "btn-danger") if auto_reboot_enabled else ("ENABLE REBOOT", "btn-success")
    event_grab_action, event_grab_button_class = ("DISABLE EVENT GRAB", "btn-danger") if event_grab_enabled else ("ENABLE EVENT GRAB", "btn-success")
    
    # Sắp xếp farm theo group để hiển thị
    sorted_farms = sorted(farm_servers, key=lambda x: x.get('group', 'zzzz'))

    return render_template_string(HTML_TEMPLATE,
        auto_reboot_delay=auto_reboot_delay, reboot_action=reboot_action, reboot_button_class=reboot_button_class,
        event_grab_action=event_grab_action, event_grab_button_class=event_grab_button_class,
        farm_servers=sorted_farms,
        groups=groups,
        sub_acc_names=list(enumerate(sub_acc_names)),
        main_panel=main_panel_settings
    )

@app.route("/status")
def status(): 
    bot_status_list = []
    with bots_lock:
        for i in range(len(main_bots)):
            name = GREEK_ALPHABET[i] if i < len(GREEK_ALPHABET) else f"Main {i}"
            bot_status_list.append({"name": name, "reboot_id": f"main_{i}", "is_active": bot_active_states.get(f'main_{i}', False)})
        for i in range(len(sub_bots)):
            name = sub_acc_names[i] if i < len(sub_acc_names) else f"Sub {i}"
            bot_status_list.append({"name": name, "reboot_id": f"sub_{i}", "is_active": bot_active_states.get(f'sub_{i}', False)})
    return jsonify({'bot_statuses': bot_status_list})

# --- API ENDPOINTS FIXED ---
@app.route("/api/groups/add", methods=['POST'])
def api_group_add():
    name = request.json.get('name')
    if name and name not in groups:
        groups[name] = {
            'spam_enabled': False, 
            'spam_accounts': [],
            'spam_message': main_panel_settings.get('spam_message', 'kcf'),
            'spam_delay': main_panel_settings.get('spam_delay', 10)
        }
        save_main_settings()
        debug_log(f"Đã tạo group mới: {name}")
        return jsonify({'status': 'success', 'message': f'Đã tạo group "{name}".', 'reload': True})
    return jsonify({'status': 'error', 'message': 'Tên group không hợp lệ hoặc đã tồn tại.'}), 400

@app.route("/api/groups/delete", methods=['POST'])
def api_group_delete():
    name = request.json.get('name')
    if name and name in groups:
        del groups[name]
        # Xóa group khỏi các farm
        for server in farm_servers:
            if server.get('group') == name:
                server['group'] = None
        save_main_settings()
        save_farm_settings()
        debug_log(f"Đã xóa group: {name}")
        return jsonify({'status': 'success', 'message': f'Đã xóa group "{name}".', 'reload': True})
    return jsonify({'status': 'error', 'message': 'Không tìm thấy group.'}), 404

@app.route("/api/groups/update", methods=['POST'])
def api_group_update():
    data = request.json
    name = data.get('name')
    if name and name in groups:
        if 'spam_enabled' in data:
            old_status = groups[name].get('spam_enabled', False)
            groups[name]['spam_enabled'] = not old_status
            debug_log(f"Group {name} spam: {old_status} -> {not old_status}")
        if 'spam_accounts' in data:
            groups[name]['spam_accounts'] = data['spam_accounts']
            debug_log(f"Group {name} spam accounts updated: {data['spam_accounts']}")
        save_main_settings()
        return jsonify({'status': 'success', 'message': f'Đã cập nhật group "{name}".'})
    return jsonify({'status': 'error', 'message': 'Không tìm thấy group.'}), 404

@app.route("/api/main_panel/update", methods=['POST'])
def api_main_panel_update():
    data = request.json
    debug_log(f"Main panel update: {data}")
    for key, value in data.items():
        if key in main_panel_settings:
            old_value = main_panel_settings[key]
            if value == 'toggle': 
                main_panel_settings[key] = not main_panel_settings[key]
            else: 
                main_panel_settings[key] = type(main_panel_settings[key])(value)
            debug_log(f"Main panel {key}: {old_value} -> {main_panel_settings[key]}")
    save_main_settings()
    return jsonify({'status': 'success', 'message': 'Đã cập nhật Main Panel.'})

@app.route("/api/main_panel/sync", methods=['POST'])
def api_main_panel_sync():
    target_groups = request.json.get('target_groups', [])
    if not target_groups: 
        return jsonify({'status': 'error', 'message': 'Chưa chọn group mục tiêu.'}), 400
    
    sync_count = 0
    debug_log(f"Syncing to groups: {target_groups}")
    for server in farm_servers:
        if server.get('group') in target_groups:
            # Sync Harvest
            server['auto_grab_enabled_alpha'] = main_panel_settings['auto_grab_enabled_alpha']
            server['heart_threshold_alpha'] = main_panel_settings['heart_threshold_alpha']
            server['auto_grab_enabled_main_other'] = main_panel_settings['auto_grab_enabled_main_other']
            server['heart_threshold_main_other'] = main_panel_settings['heart_threshold_main_other']
            # Sync Broadcast
            server['spam_message'] = main_panel_settings['spam_message']
            server['spam_delay'] = main_panel_settings['spam_delay']
            sync_count += 1
            debug_log(f"Synced settings to farm: {server['name']}")
            
    save_farm_settings()
    debug_log(f"Sync completed: {sync_count} farms updated")
    return jsonify({'status': 'success', 'message': f'Đã đồng bộ cài đặt cho {sync_count} farm.'})

@app.route("/api/farm/add", methods=['POST'])
def api_farm_add():
    name = request.json.get('name')
    if not name: 
        return jsonify({'status': 'error', 'message': 'Tên farm là bắt buộc.'}), 400
    
    # Gán vào group đầu tiên nếu có
    default_group = next(iter(groups), None)

    new_server = {
        "id": f"farm_{int(time.time())}", 
        "name": name, 
        "group": default_group,
        "main_channel_id": "", 
        "ktb_channel_id": "", 
        "spam_channel_id": "",
        "auto_grab_enabled_alpha": main_panel_settings.get('auto_grab_enabled_alpha', False), 
        "heart_threshold_alpha": main_panel_settings.get('heart_threshold_alpha', 15),
        "auto_grab_enabled_main_other": main_panel_settings.get('auto_grab_enabled_main_other', False), 
        "heart_threshold_main_other": main_panel_settings.get('heart_threshold_main_other', 50),
        "spam_enabled": False, 
        "spam_message": main_panel_settings.get('spam_message', 'kcf'), 
        "spam_delay": main_panel_settings.get('spam_delay', 10), 
        "last_spam_time": 0
    }
    farm_servers.append(new_server)
    save_farm_settings()
    debug_log(f"Đã thêm farm mới: {name} (group: {default_group})")
    return jsonify({'status': 'success', 'message': f'Farm "{name}" đã được thêm.', 'reload': True})

@app.route("/api/farm/delete", methods=['POST'])
def api_farm_delete():
    global farm_servers
    farm_id = request.json.get('farm_id')
    old_count = len(farm_servers)
    farm_servers = [s for s in farm_servers if s.get('id') != farm_id]
    save_farm_settings()
    debug_log(f"Đã xóa farm: {farm_id} (còn lại {len(farm_servers)} farms)")
    return jsonify({'status': 'success', 'message': 'Farm đã được xóa.', 'reload': True})

@app.route("/api/farm/update", methods=['POST'])
def api_farm_update():
    data = request.json
    farm_id = data.get('farm_id')
    server = next((s for s in farm_servers if s.get('id') == farm_id), None)
    if not server: 
        return jsonify({'status': 'error', 'message': 'Không tìm thấy farm.'}), 404
    
    updated_fields = []
    for key in ['main_channel_id', 'ktb_channel_id', 'spam_channel_id', 'group']:
        if key in data: 
            old_val = server.get(key)
            server[key] = data[key]
            updated_fields.append(f"{key}: {old_val} -> {data[key]}")
    
    save_farm_settings()
    debug_log(f"Farm {server['name']} updated: {', '.join(updated_fields)}")
    
    if 'group' in data: 
        return jsonify({'status': 'success', 'message': f'Đã chuyển group cho farm.', 'reload': True})
    return jsonify({'status': 'success', 'message': f'Đã cập nhật kênh cho farm.'})

# --- GLOBAL CONTROL API ---
@app.route("/api/reboot_toggle_auto", methods=['POST'])
def api_reboot_toggle_auto():
    global auto_reboot_enabled, auto_reboot_thread, auto_reboot_stop_event, auto_reboot_delay
    
    new_delay = int(request.json.get("delay", auto_reboot_delay))
    auto_reboot_delay = new_delay
    
    auto_reboot_enabled = not auto_reboot_enabled
    debug_log(f"Auto reboot toggled: {auto_reboot_enabled}, delay: {auto_reboot_delay}s")
    
    if auto_reboot_enabled and (auto_reboot_thread is None or not auto_reboot_thread.is_alive()):
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
    elif not auto_reboot_enabled and auto_reboot_stop_event: 
        auto_reboot_stop_event.set()
        auto_reboot_thread = None
    
    save_main_settings()
    return jsonify({'status': 'success', 'message': f'Auto Reboot đã {"BẬT" if auto_reboot_enabled else "TẮT"} với delay {auto_reboot_delay}s.'})

@app.route("/api/reboot_update_delay", methods=['POST'])
def api_reboot_update_delay():
    global auto_reboot_delay
    new_delay = int(request.json.get("delay", 3600))
    auto_reboot_delay = new_delay
    save_main_settings()
    debug_log(f"Auto reboot delay updated: {auto_reboot_delay}s")
    return jsonify({'status': 'success', 'message': f'Đã cập nhật Auto Reboot delay thành {auto_reboot_delay}s.'})

@app.route("/api/toggle_bot_state", methods=['POST'])
def api_toggle_bot_state():
    target = request.json.get('target')
    if target in bot_active_states:
        old_state = bot_active_states[target]
        bot_active_states[target] = not old_state
        state_text = "ONLINE" if bot_active_states[target] else "OFFLINE"
        debug_log(f"Bot {target}: {old_state} -> {bot_active_states[target]} ({state_text})")
        save_main_settings()
        return jsonify({'status': 'success', 'message': f"Bot {target.upper()} đã được đặt thành {state_text}."})
    return jsonify({'status': 'error', 'message': 'Không tìm thấy bot.'})

@app.route("/api/event_grab_toggle", methods=['POST'])
def api_event_grab_toggle():
    global event_grab_enabled
    old_state = event_grab_enabled
    event_grab_enabled = not event_grab_enabled
    debug_log(f"Event grab: {old_state} -> {event_grab_enabled}")
    save_main_settings()
    return jsonify({'status': 'success', 'message': f"Event Grab đã {'BẬT' if event_grab_enabled else 'TẮT'}"})

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    # Load settings
    load_farm_settings()
    load_main_settings()
    
    debug_log("=== FARM CONTROL SYSTEM STARTING ===")
    debug_log(f"Settings loaded - Groups: {len(groups)}, Farms: {len(farm_servers)}")
    debug_log(f"Main panel settings: {main_panel_settings}")
    
    # Initialize bots
    debug_log("Đang khởi tạo các bot...")
    with bots_lock:
        # Main bots
        if main_token_alpha:
            main_bots.append(create_bot(main_token_alpha, 'main', 0))
            if 'main_0' not in bot_active_states: 
                bot_active_states['main_0'] = True
            debug_log("Alpha bot (main_0) initialized")
                
        for i, token in enumerate(other_main_tokens):
            if token.strip():
                bot_index = i + 1
                main_bots.append(create_bot(token.strip(), 'main', bot_index))
                if f'main_{bot_index}' not in bot_active_states: 
                    bot_active_states[f'main_{bot_index}'] = True
                debug_log(f"Main bot {bot_index} initialized")
        
        # Sub bots
        for i, token in enumerate(sub_tokens):
            if token.strip():
                sub_bots.append(create_bot(token.strip(), 'sub', i))
                if f'sub_{i}' not in bot_active_states: 
                    bot_active_states[f'sub_{i}'] = True
                debug_log(f"Sub bot {i} initialized")

    debug_log(f"Bot initialization complete - Main: {len(main_bots)}, Sub: {len(sub_bots)}")
    debug_log(f"Active bot states: {bot_active_states}")

    # Start background loops
    debug_log("Đang khởi tạo các luồng nền...")
    threading.Thread(target=optimized_spam_loop, daemon=True).start()
    threading.Thread(target=periodic_save_loop, daemon=True).start()

    if auto_reboot_enabled and (auto_reboot_thread is None or not auto_reboot_thread.is_alive()):
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
        debug_log("Auto reboot thread started")
    
    port = int(os.environ.get("PORT", 10001))
    debug_log(f"=== SERVER STARTING ON PORT {port} ===")
    debug_log("Web interface: http://0.0.0.0:{port}")
    
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
