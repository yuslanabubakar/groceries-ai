# database_setup.py
# This script creates and initializes the SQLite database for the MyGroceries bot.
# It's designed to be run safely every time the container starts.

import sqlite3
from sqlite3 import Error
import os
import logging

# --- Setup ---
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

def create_connection(db_file):
    """ Create a database connection to a SQLite database """
    conn = None
    try:
        # Ensure the directory for the database file exists
        os.makedirs(os.path.dirname(db_file), exist_ok=True)
        conn = sqlite3.connect(db_file)
        return conn
    except Error as e:
        logger.error(f"Error connecting to database: {e}")
    return conn

def create_table(conn, create_table_sql):
    """ Create a table from the create_table_sql statement """
    try:
        c = conn.cursor()
        c.execute(create_table_sql)
    except Error as e:
        logger.error(f"Error creating table: {e}")

def main():
    # This path is inside the Docker container and is mapped to the ./data folder on your host
    database_path = "/app/data/groceries.db"

    # SQL statement for creating the inventory table
    # Using 'IF NOT EXISTS' makes this script safe to run multiple times
    sql_create_inventory_table = """
    CREATE TABLE IF NOT EXISTS inventory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT NOT NULL UNIQUE,
        quantity REAL NOT NULL DEFAULT 0,
        unit TEXT,
        last_updated TIMESTAMP NOT NULL,
        last_updated_by TEXT NOT NULL
    );
    """

    # SQL statement for creating the transaction_log table
    sql_create_transaction_log_table = """
    CREATE TABLE IF NOT EXISTS transaction_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        item_name TEXT NOT NULL,
        quantity_change REAL NOT NULL,
        user_name TEXT NOT NULL,
        transaction_time TIMESTAMP NOT NULL
    );
    """

    # Create a database connection
    conn = create_connection(database_path)

    # Create tables
    if conn is not None:
        create_table(conn, sql_create_inventory_table)
        logger.info("Table 'inventory' checked/created successfully.")

        create_table(conn, sql_create_transaction_log_table)
        logger.info("Table 'transaction_log' checked/created successfully.")

        conn.close()
    else:
        logger.error("Error! Cannot create the database connection.")

if __name__ == '__main__':
    main()
