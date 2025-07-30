import os
from dotenv import load_dotenv

# Proje kök dizinindeki .env dosyasını yükle
load_dotenv()

# --- Sunucu Ayarları ---
API_SERVER_URL = os.getenv("API_SERVER_URL", "http://localhost:8000")
PROXY_SERVER_PORT = int(os.getenv("PROXY_SERVER_PORT", 5001))

# --- Ollama AI Ayarları ---
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral-faiss-rag:latest")

# --- Zaman Aşımı Ayarları ---
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", 120))

# --- Multipass Path ---
MULTIPASS_BIN = os.getenv("MULTIPASS_BIN", "multipass")
