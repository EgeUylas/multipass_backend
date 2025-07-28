import os
import requests
import json
import logging
import subprocess
import shutil
import threading
import re
import shlex
from typing import List, Dict, Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
log = logging.getLogger("proxy_agent")

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "mistral-faiss-rag:latest")
REQUEST_TIMEOUT = int(os.getenv("REQUEST_TIMEOUT", "120"))
EXEC_TIMEOUT = int(os.getenv("EXEC_TIMEOUT", "120"))
ALLOW_SHELL = os.getenv("ALLOW_SHELL", "false").lower() == "true"
MULTIPASS_BIN = os.getenv("MULTIPASS_BIN", "").strip()

class ChatMessage(BaseModel):
    message: str
    sessionId: str

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

conversation_histories: Dict[str, List[Dict[str, str]]] = {}

def is_multipass_command(cmd: str) -> bool:
    cmd = cmd.strip()
    # Direct multipass command
    if cmd.startswith("multipass "):
        return True
    # Extract command from quotes if it's in conversation format
    if "'" in cmd:
        # Extract content between single quotes
        import re
        matches = re.findall(r"'([^']+)'", cmd)
        for match in matches:
            if is_potential_multipass_command(match.strip()):
                return True
    # Check if it's a potential multipass command without prefix
    return is_potential_multipass_command(cmd)

def is_potential_multipass_command(cmd: str) -> bool:
    """Check if a command looks like a multipass command even without the 'multipass' prefix"""
    cmd = cmd.strip().lower()
    multipass_keywords = [
        'launch', 'create', 'start', 'stop', 'delete', 'remove', 'list', 
        'info', 'shell', 'exec', 'mount', 'umount', 'transfer', 'copy',
        'suspend', 'restart', 'recover', 'purge', 'find', 'get', 'set'
    ]
    # Check if command starts with any multipass keyword
    for keyword in multipass_keywords:
        if cmd.startswith(keyword + ' ') or cmd == keyword:
            return True
    # Check for common multipass patterns
    if any(pattern in cmd for pattern in ['-n ', '--name ', '-c ', '--cpus ', '-m ', '--memory ', '-d ', '--disk ']):
        return True
    return False

def resolve_multipass_path() -> str:
    if MULTIPASS_BIN and os.path.exists(MULTIPASS_BIN):
        return MULTIPASS_BIN
    which = shutil.which("multipass")
    if which:
        return which
    return ""

def multipass_command_fix(cmd: str) -> str:
    # --cpu yerine --cpus düzelt
    cmd = re.sub(r"--cpu\b", "--cpus", cmd)
    # image adını kontrol et, eksikse başa 22.04 ekle
    # multipass launch --name deneme-vm ... -> multipass launch 22.04 --name deneme-vm ...
    pattern = r"(multipass launch)(?!\s+\d{2}\.\d{2}|\s+jammy|\s+noble)"
    if re.match(pattern, cmd):
        cmd = re.sub(r"(multipass launch)", r"\1 22.04", cmd, count=1)
    # 'ubuntu-22.04' gibi yanlışları da 22.04 yap
    cmd = re.sub(r"(multipass launch )ubuntu-22\.04", r"\g<1>22.04", cmd)
    cmd = re.sub(r"(multipass launch )ubuntu-24\.04", r"\g<1>24.04", cmd)
    cmd = re.sub(r"(multipass launch )ubuntu-20\.04", r"\g<1>20.04", cmd)
    return cmd

def normalize_command(cmd: str) -> str:
    cmd = cmd.strip()
    if not is_multipass_command(cmd):
        return cmd
    
    # Extract command from quotes if it's in conversation format
    if "'" in cmd and not cmd.startswith("multipass "):
        import re
        matches = re.findall(r"'([^']+)'", cmd)
        for match in matches:
            if is_potential_multipass_command(match.strip()):
                cmd = match.strip()
                break
    
    # Add multipass prefix if missing
    if not cmd.startswith("multipass "):
        cmd = "multipass " + cmd
    
    mp_path = resolve_multipass_path()
    if not mp_path:
        return cmd  # Bulunamayan multipass, execute'da yakalanacak
    qpath = f'"{mp_path}"' if " " in mp_path else mp_path
    return cmd.replace("multipass", qpath, 1)

def split_commands(text: str) -> List[str]:
    commands: List[str] = []
    for line in text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        parts = [p.strip() for p in line.split("&&")]
        commands.extend([p for p in parts if p])
    return commands

# ---- Asenkron launch için status store ----
vm_status: Dict[str, Dict[str, Any]] = {}

def async_launch_vm(cmd: str, vm_name: str):
    log.info(f"🚀 VM oluşturma başlıyor: {vm_name}")
    log.info(f"📝 Çalıştırılacak komut: {cmd}")
    vm_status[vm_name] = {"status": "creating", "message": f'VM "{vm_name}" oluşturuluyor...'}
    result = execute_multipass_command(cmd)
    log.info(f"✅ Komut sonucu - Return Code: {result['returncode']}")
    log.info(f"📤 STDOUT: {result['stdout']}")
    log.info(f"📥 STDERR: {result['stderr']}")
    if result["returncode"] == 0:
        vm_status[vm_name] = {"status": "completed", "message": f'VM "{vm_name}" başarıyla oluşturuldu.'}
        log.info(f"🎉 VM {vm_name} başarıyla oluşturuldu!")
    else:
        msg = result['stderr'] or "Bilinmeyen hata"
        vm_status[vm_name] = {"status": "error", "message": f'Oluşturulamadı: {msg}'}
        log.error(f"❌ VM {vm_name} oluşturulamadı: {msg}")

def execute_multipass_command(command: str) -> Dict[str, Any]:
    if not is_multipass_command(command):
        return {"command": command, "stdout": "", "stderr": "Multipass komutu değil.", "returncode": -1}
    mp_path = resolve_multipass_path()
    if not mp_path:
        return {
            "command": command,
            "normalized": "",
            "stdout": "",
            "stderr": "",
            "returncode": -127
        }
    normalized = normalize_command(command)
    log.info(f"\U0001F680 Multipass komutu çalıştırılıyor: {normalized} (shell=False)")
    try:
        result = subprocess.run(
            shlex.split(normalized),
            shell=False,
            capture_output=True,
            text=True,
            timeout=EXEC_TIMEOUT
        )
        log.info(f"\U0001F4E4 STDOUT: {result.stdout.strip()}")
        log.info(f"\U0001F4E5 STDERR: {result.stderr.strip()}")
        log.info(f"\U0001F51A RETURN CODE: {result.returncode}")
        return {
            "command": command,
            "normalized": normalized,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "command": command,
            "normalized": normalized,
            "stdout": "",
            "stderr": "Komut zaman aşımına uğradı.",
            "returncode": -2
        }
    except Exception as e:
        error_msg = f"Beklenmeyen hata: {str(e)}"
        log.error(f"❌ Komut çalıştırma hatası: {error_msg}")
        return {
            "command": command,
            "normalized": normalized,
            "stdout": "",
            "stderr": error_msg,
            "returncode": -3
        }

class OllamaProxy:
    def __init__(self, model: str):
        self.model = model
        self.api_generate = f"{OLLAMA_URL}/api/generate"
        self.api_tags = f"{OLLAMA_URL}/api/tags"
        self.client = requests.Session()
        self.client.headers.update({"Content-Type": "application/json"})

    def check_model_exists(self):
        try:
            resp = self.client.get(self.api_tags, timeout=REQUEST_TIMEOUT)
            resp.raise_for_status()
            data = resp.json()
            available_models = [m.get("name", "") for m in data.get("models", [])]
            if not any(m.startswith(self.model) for m in available_models):
                raise HTTPException(status_code=503, detail=f"Model '{self.model}' Ollama'da bulunamadı.")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=503, detail=f"Ollama erişim hatası: {str(e)}")

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
        payload = {"model": self.model, "prompt": prompt, "stream": False}
        try:
            response = self.client.post(self.api_generate, json=payload, timeout=REQUEST_TIMEOUT)
            response.raise_for_status()
            response_data = response.json()
            return {"response": response_data.get("response", "").strip() or "❌ Modelden boş yanıt döndü."}
        except requests.exceptions.Timeout:
            raise HTTPException(status_code=504, detail="Ollama yanıt vermedi (timeout).")
        except requests.exceptions.RequestException as e:
            raise HTTPException(status_code=503, detail=f"Ollama bağlantısı başarısız: {str(e)}")

ollama_proxy = OllamaProxy(model=OLLAMA_MODEL)

@app.on_event("startup")
def on_startup():
    ollama_proxy.check_model_exists()

@app.get("/")
def root():
    return {"message": "Ollama proxy çalışıyor.", "model": ollama_proxy.model}

@app.get("/health")
def health():
    try:
        ollama_proxy.check_model_exists()
        path = resolve_multipass_path()
        return {"ok": True, "model": ollama_proxy.model, "multipass_path": path}
    except HTTPException as e:
        return {"ok": False, "status": e.status_code, "detail": e.detail}

@app.post("/chat")
def chat(chat_message: ChatMessage, request: Request):
    session_id = chat_message.sessionId
    user_input = chat_message.message
    if not session_id:
        raise HTTPException(status_code=400, detail="Session ID zorunludur.")

    if session_id not in conversation_histories:
        conversation_histories[session_id] = [
            {"role": "system", "content": "Sen, Multipass sanal makinelerini yöneten Türkçe konuşan bir asistansın."}
        ]
    history = conversation_histories[session_id]
    history.append({"role": "user", "content": user_input})
    if len(history) > 10:
        history = [history[0]] + history[-9:]

    result = ollama_proxy.run(history)
    model_output = result["response"]
    history.append({"role": "assistant", "content": model_output})
    conversation_histories[session_id] = history

    commands = split_commands(model_output)
    log.info(f"🔍 Model çıktısından {len(commands)} komut bulundu: {commands}")
    exec_results = []
    for cmd in commands:
        log.info(f"🔧 İşlenen komut: {cmd}")
        if is_multipass_command(cmd):
            log.info(f"✅ Multipass komutu tespit edildi: {cmd}")
            # Modelin yanlış komutunu otomatik düzelt!
            cmd_fixed = multipass_command_fix(cmd)
            log.info(f"🛠️ Düzeltilmiş komut: {cmd_fixed}")
            if cmd_fixed.startswith("multipass launch"):
                match = re.search(r"--name\s+([^\s]+)", cmd_fixed)
                vm_name = match.group(1) if match else "unknown"
                log.info(f"🚀 VM launch komutu tespit edildi, VM adı: {vm_name}")
                threading.Thread(target=async_launch_vm, args=(cmd_fixed, vm_name), daemon=True).start()
                exec_results.append({
                    "command": cmd_fixed,
                    "stdout": f'VM "{vm_name}" oluşturuluyor...',
                    "stderr": "",
                    "returncode": 0
                })
            else:
                log.info(f"⚙️ Diğer multipass komutu çalıştırılıyor: {cmd_fixed}")
                exec_results.append(execute_multipass_command(cmd_fixed))
        else:
            log.warning(f"⚠️ Multipass komutu değil: {cmd}")
            exec_results.append({"command": cmd, "stdout": "", "stderr": "Geçerli bir Multipass komutu değil.", "returncode": -1})

    return {"response": model_output, "executed": exec_results}

@app.get("/vms/list")
def list_vms():
    mp_path = resolve_multipass_path()
    if not mp_path:
        return {
            "success": True,
            "vms": [],
            "message": "Hiç sanal makine oluşturulmamış veya Multipass kurulu değil."
        }
    result = execute_multipass_command("multipass list --format json")
    if result["returncode"] == 0:
        try:
            json_output = json.loads(result["stdout"])
            if not json_output.get("list"):
                return {
                    "success": True,
                    "vms": [],
                    "message": "Hiç sanal makine oluşturulmamış."
                }
            return {"success": True, "vms": json_output.get("list", [])}
        except json.JSONDecodeError:
            return {"success": False, "error": "Çıktı JSON değil.", "raw": result["stdout"]}
    if "[WinError 2]" in result["stderr"] or "bulunamadı" in result["stderr"]:
        return {
            "success": True,
            "vms": [],
            "message": "Hiç sanal makine oluşturulmamış veya Multipass kurulu değil."
        }
    return {
        "success": True,
        "vms": [],
        "message": "Hiç sanal makine oluşturulmamış."
    }

@app.get("/vms/status/{vm_name}")
def get_vm_status(vm_name: str):
    return vm_status.get(vm_name, {"status": "unknown", "message": "VM bulunamadı."})
