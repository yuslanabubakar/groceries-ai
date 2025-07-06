# utils/database.py
# Modul ini berisi semua fungsi untuk berinteraksi dengan database SQLite.

import sqlite3
import logging
from datetime import datetime

# --- Konfigurasi ---
DATABASE_PATH = "/app/data/groceries.db"  # Path di dalam kontainer Docker
logger = logging.getLogger(__name__)

# We'll import the normalization function when needed to avoid circular imports

def create_connection():
    """Membuat koneksi ke database SQLite."""
    conn = None
    try:
        conn = sqlite3.connect(DATABASE_PATH)
        return conn
    except sqlite3.Error as e:
        logger.error(f"Error saat menyambungkan ke database: {e}")
    return conn

def update_inventory(action: str, items: list, user_name: str):
    """
    Fungsi utama untuk memperbarui inventaris berdasarkan aksi (ADD/USE).
    Now with smart ingredient matching!
    """
    conn = create_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()
        for item in items:
            # Item name should already be processed by main.py
            item_name = item.get("name").lower()
            quantity = float(item.get("quantity", 0))
            unit = item.get("unit", "")

            logger.info(f"Processing item: '{item_name}'")

            # Periksa apakah item sudah ada
            cursor.execute("SELECT quantity FROM inventory WHERE item_name = ?", (item_name,))
            data = cursor.fetchone()

            if action.upper() == "ADD":
                if data is None:
                    # Item baru, lakukan INSERT
                    cursor.execute(
                        "INSERT INTO inventory (item_name, quantity, unit, last_updated, last_updated_by) VALUES (?, ?, ?, ?, ?)",
                        (item_name, quantity, unit, datetime.now(), user_name)
                    )
                    logger.info(f"INSERTED: {quantity} {unit} of {item_name} by {user_name}")
                else:
                    # Item sudah ada, lakukan UPDATE
                    new_quantity = data[0] + quantity
                    cursor.execute(
                        "UPDATE inventory SET quantity = ?, last_updated = ?, last_updated_by = ? WHERE item_name = ?",
                        (new_quantity, datetime.now(), user_name, item_name)
                    )
                    logger.info(f"UPDATED: Added {quantity} to {item_name}. New total: {new_quantity}. By {user_name}")
                
                # Catat transaksi penambahan
                log_transaction(cursor, item_name, quantity, user_name)

            elif action.upper() == "USE":
                if data is None:
                    # Tidak bisa menggunakan item yang tidak ada
                    logger.warning(f"Attempted to USE non-existent item: {item_name} by {user_name}")
                    continue # Lanjut ke item berikutnya
                else:
                    # Kurangi kuantitas
                    new_quantity = max(0, data[0] - quantity) # Pastikan tidak negatif
                    cursor.execute(
                        "UPDATE inventory SET quantity = ?, last_updated = ?, last_updated_by = ? WHERE item_name = ?",
                        (new_quantity, datetime.now(), user_name, item_name)
                    )
                    logger.info(f"UPDATED: Used {quantity} of {item_name}. New total: {new_quantity}. By {user_name}")
                
                # Catat transaksi penggunaan
                log_transaction(cursor, item_name, -quantity, user_name)

        conn.commit()
        return True
    except sqlite3.Error as e:
        logger.error(f"Database transaction failed: {e}")
        conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def log_transaction(cursor, item_name, quantity_change, user_name):
    """Mencatat setiap transaksi ke tabel transaction_log."""
    cursor.execute(
        "INSERT INTO transaction_log (item_name, quantity_change, user_name, transaction_time) VALUES (?, ?, ?, ?)",
        (item_name, quantity_change, user_name, datetime.now())
    )
    logger.info(f"LOGGED: {user_name} changed {item_name} by {quantity_change}")

def query_inventory(item_name: str) -> str:
    """
    Memeriksa stok item tertentu di database.
    Now with smart ingredient matching!
    """
    conn = create_connection()
    if not conn:
        return "Maaf, saya tidak bisa terhubung ke database saat ini."
    
    try:
        cursor = conn.cursor()
        
        # First try exact match
        cursor.execute("SELECT quantity, unit FROM inventory WHERE item_name = ?", (item_name.lower(),))
        data = cursor.fetchone()
        
        if data:
            return f"Stok untuk '{item_name}' saat ini adalah {data[0]} {data[1]}."
        
        # If not found, try to find similar items
        cursor.execute("SELECT item_name, quantity, unit FROM inventory WHERE quantity > 0")
        all_items = cursor.fetchall()
        
        # Simple fuzzy matching
        item_lower = item_name.lower()
        for db_name, quantity, unit in all_items:
            if item_lower in db_name or db_name in item_lower:
                return f"Mungkin maksud Anda '{db_name}' yang tersedia {quantity} {unit}?"
        
        return f"Maaf, saya tidak dapat menemukan item '{item_name}' di dalam stok."
    except sqlite3.Error as e:
        logger.error(f"Database query failed: {e}")
        return "Terjadi kesalahan saat memeriksa stok."
    finally:
        if conn:
            conn.close()


def query_all_inventory() -> list:
    """
    FUNGSI BARU: Mengambil semua item dari tabel inventaris.
    """
    conn = create_connection()
    if not conn:
        return []
    
    try:
        cursor = conn.cursor()
        # Mengambil semua data dan mengurutkannya berdasarkan nama item
        cursor.execute("SELECT item_name, quantity, unit FROM inventory WHERE quantity > 0 ORDER BY item_name ASC")
        all_items = cursor.fetchall()
        return all_items
    except sqlite3.Error as e:
        logger.error(f"Database query all failed: {e}")
        return []
    finally:
        if conn:
            conn.close()

def find_similar_item(item_name: str, normalize_func=None) -> str:
    """Find if there's a similar item already in the database."""
    try:
        conn = create_connection()
        if not conn:
            return item_name.lower()
        
        cursor = conn.cursor()
        cursor.execute("SELECT DISTINCT item_name FROM inventory WHERE quantity > 0")
        existing_items = [row[0] for row in cursor.fetchall()]
        conn.close()
        
        if not existing_items:
            return item_name.lower()
        
        # If we have a normalization function, use it
        if normalize_func:
            # Normalize the new item
            normalized_new = normalize_func(item_name)
            
            # Check if the normalized name already exists
            for existing in existing_items:
                normalized_existing = normalize_func(existing)
                if normalized_new == normalized_existing:
                    logger.info(f"Found match: '{item_name}' matches existing '{existing}'")
                    return existing  # Return the existing name to maintain consistency
            
            # If no match found, return the normalized name
            return normalized_new
        else:
            # Fallback: basic matching
            item_lower = item_name.lower()
            for existing in existing_items:
                if item_lower == existing.lower():
                    return existing
            return item_lower
        
    except Exception as e:
        logger.error(f"Error finding similar item for '{item_name}': {e}")
        return item_name.lower()

def clear_all_inventory(user_name: str) -> bool:
    """
    Clear all items from the inventory and log the action.
    Returns True if successful, False otherwise.
    """
    conn = create_connection()
    if not conn:
        return False

    try:
        cursor = conn.cursor()
        
        # Get all items before clearing for logging
        cursor.execute("SELECT item_name, quantity, unit FROM inventory WHERE quantity > 0")
        items_to_clear = cursor.fetchall()
        
        if not items_to_clear:
            logger.info(f"No items to clear for {user_name}")
            return True  # Nothing to clear is still a success
        
        # Log each item being cleared
        for item_name, quantity, unit in items_to_clear:
            log_transaction(cursor, item_name, -quantity, user_name)
            logger.info(f"Clearing: {quantity} {unit} of {item_name} by {user_name}")
        
        # Clear all inventory by setting quantity to 0
        cursor.execute("UPDATE inventory SET quantity = 0, last_updated = ?, last_updated_by = ?", 
                      (datetime.now(), user_name))
        
        # Also delete items with 0 quantity to keep database clean
        cursor.execute("DELETE FROM inventory WHERE quantity = 0")
        
        conn.commit()
        logger.info(f"Successfully cleared {len(items_to_clear)} items from inventory by {user_name}")
        return True
        
    except sqlite3.Error as e:
        logger.error(f"Database error while clearing inventory: {e}")
        conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

