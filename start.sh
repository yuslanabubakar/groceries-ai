#!/bin/bash

# Skrip ini akan dijalankan setiap kali kontainer Docker dimulai.

# 1. Jalankan skrip setup database untuk memastikan tabel sudah ada.
echo "--- Menjalankan setup database ---"
python database_setup.py

# 2. Mulai aplikasi utama (server FastAPI).
echo "--- Memulai server FastAPI ---"
uvicorn main:app --host 0.0.0.0 --port 80