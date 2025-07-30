import json
import subprocess
import shlex
import threading
import time
import re
import httpx
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.middleware.cors import CORSMiddleware  
from pydantic import BaseModel, Field
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor
import os
from dotenv import load_dotenv
import asyncio
import logging
import traceback

# Logging yapılandırması
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# .env dosyasını yükle
load_dotenv()

app = FastAPI(
    title="Multipass VM Management & AI Proxy API",
    description="Multipass sanal makinelerini yönetmek ve AI ile etkileşim kurmak için birleşik API",
    version="3.1.0"
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# VM oluşturma durumlarını takip etmek için
vm_creation_status: Dict[str, Dict] = {}
executor = ThreadPoolExecutor(max_workers=3)

# Config sınıfı
class Config:
    REQUEST_TIMEOUT = 30.0

config = Config()

# --- Pydantic Modelleri ---
class VM(BaseModel):
    name: str
    state: str
    ipv4: List[str]
    release: str
    cpus: Optional[str] = None
    memory: Optional[str] = None
    disk: Optional[str] = None
    image_hash: Optional[str] = None

class VMListResponse(BaseModel):
    list: List[VM]
    total: int

class StatusResponse(BaseModel):
    status: str
    message: str

class CreateVMRequest(BaseModel):
    name: str = Field(..., description="Oluşturulacak sanal makinenin adı.")
    config: Dict[str, str] = Field({}, description="Multipass launch komutu için ek yapılandırma.")

class ChatRequest(BaseModel):
    model: str
    messages: List[Dict[str, str]]
    stream: bool = False

class LegacyChatRequest(BaseModel):
    message: str
    sessionId: str

class AIVMListResponse(BaseModel):
    success: bool
    vms: List[VM]
    error: Optional[str] = None

# --- Yardımcı Fonksiyonlar ---
def format_bytes(byte_val):
    if byte_val is None: return "N/A"
    try:
        b = int(byte_val)
        if b < 1024: return f"{b}B"
        if b < 1024**2: return f"{b/1024:.1f}KB"
        if b < 1024**3: return f"{b/1024**2:.1f}MB"
        return f"{b/1024**3:.1f}GB"
    except (ValueError, TypeError): return "N/A"

def run_multipass_command(command: list, timeout=300):
    try:
        multipass_path = os.getenv("MULTIPASS_BIN", r"C:\Program Files\Multipass\bin\multipass.exe")
        logger.info(f"Multipass path: {multipass_path}")

        if not os.path.exists(multipass_path):
            logger.error(f"Multipass executable not found at path: {multipass_path}")
            return {"error": f"Multipass executable not found at path: {multipass_path}"}

        command[0] = multipass_path
        command_str = ' '.join(f'"{c}"' if ' ' in c else c for c in command)
        logger.info(f"Executing command: {command_str}")

        result = subprocess.run(
            command_str,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
            shell=True,
            encoding='utf-8',
            errors='replace'
        )
        
        logger.info(f"Command executed successfully")
        return {"success": True, "output": result.stdout}

    except FileNotFoundError as e:
        logger.error(f"FileNotFoundError: {e}")
        return {"error": f"Multipass command failed. Ensure '{multipass_path}' is correct."}
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip() if e.stderr else str(e)
        logger.error(f"Multipass command error: {error_message}")
        return {"error": f"Multipass command failed: {error_message}"}
    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out: {command_str}")
        return {"error": "Command timed out"}
    except Exception as e:
        logger.error(f"Unexpected error in run_multipass_command: {e}")
        return {"error": f"Unexpected error: {str(e)}"}

def run_multipass_command_old(command: list, timeout=300):
    """Eski endpoint'ler için uyumluluk (exception fırlatan versiyon)"""
    try:
        multipass_path = os.getenv("MULTIPASS_BIN", r"C:\Program Files\Multipass\bin\multipass.exe")
        logger.info(f"Multipass path: {multipass_path}")

        if not os.path.exists(multipass_path):
            logger.error(f"Multipass executable not found at path: {multipass_path}")
            raise HTTPException(status_code=500, detail=f"Multipass executable not found at path: {multipass_path}")

        command[0] = multipass_path
        command_str = ' '.join(f'"{c}"' if ' ' in c else c for c in command)
        logger.info(f"Executing command: {command_str}")

        result = subprocess.run(
            command_str,
            capture_output=True,
            text=True,
            check=True,
            timeout=timeout,
            shell=True,
            encoding='utf-8',
            errors='replace'
        )
        
        logger.info(f"Command executed successfully")
        return result.stdout

    except FileNotFoundError as e:
        logger.error(f"FileNotFoundError: {e}")
        raise HTTPException(500, f"Multipass command failed. Ensure '{multipass_path}' is correct.")
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip() if e.stderr else str(e)
        logger.error(f"Multipass command error: {error_message}")
        if "does not exist" in error_message:
            raise HTTPException(404, error_message)
        raise HTTPException(500, f"Multipass command failed: {error_message}")
    except subprocess.TimeoutExpired as e:
        logger.error(f"Command timed out: {command_str}")
        raise HTTPException(504, f"Command timed out")
    except Exception as e:
        logger.error(f"Unexpected error in run_multipass_command: {e}")
        raise HTTPException(500, f"Unexpected error: {str(e)}")

def extract_multipass_command(text: str) -> Optional[str]:
    """Metin içinden multipass komutunu çıkarır."""
    try:
        logger.info(f"Komut çıkarma deneniyor: {text[:200]}...")
        
        # 1. Markdown kod bloğu
        patterns = [
            r"```multipass\s+(.*?)\s*```",
            r"```bash\s*multipass\s+(.*?)\s*```", 
            r"```\s*multipass\s+(.*?)\s*```",
            r"`multipass\s+(.*?)`"
        ]
        
        for pattern in patterns:
            match = re.search(pattern, text, re.DOTALL)
            if match:
                command = match.group(1).strip()
                logger.info(f"Markdown'dan çıkarılan komut: {command}")
                return normalize_multipass_command(command)

        # 2. Satır satır arama
        lines = text.split('\n')
        for line in lines:
            cleaned_line = line.strip()
            
            if (cleaned_line.startswith("'") and cleaned_line.endswith("'")) or \
               (cleaned_line.startswith('"') and cleaned_line.endswith('"')):
                cleaned_line = cleaned_line[1:-1]

            if cleaned_line.startswith("multipass "):
                command = cleaned_line[len("multipass "):].strip()
                logger.info(f"Satırdan çıkarılan komut: {command}")
                return normalize_multipass_command(command)
        
        # 3. Basit regex araması
        simple_match = re.search(r"multipass\s+(\w+(?:\s+--?\w+(?:\s+\S+)?)*)", text)
        if simple_match:
            command = simple_match.group(1).strip()
            logger.info(f"Regex'den çıkarılan komut: {command}")
            return normalize_multipass_command(command)
        
        logger.info("Hiçbir komut bulunamadı")
        return None
        
    except Exception as e:
        logger.error(f"extract_multipass_command hatası: {e}")
        return None

def normalize_multipass_command(command: str) -> str:
    """Multipass komutlarını normalize eder."""
    try:
        if command.startswith("create"):
            command = command.replace("create", "launch", 1)
        
        replacements = {
            " -n ": " --name ",
            " -m ": " --memory ",
            " -d ": " --disk ",
            " -c ": " --cpus "
        }
        
        for short, long in replacements.items():
            command = command.replace(short, long)
        
        return command
    except Exception as e:
        logger.error(f"normalize_multipass_command hatası: {e}")
        return command

async def execute_vm_action_direct(command: str) -> Dict[str, Any]:
    """Doğrudan Multipass komutunu çalıştırır."""
    try:
        logger.info(f"execute_vm_action_direct başlatıldı: {command}")
        
        command = normalize_multipass_command(command)
        args = shlex.split(command)
        
        if args and args[0] == 'multipass':
            args = args[1:]
            
        if not args:
            return {"success": False, "error": "Geçersiz komut"}
            
        action = args[0]
        logger.info(f"Action: {action}")
        
        if action == "launch":
            vm_name = None
            vm_config = {}
            
            i = 1
            while i < len(args):
                if args[i] == "--name" and i + 1 < len(args):
                    vm_name = args[i + 1]
                    i += 2
                elif args[i].startswith("--") and i + 1 < len(args):
                    key = args[i][2:]
                    value = args[i + 1]
                    
                    if key in ['cpus', 'disk', 'memory']:
                        vm_config[key] = value
                    elif key == 'disk-size':
                        vm_config['disk'] = value
                    
                    i += 2
                else:
                    i += 1
            
            if not vm_name:
                return {"success": False, "error": "VM adı belirtilmedi"}
            
            # Asenkron VM oluşturma başlat
            asyncio.create_task(async_create_vm_background(vm_name, vm_config))
            
            return {
                "success": True, 
                "message": f"'{vm_name}' oluşturma işlemi başlatıldı. Durumu kontrol etmek için /vms/status/{vm_name} endpoint'ini kullanabilirsiniz.",
                "status": "started"
            }
            
        elif action == "start":
            if len(args) < 2:
                return {"success": False, "error": "VM adı belirtilmedi"}
            vm_name = args[1]
            
            full_command = [os.getenv("MULTIPASS_BIN", "multipass")] + args
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(executor, run_multipass_command, full_command)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            return {"success": True, "message": f"'{vm_name}' başlatıldı.", "status": "success"}
            
        elif action == "stop":
            if len(args) < 2:
                return {"success": False, "error": "VM adı belirtilmedi"}
            vm_name = args[1]
            
            full_command = [os.getenv("MULTIPASS_BIN", "multipass")] + args
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(executor, run_multipass_command, full_command)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            return {"success": True, "message": f"'{vm_name}' durduruldu.", "status": "success"}
            
        elif action == "delete":
            if len(args) < 2:
                return {"success": False, "error": "VM adı belirtilmedi"}
            vm_name = args[1]
            
            # Delete komutu
            delete_command = [os.getenv("MULTIPASS_BIN", "multipass"), "delete", vm_name]
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(executor, run_multipass_command, delete_command)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            # Purge komutu
            purge_command = [os.getenv("MULTIPASS_BIN", "multipass"), "purge"]
            purge_result = await loop.run_in_executor(executor, run_multipass_command, purge_command)
            
            return {"success": True, "message": f"'{vm_name}' silindi ve temizlendi.", "status": "success"}
            
        elif action == "purge":
            # Sadece purge komutu
            purge_command = [os.getenv("MULTIPASS_BIN", "multipass"), "purge"]
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(executor, run_multipass_command, purge_command)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            return {"success": True, "message": "Silinen sanal makineler tamamen temizlendi.", "status": "success"}
            
        else:
            full_command = [os.getenv("MULTIPASS_BIN", "multipass")] + args
            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(executor, run_multipass_command, full_command)
            
            if "error" in result:
                return {"success": False, "error": result["error"]}
            
            return {"success": True, "message": result.get("output", "Komut başarıyla çalıştırıldı"), "status": "success"}
    
    except Exception as e:
        logger.error(f"execute_vm_action_direct genel hatası: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {"success": False, "error": f"Komut çalıştırma hatası: {str(e)}"}

# --- VM İşlemleri ---
async def async_create_vm_background(vm_name: str, config_dict: Dict):
    """Arka planda VM oluşturur."""
    try:
        vm_creation_status[vm_name] = {"status": "creating", "message": f"'{vm_name}' oluşturuluyor..."}
        
        if not vm_name.replace('-', '').replace('_', '').isalnum():
            vm_creation_status[vm_name] = {"status": "error", "message": "VM adı sadece harf, rakam, tire ve alt çizgi içerebilir."}
            return

        allowed_params = {
            "mem": "--memory", "memory": "--memory", "disk": "--disk", "cpus": "--cpus"
        }

        command = [os.getenv("MULTIPASS_BIN", "multipass"), "launch", "--name", vm_name]

        for key, value in config_dict.items():
            if key in allowed_params and value:
                command.extend([allowed_params[key], str(value)])
        
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(executor, run_multipass_command, command, 600)
        
        if "error" in result:
            vm_creation_status[vm_name] = {
                "status": "error", 
                "message": f"VM oluşturma hatası: {result['error']}"
            }
            return
        
        time.sleep(3)
        
        # VM bilgilerini al
        try:
            info_result = await loop.run_in_executor(executor, run_multipass_command, ["multipass", "info", vm_name, "--format", "json"])
            if "error" not in info_result:
                vm_info = json.loads(info_result["output"])
                vm_creation_status[vm_name] = {
                    "status": "completed", 
                    "message": f"'{vm_name}' başarıyla oluşturuldu!",
                    "vm_info": vm_info
                }
            else:
                vm_creation_status[vm_name] = {
                    "status": "completed", 
                    "message": f"'{vm_name}' oluşturuldu ancak detaylar alınamadı."
                }
        except Exception as info_error:
            logger.error(f"VM info alınamadı: {info_error}")
            vm_creation_status[vm_name] = {
                "status": "completed", 
                "message": f"'{vm_name}' oluşturuldu ancak detaylar alınamadı."
            }
            
    except Exception as e:
        logger.error(f"async_create_vm_background hatası: {e}")
        vm_creation_status[vm_name] = {
            "status": "error", 
            "message": f"VM oluşturma hatası: {str(e)}"
        }

# --- API Endpoint'leri ---
@app.get("/vms/list", response_model=VMListResponse)
async def list_vms():
    """VM listesini döndürür."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor,
            run_multipass_command_old,
            [os.getenv("MULTIPASS_BIN", "multipass"), "list", "--format=json"]
        )
        data = json.loads(result)
        
        vms_raw = data.get("list", [])
        vms_final = []

        async def get_vm_info(vm_data):
            final_data = vm_data.copy()
            
            return VM(
                name=final_data.get("name", ""),
                state=final_data.get("state", ""),
                ipv4=final_data.get("ipv4", []),
                release=final_data.get("release", ""),
                cpus=str(final_data.get("cpus")) if final_data.get("cpus") else None,
                memory=str(final_data.get("memory")) if final_data.get("memory") else None,
                disk=str(final_data.get("disk")) if final_data.get("disk") else None,
                image_hash=final_data.get("image_hash", "")
            )

        tasks = [get_vm_info(vm) for vm in vms_raw]
        vms_final = await asyncio.gather(*tasks)
        
        return VMListResponse(list=vms_final, total=len(vms_final))
    except Exception as e:
        logger.error(f"list_vms hatası: {e}")
        raise HTTPException(500, f"VM listesi alınamadı: {str(e)}")

@app.post("/vms/start/{vm_name}", response_model=StatusResponse)
async def start_vm(vm_name: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, run_multipass_command_old, ["multipass", "start", vm_name])
    return StatusResponse(status="başlatıldı", message=f"'{vm_name}' başlatıldı.")

@app.post("/vms/stop/{vm_name}", response_model=StatusResponse)
async def stop_vm(vm_name: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, run_multipass_command_old, ["multipass", "stop", vm_name])
    return StatusResponse(status="durduruldu", message=f"'{vm_name}' durduruldu.")

@app.delete("/vms/delete/{vm_name}", response_model=StatusResponse)
async def delete_vm(vm_name: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, run_multipass_command_old, ["multipass", "delete", vm_name])
    await loop.run_in_executor(executor, run_multipass_command_old, ["multipass", "purge"])
    return StatusResponse(status="silindi", message=f"'{vm_name}' silindi ve temizlendi.")

@app.post("/vms/purge", response_model=StatusResponse)
async def purge_vms():
    """Silinen VM'leri tamamen temizler (purge komutu)."""
    try:
        loop = asyncio.get_event_loop()
        await loop.run_in_executor(executor, run_multipass_command_old, ["multipass", "purge"])
        return StatusResponse(status="temizlendi", message="Silinen sanal makineler tamamen temizlendi.")
    except Exception as e:
        logger.error(f"purge_vms hatası: {e}")
        raise HTTPException(500, f"Temizleme işlemi başarısız: {str(e)}")

@app.post("/vms/create", response_model=StatusResponse)
async def create_vm(create_vm_request: CreateVMRequest):
    """Senkron VM oluşturma."""
    vm_name = create_vm_request.name
    if not vm_name.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(400, "VM adı sadece harf, rakam, tire ve alt çizgi içerebilir.")
    
    try:
        command = [os.getenv("MULTIPASS_BIN", "multipass"), "launch", "--name", vm_name]
        
        for key, value in create_vm_request.config.items():
            if key in ['cpus', 'memory', 'disk']:
                command.extend([f"--{key}", value])
            else:
                command.extend([f"--{key}", value])
        
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(executor, run_multipass_command_old, command)
        return StatusResponse(status="created", message=f"Sanal makine '{vm_name}' başarıyla oluşturuldu.")
    
    except Exception as e:
        logger.error(f"create_vm hatası: {e}")
        raise HTTPException(500, f"VM oluşturma hatası: {str(e)}")

@app.post("/vms/create-async", response_model=StatusResponse)
async def create_vm_async(create_vm_request: CreateVMRequest):
    """Asenkron VM oluşturma."""
    vm_name = create_vm_request.name
    if not vm_name.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(400, "VM adı sadece harf, rakam, tire ve alt çizgi içerebilir.")
    
    asyncio.create_task(async_create_vm_background(vm_name, create_vm_request.config))
    
    return StatusResponse(
        status="started", 
        message=f"'{vm_name}' oluşturma işlemi başlatıldı."
    )

@app.get("/vms/status/{vm_name}")
async def get_vm_creation_status(vm_name: str):
    """VM oluşturma durumunu kontrol eder."""
    if vm_name not in vm_creation_status:
        return {"status": "unknown", "message": f"'{vm_name}' için oluşturma işlemi bulunamadı."}
    
    return vm_creation_status[vm_name]

@app.get("/")
async def root():
    """API durumu."""
    return {
        "message": "Multipass VM Management API çalışıyor",
        "version": "3.1.0",
        "multipass_bin": os.getenv("MULTIPASS_BIN", "multipass")
    }

@app.get("/health")
async def health_check():
    """Sağlık kontrolü."""
    try:
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            executor, 
            run_multipass_command, 
            [os.getenv("MULTIPASS_BIN", "multipass"), "version"]
        )
        
        if "error" in result:
            return {
                "status": "unhealthy", 
                "multipass": "unavailable",
                "error": result["error"],
                "multipass_bin": os.getenv("MULTIPASS_BIN", "multipass")
            }
        
        return {
            "status": "healthy", 
            "multipass": "available", 
            "version": result.get("output", "").strip(),
            "multipass_bin": os.getenv("MULTIPASS_BIN", "multipass")
        }
    except Exception as e:
        logger.error(f"Health check hatası: {e}")
        return {
            "status": "unhealthy", 
            "multipass": "unavailable",
            "error": str(e),
            "multipass_bin": os.getenv("MULTIPASS_BIN", "multipass")
        }

# --- DÜZELTILEN CHAT ENDPOINT ---
@app.post("/chat")
async def chat_endpoint(request: LegacyChatRequest):
    """Kullanıcıdan gelen mesajı işler ve gerekli komutları çalıştırır."""
    try:
        logger.info(f"Chat endpoint çağrıldı. Message: {request.message[:100]}...")
        
        # Doğrudan multipass komutu kontrolü
        if request.message.strip().startswith('multipass '):
            logger.info("Doğrudan multipass komutu tespit edildi")
            command = request.message.strip()[len('multipass '):].strip()
            result = await execute_vm_action_direct(command)
            
            if not result.get('success', False):
                return {
                    "response": f"❌ Hata: {result.get('error', 'Bilinmeyen hata')}",
                    "execution_results": [{
                        "success": False,
                        "operation": "direct_command",
                        "details": result.get('error', 'Bilinmeyen hata')
                    }]
                }
            
            return {
                "response": f"✅ Komut başarıyla çalıştırıldı!\n\nSonuç: {result.get('message', 'Başarılı')}",
                "execution_results": [{
                    "success": True,
                    "operation": "direct_command",
                    "details": result.get('message', 'Başarılı')
                }]
            }
    
        # AI ile işlem yapma kısmı
        logger.info("AI ile işlem başlatılıyor")
        
        # OLLAMA_URL ve OLLAMA_MODEL kontrolü
        ollama_url = os.getenv("OLLAMA_URL")
        ollama_model = os.getenv("OLLAMA_MODEL")
        
        if not ollama_url:
            logger.error("OLLAMA_URL tanımlı değil")
            return {
                "response": "❌ Hata: OLLAMA_URL environment variable tanımlı değil. Lütfen yapılandırmanızı kontrol edin.",
                "execution_results": [{
                    "success": False,
                    "operation": "ai_config_check",
                    "details": "OLLAMA_URL environment variable is not set"
                }]
            }
        
        if not ollama_model:
            logger.error("OLLAMA_MODEL tanımlı değil")
            return {
                "response": "❌ Hata: OLLAMA_MODEL environment variable tanımlı değil. Lütfen yapılandırmanızı kontrol edin.",
                "execution_results": [{
                    "success": False,
                    "operation": "ai_config_check",
                    "details": "OLLAMA_MODEL environment variable is not set"
                }]
            }
        
        logger.info(f"Ollama URL: {ollama_url}, Model: {ollama_model}")
        
        # AI ile iletişim
        try:
            async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
                ollama_request = {
                    "model": ollama_model,
                    "prompt": f"""Sen, Multipass sanal makinelerini yöneten ve her zaman Türkçe cevap veren yardımsever bir asistansın. 

ÖNEMLİ KURALLAR:
1. Tüm multipass komutlarını HER ZAMAN ```multipass ...``` şeklinde ver
2. 'create' komutu yoktur, 'launch' kullan
3. Kısa parametreler (-n, -m, -d, -c) yerine uzun parametreler (--name, --memory, --disk, --cpus) kullan

ÖRNEKLER:
- VM oluştur: ```multipass launch --name test-vm --memory 2G --disk 10G --cpus 2```
- VM başlat: ```multipass start vm-adı```
- VM durdur: ```multipass stop vm-adı```
- VM sil: ```multipass delete vm-adı```
- VM listele: ```multipass list```

Kullanıcı: {request.message}
Asistan:""",
                    "stream": False
                }
                
                logger.info("Ollama'ya istek gönderiliyor")
                
                response = await client.post(f"{ollama_url}/api/generate", json=ollama_request)
                response.raise_for_status()
                ai_response = response.json()
                ai_message = ai_response.get("response", "")
                
                logger.info(f"AI yanıtı alındı, uzunluk: {len(ai_message)}")
                
        except httpx.RequestError as e:
            logger.error(f"Ollama RequestError: {e}")
            return {
                "response": f"❌ Hata: Ollama sunucusuna ulaşılamadı. Sunucunun çalıştığından emin olun.\n\nDetay: {str(e)}",
                "execution_results": [{
                    "success": False,
                    "operation": "ai_request",
                    "details": f"Ollama RequestError: {str(e)}"
                }]
            }
        except httpx.HTTPStatusError as e:
            logger.error(f"Ollama HTTPStatusError: {e}")
            error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
            return {
                "response": f"❌ Hata: Ollama API hatası.\n\nDetay: {error_text}",
                "execution_results": [{
                    "success": False,
                    "operation": "ai_request",
                    "details": f"Ollama HTTPStatusError: {error_text}"
                }]
            }
        except Exception as e:
            logger.error(f"AI request genel hatası: {e}")
            return {
                "response": f"❌ Hata: AI ile iletişim kurulurken beklenmeyen bir hata oluştu.\n\nDetay: {str(e)}",
                "execution_results": [{
                    "success": False,
                    "operation": "ai_request",
                    "details": f"Unexpected AI error: {str(e)}"
                }]
            }

        # AI yanıtından komut çıkar ve çalıştır
        command = extract_multipass_command(ai_message)
        execution_results = []
        
        if command:
            logger.info(f"AI'dan çıkarılan komut: {command}")
            try:
                execution_result = await execute_vm_action_direct(command)
                logger.info(f"Çalıştırma sonucu: {execution_result}")
                
                execution_results.append({
                    "success": execution_result.get("success", False),
                    "operation": "multipass_command", 
                    "command": command,
                    "details": execution_result.get("message") or execution_result.get("error", "Bilinmeyen sonuç")
                })
                
                # Sonucu AI mesajına ekle
                is_async_creation = execution_result.get("status") == "started"

                if execution_result.get("success") or is_async_creation:
                    result_text = execution_result.get("message", "İşlem başarılı")
                    ai_message += f"\n\n✅ **İşlem Sonucu:** {result_text}"
                else:
                    error_text = execution_result.get("error", "Bilinmeyen hata")
                    ai_message += f"\n\n❌ **Hata:** {error_text}"
                    
            except Exception as command_error:
                logger.error(f"Komut çalıştırma hatası: {command_error}")
                execution_results.append({
                    "success": False,
                    "operation": "multipass_command", 
                    "command": command,
                    "details": f"Komut çalıştırma hatası: {str(command_error)}"
                })
                ai_message += f"\n\n❌ **Komut Çalıştırma Hatası:** {str(command_error)}"
        else:
            logger.info("AI mesajından komut çıkarılamadı")
        
        logger.info("Chat endpoint başarıyla tamamlandı")
        
        return {
            "response": ai_message,
            "execution_results": execution_results
        }

    except Exception as e:
        logger.error(f"Chat endpoint genel hatası: {e}")
        logger.error(f"Traceback: {traceback.format_exc()}")
        return {
            "response": f"❌ Beklenmeyen bir hata oluştu: {str(e)}",
            "execution_results": [{
                "success": False,
                "operation": "chat_endpoint",
                "details": f"Unexpected error: {str(e)}"
            }]
        }

@app.get("/vms/list-ai", response_model=AIVMListResponse)
async def list_vms_ai():
    """VM listesini AI proxy formatında döndürür."""
    try:
        vm_list_response = await list_vms()
        return AIVMListResponse(success=True, vms=vm_list_response.list)
    except Exception as e:
        logger.error(f"list_vms_ai hatası: {e}")
        error_message = str(e)
        return AIVMListResponse(success=False, vms=[], error=f"VM listesi alınamadı: {error_message}")

if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PROXY_SERVER_PORT", 8001))
    uvicorn.run(app, host="0.0.0.0", port=port)