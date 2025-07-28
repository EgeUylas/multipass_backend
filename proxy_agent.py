import os
import requests
import json
import logging
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

# --- Logging Configuration ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("proxy_agent")

# ✅ ENV ayarları (default fallback)
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral-faiss-rag:latest")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))

# --- Pydantic Model ---
class ChatMessage(BaseModel):
    message: str
    sessionId: str

# --- FastAPI Initialization ---
app = FastAPI(
    title="Ollama Proxy Agent",
    description="FastAPI proxy to interact with Ollama's /api/generate endpoint.",
    version="1.1.1"
)

# --- CORS Setup ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- In-memory conversation store ---
conversation_histories = {}

# --- Ollama Proxy Class ---
class OllamaProxy:
    def __init__(self, model: str):
        self.model = model
        self.api_generate = f"{OLLAMA_URL}/api/generate"
        self.api_tags = f"{OLLAMA_URL}/api/tags"
        self.client = requests.Session()
        self.client.headers.update({
            "Content-Type": "application/json"
        })

    def check_model_exists(self):
        try:
            resp = self.client.get(self.api_tags, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            available_models = [m.get("name", "") for m in data.get("models", [])]
            log.info(f"📦 Ollama'daki modeller: {available_models}")

            # 🔧 Esnek kontrol: model adı başıyla başlıyorsa yeterli
            if not any(m.startswith(self.model) for m in available_models):
                raise HTTPException(
                    status_code=503,
                    detail=(
                        f"Model '{self.model}' Ollama'da bulunamadı. "
                        f"ollama list çıktınızı ve 'ollama create {self.model}' adımlarını kontrol edin."
                    )
                )
            log.info(f"🟢 Model doğrulandı: {self.model}")
        except requests.exceptions.RequestException as e:
            log.error(f"❌ Ollama /api/tags erişim hatası: {e}")
            raise HTTPException(status_code=503, detail=f"Ollama'a erişilemedi: {str(e)}")

    def _convert_messages_to_prompt(self, messages: list) -> str:
        prompt = ""
        for msg in messages:
            role = msg.get("role", "user")
            content = msg.get("content", "")
            if role == "system":
                prompt += f"[SİSTEM]: {content}\n"
            elif role == "user":
                prompt += f"[KULLANICI]: {content}\n"
            elif role == "assistant":
                prompt += f"[ASİSTAN]: {content}\n"
        return prompt + "[ASİSTAN]:"

    def run(self, messages: list):
        if not messages:
            raise HTTPException(status_code=400, detail="Mesaj listesi boş gönderilemez.")

        prompt = self._convert_messages_to_prompt(messages)

        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False
        }

        log.info(f"➡️ Ollama'ya istek gönderiliyor ({self.api_generate}) | model={self.model}")
        log.debug(f"Payload: {json.dumps(payload, indent=2, ensure_ascii=False)}")

        try:
            response = self.client.post(self.api_generate, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            response_data = response.json()

            content = response_data.get("response", "").strip()
            if not content:
                content = "❌ Modelden boş yanıt döndü."
            log.info(f"✅ Cevap alındı: {content[:200]}{'...' if len(content) > 200 else ''}")
            return {"response": content}

        except requests.exceptions.Timeout:
            log.error("⏱️ Ollama zaman aşımına uğradı.")
            raise HTTPException(status_code=504, detail="Ollama yanıt vermedi (timeout).")
        except requests.exceptions.RequestException as e:
            log.error(f"❌ Ollama bağlantı hatası: {e}")
            raise HTTPException(status_code=503, detail=f"Ollama bağlantısı başarısız: {str(e)}")
        except Exception as e:
            log.error(f"🚨 Beklenmeyen hata: {e}", exc_info=True)
            raise HTTPException(status_code=500, detail="Sunucu hatası.")

# --- Proxy instance ---
ollama_proxy = OllamaProxy(model=OLLAMA_MODEL)

# --- FastAPI Routes ---
@app.on_event("startup")
async def on_startup():
    log.info("🚀 Ollama Proxy başlatıldı.")
    log.info(f"📡 Model: {ollama_proxy.model}")
    log.info(f"🌐 API Generate: {ollama_proxy.api_generate}")
    ollama_proxy.check_model_exists()

@app.get("/")
def root():
    return {
        "message": "Ollama proxy çalışıyor.",
        "model": ollama_proxy.model,
        "ollama_url": OLLAMA_URL,
        "timeout": REQUEST_TIMEOUT
    }

@app.get("/health")
def health():
    try:
        ollama_proxy.check_model_exists()
        return {"ok": True, "model": ollama_proxy.model}
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "detail": e.detail}

@app.post("/chat")
async def chat(chat_message: ChatMessage, request: Request):
    session_id = chat_message.sessionId
    user_input = chat_message.message
    client_ip = request.client.host

    if not session_id:
        raise HTTPException(status_code=400, detail="Session ID zorunludur.")

    log.info(f"💬 Yeni mesaj alındı [{client_ip}] | Oturum: {session_id}")

    if session_id not in conversation_histories:
        conversation_histories[session_id] = [
            {"role": "system", "content": "Sen, Multipass sanal makinelerini yöneten ve her zaman Türkçe cevap veren yardımsever bir asistansın. Kullanıcının komutlarını yorumla ve VM işlemleri için yardımcı ol."}
        ]

    history = conversation_histories[session_id]
    history.append({"role": "user", "content": user_input})

    if len(history) > 10:
        history = [history[0]] + history[-9:]

    try:
        result = ollama_proxy.run(history)

        if result and 'response' in result:
            history.append({"role": "assistant", "content": result['response']})
            conversation_histories[session_id] = history

        return result

    except HTTPException as e:
        log.error(f"🔥 Hata (HTTP {e.status_code}): {e.detail}")
        raise e
    except Exception as e:
        log.error(f"🔥 Beklenmeyen hata: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Sunucu hatası oluştu.")

@app.get("/vms/list")
async def list_vms_mock():
    log.info("📥 /vms/list mock endpoint çağrıldı.")
    return {"success": True, "vms": []}

# Çalıştırma komutu:
# uvicorn proxy_agent:app --host 0.0.0.0 --port 5001 --reload
