# PHIÊN BẢN TỐI ỮU - MEMORY SAFE FARM CONTROL
import discum
import threading
import time
import os
import random
import re
import requests
import json
import queue
import weakref
from concurrent.futures import ThreadPoolExecutor
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv

load_dotenv()

# --- CẤU HÌNH ---
main_token_alpha = os.getenv("MAIN_TOKEN")
other_main_tokens = os.getenv("MAIN_TOKENS").split(",") if os.getenv("MAIN_TOKENS") else []
GREEK_ALPHABET = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta', 'Iota', 'Kappa', 'Lambda', 'Mu']

karuta_id = "646937666251915264"
yoru_bot_id = "1311684840462225440"

# --- MEMORY MANAGEMENT ---
THREAD_POOL = ThreadPoolExecutor(max_workers=20, thread_name_prefix="farm_worker")
GRAB_QUEUE = queue.Queue(maxsize=1000)
ACTIVE_TIMERS = set()
CONNECTION_CLEANUP_INTERVAL = 300

# --- BIẾN TRẠNG THÁI ---
main_bots = []
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
main_panel_settings = {
    "auto_grab_enabled_alpha": False, "heart_threshold_alpha": 15,
    "auto_grab_enabled_main_other": False, "heart_threshold_main_other": 50,
}

# --- MEMORY SAFE TIMER CLASS ---
class ManagedTimer:
    def __init__(self, delay, func, args=None):
        self.timer = threading.Timer(delay, self._execute, args=args or [])
        self.func = func
        self.args = args or []
        ACTIVE_TIMERS.add(self)
    
    def _execute(self):
        try:
            self.func(*self.args)
        finally:
            ACTIVE_TIMERS.discard(self)
    
    def start(self):
        self.timer.start()
    
    def cancel(self):
        self.timer.cancel()
        ACTIVE_TIMERS.discard(self)

def cleanup_timers():
    expired = [timer for timer in ACTIVE_TIMERS if not timer.timer.is_alive()]
    for timer in expired:
        ACTIVE_TIMERS.discard(timer)

# --- GRAB QUEUE PROCESSOR ---
def grab_queue_processor():
    while True:
        try:
            grab_task = GRAB_QUEUE.get(timeout=1)
            if grab_task is None: break
            
            bot, channel_id, message_id, emoji, delay = grab_task
            time.sleep(delay)
            
            try:
                bot.addReaction(channel_id, message_id, emoji)
            except Exception as e:
                print(f"[GRAB QUEUE] Lỗi khi grab: {e}", flush=True)
            finally:
                GRAB_QUEUE.task_done()
                
        except queue.Empty:
            continue
        except Exception as e:
            print(f"[GRAB QUEUE] Lỗi processor: {e}", flush=True)

THREAD_POOL.submit(grab_queue_processor)

# --- <<< NEW FUNCTIONS: SAVE/LOAD MAIN SETTINGS >>> ---
def save_main_settings():
    """Lưu các cài đặt global (main panel, auto reboot, etc.)"""
    api_key = os.getenv("JSONBIN_API_KEY")
    main_bin_id = os.getenv("MAIN_JSONBIN_BIN_ID") # Cần thêm variable này vào .env
    if not api_key or not main_bin_id: return

    settings_to_save = {
        'main_panel_settings': main_panel_settings,
        'event_grab_enabled': event_grab_enabled,
        'auto_reboot_enabled': auto_reboot_enabled,
        'auto_reboot_delay': auto_reboot_delay,
        'bot_active_states': bot_active_states
    }

    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{main_bin_id}"
    try:
        req = requests.put(url, json=settings_to_save, headers=headers, timeout=10)
        if req.status_code == 200: 
            print("[Main Settings] Đã lưu cài đặt chính.", flush=True)
    except Exception as e: 
        print(f"[Main Settings] Lỗi khi lưu cài đặt chính: {e}", flush=True)

def load_main_settings():
    """Tải các cài đặt global khi khởi động"""
    global main_panel_settings, event_grab_enabled, auto_reboot_enabled, auto_reboot_delay, bot_active_states
    api_key = os.getenv("JSONBIN_API_KEY")
    main_bin_id = os.getenv("MAIN_JSONBIN_BIN_ID")
    if not api_key or not main_bin_id: return

    headers = {'X-Master-Key': api_key, 'X-Bin-Meta': 'false'}
    url = f"https://api.jsonbin.io/v3/b/{main_bin_id}/latest"
    try:
        req = requests.get(url, headers=headers, timeout=10)
        if req.status_code == 200:
            data = req.json()
            main_panel_settings = data.get('main_panel_settings', main_panel_settings)
            event_grab_enabled = data.get('event_grab_enabled', event_grab_enabled)
            auto_reboot_enabled = data.get('auto_reboot_enabled', auto_reboot_enabled)
            auto_reboot_delay = data.get('auto_reboot_delay', auto_reboot_delay)
            bot_active_states = data.get('bot_active_states', bot_active_states)
            print("[Main Settings] Đã tải cài đặt chính.", flush=True)
    except Exception as e:
        print(f"[Main Settings] Không thể tải cài đặt chính, dùng mặc định: {e}", flush=True)
        
# --- OPTIMIZED FUNCTIONS ---
def save_farm_settings():
    api_key = os.getenv("JSONBIN_API_KEY")
    farm_bin_id = os.getenv("FARM_JSONBIN_BIN_ID")
    if not api_key or not farm_bin_id: return
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{farm_bin_id}"
    try:
        req = requests.put(url, json=farm_servers, headers=headers, timeout=10)
        if req.status_code == 200: print("[Farm Settings] Đã lưu cài đặt farm panels.", flush=True)
    except Exception as e: print(f"[Farm Settings] Lỗi khi lưu farm panels: {e}", flush=True)

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
            if isinstance(data, list): farm_servers = data
            print(f"[Farm Settings] Đã tải {len(farm_servers)} cấu hình farm.", flush=True)
    except Exception: farm_servers = []

def get_grab_settings(target_server, bot_type, bot_index):
    if bot_type == 'main' and bot_index == 0:
        return target_server.get('auto_grab_enabled_alpha', False), target_server.get('heart_threshold_alpha', 15), {0: 0.2, 1: 1.2, 2: 2.0}
    else:
        return target_server.get('auto_grab_enabled_main_other', False), target_server.get('heart_threshold_main_other', 50), {0: 1.0, 1: 2.0, 2: 2.8}

def broadcast_grab_to_main_bots(channel_id, message_id, emoji, max_index, target_server):
    with bots_lock:
        for i, bot in enumerate(main_bots):
            if not bot_active_states.get(f'main_{i}', False):
                continue
            is_enabled, _, delays = get_grab_settings(target_server, 'main', i)
            if not is_enabled:
                continue
            delay = delays.get(max_index, 1.5)
            try:
                GRAB_QUEUE.put((bot, channel_id, message_id, emoji, delay), block=False)
            except queue.Full:
                print(f"[FARM BROADCAST] Queue đầy, bỏ qua grab task cho Bot {i}", flush=True)
            
            if i == 0:
                ktb_channel_id = target_server.get('ktb_channel_id')
                if ktb_channel_id:
                    ManagedTimer(delay + 2, lambda: bot.sendMessage(ktb_channel_id, "kt b")).start()

def initiate_grab_sequence(alpha_bot, msg):
    channel_id = msg.get("channel_id")
    target_server = next((s for s in farm_servers if s.get('main_channel_id') == channel_id), None)
    if not target_server: return

    if event_grab_enabled:
        def check_farm_event():
            try:
                time.sleep(5)
                full_msg_obj = alpha_bot.getMessage(channel_id, msg["id"]).json()[0]
                if 'reactions' in full_msg_obj and any(r['emoji']['name'] == '🉐' for r in full_msg_obj['reactions']):
                    alpha_bot.addReaction(channel_id, msg["id"], "🉐")
            except Exception as e: print(f"Lỗi kiểm tra event: {e}", flush=True)
        THREAD_POOL.submit(check_farm_event)

    def read_yoru_and_coordinate():
        time.sleep(0.6)
        try:
            messages = alpha_bot.getMessages(channel_id, num=5).json()
            for msg_item in messages:
                if msg_item.get("author", {}).get("id") == yoru_bot_id and msg_item.get("embeds"):
                    desc = msg_item["embeds"][0].get("description", "")
                    heart_numbers = [int(match.group(1)) if (match := re.search(r'♡(\d+)', line)) else 0 for line in desc.split('\n')[:3]]
                    if not any(heart_numbers): break
                    _, heart_threshold, _ = get_grab_settings(target_server, 'main', 0)
                    max_num = max(heart_numbers)
                    if max_num >= heart_threshold:
                        max_index = heart_numbers.index(max_num)
                        emoji = ["1️⃣", "2️⃣", "3️⃣"][max_index]
                        broadcast_grab_to_main_bots(channel_id, msg["id"], emoji, max_index, target_server)
                    break 
        except Exception as e: print(f"Lỗi đọc Yoru Bot và điều phối: {e}", flush=True)
    THREAD_POOL.submit(read_yoru_and_coordinate)

def create_bot(token, bot_type, bot_index):
    bot = discum.Client(token=token, log=False)
    @bot.gateway.command
    def on_ready(resp):
        if resp.event.ready:
            user = resp.raw.get('user', {})
            print(f"Bot '{GREEK_ALPHABET[bot_index]}' đã đăng nhập: {user.get('username')}", flush=True)
    @bot.gateway.command
    def on_message(resp):
        if bot_index == 0:
            if not (resp.event.message or (resp.raw and resp.raw.get('t') == 'MESSAGE_UPDATE')): return
            msg = resp.parsed.auto()
            if msg.get("author", {}).get("id") == karuta_id and 'dropping 3' in msg.get("content", ""):
                initiate_grab_sequence(bot, msg)
    THREAD_POOL.submit(bot.gateway.run)
    return bot

# --- MEMORY CLEANUP FUNCTIONS ---
def cleanup_expired_connections():
    cleanup_timers()
    print(f"[CLEANUP] Active timers: {len(ACTIVE_TIMERS)}, Queue size: {GRAB_QUEUE.qsize()}", flush=True)

def reboot_bot(target_id):
    with bots_lock:
        bot_type, index_str = target_id.split('_')
        index = int(index_str)
        if bot_type == 'main' and index < len(main_bots):
            old_bot = main_bots[index]
            try:
                if hasattr(old_bot, 'gateway') and hasattr(old_bot.gateway, 'close'): old_bot.gateway.close()
                if hasattr(old_bot, 'close'): old_bot.close()
            except Exception as e: print(f"[CLEANUP] Lỗi khi đóng bot cũ: {e}", flush=True)
            
            token = main_token_alpha if index == 0 else other_main_tokens[index - 1]
            main_bots[index] = create_bot(token, 'main', index)
            print(f"[Reboot] Main Bot {index} ({GREEK_ALPHABET[index]}) đã khởi động lại.", flush=True)

def auto_reboot_loop():
    while not auto_reboot_stop_event.is_set():
        try:
            current_time = time.time()
            if current_time % CONNECTION_CLEANUP_INTERVAL < 60:
                cleanup_expired_connections()
            if auto_reboot_enabled and (current_time - last_reboot_cycle_time) >= auto_reboot_delay:
                print("[Reboot] Bắt đầu chu kỳ reboot tự động...", flush=True)
                with bots_lock:
                    for i in range(len(main_bots)):
                        if bot_active_states.get(f'main_{i}', True):
                            reboot_bot(f'main_{i}')
                            time.sleep(5)
                globals()['last_reboot_cycle_time'] = time.time()
            if auto_reboot_stop_event.wait(timeout=60): break
        except Exception as e:
            print(f"[ERROR in auto_reboot_loop] {e}", flush=True)
            time.sleep(60)
    print("[Reboot] Luồng tự động reboot đã dừng.", flush=True)

def periodic_save_loop():
    while True:
        time.sleep(300)
        save_farm_settings()
        try:
            import psutil
            process = psutil.Process()
            memory_mb = process.memory_info().rss / 1024 / 1024
            print(f"[MEMORY] RAM usage: {memory_mb:.1f}MB, Active timers: {len(ACTIVE_TIMERS)}, Queue: {GRAB_QUEUE.qsize()}", flush=True)
            if memory_mb > 500:
                print("[MEMORY] High memory usage detected, running cleanup...", flush=True)
                cleanup_expired_connections()
        except ImportError:
            pass # psutil might not be installed

# --- SHUTDOWN HANDLER ---
def shutdown_handler():
    print("[SHUTDOWN] Cleaning up resources...", flush=True)
    GRAB_QUEUE.put(None)
    for timer in ACTIVE_TIMERS.copy():
        timer.cancel()
    THREAD_POOL.shutdown(wait=False)
    with bots_lock:
        for bot in main_bots:
            try:
                if hasattr(bot, 'gateway'): bot.gateway.close()
            except: pass

import atexit
atexit.register(shutdown_handler)

# --- FLASK APP ---
app = Flask(__name__)
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Karuta Farm Control - Main Only</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Courier+Prime:wght@400;700&family=Nosifer&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-bg: #0a0a0a; --secondary-bg: #1a1a1a; --panel-bg: #111111; --border-color: #333333;
            --blood-red: #8b0000; --necro-green: #228b22; --shadow-cyan: #008b8b; --text-primary: #f0f0f0;
            --text-secondary: #cccccc; --hot-pink: #FF69B4; --gold: #FFD700; --main-blue: #00BFFF;
        }
        body { font-family: 'Courier Prime', monospace; background: var(--primary-bg); color: var(--text-primary); margin: 0; padding: 20px; }
        .container { max-width: 1600px; margin: 0 auto; }
        .header { text-align: center; margin-bottom: 30px; }
        .title { font-family: 'Nosifer', cursive; font-size: 2.5rem; color: var(--hot-pink); text-shadow: 0 0 15px var(--hot-pink);}
        .panel { background: #111; border: 1px solid var(--border-color); border-radius: 10px; padding: 20px; margin-bottom: 20px; position: relative; }
        .panel h2, .panel h3 { font-family: 'Orbitron', monospace; color: var(--text-secondary); border-bottom: 1px solid var(--border-color); padding-bottom: 10px; margin-top: 0;}
        .main-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(350px, 1fr)); gap: 20px; }
        .btn { background: var(--secondary-bg); border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px 12px; border-radius: 4px; cursor: pointer; font-family: 'Orbitron'; }
        .btn:hover { filter: brightness(1.2); }
        .btn-danger { border-color: var(--blood-red); color: var(--blood-red); } .btn-danger:hover { background: var(--blood-red); color: var(--primary-bg); }
        .btn-success { border-color: var(--necro-green); color: var(--necro-green); } .btn-success:hover { background: var(--necro-green); color: var(--primary-bg); }
        .btn-primary { border-color: var(--main-blue); color: var(--main-blue); } .btn-primary:hover { background: var(--main-blue); color: var(--primary-bg); }
        .input-group { display: flex; align-items: stretch; gap: 5px; margin-bottom: 10px; }
        .input-group label { padding: 8px; background: #222; border: 1px solid var(--border-color); border-radius: 5px 0 0 5px; white-space: nowrap; }
        .input-group input { width: 100%; background: #000; border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px; border-radius: 0 5px 5px 0; }
        .bot-status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(200px, 1fr)); gap: 8px; }
        .bot-status-item { display: flex; justify-content: space-between; align-items: center; padding: 5px 8px; background: #222; border-radius: 4px; }
        .btn-toggle-state { cursor: pointer; background: transparent; border: none; font-weight: 700; }
        .msg-status { text-align: center; color: var(--shadow-cyan); padding: 12px; border: 1px dashed var(--border-color); margin-bottom: 20px; background: rgba(0, 139, 139, 0.1); display: none; }
        .main-panel { border: 2px solid var(--main-blue); box-shadow: 0 0 15px var(--main-blue); }
        .delete-btn { background: var(--blood-red); color: white; border: none; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header"><h1 class="title">FARM CONTROL PANEL - MAIN ACCOUNTS</h1></div>
        <div id="msg-status-container" class="msg-status"></div>

        <div class="panel">
            <h2><i class="fas fa-server"></i> System Status & Global Controls</h2>
            <div class="main-grid">
                <div id="bot-status-list" class="bot-status-grid"></div>
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
                </div>
                <div>
                    <h4><i class="fas fa-sync-alt"></i> Đồng Bộ Hóa</h4>
                     <p style="font-size: 0.9em; color: var(--text-secondary);">Đồng bộ cài đặt Harvest ở trên cho TOÀN BỘ các farm panel bên dưới.</p>
                    <button id="sync-from-main-btn" class="btn btn-primary" style="width:100%;">Đồng Bộ Hóa Ngay</button>
                </div>
            </div>
        </div>
        
        <div class="panel">
            <h2><i class="fas fa-plus-circle"></i> Add & Manage Farm Panels</h2>
            <div id="farm-grid" class="main-grid">
                {% for server in farm_servers %}
                <div class="panel" style="border-left: 5px solid var(--hot-pink);">
                    <button class="delete-btn delete-farm-btn" data-farm-id="{{ server.id }}" style="position:absolute; top:10px; right: 10px;">XÓA</button>
                    <h3>{{ server.name }}</h3>
                    <div class="input-group"><label>Main CH</label><input type="text" class="farm-channel-input" data-farm-id="{{ server.id }}" data-field="main_channel_id" value="{{ server.main_channel_id or '' }}"></div>
                    <div class="input-group"><label>KTB CH</label><input type="text" class="farm-channel-input" data-farm-id="{{ server.id }}" data-field="ktb_channel_id" value="{{ server.ktb_channel_id or '' }}"></div>
                </div>
                {% endfor %}
                <div id="add-farm-btn" style="display:flex; align-items:center; justify-content:center; min-height:150px; border: 2px dashed var(--border-color); cursor:pointer; border-radius: 8px;">
                    <i class="fas fa-plus" style="font-size: 3rem;"></i>
                </div>
            </div>
        </div>
    </div>
<script>
document.addEventListener('DOMContentLoaded', function () {
    const msgContainer = document.getElementById('msg-status-container');
    const showMsg = (msg) => {
        if (!msg) return;
        msgContainer.textContent = msg;
        msgContainer.style.display = 'block';
        setTimeout(() => { msgContainer.style.display = 'none'; }, 4000);
    };
    const postData = async (url, data) => {
        try {
            const response = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(data) });
            const result = await response.json();
            showMsg(result.message);
            if (result.reload) setTimeout(() => location.reload(), 500);
            return result;
        } catch (error) { showMsg('Lỗi giao tiếp với server.'); }
    };
    const fetchBotStatus = async () => {
        try {
            const response = await fetch('/status');
            const data = await response.json();
            const botListDiv = document.getElementById('bot-status-list');
            let content = '<h4><i class="fas fa-robot"></i> Bot Status</h4>';
            data.bot_statuses.forEach(bot => {
                const stateClass = bot.is_active ? 'btn-success' : 'btn-danger';
                const stateText = bot.is_active ? 'ONLINE' : 'OFFLINE';
                content += `<div class="bot-status-item"><span>${bot.name}</span><button class="btn btn-sm ${stateClass} btn-toggle-state" data-target="${bot.reboot_id}">${stateText}</button></div>`;
            });
            botListDiv.innerHTML = content;
        } catch (error) { console.error("Failed to fetch bot status:", error); }
    };
    document.getElementById('auto-reboot-toggle-btn').addEventListener('click', () => {
        const delay = document.getElementById('auto-reboot-delay').value;
        postData('/api/reboot_toggle_auto', { delay: delay }).then(r => { if (!r || !r.reload) { location.reload(); } });
    });
    document.getElementById('event-grab-toggle-btn').addEventListener('click', () => {
        postData('/api/event_grab_toggle', {}).then(r => { if (!r || !r.reload) { location.reload(); } });
    });
    document.getElementById('bot-status-list').addEventListener('click', e => {
        if (e.target.matches('.btn-toggle-state')) {
            postData('/api/toggle_bot_state', { target: e.target.dataset.target });
            setTimeout(fetchBotStatus, 500);
        }
    });
    const mainPanel = document.querySelector('.main-panel');
    mainPanel.addEventListener('change', e => {
        if (e.target.matches('.main-panel-input')) {
            const data = {}; data[e.target.dataset.field] = e.target.value;
            postData('/api/main_panel/update', data);
        }
    });
    mainPanel.addEventListener('click', e => {
        if (e.target.matches('.main-panel-toggle')) {
            const data = {}; data[e.target.dataset.field] = 'toggle';
            postData('/api/main_panel/update', data).then(() => location.reload());
        }
    });
    document.getElementById('sync-from-main-btn').addEventListener('click', () => {
        if (confirm('Bạn có chắc muốn đồng bộ cài đặt từ Main Panel cho TẤT CẢ các farm không?')) {
            postData('/api/main_panel/sync', {});
        }
    });
    document.getElementById('add-farm-btn').addEventListener('click', () => {
        const name = prompt("Nhập tên farm mới:");
        if (name) postData('/api/farm/add', { name });
    });
    const farmGrid = document.getElementById('farm-grid');
    farmGrid.addEventListener('click', e => {
        if (e.target.matches('.delete-farm-btn')) {
            if (confirm('Xóa farm này?')) {
                postData('/api/farm/delete', { farm_id: e.target.dataset.farmId });
            }
        }
    });
    farmGrid.addEventListener('change', e => {
        if (e.target.matches('.farm-channel-input')) {
            const data = { farm_id: e.target.dataset.farmId };
            data[e.target.dataset.field] = e.target.value;
            postData('/api/farm/update', data);
        }
    });
    fetchBotStatus();
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
    return render_template_string(HTML_TEMPLATE,
        auto_reboot_delay=auto_reboot_delay, reboot_action=reboot_action, reboot_button_class=reboot_button_class,
        event_grab_action=event_grab_action, event_grab_button_class=event_grab_button_class,
        farm_servers=farm_servers, main_panel=main_panel_settings
    )

@app.route("/status")
def status():
    bot_status_list = []
    with bots_lock:
        for i in range(len(main_bots)):
            name = GREEK_ALPHABET[i] if i < len(GREEK_ALPHABET) else f"Main {i}"
            bot_status_list.append({
                "name": name, "reboot_id": f"main_{i}", "is_active": bot_active_states.get(f'main_{i}', True)
            })
    return jsonify({'bot_statuses': bot_status_list})

@app.route("/api/main_panel/update", methods=['POST'])
def api_main_panel_update():
    data = request.json
    for key, value in data.items():
        if key in main_panel_settings:
            if value == 'toggle':
                main_panel_settings[key] = not main_panel_settings[key]
            else:
                original_type = type(main_panel_settings.get(key, ''))
                try: main_panel_settings[key] = original_type(value)
                except (ValueError, TypeError): pass
    save_main_settings()
    return jsonify({'status': 'success', 'message': 'Đã cập nhật Main Panel.'})

@app.route("/api/main_panel/sync", methods=['POST'])
def api_main_panel_sync():
    sync_count = 0
    for server in farm_servers:
        server.update({
            'auto_grab_enabled_alpha': main_panel_settings['auto_grab_enabled_alpha'],
            'heart_threshold_alpha': main_panel_settings['heart_threshold_alpha'],
            'auto_grab_enabled_main_other': main_panel_settings['auto_grab_enabled_main_other'],
            'heart_threshold_main_other': main_panel_settings['heart_threshold_main_other']
        })
        sync_count += 1
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Đã đồng bộ cài đặt cho {sync_count} farm.'})

@app.route("/api/farm/add", methods=['POST'])
def api_farm_add():
    name = request.json.get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Tên farm là bắt buộc.'}), 400
    new_server = {
        "id": f"farm_{int(time.time())}_{random.randint(100,999)}", "name": name,
        "main_channel_id": "", "ktb_channel_id": "",
        **main_panel_settings
    }
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
    for key in ['main_channel_id', 'ktb_channel_id']:
        if key in data: server[key] = data[key]
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Đã cập nhật kênh cho farm.'})

@app.route("/api/reboot_toggle_auto", methods=['POST'])
def api_reboot_toggle_auto():
    global auto_reboot_enabled, auto_reboot_thread, auto_reboot_stop_event, auto_reboot_delay
    auto_reboot_enabled = not auto_reboot_enabled
    try: auto_reboot_delay = int(request.json.get("delay", auto_reboot_delay))
    except (ValueError, TypeError): pass
    if auto_reboot_enabled:
        if auto_reboot_thread is None or not auto_reboot_thread.is_alive():
            auto_reboot_stop_event = threading.Event()
            auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
            auto_reboot_thread.start()
    else:
        if auto_reboot_stop_event: auto_reboot_stop_event.set()
        auto_reboot_thread = None
    save_main_settings()
    return jsonify({'status': 'success', 'message': f'Auto Reboot đã {"BẬT" if auto_reboot_enabled else "TẮT"}. Delay: {auto_reboot_delay}s.', 'reload': True})

@app.route("/api/toggle_bot_state", methods=['POST'])
def api_toggle_bot_state():
    target = request.json.get('target')
    if target in bot_active_states:
        bot_active_states[target] = not bot_active_states.get(target, True)
        state_text = "ONLINE" if bot_active_states[target] else "OFFLINE"
        save_main_settings()
        return jsonify({'status': 'success', 'message': f"Bot {target.replace('_', ' ').upper()} đã được đặt thành {state_text}."})
    return jsonify({'status': 'error', 'message': 'Không tìm thấy bot.'})

@app.route("/api/event_grab_toggle", methods=['POST'])
def api_event_grab_toggle():
    global event_grab_enabled
    event_grab_enabled = not event_grab_enabled
    save_main_settings()
    return jsonify({'status': 'success', 'message': f"Event Grab đã {'BẬT' if event_grab_enabled else 'TẮT'}", 'reload': True})

if __name__ == "__main__":
    print("Đang tải cấu hình...", flush=True)
    # --- <<< MODIFIED: Load both settings files >>> ---
    load_main_settings()
    load_farm_settings()
    
    print("Đang khởi tạo các main bots...", flush=True)
    with bots_lock:
        if main_token_alpha:
            main_bots.append(create_bot(main_token_alpha, 'main', 0))
            if 'main_0' not in bot_active_states: bot_active_states['main_0'] = True
        
        for i, token in enumerate(other_main_tokens):
            if token.strip():
                bot_index = i + 1
                main_bots.append(create_bot(token.strip(), 'main', bot_index))
                if f'main_{bot_index}' not in bot_active_states: bot_active_states[f'main_{bot_index}'] = True

    print("Đang khởi tạo các luồng nền...", flush=True)
    THREAD_POOL.submit(periodic_save_loop)

    if auto_reboot_enabled:
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
    
    port = int(os.environ.get("PORT", 10001))
    print(f"Khởi động Farm Control Panel tại http://0.0.0.0:{port}", flush=True)
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
