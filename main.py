import os
import logging
import random
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, MessageHandler, filters, CallbackQueryHandler
from dotenv import load_dotenv
import google.generativeai as genai
import json
from PIL import Image # Impor baru untuk gambar

# --- Local Imports ---
from utils.SpeechtoText import SpeechToText
from utils.database import update_inventory, query_inventory, query_all_inventory, clear_all_inventory

# --- Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)
load_dotenv()

# --- Environment Variables & Configuration ---
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
WEBHOOK_URL = os.getenv("WEBHOOK_URL")
GOOGLE_CREDS_PATH_IN_CONTAINER = "/app/training-telkom.json" 

# --- Initialization ---
try:
    transcriber = SpeechToText(GOOGLE_CREDS_PATH_IN_CONTAINER)
    logger.info("SpeechToText transcriber initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize SpeechToText transcriber: {e}")
    exit()

if not GEMINI_API_KEY:
    logger.error("GEMINI_API_KEY not found in .env file.")
    exit()
try:
    genai.configure(api_key=GEMINI_API_KEY)
    llm = genai.GenerativeModel('gemini-2.5-flash-lite-preview-06-17')
    logger.info("Gemini model initialized successfully.")
except Exception as e:
    logger.error(f"Failed to initialize Gemini: {e}")
    exit()

# Inisialisasi ptb_app tanpa handler terlebih dahulu
ptb_app = Application.builder().token(TELEGRAM_TOKEN).build()

# --- FastAPI Lifespan Events ---
# PERBAIKAN: Gunakan lifespan untuk mengelola inisialisasi dan shutdown
@asynccontextmanager
async def lifespan(app: FastAPI):
    # Sebelum server dimulai (startup)
    logger.info("Initializing PTB Application...")
    await ptb_app.initialize()
    if WEBHOOK_URL:
        logger.info(f"Setting webhook to {WEBHOOK_URL}/webhook")
        await ptb_app.bot.set_webhook(f"{WEBHOOK_URL}/webhook")
    
    # Daftarkan semua handler di sini setelah inisialisasi
    text_handler = MessageHandler(filters.TEXT & ~filters.COMMAND, lambda u, c: handle_text_message(u, u.message.text))
    voice_handler = MessageHandler(filters.VOICE, handle_voice_message)
    image_handler = MessageHandler(filters.PHOTO, handle_image_message)
    sticker_handler = MessageHandler(filters.Sticker.ALL, handle_sticker_message)
    unsupported_handler = MessageHandler(filters.Document.ALL | filters.VIDEO | filters.ANIMATION | 
                                       filters.AUDIO | filters.VIDEO_NOTE | filters.LOCATION | 
                                       filters.CONTACT, handle_unsupported_message)
    button_handler = CallbackQueryHandler(button_callback_handler)
    
    ptb_app.add_handler(text_handler)
    ptb_app.add_handler(voice_handler)
    ptb_app.add_handler(image_handler)
    ptb_app.add_handler(sticker_handler)
    ptb_app.add_handler(unsupported_handler)
    ptb_app.add_handler(button_handler)
    
    logger.info("PTB Application initialized and handlers registered.")
    yield
    # Setelah server dimatikan (shutdown)
    logger.info("Shutting down PTB Application...")
    await ptb_app.shutdown()

# Inisialisasi FastAPI dengan lifespan manager
app = FastAPI(lifespan=lifespan)

# --- Gemini Functions ---
def get_intent_from_text(text: str) -> dict:
    prompt = f"""
    You are a grocery management assistant. Analyze the user's text and determine the action.
    The possible actions are 'ADD', 'USE', 'QUERY', 'QUERY_ALL', 'RECIPE', 'CLEAR_ALL', or 'UNRELATED'.
    - 'ADD': for buying or getting new groceries.
    - 'USE': for consuming or using up groceries.
    - 'QUERY': for asking about a specific item's stock.
    - 'QUERY_ALL': for asking to see all available stock.
    - 'RECIPE': for asking recipe suggestions based on available ingredients (e.g., "apa yang bisa dimasak?", "resep apa yang bisa dibuat?", "mau masak apa?").
    - 'CLEAR_ALL': for clearing all inventory items (e.g., "hapus semua", "kosongkan stok", "clear all", "reset inventory", "bersihkan semua").
    - 'UNRELATED': for greetings, tests, or any conversation not related to groceries.

    Respond with only a JSON object in the following format:
    {{
      "action": "ADD|USE|QUERY|QUERY_ALL|RECIPE|CLEAR_ALL|UNRELATED",
      "items": [
        {{"name": "item name", "quantity": number, "unit": "unit type"}}
      ]
    }}
    For QUERY_ALL, RECIPE, CLEAR_ALL, and UNRELATED, the items list can be empty.

    User text: "{text}"
    """
    try:
        response = llm.generate_content(prompt)
        cleaned_response = response.text.strip().replace('`', '').replace('json', '')
        logger.info(f"Gemini raw response: {cleaned_response}")
        return json.loads(cleaned_response)
    except Exception as e:
        logger.error(f"Error parsing intent with Gemini: {e}")
        return None

def get_items_from_receipt(image_path: str) -> dict:
    """Uses Gemini Vision to extract items from a receipt image."""
    logger.info(f"Starting receipt analysis for image: {image_path}")
    try:
        # Check if file exists
        if not os.path.exists(image_path):
            logger.error(f"Image file not found: {image_path}")
            return None
            
        img = Image.open(image_path)
        logger.info(f"Image opened successfully. Size: {img.size}, Mode: {img.mode}")
        
        prompt = """
        You are an expert receipt scanner for an Indonesian grocery bot. 
        Analyze this receipt image and extract all grocery items and their quantities.
        - Ignore headers, footers, totals, taxes, discounts, and any non-grocery items.
        - Standardize item names to common Indonesian grocery terms (e.g., "AYAM BROILER" → "ayam", "DAGING SAPI" → "sapi").
        - Remove brand names and focus on the core ingredient (e.g., "INDOMIE GORENG" → "mie instan").
        - Determine the quantity and unit for each item.
        - If you can't determine the quantity, assume 1 piece.
        - Use standard units: kg, gram, liter, ml, pcs, butir, bungkus.
        - Common Indonesian grocery items: beras (rice), minyak goreng (cooking oil), gula (sugar), telur (eggs), ayam (chicken), sapi (beef), etc.

        Respond with only a JSON object in the following format:
        {
          "action": "ADD",
          "items": [
            {"name": "item name", "quantity": number, "unit": "unit type"}
          ]
        }
        
        If you cannot find any grocery items, return:
        {
          "action": "ADD",
          "items": []
        }
        """
        
        logger.info("Sending image to Gemini Vision API...")
        response = llm.generate_content([prompt, img])
        
        if not response or not response.text:
            logger.error("No response from Gemini Vision API")
            return None
            
        cleaned_response = response.text.strip().replace('`', '').replace('json', '').strip()
        logger.info(f"Gemini Vision raw response: {cleaned_response}")
        
        # Try to parse JSON
        parsed_data = json.loads(cleaned_response)
        logger.info(f"Successfully parsed receipt data: {parsed_data}")
        return parsed_data
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error in receipt analysis: {e}")
        logger.error(f"Raw response was: {cleaned_response if 'cleaned_response' in locals() else 'No response'}")
        return None
    except Exception as e:
        logger.error(f"Error parsing receipt with Gemini Vision: {e}", exc_info=True)
        return None

def get_recipe_suggestions(available_items: list) -> dict:
    """Uses Gemini to suggest recipes based on available ingredients."""
    logger.info(f"Getting recipe suggestions for {len(available_items)} items")
    try:
        # Create a list of available ingredients
        ingredients_text = ", ".join([f"{item[1]} {item[2]} {item[0]}" for item in available_items])
        
        prompt = f"""
        You are an Indonesian recipe expert. Based on the available ingredients below, suggest 3-5 delicious Indonesian recipes that can be made.
        
        Available ingredients: {ingredients_text}
        
        Requirements:
        - Focus on popular Indonesian dishes
        - Use as many available ingredients as possible
        - Include recipes that are practical and easy to make
        - If some ingredients are missing, mention them as "additional ingredients needed"
        - Provide brief cooking instructions
        
        Respond with only a JSON object in the following format:
        {{
          "recipes": [
            {{
              "name": "Recipe Name",
              "description": "Brief description of the dish",
              "ingredients_used": ["ingredient1", "ingredient2"],
              "additional_ingredients": ["ingredient3", "ingredient4"],
              "cooking_time": "30 minutes",
              "difficulty": "Easy/Medium/Hard",
              "instructions": "Brief cooking steps"
            }}
          ]
        }}
        """
        
        logger.info("Sending ingredients to Gemini for recipe suggestions...")
        response = llm.generate_content(prompt)
        
        if not response or not response.text:
            logger.error("No response from Gemini for recipe suggestions")
            return None
            
        cleaned_response = response.text.strip().replace('`', '').replace('json', '').strip()
        logger.info(f"Gemini recipe response: {cleaned_response}")
        
        # Try to parse JSON
        parsed_data = json.loads(cleaned_response)
        logger.info(f"Successfully parsed recipe suggestions: {len(parsed_data.get('recipes', []))} recipes")
        return parsed_data
        
    except json.JSONDecodeError as e:
        logger.error(f"JSON parsing error in recipe suggestions: {e}")
        return None
    except Exception as e:
        logger.error(f"Error getting recipe suggestions: {e}", exc_info=True)
        return None

def normalize_ingredient_name(item_name: str) -> str:
    """Normalize ingredient names to handle variations and synonyms."""
    logger.info(f"Normalizing ingredient name: {item_name}")
    try:
        prompt = f"""
        You are an Indonesian grocery expert. Normalize the following ingredient name to its standard form.
        
        Rules:
        - Convert to lowercase
        - Use the most common Indonesian name
        - Remove unnecessary words like "daging", "buah", "biji" when they don't change the core ingredient
        - Standardize similar items to one name
        
        Examples:
        - "daging ayam" → "ayam"
        - "ayam broiler" → "ayam" 
        - "daging sapi" → "sapi"
        - "beras putih" → "beras"
        - "telur ayam" → "telur"
        - "minyak goreng" → "minyak goreng" (keep as is, it's specific)
        - "gula pasir" → "gula"
        - "kentang" → "kentang"
        - "wortel" → "wortel"
        
        Input ingredient: "{item_name}"
        
        Respond with only the normalized name in lowercase, no quotes, no explanations.
        """
        
        response = llm.generate_content(prompt)
        normalized = response.text.strip().lower()
        logger.info(f"Normalized '{item_name}' to '{normalized}'")
        return normalized
        
    except Exception as e:
        logger.error(f"Error normalizing ingredient '{item_name}': {e}")
        # Fallback: basic normalization
        normalized = item_name.lower().strip()
        # Remove common prefixes
        for prefix in ["daging ", "buah ", "biji "]:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):]
        return normalized

# --- Bot Logic Handlers ---
async def handle_text_message(update: Update, user_text: str):
    user_name = update.message.from_user.first_name
    chat_id = update.message.chat_id
    
    logger.info(f"Processing text from {user_name}: {user_text}")
    
    intent_data = get_intent_from_text(user_text)
    
    if intent_data:
        action_value = intent_data.get("action")
        action = action_value.upper() if action_value else "UNKNOWN"
        items = intent_data.get("items", [])
        
        if action in ["ADD", "USE"]:
            if not items:
                reply_text = "Maaf, saya tidak bisa menemukan item apa pun dalam permintaan Anda."
            else:
                # Smart ingredient matching and feedback
                merged_items = []
                for item in items:
                    original_name = item.get("name")
                    from utils.database import find_similar_item
                    matched_name = find_similar_item(original_name, normalize_ingredient_name)
                    
                    if original_name.lower() != matched_name:
                        merged_items.append(f"'{original_name}' → '{matched_name}'")
                    
                    item["name"] = matched_name
                    logger.info(f"Item '{original_name}' processed as '{matched_name}'")
                
                success = update_inventory(action, items, user_name)
                if success:
                    reply_lines = [f"Sip, {user_name}! Stok telah berhasil diperbarui."]
                    
                    # Show merged items if any
                    if merged_items:
                        reply_lines.append("")
                        reply_lines.append("🔄 *Item yang digabung:*")
                        for merge in merged_items[:3]:  # Show max 3 merges
                            reply_lines.append(f"   {merge}")
                        if len(merged_items) > 3:
                            reply_lines.append(f"   ... dan {len(merged_items) - 3} lainnya")
                    
                    all_items = query_all_inventory()
                    if not all_items:
                        reply_lines.append("\nStok sekarang kosong.")
                    else:
                        reply_lines.append("\n*Stok Saat Ini:*")
                        for item_name, quantity, unit in all_items:
                            reply_lines.append(f"- {item_name.capitalize()}: {quantity} {unit}")
                        
                        # Add recipe suggestion tip
                        reply_lines.append("\n💡 *Tip:* Ketik 'resep' untuk saran resep!")
                    
                    reply_text = "\n".join(reply_lines)
                    
                    # Add quick recipe button if we have items
                    if all_items and len(all_items) >= 2:  # At least 2 ingredients
                        keyboard = [[InlineKeyboardButton("🍳 Lihat Resep", callback_data="get_recipes")]]
                        reply_markup = InlineKeyboardMarkup(keyboard)
                        await ptb_app.bot.send_message(chat_id=chat_id, text=reply_text, parse_mode='Markdown', reply_markup=reply_markup)
                        return
                else:
                    reply_text = "Maaf, terjadi kesalahan saat memperbarui stok."
        
        elif action == "QUERY":
            if not items:
                 reply_text = "Maaf, item apa yang ingin Anda cek?"
            else:
                item_name_to_check = items[0].get("name")
                reply_text = query_inventory(item_name_to_check)

        elif action == "QUERY_ALL":
            all_items = query_all_inventory()
            if not all_items:
                reply_text = "Saat ini stok masih kosong."
            else:
                reply_lines = ["Berikut adalah semua stok yang tersedia:\n"]
                for item_name, quantity, unit in all_items:
                    reply_lines.append(f"- *{item_name.capitalize()}*: {quantity} {unit}")
                reply_text = "\n".join(reply_lines)
        
        elif action == "RECIPE":
            all_items = query_all_inventory()
            if not all_items:
                reply_text = "Maaf, stok Anda masih kosong. Tambahkan bahan-bahan terlebih dahulu untuk mendapatkan saran resep! 🍳"
            else:
                # Send "thinking" message
                thinking_msg = await ptb_app.bot.send_message(chat_id=chat_id, text="🍳 Sedang mencari resep yang cocok dengan bahan Anda...")
                
                recipe_data = get_recipe_suggestions(all_items)
                
                # Delete thinking message
                await ptb_app.bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
                
                if recipe_data and recipe_data.get("recipes"):
                    recipes = recipe_data.get("recipes")
                    reply_lines = [f"🍳 *Resep yang bisa dibuat dengan bahan Anda:*\n"]
                    
                    for i, recipe in enumerate(recipes[:3], 1):  # Limit to 3 recipes
                        reply_lines.append(f"*{i}. {recipe.get('name', 'Resep Tanpa Nama')}*")
                        reply_lines.append(f"⏱️ {recipe.get('cooking_time', 'N/A')} | 📊 {recipe.get('difficulty', 'N/A')}")
                        reply_lines.append(f"📝 {recipe.get('description', 'Tidak ada deskripsi')}")
                        
                        # Show ingredients used from inventory
                        used_ingredients = recipe.get('ingredients_used', [])
                        if used_ingredients:
                            reply_lines.append(f"✅ *Bahan tersedia:* {', '.join(used_ingredients)}")
                        
                        # Show additional ingredients needed
                        additional = recipe.get('additional_ingredients', [])
                        if additional:
                            reply_lines.append(f"🛒 *Perlu beli:* {', '.join(additional)}")
                        
                        # Show brief instructions
                        instructions = recipe.get('instructions', '')
                        if instructions:
                            # Truncate if too long
                            if len(instructions) > 200:
                                instructions = instructions[:200] + "..."
                            reply_lines.append(f"👩‍🍳 *Cara masak:* {instructions}")
                        
                        reply_lines.append("")  # Empty line between recipes
                    
                    reply_text = "\n".join(reply_lines)
                else:
                    reply_text = "Maaf, saya tidak bisa menemukan resep yang cocok dengan bahan yang tersedia saat ini. Coba tambah lebih banyak bahan! 😅"
        
        elif action == "CLEAR_ALL":
            all_items = query_all_inventory()
            if not all_items:
                reply_text = "Stok Anda sudah kosong, tidak ada yang perlu dihapus."
            else:
                # Show confirmation with buttons
                reply_lines = [f"⚠️ *Peringatan!*\n"]
                reply_lines.append(f"Anda akan menghapus *SEMUA* {len(all_items)} item dari stok:")
                reply_lines.append("")
                for item_name, quantity, unit in all_items[:5]:  # Show max 5 items
                    reply_lines.append(f"- {item_name.capitalize()}: {quantity} {unit}")
                if len(all_items) > 5:
                    reply_lines.append(f"... dan {len(all_items) - 5} item lainnya")
                reply_lines.append("")
                reply_lines.append("Apakah Anda yakin ingin menghapus semua item?")
                
                keyboard = [
                    [
                        InlineKeyboardButton("✅ Ya, Hapus Semua", callback_data="confirm_clear_all"),
                        InlineKeyboardButton("❌ Batal", callback_data="cancel_clear_all"),
                    ]
                ]
                reply_markup = InlineKeyboardMarkup(keyboard)
                await ptb_app.bot.send_message(chat_id=chat_id, text="\n".join(reply_lines), parse_mode='Markdown', reply_markup=reply_markup)
                return  # Don't send the regular reply_text
            
        elif action == "UNRELATED":
            reply_text = f"Halo {user_name}! Ada yang bisa saya bantu dengan daftar belanjaan Anda?"
            
        else:
            reply_text = f"Maaf {user_name}, Aku belum paham maksud permintaannya."
            
    else:
        reply_text = f"Maaf {user_name}, terjadi kesalahan saat mencoba memahami permintaan Anda."
        
    await ptb_app.bot.send_message(chat_id=chat_id, text=reply_text, parse_mode='Markdown')

async def handle_voice_message(update: Update, context):
    chat_id = update.message.chat_id
    try:
        user_name = update.message.from_user.first_name
        voice = update.message.voice
        voice_file = await voice.get_file()
        temp_audio_path = f"{voice.file_id}.ogg"
        await voice_file.download_to_drive(temp_audio_path)
        logger.info(f"Downloaded voice note from {user_name} to {temp_audio_path}")
        results = transcriber.transcribe_audio(temp_audio_path)
        os.remove(temp_audio_path)
        if results:
            transcribed_text = results[0].alternatives[0].transcript
            logger.info(f"Transcription result: '{transcribed_text}'")
            await handle_text_message(update, transcribed_text)
        else:
            await ptb_app.bot.send_message(chat_id=chat_id, text="Maaf, saya tidak bisa memahami audio tersebut.")
    except Exception as e:
        logger.error(f"Failed to handle voice message: {e}")
        await ptb_app.bot.send_message(chat_id=chat_id, text="Maaf, terjadi kesalahan saat memproses pesan suara Anda.")

async def handle_image_message(update: Update, context):
    chat_id = update.message.chat_id
    user_name = update.message.from_user.first_name
    
    logger.info(f"Received image from {user_name} in chat {chat_id}")
    
    try:
        # Check if photo exists
        if not update.message.photo:
            logger.warning("No photo found in the message")
            await update.message.reply_text("Maaf, tidak ada foto yang ditemukan dalam pesan.")
            return
            
        photo = update.message.photo[-1]  # Get the highest resolution photo
        logger.info(f"Processing photo with file_id: {photo.file_id}")
        
        photo_file = await photo.get_file()
        temp_image_path = f"{photo.file_id}.jpg"
        
        await photo_file.download_to_drive(temp_image_path)
        logger.info(f"Downloaded receipt from {user_name} to {temp_image_path}")

        # Send processing message to user
        processing_msg = await update.message.reply_text("📸 Sedang memproses foto struk Anda...")
        
        receipt_data = get_items_from_receipt(temp_image_path)
        logger.info(f"Receipt data extracted: {receipt_data}")
        
        # Clean up the image file
        if os.path.exists(temp_image_path):
            os.remove(temp_image_path)
            logger.info(f"Cleaned up temporary file: {temp_image_path}")

        # Delete the processing message
        await processing_msg.delete()

        if receipt_data and receipt_data.get("items"):
            items = receipt_data.get("items")
            logger.info(f"Found {len(items)} items in receipt")
            context.user_data['pending_items'] = items

            reply_lines = ["Saya menemukan item berikut di struk Anda:\n"]
            for item in items:
                quantity = item.get('quantity', 1)
                unit = item.get('unit', 'pcs')
                name = item.get('name', 'Unknown item')
                reply_lines.append(f"- {quantity} {unit} {name}")
            reply_lines.append("\nApakah data ini benar dan ingin ditambahkan ke stok?")
            
            keyboard = [
                [
                    InlineKeyboardButton("✅ Ya, Tambahkan", callback_data="confirm_add_receipt"),
                    InlineKeyboardButton("❌ Tidak, Batalkan", callback_data="cancel_add_receipt"),
                ]
            ]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await update.message.reply_text("\n".join(reply_lines), reply_markup=reply_markup)
        else:
            logger.warning("No items found in receipt or receipt_data is None")
            await update.message.reply_text("Maaf, saya tidak dapat membaca item dari struk tersebut. Coba foto dengan lebih jelas atau pastikan ini adalah struk belanja.")

    except Exception as e:
        logger.error(f"Failed to handle image message: {e}", exc_info=True)
        # Clean up file if it exists
        if 'temp_image_path' in locals() and os.path.exists(temp_image_path):
            os.remove(temp_image_path)
        await update.message.reply_text("Maaf, terjadi kesalahan saat memproses gambar Anda. Silakan coba lagi.")

async def button_callback_handler(update: Update, context):
    query = update.callback_query
    await query.answer()
    
    user_name = query.from_user.first_name
    
    if query.data == "confirm_add_receipt":
        pending_items = context.user_data.get('pending_items')
        if pending_items:
            success = update_inventory("ADD", pending_items, user_name)
            if success:
                # Tampilkan daftar stok lengkap setelah konfirmasi struk
                reply_lines = ["Sip! Stok telah berhasil diperbarui dari struk."]
                all_items = query_all_inventory()
                if not all_items:
                    reply_lines.append("\nStok sekarang kosong.")
                else:
                    reply_lines.append("\n*Stok Saat Ini:*\n")
                    for item_name, quantity, unit in all_items:
                        reply_lines.append(f"- {item_name.capitalize()}: {quantity} {unit}")
                
                final_reply = "\n".join(reply_lines)
                await query.edit_message_text(text=final_reply, parse_mode='Markdown')
            else:
                await query.edit_message_text(text="Maaf, terjadi kesalahan saat memperbarui stok.")
            
            del context.user_data['pending_items']
        else:
            await query.edit_message_text(text="Maaf, data struk sudah tidak valid. Silakan kirim ulang.")
            
    elif query.data == "cancel_add_receipt":
        if 'pending_items' in context.user_data:
            del context.user_data['pending_items']
        await query.edit_message_text(text="Baik, penambahan dari struk dibatalkan.")
    
    elif query.data == "get_recipes":
        user_name = query.from_user.first_name
        all_items = query_all_inventory()
        
        if not all_items:
            await query.edit_message_text(text="Maaf, tidak ada bahan yang tersedia untuk membuat resep.")
            return
        
        # Show loading message
        await query.edit_message_text(text="🍳 Sedang mencari resep yang cocok...")
        
        recipe_data = get_recipe_suggestions(all_items)
        
        if recipe_data and recipe_data.get("recipes"):
            recipes = recipe_data.get("recipes")
            reply_lines = [f"🍳 *Resep untuk {user_name}:*\n"]
            
            for i, recipe in enumerate(recipes[:3], 1):
                reply_lines.append(f"*{i}. {recipe.get('name', 'Resep')}*")
                reply_lines.append(f"⏱️ {recipe.get('cooking_time', 'N/A')} | 📊 {recipe.get('difficulty', 'N/A')}")
                reply_lines.append(f"📝 {recipe.get('description', '')}")
                
                # Show used ingredients
                used = recipe.get('ingredients_used', [])
                if used:
                    reply_lines.append(f"✅ *Tersedia:* {', '.join(used[:3])}{'...' if len(used) > 3 else ''}")
                
                additional = recipe.get('additional_ingredients', [])
                if additional:
                    reply_lines.append(f"🛒 *Perlu beli:* {', '.join(additional[:3])}{'...' if len(additional) > 3 else ''}")
                
                reply_lines.append("")
            
            final_text = "\n".join(reply_lines)
            await query.edit_message_text(text=final_text, parse_mode='Markdown')
        else:
            await query.edit_message_text(text="Maaf, tidak bisa menemukan resep yang cocok saat ini. Coba tambah lebih banyak bahan! 😅")
    
    elif query.data == "confirm_clear_all":
        user_name = query.from_user.first_name
        
        # Get items count before clearing
        all_items = query_all_inventory()
        items_count = len(all_items)
        
        success = clear_all_inventory(user_name)
        if success:
            if items_count > 0:
                reply_text = f"✅ *Berhasil!*\n\nSemua {items_count} item telah dihapus dari stok oleh {user_name}.\nStok sekarang kosong."
            else:
                reply_text = "Stok sudah kosong sebelumnya."
        else:
            reply_text = "❌ Maaf, terjadi kesalahan saat menghapus stok. Silakan coba lagi."
        
        await query.edit_message_text(text=reply_text, parse_mode='Markdown')
    
    elif query.data == "cancel_clear_all":
        await query.edit_message_text(text="✅ Baik, penghapusan stok dibatalkan. Semua item tetap tersimpan.")

async def handle_recipe_button(update: Update, context):
    """Handle recipe button press to show recipe suggestions."""
    chat_id = update.message.chat_id
    user_name = update.message.from_user.first_name
    
    all_items = query_all_inventory()
    if not all_items:
        await update.message.reply_text("Maaf, stok Anda masih kosong. Tambahkan bahan-bahan terlebih dahulu untuk mendapatkan saran resep! 🍳")
        return
    
    # Send thinking message
    thinking_msg = await update.message.reply_text("🍳 Sedang mencari resep yang cocok...")
    
    recipe_data = get_recipe_suggestions(all_items)
    
    # Delete thinking message
    await thinking_msg.delete()
    
    if recipe_data and recipe_data.get("recipes"):
        recipes = recipe_data.get("recipes")
        reply_lines = [f"🍳 *Resep untuk {user_name}:*\n"]
        
        for i, recipe in enumerate(recipes[:3], 1):
            reply_lines.append(f"*{i}. {recipe.get('name')}*")
            reply_lines.append(f"⏱️ {recipe.get('cooking_time')} | 📊 {recipe.get('difficulty')}")
            reply_lines.append(f"📝 {recipe.get('description')}")
            reply_lines.append("")
        
        await update.message.reply_text("\n".join(reply_lines), parse_mode='Markdown')
    else:
        await update.message.reply_text("Maaf, tidak ada resep yang cocok saat ini. Coba tambah lebih banyak bahan!")

async def handle_sticker_message(update: Update, context):
    """Handle sticker messages with a friendly response."""
    user_name = update.message.from_user.first_name
    chat_id = update.message.chat_id
    
    logger.info(f"Received sticker from {user_name} in chat {chat_id}")
    
    responses = [
        f"Haha, stiker yang lucu {user_name}! 😄\nAda yang bisa saya bantu dengan stok belanja Anda?",
        f"Terima kasih stikernya {user_name}! 😊\nBtw, mau cek stok atau tambah belanja?",
        f"Saya suka stiker itu {user_name}! 🎉\nAda bahan makanan yang mau ditambah ke stok?"
    ]
    
    reply_text = random.choice(responses)
    await ptb_app.bot.send_message(chat_id=chat_id, text=reply_text)

async def handle_unsupported_message(update: Update, context):
    """Handle unsupported message types (documents, videos, etc.)."""
    user_name = update.message.from_user.first_name
    chat_id = update.message.chat_id
    
    message_type = "pesan"
    if update.message.document:
        message_type = "dokumen"
    elif update.message.video:
        message_type = "video"
    elif update.message.animation:
        message_type = "GIF"
    elif update.message.audio:
        message_type = "audio"
    elif update.message.video_note:
        message_type = "video note"
    elif update.message.location:
        message_type = "lokasi"
    elif update.message.contact:
        message_type = "kontak"
    
    logger.info(f"Received {message_type} from {user_name} in chat {chat_id}")
    
    reply_text = f"Maaf {user_name}, saya tidak bisa memproses {message_type}.\n\n"
    reply_text += "Saya bisa membantu dengan:\n"
    reply_text += "📝 *Teks* - untuk menambah/gunakan/cek stok\n"
    reply_text += "🎤 *Voice note* - bicara tentang belanja\n"
    reply_text += "📸 *Foto struk* - scan otomatis\n"
    reply_text += "🍳 *Resep* - ketik 'resep' untuk saran masakan\n"
    reply_text += "🗑️ *Hapus semua* - ketik 'hapus semua' untuk kosongkan stok"
    
    await ptb_app.bot.send_message(chat_id=chat_id, text=reply_text, parse_mode='Markdown')

# --- FastAPI Webhook Endpoint ---
@app.post("/webhook")
async def telegram_webhook(request: Request):
    try:
        update_json = await request.json()
        logger.info(f"Webhook received update: {update_json}")
        
        update = Update.de_json(update_json, ptb_app.bot)
        
        # Log what type of update this is
        if update.message:
            if update.message.text:
                logger.info(f"Processing text message: {update.message.text}")
            elif update.message.voice:
                logger.info(f"Processing voice message from {update.message.from_user.first_name}")
            elif update.message.photo:
                logger.info(f"Processing photo message from {update.message.from_user.first_name}")
            else:
                logger.info(f"Processing other message type: {type(update.message)}")
        elif update.callback_query:
            logger.info(f"Processing callback query: {update.callback_query.data}")
        else:
            logger.info(f"Processing other update type: {type(update)}")
        
        await ptb_app.process_update(update)
        return {"status": "ok"}
    except Exception as e:
        logger.error(f"Error processing webhook: {e}", exc_info=True)
        return {"status": "error", "message": str(e)}

@app.get("/")
def index():
    return {"message": "MyGroceries Bot server is running!"}
