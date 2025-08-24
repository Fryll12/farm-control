# PHIÊN BẢN ĐIỀU KHIỂN FARM - NÂNG CẤP VỚI DISCORD.PY-SELF - FULL CODE
import discord_self as discord
from discord_self.ext import commands, tasks
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
import asyncio
import aiohttp
import logging

# Tắt logging để giảm noise
logging.getLogger('discord_self').setLevel(logging.WARNING)

load_dotenv()

# --- CẤU HÌNH ---
main_token_alpha = os.getenv("MAIN_TOKEN")
other_main_tokens = os.getenv("MAIN_TOKENS").split(",") if os.getenv("MAIN_TOKENS") else []
sub_tokens = os.getenv("TOKENS").split(",") if os.getenv("TOKENS") else []
acc_names_str = os.getenv("SUB_ACC_NAMES")
sub_acc_names = [name.strip() for name in acc_names_str.split(',')] if acc_names_str else []
GREEK_ALPHABET = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta', 'Iota', 'Kappa', 'Lambda', 'Mu']

karuta_id = 646937666251915264
yoru_bot_id = 1311684840462225440

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
groups = {}
main_panel_settings = {
    "auto_grab_enabled_alpha": False, "heart_threshold_alpha": 15,
    "auto_grab_enabled_main_other": False, "heart_threshold_main_other": 50,
    "spam_message": "kcf", "spam_delay": 10
}

heart_sharing_data = {}
heart_data_lock = threading.Lock()

spam_executor = ThreadPoolExecutor(max_workers=20, thread_name_prefix="SpamWorker")
active_spam_tasks = set()

# --- DISCORD BOT CLASS (Đã chỉnh sửa cho self-bot) ---
class FarmBot(commands.Bot):
    def __init__(self, bot_type, bot_index, *args, **kwargs):
        super().__init__(
            command_prefix='!', 
            help_command=None,
            self_bot=True,
            *args, **kwargs
        )
        self.bot_type = bot_type
        self.bot_index = bot_index
        self.is_ready = False
        
    async def on_ready(self):
        self.is_ready = True
        bot_name = GREEK_ALPHABET[self.bot_index] if self.bot_type == 'main' and self.bot_index < len(GREEK_ALPHABET) else f"{self.bot_type.capitalize()} {self.bot_index}"
        print(f"User Account '{bot_name}' đã đăng nhập: {self.user}", flush=True)
        
    async def on_message(self, message):
        if message.author == self.user:
            return
        if message.author.bot:
            await self.handle_farm_grab(message)
        await self.process_commands(message)

    async def handle_farm_grab(self, message):
        channel_id = message.channel.id
        target_server = next((s for s in farm_servers if s.get('main_channel_id') == str(channel_id)), None)
        if not target_server:
            return

        if message.author.id == karuta_id and 'dropping 3' in message.content:
            last_drop_msg_id = message.id

            if self.bot_type == 'main' and self.bot_index == 0:
                is_card_grab_enabled, _, _ = self.get_grab_settings(target_server)
                ktb_channel_id = target_server.get('ktb_channel_id')
                if is_card_grab_enabled and ktb_channel_id:
                    asyncio.create_task(self.read_yoru_and_share_hearts(message.channel, last_drop_msg_id, target_server))

            elif self.bot_type == 'main' and self.bot_index > 0:
                await asyncio.sleep(0.6)
                await self.process_shared_hearts(channel_id)

            if event_grab_enabled and self.bot_type == 'main' and self.bot_index == 0:
                asyncio.create_task(self.check_farm_event(message.channel, last_drop_msg_id, target_server))

    def get_grab_settings(self, target_server):
        if self.bot_type == 'main' and self.bot_index == 0:
            return (target_server.get('auto_grab_enabled_alpha', False), 
                    target_server.get('heart_threshold_alpha', 15), 
                    {0: 0.2, 1: 1.2, 2: 2.0})
        elif self.bot_type == 'main':
            return (target_server.get('auto_grab_enabled_main_other', False), 
                    target_server.get('heart_threshold_main_other', 50), 
                    {0: 1.0, 1: 2.0, 2: 2.8})
        else:
            return False, 0, {}

    async def read_yoru_and_share_hearts(self, channel, drop_msg_id, target_server):
        try:
            await asyncio.sleep(0.6)
            messages = [msg async for msg in channel.history(limit=5)]
            for msg in messages:
                if msg.author.id == yoru_bot_id and msg.embeds:
                    desc = msg.embeds[0].description or ""
                    heart_numbers = [int(match.group(1)) if (match := re.search(r'♡(\d+)', line)) else 0 for line in desc.split('\n')[:3]]
                    if not any(heart_numbers): break
                    
                    with heart_data_lock:
                        heart_sharing_data[channel.id] = {
                            'hearts': heart_numbers,
                            'timestamp': time.time(),
                            'drop_msg_id': drop_msg_id,
                            'target_server': target_server,
                            'processed_by': {0}
                        }
                    
                    print(f"[ALPHA SHARED HEARTS: {target_server['name']}] Tim: {heart_numbers}", flush=True)
                    await self.execute_grab(channel, drop_msg_id, target_server, heart_numbers)
                    break
        except Exception as e:
            print(f"Lỗi đọc Yoru Bot: {e}", flush=True)

    async def process_shared_hearts(self, channel_id):
        # Đây là phiên bản cho phép nhiều bot cùng grab để dự phòng cooldown
        with heart_data_lock:
            if channel_id not in heart_sharing_data:
                return
            data = heart_sharing_data[channel_id]
        
        channel = self.get_channel(channel_id)
        if channel:
            await self.execute_grab(channel, data['drop_msg_id'], data['target_server'], data['hearts'])

    async def execute_grab(self, channel, drop_msg_id, target_server, heart_numbers):
        is_card_grab_enabled, heart_threshold, delays = self.get_grab_settings(target_server)
        ktb_channel_id = target_server.get('ktb_channel_id')

        if is_card_grab_enabled and ktb_channel_id and any(heart_numbers):
            max_num = max(heart_numbers)
            if max_num >= heart_threshold:
                max_index = heart_numbers.index(max_num)
                emoji = ["1️⃣", "2️⃣", "3️⃣"][max_index]
                delay = delays.get(max_index, 1.5)
                
                bot_name = GREEK_ALPHABET[self.bot_index] if self.bot_type == 'main' and self.bot_index < len(GREEK_ALPHABET) else f"{self.bot_type.capitalize()} {self.bot_index}"
                print(f"[FARM: {target_server['name']} | Acc {bot_name}] Grab -> {max_num} tim, delay {delay}s", flush=True)
                
                await asyncio.sleep(delay)
                try:
                    message = await channel.fetch_message(drop_msg_id)
                    await message.add_reaction(emoji)
                    await asyncio.sleep(2)
                    ktb_channel = self.get_channel(int(ktb_channel_id))
                    if ktb_channel:
                        await ktb_channel.send("kt b")
                except Exception as e:
                    print(f"Lỗi khi grab [{bot_name}]: {e}", flush=True)

    async def check_farm_event(self, channel, drop_msg_id, target_server):
        try:
            await asyncio.sleep(5)
            message = await channel.fetch_message(drop_msg_id)
            for reaction in message.reactions:
                if str(reaction.emoji) == '🍉':
                    print(f"[EVENT GRAB | FARM: {target_server['name']}] Phát hiện dưa hấu! Alpha Acc nhặt.", flush=True)
                    await message.add_reaction('🍉')
                    break
        except Exception as e:
            print(f"Lỗi kiểm tra event: {e}", flush=True)

def save_farm_settings():
    api_key = os.getenv("JSONBIN_API_KEY"); farm_bin_id = os.getenv("FARM_JSONBIN_BIN_ID")
    if not api_key or not farm_bin_id: return
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{farm_bin_id}"
    try:
        req = requests.put(url, json=farm_servers, headers=headers, timeout=10)
        if req.status_code == 200: print("[Farm Settings] Đã lưu cài đặt farm panels.", flush=True)
    except Exception as e: print(f"[Farm Settings] Lỗi khi lưu farm panels: {e}", flush=True)

def load_farm_settings():
    global farm_servers
    api_key = os.getenv("JSONBIN_API_KEY"); farm_bin_id = os.getenv("FARM_JSONBIN_BIN_ID")
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

def save_main_settings():
    api_key = os.getenv("JSONBIN_API_KEY"); bin_id = os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    settings = {
        'event_grab_enabled': event_grab_enabled, 'auto_reboot_enabled': auto_reboot_enabled, 
        'auto_reboot_delay': auto_reboot_delay, 'bot_active_states': bot_active_states,
        'last_reboot_cycle_time': last_reboot_cycle_time,
        'groups': groups, 'main_panel_settings': main_panel_settings
    }
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    try:
        req = requests.put(url, json=settings, headers=headers, timeout=10)
        if req.status_code == 200: print("[Settings] Đã lưu cài đặt chính.", flush=True)
    except Exception as e: print(f"[Settings] Lỗi khi lưu cài đặt chính: {e}", flush=True)

def load_main_settings():
    api_key = os.getenv("JSONBIN_API_KEY"); bin_id = os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: return
    headers = {'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
    try:
        req = requests.get(url, headers=headers, timeout=10)
        if req.status_code == 200:
            settings = req.json().get("record", {})
            if settings:
                globals().update(settings)
                if 'groups' not in globals() or not isinstance(globals()['groups'], dict): globals()['groups'] = {}
                if 'main_panel_settings' not in globals() or not isinstance(globals()['main_panel_settings'], dict):
                    globals()['main_panel_settings'] = {
                        "auto_grab_enabled_alpha": False, "heart_threshold_alpha": 15,
                        "auto_grab_enabled_main_other": False, "heart_threshold_main_other": 50,
                        "spam_message": "kcf", "spam_delay": 10
                    }
                print("[Settings] Đã tải cài đặt chính.", flush=True)
            else: save_main_settings()
    except Exception as e: print(f"[Settings] Lỗi khi tải cài đặt chính: {e}", flush=True)

def create_bot(token, bot_type, bot_index):
    bot = FarmBot(bot_type, bot_index)
    
    def run_it():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(bot.start(token))
        except Exception as e:
            print(f"Lỗi khi chạy bot {bot_type} {bot_index}: {e}", flush=True)

    thread = threading.Thread(target=run_it, daemon=True)
    thread.start()
    
    start_time = time.time()
    while not bot.is_ready and (time.time() - start_time) < 30:
        time.sleep(0.5)
        
    return bot

def execute_spam_task(task_data):
    try:
        task_id, channel_id, message, bots_to_use, inter_bot_delay = task_data
        for bot in bots_to_use:
            try:
                if bot.is_ready:
                    channel = bot.get_channel(int(channel_id))
                    if channel:
                        # Cách an toàn để gọi hàm async từ thread khác
                        asyncio.run_coroutine_threadsafe(channel.send(message), bot.loop)
                time.sleep(inter_bot_delay)
            except Exception as e:
                print(f"[SPAM ERROR] Bot spam failed: {e}", flush=True)
    finally:
        if task_id in active_spam_tasks:
            active_spam_tasks.remove(task_id)

def optimized_spam_loop():
    while True:
        try:
            now = time.time()
            for group_name, group_data in groups.items():
                if not group_data.get('spam_enabled', False): continue
                farms_in_group = [s for s in farm_servers if s.get('group') == group_name]
                if not farms_in_group: continue
                with bots_lock:
                    account_indices = group_data.get('spam_accounts', [])
                    bots_to_use = [sub_bots[i] for i in account_indices if i < len(sub_bots) and bot_active_states.get(f'sub_{i}', False) and sub_bots[i].is_ready]
                if not bots_to_use: continue
                
                spam_batch = []
                for server in farms_in_group:
                    server_id = server.get('id', 'unknown_farm')
                    if server.get('spam_message') and server.get('spam_channel_id'):
                        last_spam = server.get('last_spam_time', 0)
                        delay = server.get('spam_delay', 10)
                        task_id = f"spam_{server_id}_{group_name}"
                        if (now - last_spam) >= delay and task_id not in active_spam_tasks:
                            spam_batch.append({'task_id': task_id, 'channel_id': server['spam_channel_id'], 'message': server['spam_message'], 'server': server})
                
                for spam_task in spam_batch:
                    task_id = spam_task['task_id']
                    active_spam_tasks.add(task_id)
                    spam_task['server']['last_spam_time'] = now
                    task_data = (task_id, spam_task['channel_id'], spam_task['message'], bots_to_use.copy(), 2)
                    spam_executor.submit(execute_spam_task, task_data)
            time.sleep(2)
        except Exception as e: 
            print(f"[ERROR in optimized_spam_loop] {e}", flush=True)
            time.sleep(5)

def reboot_bot(target_id):
    with bots_lock:
        bot_type, index_str = target_id.split('_')
        index = int(index_str)
        if bot_type == 'main':
            if index < len(main_bots):
                try: asyncio.run(main_bots[index].close())
                except: pass
                token = main_token_alpha if index == 0 else other_main_tokens[index - 1]
                main_bots[index] = create_bot(token, 'main', index)
                print(f"[Reboot] Main Acc {index} đã khởi động lại.", flush=True)
        elif bot_type == 'sub':
            if index < len(sub_bots):
                try: asyncio.run(sub_bots[index].close())
                except: pass
                sub_bots[index] = create_bot(sub_tokens[index], 'sub', index)
                print(f"[Reboot] Sub Acc {index} đã khởi động lại.", flush=True)

def auto_reboot_loop():
    global last_reboot_cycle_time
    while not auto_reboot_stop_event.is_set():
        try:
            if auto_reboot_enabled and (time.time() - last_reboot_cycle_time) >= auto_reboot_delay:
                print("[Reboot] Bắt đầu chu kỳ reboot tự động...", flush=True)
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
            if auto_reboot_stop_event.wait(timeout=60): break
        except Exception as e: print(f"[ERROR in auto_reboot_loop] {e}", flush=True); time.sleep(60)

def periodic_save_loop():
    while True:
        time.sleep(300)
        save_farm_settings()
        save_main_settings()

def heart_data_cleanup():
    while True:
        time.sleep(15)
        with heart_data_lock:
            current_time = time.time()
            expired_keys = [k for k, v in heart_sharing_data.items() if current_time - v.get('timestamp', 0) > 30]
            for key in expired_keys:
                if key in heart_sharing_data:
                    del heart_sharing_data[key]

app = Flask(__name__)

HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Karuta Farm Control - Self-Bot</title>
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
    </style>
</head>
<body>
    <div class="container">
        <div class="header"><h1 class="title">FARM CONTROL PANEL - SELF BOT</h1></div>
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
                    <h4><i class="fas fa-robot"></i> Spam Accounts for this Group</h4>
                    <div class="spam-account-list">
                        {% for i, sub_name in sub_acc_names %}
                        <div class="spam-account-item"><label><input type="checkbox" class="spam-account-checkbox" value="{{ i }}" {% if i in data.get('spam_accounts', []) %}checked{% endif %}> {{ sub_name }}</label></div>
                        {% endfor %}
                    </div>
                    <h4 style="margin-top: 20px;"><i class="fas fa-network-wired"></i> Farms in this Group</h4>
                    <div class="farms-list">
                        {% for server in farm_servers %}{% if server.group == name %}
                        <div class="farm-in-group"><span>{{ server.name }}</span><select class="farm-group-selector" data-farm-id="{{ server.id }}">
                            {% for g_name in groups %}<option value="{{ g_name }}" {% if g_name == name %}selected{% endif %}>{{ g_name }}</option>{% endfor %}
                        </select></div>
                        {% endif %}{% endfor %}
                    </div>
                </div>
                {% endfor %}
            </div>
        </div>
        <div class="panel">
            <h2><i class="fas fa-plus-circle"></i> Add & Manage Farm Panels</h2>
            <div id="farm-grid" class="main-grid">
                {% for server in farm_servers %}
                <div class="panel" style="border-left: 5px solid var(--hot-pink);">
                    <button class="delete-btn delete-farm-btn" data-farm-id="{{ server.id }}" style="position:absolute; top:10px; right: 10px;">XÓA</button>
                    <h3>{{ server.name }} (Group: {{ server.group or 'None' }})</h3>
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

    document.getElementById('auto-reboot-toggle-btn').addEventListener('click', () => postData('/api/reboot_toggle_auto', { delay: document.getElementById('auto-reboot-delay').value }).then(r => r && r.reload ? null : location.reload()));
    document.getElementById('auto-reboot-delay').addEventListener('change', () => postData('/api/reboot_update_delay', { delay: document.getElementById('auto-reboot-delay').value }));
    document.getElementById('event-grab-toggle-btn').addEventListener('click', () => postData('/api/event_grab_toggle', {}).then(r => r && r.reload ? null : location.reload()));
    document.getElementById('bot-status-list').addEventListener('click', e => { if (e.target.matches('.btn-toggle-state')) { postData('/api/toggle_bot_state', { target: e.target.dataset.target }); setTimeout(fetchStatus, 500); }});

    const mainPanel = document.querySelector('.main-panel');
    mainPanel.addEventListener('change', e => { if (e.target.matches('.main-panel-input')) { const data = {}; data[e.target.dataset.field] = e.target.value; postData('/api/main_panel/update', data); } });
    mainPanel.addEventListener('click', e => { if (e.target.matches('.main-panel-toggle')) { const data = {}; data[e.target.dataset.field] = 'toggle'; postData('/api/main_panel/update', data).then(() => location.reload()); } });
    document.getElementById('sync-from-main-btn').addEventListener('click', () => {
        const targets = Array.from(document.getElementById('sync-target-groups').selectedOptions).map(opt => opt.value);
        if (targets.length === 0) { showMsg('Vui lòng chọn ít nhất một group để đồng bộ.'); return; }
        postData('/api/main_panel/sync', { target_groups: targets });
    });

    document.getElementById('add-group-btn').addEventListener('click', () => { const name = document.getElementById('new-group-name').value; if (name) postData('/api/groups/add', { name }); });
    const groupsContainer = document.getElementById('groups-container');
    groupsContainer.addEventListener('click', e => {
        const groupDiv = e.target.closest('.group-container');
        if (!groupDiv) return;
        const groupName = groupDiv.dataset.groupName;
        if (e.target.matches('.delete-group-btn')) { if (confirm(`Xóa group "${groupName}"?`)) postData('/api/groups/delete', { name: groupName }); }
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

    document.getElementById('add-farm-btn').addEventListener('click', () => { const name = prompt("Nhập tên farm mới:"); if (name) postData('/api/farm/add', { name }); });
    const farmGrid = document.getElementById('farm-grid');
    farmGrid.addEventListener('click', e => { if (e.target.matches('.delete-farm-btn')) { if (confirm('Xóa farm này?')) postData('/api/farm/delete', { farm_id: e.target.dataset.farmId }); } });
    farmGrid.addEventListener('change', e => { if (e.target.matches('.farm-channel-input')) { const data = { farm_id: e.target.dataset.farmId }; data[e.target.dataset.field] = e.target.value; postData('/api/farm/update', data); } });

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
    setInterval(fetchStatus, 10000);
});
</script>
</body>
</html>
"""

@app.route("/")
def index():
    reboot_action, reboot_button_class = ("DISABLE REBOOT", "btn-danger") if auto_reboot_enabled else ("ENABLE REBOOT", "btn-success")
    event_grab_action, event_grab_button_class = ("DISABLE EVENT GRAB", "btn-danger") if event_grab_enabled else ("ENABLE EVENT GRAB", "btn-success")
    sorted_farms = sorted(farm_servers, key=lambda x: x.get('group', 'zzzz'))
    return render_template_string(HTML_TEMPLATE,
        auto_reboot_delay=auto_reboot_delay, reboot_action=reboot_action, reboot_button_class=reboot_button_class,
        event_grab_action=event_grab_action, event_grab_button_class=event_grab_button_class,
        farm_servers=sorted_farms, groups=groups, sub_acc_names=list(enumerate(sub_acc_names)),
        main_panel=main_panel_settings
    )

@app.route("/status")
def status():
    bot_status_list = []
    with bots_lock:
        for i, bot in enumerate(main_bots):
            name = GREEK_ALPHABET[i] if i < len(GREEK_ALPHABET) else f"Main {i}"
            bot_status_list.append({"name": name, "reboot_id": f"main_{i}", "is_active": bot_active_states.get(f'main_{i}', False)})
        for i, bot in enumerate(sub_bots):
            name = sub_acc_names[i] if i < len(sub_acc_names) else f"Sub {i}"
            bot_status_list.append({"name": name, "reboot_id": f"sub_{i}", "is_active": bot_active_states.get(f'sub_{i}', False)})
    return jsonify({'bot_statuses': bot_status_list})

@app.route("/api/groups/add", methods=['POST'])
def api_group_add():
    name = request.json.get('name')
    if name and name not in groups:
        groups[name] = {'spam_enabled': False, 'spam_accounts': []}
        save_main_settings()
        return jsonify({'status': 'success', 'message': f'Đã tạo group "{name}".', 'reload': True})
    return jsonify({'status': 'error', 'message': 'Tên group không hợp lệ hoặc đã tồn tại.'}), 400

@app.route("/api/groups/delete", methods=['POST'])
def api_group_delete():
    name = request.json.get('name')
    if name and name in groups:
        del groups[name]
        for server in farm_servers:
            if server.get('group') == name: server['group'] = None
        save_main_settings(); save_farm_settings()
        return jsonify({'status': 'success', 'message': f'Đã xóa group "{name}".', 'reload': True})
    return jsonify({'status': 'error', 'message': 'Không tìm thấy group.'}), 404

@app.route("/api/groups/update", methods=['POST'])
def api_group_update():
    data = request.json; name = data.get('name')
    if name and name in groups:
        if 'spam_enabled' in data:
            groups[name]['spam_enabled'] = not groups[name].get('spam_enabled', False)
        if 'spam_accounts' in data:
            groups[name]['spam_accounts'] = data['spam_accounts']
        save_main_settings()
        return jsonify({'status': 'success', 'message': f'Đã cập nhật group "{name}".'})
    return jsonify({'status': 'error', 'message': 'Không tìm thấy group.'}), 404

@app.route("/api/main_panel/update", methods=['POST'])
def api_main_panel_update():
    data = request.json
    for key, value in data.items():
        if key in main_panel_settings:
            if value == 'toggle': main_panel_settings[key] = not main_panel_settings[key]
            else: main_panel_settings[key] = type(main_panel_settings[key])(value)
    save_main_settings()
    return jsonify({'status': 'success', 'message': 'Đã cập nhật Main Panel.'})

@app.route("/api/main_panel/sync", methods=['POST'])
def api_main_panel_sync():
    target_groups = request.json.get('target_groups', [])
    if not target_groups: return jsonify({'status': 'error', 'message': 'Chưa chọn group mục tiêu.'}), 400
    sync_count = 0
    for server in farm_servers:
        if server.get('group') in target_groups:
            server['auto_grab_enabled_alpha'] = main_panel_settings['auto_grab_enabled_alpha']
            server['heart_threshold_alpha'] = main_panel_settings['heart_threshold_alpha']
            server['auto_grab_enabled_main_other'] = main_panel_settings['auto_grab_enabled_main_other']
            server['heart_threshold_main_other'] = main_panel_settings['heart_threshold_main_other']
            server['spam_message'] = main_panel_settings['spam_message']
            server['spam_delay'] = main_panel_settings['spam_delay']
            sync_count += 1
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Đã đồng bộ cài đặt cho {sync_count} farm.'})

@app.route("/api/farm/add", methods=['POST'])
def api_farm_add():
    name = request.json.get('name')
    if not name: return jsonify({'status': 'error', 'message': 'Tên farm là bắt buộc.'}), 400
    default_group = next(iter(groups), None)
    new_server = {
        "id": f"farm_{int(time.time())}", "name": name, "group": default_group,
        "main_channel_id": "", "ktb_channel_id": "", "spam_channel_id": "",
        "auto_grab_enabled_alpha": False, "heart_threshold_alpha": 15,
        "auto_grab_enabled_main_other": False, "heart_threshold_main_other": 50,
        "spam_enabled": False, "spam_message": "kcf", "spam_delay": 10, "last_spam_time": 0
    }
    farm_servers.append(new_server); save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Farm "{name}" đã được thêm.', 'reload': True})

@app.route("/api/farm/delete", methods=['POST'])
def api_farm_delete():
    global farm_servers
    farm_id = request.json.get('farm_id')
    farm_servers = [s for s in farm_servers if s.get('id') != farm_id]; save_farm_settings()
    return jsonify({'status': 'success', 'message': 'Farm đã được xóa.', 'reload': True})

@app.route("/api/farm/update", methods=['POST'])
def api_farm_update():
    data = request.json; farm_id = data.get('farm_id')
    server = next((s for s in farm_servers if s.get('id') == farm_id), None)
    if not server: return jsonify({'status': 'error', 'message': 'Không tìm thấy farm.'}), 404
    for key in ['main_channel_id', 'ktb_channel_id', 'spam_channel_id', 'group']:
        if key in data: server[key] = data[key]
    save_farm_settings()
    if 'group' in data: return jsonify({'status': 'success', 'message': 'Đã chuyển group cho farm.', 'reload': True})
    return jsonify({'status': 'success', 'message': 'Đã cập nhật kênh cho farm.'})

@app.route("/api/reboot_toggle_auto", methods=['POST'])
def api_reboot_toggle_auto():
    global auto_reboot_enabled, auto_reboot_thread, auto_reboot_stop_event, auto_reboot_delay
    new_delay = int(request.json.get("delay", auto_reboot_delay))
    auto_reboot_delay = new_delay
    auto_reboot_enabled = not auto_reboot_enabled
    if auto_reboot_enabled and (auto_reboot_thread is None or not auto_reboot_thread.is_alive()):
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
    elif not auto_reboot_enabled and auto_reboot_stop_event: 
        auto_reboot_stop_event.set(); auto_reboot_thread = None
    save_main_settings()
    return jsonify({'status': 'success', 'message': f'Auto Reboot đã {"BẬT" if auto_reboot_enabled else "TẮT"} với delay {auto_reboot_delay}s.'})

@app.route("/api/reboot_update_delay", methods=['POST'])
def api_reboot_update_delay():
    global auto_reboot_delay
    new_delay = int(request.json.get("delay", 3600))
    auto_reboot_delay = new_delay
    save_main_settings()
    return jsonify({'status': 'success', 'message': f'Đã cập nhật Auto Reboot delay thành {auto_reboot_delay}s.'})

@app.route("/api/toggle_bot_state", methods=['POST'])
def api_toggle_bot_state():
    target = request.json.get('target')
    if target in bot_active_states:
        bot_active_states[target] = not bot_active_states[target]
        state_text = "ONLINE" if bot_active_states[target] else "OFFLINE"
        msg = f"Bot {target.upper()} đã được đặt thành {state_text}."
        save_main_settings()
        return jsonify({'status': 'success', 'message': msg})
    return jsonify({'status': 'error', 'message': 'Không tìm thấy bot.'})

@app.route("/api/event_grab_toggle", methods=['POST'])
def api_event_grab_toggle():
    global event_grab_enabled
    event_grab_enabled = not event_grab_enabled
    save_main_settings()
    return jsonify({'status': 'success', 'message': f"Event Grab đã {'BẬT' if event_grab_enabled else 'TẮT'}"})

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    load_farm_settings()
    load_main_settings()
    print("Đang khởi tạo các user account với Discord.py-self...", flush=True)
    
    with bots_lock:
        if main_token_alpha:
            main_bots.append(create_bot(main_token_alpha, 'main', 0))
            if 'main_0' not in bot_active_states: bot_active_states['main_0'] = True
        for i, token in enumerate(other_main_tokens):
            if token.strip():
                main_bots.append(create_bot(token.strip(), 'main', i + 1))
                if f'main_{i+1}' not in bot_active_states: bot_active_states[f'main_{i+1}'] = True
        for i, token in enumerate(sub_tokens):
            if token.strip():
                sub_bots.append(create_bot(token.strip(), 'sub', i))
                if f'sub_{i}' not in bot_active_states: bot_active_states[f'sub_{i}'] = True

    print("Đang khởi tạo các luồng nền...", flush=True)
    threading.Thread(target=optimized_spam_loop, daemon=True).start()
    threading.Thread(target=periodic_save_loop, daemon=True).start()
    threading.Thread(target=heart_data_cleanup, daemon=True).start()

    if auto_reboot_enabled and (auto_reboot_thread is None or not auto_reboot_thread.is_alive()):
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
    
    port = int(os.environ.get("PORT", 10001))
    print(f"Khởi động Farm Control Panel tại http://0.0.0.0:{port}", flush=True)
    
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
