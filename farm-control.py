# PHIÊN BẢN ĐIỀU KHIỂN FARM - TỐI ƯU HÓA CHỈ GRAB (discord.py-selfbot)
import discord
import threading
import time
import os
import random
import re
import requests
import json
import gc
import asyncio
import logging
from datetime import datetime
from flask import Flask, request, render_template_string, jsonify
from dotenv import load_dotenv

# Cấu hình logging chi tiết
logging.basicConfig(
    level=logging.DEBUG,  # Thay đổi từ INFO sang DEBUG để có thêm log
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("farm_bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

load_dotenv()

# --- CẤU HÌNH ---
main_token_alpha = os.getenv("MAIN_TOKEN")
other_main_tokens = os.getenv("MAIN_TOKENS").split(",") if os.getenv("MAIN_TOKENS") else []
GREEK_ALPHABET = ['Alpha', 'Beta', 'Gamma', 'Delta', 'Epsilon', 'Zeta', 'Eta', 'Theta', 'Iota', 'Kappa', 'Lambda', 'Mu']

karuta_id = 646937666251915264
yoru_bot_id = 1311684840462225440

# --- BIẾN TRẠNG THÁI ---
main_bots = []
event_grab_enabled = False
auto_reboot_enabled = False
auto_reboot_delay = 3600
last_reboot_cycle_time = 0
auto_reboot_stop_event = threading.Event()
auto_reboot_thread = None
bots_lock = threading.RLock()
server_start_time = time.time()
bot_active_states = {}
bot_reconnect_attempts = {}

# Dữ liệu mới
farm_servers = []
main_panel_settings = {
    "auto_grab_enabled_alpha": False, "heart_threshold_alpha": 15,
    "auto_grab_enabled_main_other": False, "heart_threshold_main_other": 50,
}

# Shared grab queue để tránh đọc trùng lặp
grab_queue = []
grab_queue_lock = threading.Lock()

# Thêm biến đếm số drop đã xử lý
processed_drops = 0

# --- HÀM LƯU VÀ TẢI CÀI ĐẶT ---
def save_farm_settings():
    api_key = os.getenv("JSONBIN_API_KEY")
    farm_bin_id = os.getenv("FARM_JSONBIN_BIN_ID")
    if not api_key or not farm_bin_id: 
        logger.warning("JSONBIN API key hoặc bin ID không được cấu hình")
        return
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{farm_bin_id}"
    try:
        req = requests.put(url, json=farm_servers, headers=headers, timeout=10)
        if req.status_code == 200: 
            logger.info("Đã lưu cài đặt farm panels")
    except Exception as e: 
        logger.error(f"Lỗi khi lưu farm panels: {e}")

def load_farm_settings():
    global farm_servers
    api_key = os.getenv("JSONBIN_API_KEY")
    farm_bin_id = os.getenv("FARM_JSONBIN_BIN_ID")
    if not api_key or not farm_bin_id: 
        logger.warning("JSONBIN API key hoặc bin ID không được cấu hình")
        return
    headers = {'X-Master-Key': api_key, 'X-Bin-Meta': 'false'}
    url = f"https://api.jsonbin.io/v3/b/{farm_bin_id}/latest"
    try:
        req = requests.get(url, headers=headers, timeout=10)
        if req.status_code == 200:
            data = req.json()
            if isinstance(data, list): farm_servers = data
            logger.info(f"Đã tải {len(farm_servers)} cấu hình farm")
    except Exception as e: 
        logger.error(f"Lỗi khi tải farm settings: {e}")
        farm_servers = []
        
def save_main_settings():
    api_key = os.getenv("JSONBIN_API_KEY")
    bin_id = os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: 
        logger.warning("JSONBIN API key hoặc bin ID không được cấu hình")
        return
    settings = {
        'event_grab_enabled': event_grab_enabled, 
        'auto_reboot_enabled': auto_reboot_enabled, 
        'auto_reboot_delay': auto_reboot_delay, 
        'bot_active_states': bot_active_states,
        'last_reboot_cycle_time': last_reboot_cycle_time,
        'main_panel_settings': main_panel_settings
    }
    headers = {'Content-Type': 'application/json', 'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}"
    try:
        req = requests.put(url, json=settings, headers=headers, timeout=10)
        if req.status_code == 200: 
            logger.info("Đã lưu cài đặt chính")
    except Exception as e: 
        logger.error(f"Lỗi khi lưu cài đặt chính: {e}")

def load_main_settings():
    global event_grab_enabled, auto_reboot_enabled, auto_reboot_delay, bot_active_states, last_reboot_cycle_time, main_panel_settings
    api_key = os.getenv("JSONBIN_API_KEY")
    bin_id = os.getenv("JSONBIN_BIN_ID")
    if not api_key or not bin_id: 
        logger.warning("JSONBIN API key hoặc bin ID không được cấu hình")
        return
    headers = {'X-Master-Key': api_key}
    url = f"https://api.jsonbin.io/v3/b/{bin_id}/latest"
    try:
        req = requests.get(url, headers=headers, timeout=10)
        if req.status_code == 200:
            settings = req.json().get("record", {})
            if settings:
                event_grab_enabled = settings.get('event_grab_enabled', False)
                auto_reboot_enabled = settings.get('auto_reboot_enabled', False)
                auto_reboot_delay = settings.get('auto_reboot_delay', 3600)
                bot_active_states = settings.get('bot_active_states', {})
                last_reboot_cycle_time = settings.get('last_reboot_cycle_time', 0)
                main_panel_settings = settings.get('main_panel_settings', main_panel_settings)
                logger.info("Đã tải cài đặt chính")
            else: 
                save_main_settings()
    except Exception as e: 
        logger.error(f"Lỗi khi tải cài đặt chính: {e}")

# --- LOGIC BOT FARM TỐI ƯU ---
def get_grab_settings(target_server, bot_type, bot_index):
    if bot_type == 'main' and bot_index == 0:  # Alpha Bot
        return (target_server.get('auto_grab_enabled_alpha', False), 
                target_server.get('heart_threshold_alpha', 15), 
                {0: 0.2, 1: 1.2, 2: 2.0})
    else:  # Other Main Bots
        return (target_server.get('auto_grab_enabled_main_other', False), 
                target_server.get('heart_threshold_main_other', 50), 
                {0: 1.0, 1: 2.0, 2: 2.8})

async def handle_alpha_message(bot, message):
    """Chỉ Alpha bot xử lý message và phân phối grab cho các bot khác"""
    global processed_drops
    
    # Log tất cả message từ Karuta bot
    if message.author.id == karuta_id:
        logger.debug(f"Nhận message từ Karuta bot: {message.content[:100]}...")
    
    channel_id = str(message.channel.id)
    target_server = next((s for s in farm_servers if s.get('main_channel_id') == channel_id), None)
    
    # Log nếu không tìm thấy server
    if not target_server:
        logger.debug(f"Không tìm thấy server cho channel ID: {channel_id}")
        return
    
    logger.debug(f"Tìm thấy server: {target_server['name']} cho channel ID: {channel_id}")

    if message.author.id == karuta_id and 'dropping' in message.content:
        processed_drops += 1
        logger.info(f"[DROP #{processed_drops}] Phát hiện drop từ Karuta bot trong server {target_server['name']}")
        last_drop_msg_id = message.id
        
        # Chỉ Alpha đọc Yoru Bot và phân phối grab
        async def process_grab_distribution():
            logger.debug(f"Bắt đầu xử lý drop ID: {last_drop_msg_id}")
            await asyncio.sleep(0.6)
            try:
                messages = [msg async for msg in message.channel.history(limit=5)]
                logger.debug(f"Đọc được {len(messages)} messages gần nhất")
                
                for msg_item in messages:
                    logger.debug(f"Kiểm tra message từ {msg_item.author.id} (Yoru: {yoru_bot_id})")
                    if msg_item.author.id == yoru_bot_id and msg_item.embeds:
                        logger.debug("Tìm thấy message từ Yoru bot với embeds")
                        desc = msg_item.embeds[0].description
                        logger.debug(f"Nội dung embed: {desc[:100]}...")
                        
                        heart_numbers = [int(match.group(1)) if (match := re.search(r'♡(\d+)', line)) else 0 
                                       for line in desc.split('\n')[:3]]
                        logger.info(f"Tim values: {heart_numbers}")
                        
                        if not any(heart_numbers): 
                            logger.debug("Không tìm thấy giá trị tim nào")
                            break
                        
                        # Phân phối grab cho các bot
                        with grab_queue_lock:
                            grab_queue.append({
                                'channel_id': channel_id,
                                'message_id': last_drop_msg_id,
                                'heart_numbers': heart_numbers,
                                'target_server': target_server,
                                'timestamp': time.time()
                            })
                            logger.info(f"Đã thêm drop vào queue, queue length: {len(grab_queue)}")
                        break
                else:
                    logger.debug("Không tìm thấy message từ Yoru bot trong 5 messages gần nhất")
            except Exception as e: 
                logger.error(f"Lỗi đọc Yoru Bot: {e}")
            
            # Event grab chỉ Alpha làm
            if event_grab_enabled:
                logger.debug("Event grab được bật, kiểm tra event...")
                async def check_farm_event():
                    try:
                        await asyncio.sleep(5)
                        full_msg_obj = await message.channel.fetch_message(last_drop_msg_id)
                        if full_msg_obj.reactions and any(r.emoji == '🍉' for r in full_msg_obj.reactions):
                            logger.info(f"[EVENT GRAB | FARM: {target_server['name']}] Phát hiện dưa hấu! Alpha Bot nhặt.")
                            await full_msg_obj.add_reaction("🍉")
                        else:
                            logger.debug("Không tìm thấy reaction dưa hấu")
                    except Exception as e: 
                        logger.error(f"Lỗi kiểm tra event: {e}")
                
                asyncio.create_task(check_farm_event())
        
        asyncio.create_task(process_grab_distribution())

async def grab_processor_loop():
    """Vòng lặp xử lý grab queue"""
    logger.info("Bắt đầu grab processor loop")
    while True:
        try:
            current_time = time.time()
            with grab_queue_lock:
                # Xóa grab cũ (>30s)
                old_count = len(grab_queue)
                grab_queue[:] = [g for g in grab_queue if current_time - g['timestamp'] < 30]
                if old_count != len(grab_queue):
                    logger.debug(f"Đã xóa {old_count - len(grab_queue)} grab cũ")
                
                if grab_queue:
                    grab_data = grab_queue.pop(0)
                    logger.debug(f"Lấy grab từ queue, còn lại: {len(grab_queue)}")
                else:
                    grab_data = None
            
            if grab_data:
                # Lấy dữ liệu ra các biến cục bộ ngay lập tức
                current_heart_numbers = grab_data['heart_numbers']
                current_max_num = max(current_heart_numbers)
                current_target_server = grab_data['target_server']
                current_grab_data = grab_data
                
                logger.info(f"Xử lý grab với tim values: {current_heart_numbers}, max: {current_max_num}")
                
                # Xử lý grab cho từng bot
                with bots_lock:
                    for bot_index, bot in enumerate(main_bots):
                        if not bot_active_states.get(f'main_{bot_index}', False):
                            logger.debug(f"Bot {bot_index} không active, bỏ qua")
                            continue
                            
                        is_enabled, threshold, delays = get_grab_settings(current_target_server, 'main', bot_index)
                        logger.debug(f"Bot {bot_index}: enabled={is_enabled}, threshold={threshold}")
                        
                        if is_enabled and current_max_num >= threshold:
                            max_index = current_heart_numbers.index(current_max_num)
                            emoji = ["1️⃣", "2️⃣", "3️⃣"][max_index]
                            delay = delays.get(max_index, 1.5)
                            actual_delay = delay
                            
                            bot_name = GREEK_ALPHABET[bot_index] if bot_index < len(GREEK_ALPHABET) else f'Main {bot_index}'
                            logger.info(f"Bot {bot_name} sẽ grab {emoji} với delay {actual_delay}s")
                            
                            # Hàm grab_action giờ nhận thêm dữ liệu cần thiết
                            async def grab_action(bot_ref, bot_idx, g_data, s_config, h_num, e):
                                try:
                                    # Sử dụng dữ liệu được truyền vào
                                    channel = bot_ref.get_channel(int(g_data['channel_id']))
                                    message = await channel.fetch_message(g_data['message_id'])
                                    await message.add_reaction(e)
                                    
                                    ktb_channel_id = s_config.get('ktb_channel_id')
                                    if ktb_channel_id:
                                        await asyncio.sleep(2)
                                        ktb_channel = bot_ref.get_channel(int(ktb_channel_id))
                                        await ktb_channel.send("kt b")
                                    
                                    bot_name = GREEK_ALPHABET[bot_idx] if bot_idx < len(GREEK_ALPHABET) else f'Main {bot_idx}'
                                    logger.info(f"[FARM: {s_config['name']} | Bot {bot_name}] Grab -> {h_num} tim, delay {actual_delay}s")
                                except Exception as e:
                                    logger.error(f"Lỗi grab bot {bot_idx}: {e}")
                            
                            # Truyền dữ liệu vào Task
                            asyncio.create_task(grab_action(bot, bot_index, current_grab_data, current_target_server, current_max_num, emoji))
                        else:
                            logger.debug(f"Bot {bot_index} không đủ điều kiện grab (enabled={is_enabled}, max={current_max_num}, threshold={threshold})")
            
            await asyncio.sleep(0.1)  # Giảm CPU usage
        except Exception as e:
            logger.error(f"[ERROR in grab_processor_loop] {e}")
            await asyncio.sleep(1)

def create_bot(token, bot_type, bot_index):
    try:
        logger.info(f"Tạo bot {bot_type} {bot_index}")
        # Tạo event loop riêng cho mỗi bot
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        # Khởi tạo bot với intents cần thiết
        intents = discord.Intents.default()
        intents.messages = True
        intents.message_content = True
        intents.reactions = True
        
        # Sử dụng SelfBot từ discord.py-selfbot
        bot = discord.Client(intents=intents, self_bot=True)
        
        @bot.event
        async def on_ready():
            logger.info(f"Bot '{bot_type.capitalize()} {bot_index}' đã đăng nhập: {bot.user}")
            # Reset số lần thử kết nối lại khi đăng nhập thành công
            bot_reconnect_attempts[f'{bot_type}_{bot_index}'] = 0
            # Force garbage collection sau khi connect
            gc.collect()

        @bot.event
        async def on_message(message):
            # Log tất cả message để debug
            if message.author.id == karuta_id:
                logger.debug(f"Alpha bot nhận message từ Karuta: {message.content[:50]}...")
            
            # Chỉ Alpha bot xử lý message
            if bot_type == 'main' and bot_index == 0:
                await handle_alpha_message(bot, message)
        
        @bot.event
        async def on_disconnect():
            logger.warning(f"Bot {bot_type} {bot_index} đã mất kết nối")
            # Tăng số lần thử kết nối lại
            bot_key = f'{bot_type}_{bot_index}'
            bot_reconnect_attempts[bot_key] = bot_reconnect_attempts.get(bot_key, 0) + 1
            
            # Nếu thử quá 5 lần, tạm dừng bot
            if bot_reconnect_attempts[bot_key] > 5:
                logger.error(f"Bot {bot_type} {bot_index} đã thử kết nối lại quá 5 lần. Tạm dừng bot.")
                bot_active_states[f'{bot_type}_{bot_index}'] = False
                save_main_settings()
                return
                
            # Thử kết nối lại sau 30 giây
            await asyncio.sleep(30)
            try:
                logger.info(f"Thử kết nối lại bot {bot_type} {bot_index}...")
                await bot.start(token)
            except Exception as e:
                logger.error(f"Không thể kết nối lại bot {bot_type} {bot_index}: {e}")
        
        # Lưu event loop để có thể đóng sau này
        bot.loop = loop
        
        # Chạy bot trong thread riêng
        def run_bot():
            try:
                loop.run_until_complete(bot.start(token))
            except Exception as e:
                logger.error(f"Lỗi chạy bot {bot_type} {bot_index}: {e}")
                # Thử kết nối lại sau 30 giây
                time.sleep(30)
                try:
                    loop.run_until_complete(bot.start(token))
                except Exception as e2:
                    logger.error(f"Lỗi kết nối lại bot {bot_type} {bot_index}: {e2}")
        
        threading.Thread(target=run_bot, daemon=True).start()
        return bot
    except Exception as e:
        logger.error(f"Lỗi tạo bot {bot_type} {bot_index}: {e}")
        return None

# --- REBOOT FUNCTIONS ---
def reboot_bot(target_id):
    with bots_lock:
        bot_type, index_str = target_id.split('_')
        index = int(index_str)
        if bot_type == 'main' and index < len(main_bots):
            try: 
                if main_bots[index]:
                    # Đóng bot cũ
                    try:
                        asyncio.run_coroutine_threadsafe(main_bots[index].close(), main_bots[index].loop)
                        main_bots[index].loop.stop()
                    except Exception as e:
                        logger.error(f"Lỗi khi đóng bot {target_id}: {e}")
            except: 
                pass
            finally:
                token = main_token_alpha if index == 0 else other_main_tokens[index - 1]
                main_bots[index] = create_bot(token, 'main', index)
                logger.info(f"Main Bot {index} đã khởi động lại")
                # Force cleanup
                gc.collect()

def auto_reboot_loop():
    global last_reboot_cycle_time
    while not auto_reboot_stop_event.is_set():
        try:
            if auto_reboot_enabled and (time.time() - last_reboot_cycle_time) >= auto_reboot_delay:
                logger.info("Bắt đầu chu kỳ reboot tự động...")
                # Bỏ lock ở đây để tránh giữ khóa quá lâu
                for i in range(len(main_bots)):
                    # Kiểm tra trạng thái active trước khi reboot
                    if bot_active_states.get(f'main_{i}', False): 
                        reboot_bot(f'main_{i}') # Hàm reboot_bot đã có lock riêng của nó
                        time.sleep(5)
                
                last_reboot_cycle_time = time.time()
                save_main_settings()  # Lưu thời gian reboot mới
            if auto_reboot_stop_event.wait(timeout=60): 
                break
        except Exception as e: 
            logger.error(f"[ERROR in auto_reboot_loop] {e}")
            time.sleep(60)
    logger.info("Luồng tự động reboot đã dừng")

def periodic_save_loop():
    while True:
        time.sleep(300)
        logger.info("Bắt đầu lưu định kỳ...")
        save_farm_settings()
        save_main_settings()
        # Cleanup memory
        gc.collect()

app = Flask(__name__)

# --- GIAO DIỆN WEB TỐI ƯU (Giữ nguyên HTML_TEMPLATE từ code gốc) ---
HTML_TEMPLATE = """
<!DOCTYPE html>
<html lang="vi">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Karuta Farm Control - Optimized</title>
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.4.0/css/all.min.css">
    <link href="https://fonts.googleapis.com/css2?family=Orbitron:wght@400;700&family=Courier+Prime:wght@400;700&family=Nosifer&display=swap" rel="stylesheet">
    <style>
        :root {
            --primary-bg: #0a0a0a; --secondary-bg: #1a1a1a; --panel-bg: #111111; --border-color: #333333;
            --blood-red: #8b0000; --necro-green: #228b22; --shadow-cyan: #008b8b; --text-primary: #f0f0f0;
            --text-secondary: #cccccc; --hot-pink: #FF69B4; --gold: #FFD700; --main-blue: #00BFFF;
        }
        body { font-family: 'Courier Prime', monospace; background: var(--primary-bg); color: var(--text-primary); margin: 0; padding: 20px; }
        .container { max-width: 1400px; margin: 0 auto; }
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
        .input-group input, .input-group select { width: 100%; background: #000; border: 1px solid var(--border-color); color: var(--text-primary); padding: 8px; border-radius: 0 5px 5px 0; }
        .bot-status-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr)); gap: 8px; }
        .bot-status-item { display: flex; justify-content: space-between; align-items: center; padding: 5px 8px; background: #222; border-radius: 4px; }
        .btn-toggle-state { cursor: pointer; background: transparent; border: none; font-weight: 700; }
        .btn-rise { color: var(--necro-green); } .btn-rest { color: var(--blood-red); }
        .msg-status { text-align: center; color: var(--shadow-cyan); padding: 12px; border: 1px dashed var(--border-color); margin-bottom: 20px; background: rgba(0, 139, 139, 0.1); display: none; }
        .main-panel { border: 2px solid var(--main-blue); box-shadow: 0 0 15px var(--main-blue); }
        .delete-btn { background: var(--blood-red); color: white; border: none; cursor: pointer; padding: 2px 6px; border-radius: 4px; }
    </style>
</head>
<body>
    <div class="container">
        <div class="header"><h1 class="title">FARM CONTROL PANEL - OPTIMIZED</h1></div>
        <div id="msg-status-container" class="msg-status"></div>

        <div class="panel">
            <h2><i class="fas fa-server"></i> System Status & Global Controls</h2>
            <div class="main-grid">
                <div id="bot-status-list"></div>
                <div>
                    <div class="input-group">
                        <label>Auto Reboot Delay (s)</label>
                        <input type="number" id="auto-reboot-delay" value="{{ auto_reboot_delay }}" min="300">
                        <button id="auto-reboot-toggle-btn" class="btn {{ reboot_button_class }}">{{ reboot_action }}</button>
                    </div>
                    <button id="event-grab-toggle-btn" class="btn {{ event_grab_button_class }}" style="width: 100%;">{{ event_grab_action }}</button>
                </div>
            </div>
        </div>
        
        <div class="panel main-panel">
            <h2><i class="fas fa-crown"></i> Main Control Panel</h2>
            <div class="main-grid">
                <div>
                    <h4><i class="fas fa-crosshairs"></i> Harvest Settings</h4>
                    <div class="input-group">
                        <label>ALPHA</label>
                        <input type="number" class="main-panel-input" data-field="heart_threshold_alpha" value="{{ main_panel.heart_threshold_alpha }}">
                        <button class="btn main-panel-toggle" data-field="auto_grab_enabled_alpha">{{ 'TẮT' if main_panel.auto_grab_enabled_alpha else 'BẬT' }}</button>
                    </div>
                    <div class="input-group">
                        <label>BETA+</label>
                        <input type="number" class="main-panel-input" data-field="heart_threshold_main_other" value="{{ main_panel.heart_threshold_main_other }}">
                        <button class="btn main-panel-toggle" data-field="auto_grab_enabled_main_other">{{ 'TẮT' if main_panel.auto_grab_enabled_main_other else 'BẬT' }}</button>
                    </div>
                </div>
                <div>
                    <h4><i class="fas fa-sync-alt"></i> Đồng Bộ Hóa</h4>
                    <button id="sync-from-main-btn" class="btn btn-primary" style="width:100%;">Đồng Bộ Cài Đặt Cho Tất Cả Farm</button>
                </div>
            </div>
        </div>

        <div class="panel">
            <h2><i class="fas fa-plus-circle"></i> Farm Panels Management</h2>
            <div id="farm-grid" class="main-grid">
                {% for server in farm_servers %}
                <div class="panel" style="border-left: 5px solid var(--hot-pink);">
                    <button class="delete-btn delete-farm-btn" data-farm-id="{{ server.id }}" style="position:absolute; top:10px; right: 10px;">XÓA</button>
                    <h3>{{ server.name }}</h3>
                    <div class="input-group">
                        <label>Main CH</label>
                        <input type="text" class="farm-channel-input" data-farm-id="{{ server.id }}" data-field="main_channel_id" value="{{ server.main_channel_id or '' }}">
                    </div>
                    <div class="input-group">
                        <label>KTB CH</label>
                        <input type="text" class="farm-channel-input" data-farm-id="{{ server.id }}" data-field="ktb_channel_id" value="{{ server.ktb_channel_id or '' }}">
                    </div>
                </div>
                {% endfor %}
                <div id="add-farm-btn" style="display:flex; align-items:center; justify-content:center; min-height:150px; border-style:dashed; cursor:pointer;">
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
            const response = await fetch(url, { 
                method: 'POST', 
                headers: { 'Content-Type': 'application/json' }, 
                body: JSON.stringify(data) 
            });
            const result = await response.json();
            showMsg(result.message);
            if (result.reload) setTimeout(() => location.reload(), 500);
            return result;
        } catch (error) { 
            showMsg('Lỗi giao tiếp với server.'); 
        }
    };

    // Global controls
    document.getElementById('auto-reboot-toggle-btn').addEventListener('click', () => {
        const delay = parseInt(document.getElementById('auto-reboot-delay').value);
        if (delay < 300) {
            showMsg('Delay tối thiểu là 300 giây (5 phút)');
            document.getElementById('auto-reboot-delay').value = 300;
            return;
        }
        postData('/api/reboot_toggle_auto', { delay: delay });
    });
    
    document.getElementById('event-grab-toggle-btn').addEventListener('click', () => 
        postData('/api/event_grab_toggle', {}));
    
    document.getElementById('bot-status-list').addEventListener('click', e => { 
        if (e.target.matches('.btn-toggle-state')) { 
            postData('/api/toggle_bot_state', { target: e.target.dataset.target }); 
            setTimeout(fetchStatus, 500); 
        }
    });

    // Main panel
    const mainPanel = document.querySelector('.main-panel');
    mainPanel.addEventListener('change', e => { 
        if (e.target.matches('.main-panel-input')) { 
            const data = {}; 
            data[e.target.dataset.field] = parseInt(e.target.value) || 0; 
            postData('/api/main_panel/update', data); 
        } 
    });
    
    mainPanel.addEventListener('click', e => { 
        if (e.target.matches('.main-panel-toggle')) { 
            const data = {}; 
            data[e.target.dataset.field] = 'toggle'; 
            postData('/api/main_panel/update', data).then(() => location.reload()); 
        } 
    });
    
    document.getElementById('sync-from-main-btn').addEventListener('click', () => 
        postData('/api/main_panel/sync', {}));

    // Farm Management
    document.getElementById('add-farm-btn').addEventListener('click', () => { 
        const name = prompt("Nhập tên farm mới:"); 
        if (name) postData('/api/farm/add', { name }); 
    });
    
    const farmGrid = document.getElementById('farm-grid');
    farmGrid.addEventListener('click', e => { 
        if (e.target.matches('.delete-farm-btn')) { 
            if (confirm('Xóa farm này?')) 
                postData('/api/farm/delete', { farm_id: e.target.dataset.farmId }); 
        } 
    });
    
    farmGrid.addEventListener('change', e => { 
        if (e.target.matches('.farm-channel-input')) { 
            const data = { farm_id: e.target.dataset.farmId }; 
            data[e.target.dataset.field] = e.target.value; 
            postData('/api/farm/update', data); 
        } 
    });

    // Initial Status Fetch
    async function fetchStatus() {
        try {
            const response = await fetch('/status');
            const data = await response.json();
            const container = document.getElementById('bot-status-list');
            container.innerHTML = '<div class="bot-status-grid">' + 
                data.bot_statuses.map(bot => 
                    `<div class="bot-status-item">
                        <span>${bot.name}</span>
                        <button class="btn-toggle-state ${bot.is_active ? 'btn-rise' : 'btn-rest'}" data-target="${bot.reboot_id}">${bot.is_active ? 'ONLINE' : 'OFFLINE'}</button>
                    </div>`
                ).join('') + '</div>';
        } catch (error) {
            console.error('Error fetching status:', error);
        }
    }
    
    fetchStatus();
    setInterval(fetchStatus, 10000); // Update mỗi 10s
});
</script>
</body>
</html>
"""

# --- FLASK ROUTES (Giữ nguyên từ code gốc) ---
@app.route("/")
def index():
    reboot_action = "DISABLE REBOOT" if auto_reboot_enabled else "ENABLE REBOOT"
    reboot_button_class = "btn-danger" if auto_reboot_enabled else "btn-success"
    event_grab_action = "DISABLE EVENT GRAB" if event_grab_enabled else "ENABLE EVENT GRAB"  
    event_grab_button_class = "btn-danger" if event_grab_enabled else "btn-success"

    return render_template_string(HTML_TEMPLATE,
        auto_reboot_delay=auto_reboot_delay,
        reboot_action=reboot_action,
        reboot_button_class=reboot_button_class,
        event_grab_action=event_grab_action,
        event_grab_button_class=event_grab_button_class,
        farm_servers=farm_servers,
        main_panel=main_panel_settings
    )

@app.route("/status")
def status():
    bot_status_list = []
    with bots_lock:
        for i in range(len(main_bots)):
            name = GREEK_ALPHABET[i] if i < len(GREEK_ALPHABET) else f"Main {i}"
            bot_status_list.append({
                "name": name, 
                "reboot_id": f"main_{i}", 
                "is_active": bot_active_states.get(f'main_{i}', False)
            })
    return jsonify({'bot_statuses': bot_status_list})

# --- API ENDPOINTS (Giữ nguyên từ code gốc) ---
@app.route("/api/main_panel/update", methods=['POST'])
def api_main_panel_update():
    data = request.json
    for key, value in data.items():
        if key in main_panel_settings:
            if value == 'toggle': 
                main_panel_settings[key] = not main_panel_settings[key]
            else: 
                main_panel_settings[key] = int(value) if isinstance(main_panel_settings[key], int) else value
    save_main_settings()
    return jsonify({'status': 'success', 'message': 'Đã cập nhật Main Panel.'})

@app.route("/api/main_panel/sync", methods=['POST'])
def api_main_panel_sync():
    sync_count = 0
    for server in farm_servers:
        # Sync tất cả farm
        server['auto_grab_enabled_alpha'] = main_panel_settings['auto_grab_enabled_alpha']
        server['heart_threshold_alpha'] = main_panel_settings['heart_threshold_alpha']
        server['auto_grab_enabled_main_other'] = main_panel_settings['auto_grab_enabled_main_other']
        server['heart_threshold_main_other'] = main_panel_settings['heart_threshold_main_other']
        sync_count += 1
            
    save_farm_settings()
    return jsonify({'status': 'success', 'message': f'Đã đồng bộ cài đặt cho {sync_count} farm.'})

@app.route("/api/farm/add", methods=['POST'])
def api_farm_add():
    name = request.json.get('name')
    if not name: 
        return jsonify({'status': 'error', 'message': 'Tên farm là bắt buộc.'}), 400

    new_server = {
        "id": f"farm_{int(time.time())}", 
        "name": name,
        "main_channel_id": "", 
        "ktb_channel_id": "",
        "auto_grab_enabled_alpha": False, 
        "heart_threshold_alpha": 15,
        "auto_grab_enabled_main_other": False, 
        "heart_threshold_main_other": 50
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
    if not server: 
        return jsonify({'status': 'error', 'message': 'Không tìm thấy farm.'}), 404
    
    for key in ['main_channel_id', 'ktb_channel_id']:
        if key in data: 
            server[key] = data[key]
    
    save_farm_settings()
    return jsonify({'status': 'success', 'message': 'Đã cập nhật farm.'})

@app.route("/api/reboot_toggle_auto", methods=['POST'])
def api_reboot_toggle_auto():
    global auto_reboot_enabled, auto_reboot_thread, auto_reboot_stop_event, auto_reboot_delay
    
    # Cập nhật delay từ request
    new_delay = int(request.json.get("delay", 3600))
    if new_delay < 300:  # Tối thiểu 5 phút
        return jsonify({'status': 'error', 'message': 'Delay tối thiểu là 300 giây (5 phút).'}), 400
    
    auto_reboot_delay = new_delay
    auto_reboot_enabled = not auto_reboot_enabled
    
    if auto_reboot_enabled and (auto_reboot_thread is None or not auto_reboot_thread.is_alive()):
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
    elif not auto_reboot_enabled and auto_reboot_stop_event: 
        auto_reboot_stop_event.set()
        auto_reboot_thread = None
    
    save_main_settings()
    return jsonify({
        'status': 'success', 
        'message': f'Auto Reboot đã {"BẬT" if auto_reboot_enabled else "TẮT"} với delay {auto_reboot_delay}s.',
        'reload': True
    })

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
    return jsonify({
        'status': 'success', 
        'message': f"Event Grab đã {'BẬT' if event_grab_enabled else 'TẮT'}",
        'reload': True
    })

# --- MAIN EXECUTION ---
if __name__ == "__main__":
    load_farm_settings()
    load_main_settings()
    
    logger.info("Đang khởi tạo các bot...")
    with bots_lock:
        # Khởi tạo Main bots
        if main_token_alpha:
            main_bots.append(create_bot(main_token_alpha, 'main', 0))
            if 'main_0' not in bot_active_states: 
                bot_active_states['main_0'] = True
        
        for i, token in enumerate(other_main_tokens):
            if token.strip():
                bot_index = i + 1
                main_bots.append(create_bot(token.strip(), 'main', bot_index))
                if f'main_{bot_index}' not in bot_active_states: 
                    bot_active_states[f'main_{bot_index}'] = True

    logger.info("Đang khởi tạo các luồng nền...")
    
    # Khởi tạo grab processor
    def start_grab_processor():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        loop.run_until_complete(grab_processor_loop())
    
    threading.Thread(target=start_grab_processor, daemon=True).start()
    
    # Khởi tạo periodic save
    threading.Thread(target=periodic_save_loop, daemon=True).start()

    # Khởi tạo auto reboot nếu được bật
    if auto_reboot_enabled:
        auto_reboot_stop_event = threading.Event()
        auto_reboot_thread = threading.Thread(target=auto_reboot_loop, daemon=True)
        auto_reboot_thread.start()
    
    port = int(os.environ.get("PORT", 10001))
    logger.info(f"Khởi động Farm Control Panel tại http://0.0.0.0:{port}")
    logger.info("=== OPTIMIZATIONS APPLIED ===")
    logger.info("✓ Chỉ Alpha bot đọc message và phân phối grab")
    logger.info("✓ Removed spam features và sub bots") 
    logger.info("✓ Optimized memory usage với garbage collection")
    logger.info("✓ Fixed reboot delay persistence")
    logger.info("✓ Reduced thread count và CPU usage")
    logger.info("✓ Chuyển đổi sang discord.py-selfbot")
    logger.info("✓ Thêm cơ chế xử lý lỗi và kết nối lại")
    logger.info("✓ Thêm logging chi tiết để debug")
    
    app.run(host="0.0.0.0", port=port, debug=False, use_reloader=False)
