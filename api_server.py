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
import config
import asyncio

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

# AI Proxy için Pydantic Modelleri
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
        if command[0] == "multipass": 
            command[0] = config.MULTIPASS_BIN
        
        print(f"Çalıştırılacak komut: {' '.join(command)}")  # Debug için
        
        result = subprocess.run(
            command, 
            capture_output=True, 
            text=True, 
            check=True, 
            timeout=timeout,
            shell=False  # Windows uyumluluğu için
        )
        return result.stdout
    except FileNotFoundError:
        raise HTTPException(500, f"Multipass bulunamadı. 'multipass' komutu PATH'de mevcut değil veya kurulu değil.")
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip() if e.stderr else str(e)
        if "does not exist" in error_message: 
            raise HTTPException(404, error_message)
        raise HTTPException(500, f"Multipass komutu başarısız: {error_message}")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"Komut zaman aşımına uğradı: {' '.join(command)}")
    except FileNotFoundError:
        raise HTTPException(500, f"Multipass bulunamadı. 'multipass' komutu PATH'de mevcut değil veya kurulu değil.")
    except subprocess.CalledProcessError as e:
        error_message = e.stderr.strip() if e.stderr else str(e)
        if "does not exist" in error_message: 
            raise HTTPException(404, error_message)
        raise HTTPException(500, f"Multipass komutu başarısız: {error_message}")
    except subprocess.TimeoutExpired:
        raise HTTPException(504, f"Komut zaman aşımına uğradı: {' '.join(command)}")

# --- AI ve Komut İşleme Fonksiyonları ---
def extract_multipass_command(text: str) -> Optional[str]:
    """Metin içinden multipass komutunu, çeşitli formatlarda arayarak çıkarır ve düzeltir."""
    print(f"Komut çıkarma deneniyor: {text[:200]}...")  # Debug
    
    # 1. Markdown kod bloğu (```multipass ...``` veya ```bash multipass ...```)
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
            print(f"Markdown'dan çıkarılan komut: {command}")  # Debug
            return normalize_multipass_command(command)

    # 2. Satır satır arama
    lines = text.split('\n')
    for line in lines:
        cleaned_line = line.strip()
        
        # Tırnak işaretlerini kaldır (varsa)
        if (cleaned_line.startswith("'") and cleaned_line.endswith("'")) or \
           (cleaned_line.startswith('"') and cleaned_line.endswith('"')):
            cleaned_line = cleaned_line[1:-1]

        if cleaned_line.startswith("multipass "):
            # "multipass " önekini kaldır ve komutu döndür
            command = cleaned_line[len("multipass "):].strip()
            print(f"Satırdan çıkarılan komut: {command}")  # Debug
            return normalize_multipass_command(command)
    
    # 3. Basit regex araması
    simple_match = re.search(r"multipass\s+(\w+(?:\s+--?\w+(?:\s+\S+)?)*)", text)
    if simple_match:
        command = simple_match.group(1).strip()
        print(f"Regex'den çıkarılan komut: {command}")  # Debug
        return normalize_multipass_command(command)
    
    print("Hiçbir komut bulunamadı")  # Debug       
    return None

def normalize_multipass_command(command: str) -> str:
    """Multipass komutlarını normalize eder (create -> launch, kısa parametreler -> uzun)"""
    # create -> launch dönüşümü
    if command.startswith("create"):
        command = command.replace("create", "launch", 1)
    
    # Kısa parametreleri uzun parametrelere çevir
    replacements = {
        " -n ": " --name ",
        " -m ": " --memory ",
        " -d ": " --disk ",
        " -c ": " --cpus "
    }
    
    for short, long in replacements.items():
        command = command.replace(short, long)
    
    return command

async def execute_vm_action_direct(command: str) -> Dict[str, Any]:
    """Doğrudan Multipass komutunu çalıştırır."""
    try:
        # Komutu normalize et
        command = normalize_multipass_command(command)
        
        # Komutu güvenli bir şekilde ayrıştır
        args = shlex.split(command)
        
        # Eğer komut 'multipass' ile başlıyorsa kaldır
        if args and args[0] == 'multipass':
            args = args[1:]
            
        if not args:
            return {"error": "Geçersiz komut"}
            
        action = args[0]
        
        if action == "launch":
            # launch --name vm-name --cpus 2 --memory 1G --disk 4G ...
            vm_name = None
            vm_config = {}
            
            i = 1
            while i < len(args):
                if args[i] == "--name" and i + 1 < len(args):
                    vm_name = args[i + 1]
                    i += 2
                elif args[i].startswith("--") and i + 1 < len(args):
                    key = args[i][2:]  # -- önekini kaldır
                    value = args[i + 1]
                    
                    # Özel işlem gerektiren parametreler
                    if key in ['cpus', 'disk', 'memory']:
                        vm_config[key] = value
                    elif key == 'disk-size':
                        vm_config['disk'] = value
                    
                    i += 2
                else:
                    i += 1
            
            if not vm_name:
                return {"error": "VM adı belirtilmedi"}
            
            # VM oluştur (asenkron)
            create_request = CreateVMRequest(name=vm_name, config=vm_config)
            # create_vm_async fonksiyonunu çağır
            return await create_vm_async(create_request)
            
        elif action == "start":
            if len(args) < 2:
                return {"error": "VM adı belirtilmedi"}
            vm_name = args[1]
            result = start_vm(vm_name)
            return {"success": True, "message": result.message, "status": result.status}
            
        elif action == "stop":
            if len(args) < 2:
                return {"error": "VM adı belirtilmedi"}
            vm_name = args[1]
            result = stop_vm(vm_name)
            return {"success": True, "message": result.message, "status": result.status}
            
        elif action == "delete":
            if len(args) < 2:
                return {"error": "VM adı belirtilmedi"}
            vm_name = args[1]
            result = delete_vm(vm_name)
            return {"success": True, "message": result.message, "status": result.status}
            
        else:
            # Diğer tüm komutları doğrudan çalıştır
            try:
                # Komutun başına multipass path'ini ekle
                full_command = [config.MULTIPASS_BIN] + args
                output = run_multipass_command(full_command)
                return {"success": True, "message": output, "status": "success"}
            except Exception as e:
                return {"success": False, "error": str(e), "status": "error"}
    
    except Exception as e:
        return {"error": f"Komut çalıştırma hatası: {str(e)}"}

# --- Yardımcı Fonksiyonlar (Asenkron VM Oluşturma) ---
async def async_create_vm_background(vm_name: str, config_dict: Dict):
    """Arka planda VM oluşturur ve durumu günceller"""
    try:
        vm_creation_status[vm_name] = {"status": "creating", "message": f"'{vm_name}' oluşturuluyor..."}
        
        # Güvenli karakter kontrolü
        if not vm_name.replace('-', '').replace('_', '').isalnum():
            vm_creation_status[vm_name] = {"status": "error", "message": "VM adı sadece harf, rakam, tire ve alt çizgi içerebilir."}
            return

        # Multipass için geçerli parametreler
        allowed_params = {
            "mem": "--memory", "memory": "--memory", "disk": "--disk", "cpus": "--cpus"
        }

        # Temel komutu oluştur - doğrudan multipass binary path'ini kullan
        command = [config.MULTIPASS_BIN, "launch", "--name", vm_name]

        # Parametreleri ekle
        for key, value in config_dict.items():
            if key in allowed_params and value:
                command.extend([allowed_params[key], str(value)])
        
        # VM'i oluştur
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(executor, run_multipass_command, command, 600)
        
        # VM'in oluştuğunu doğrula
        time.sleep(3)
        try:
            info_result = await loop.run_in_executor(executor, run_multipass_command, ["multipass", "info", vm_name, "--format", "json"])
            vm_info = json.loads(info_result)
            vm_creation_status[vm_name] = {
                "status": "completed", 
                "message": f"'{vm_name}' başarıyla oluşturuldu!",
                "vm_info": vm_info
            }
        except:
            vm_creation_status[vm_name] = {
                "status": "completed", 
                "message": f"'{vm_name}' oluşturuldu ancak detaylar alınamadı."
            }
            
    except Exception as e:
        vm_creation_status[vm_name] = {
            "status": "error", 
            "message": f"VM oluşturma hatası: {str(e)}"
        }

# --- API Endpoint'leri ---
@app.get("/vms/list", response_model=VMListResponse, summary="Tüm VM'leri Listele")
async def list_vms():
    """Tüm sanal makinelerin listesini döndürür (optimize edilmiş)."""
    try:
        # Asenkron subprocess çağrısı için thread pool kullan
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(
            executor,
            run_multipass_command,
            [config.MULTIPASS_BIN, "list", "--format=json"]
        )
        data = json.loads(output)
        
        vms_raw = data.get("list", [])
        vms_final = []

        async def get_vm_info(vm_data):
            final_data = vm_data.copy()

            state = final_data.get("state", "").lower()
            cpus = final_data.get("cpus")
            memory = final_data.get("memory")
            disk = final_data.get("disk")

            # Trigger fallback if resource info is missing, which is common for non-running VMs.
            if not cpus or memory in [None, '-'] or disk in [None, '-']:
                try:
                    info_output = await loop.run_in_executor(
                        executor,
                        run_multipass_command,
                        [config.MULTIPASS_BIN, "info", final_data["name"], "--format=json"]
                    )
                    info_data = json.loads(info_output).get("info", {}).get(final_data["name"], {})
                    
                    cpus_list = info_data.get("cpus", [])
                    if cpus_list: final_data["cpus"] = cpus_list[0].get("value")

                    memory_list = info_data.get("memory", [])
                    if memory_list: final_data["memory"] = format_bytes(memory_list[0].get("value"))

                    disk_list = info_data.get("disk", [])
                    if disk_list: final_data["disk"] = format_bytes(disk_list[0].get("value"))

                except Exception as e:
                    print(f"Could not get info for {final_data['name']}: {e}")
            
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
        raise HTTPException(500, f"VM listesi alınamadı: {str(e)}")

@app.post("/vms/start/{vm_name}", response_model=StatusResponse, summary="VM'i Başlat")
async def start_vm(vm_name: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, run_multipass_command, ["multipass", "start", vm_name])
    return {"status": "başlatıldı", "message": f"'{vm_name}' başlatıldı."}

@app.post("/vms/stop/{vm_name}", response_model=StatusResponse, summary="VM'i Durdur")
async def stop_vm(vm_name: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, run_multipass_command, ["multipass", "stop", vm_name])
    return {"status": "durduruldu", "message": f"'{vm_name}' durduruldu."}

@app.delete("/vms/delete/{vm_name}", response_model=StatusResponse, summary="VM'i Sil")
async def delete_vm(vm_name: str):
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(executor, run_multipass_command, ["multipass", "delete", vm_name])
    await loop.run_in_executor(executor, run_multipass_command, ["multipass", "purge"]) # Silinen VM'leri temizle
    return {"status": "silindi", "message": f"'{vm_name}' silindi ve temizlendi."}

@app.post("/vms/create", response_model=StatusResponse, summary="Yeni VM Oluştur (Senkron)")
async def create_vm(create_vm_request: CreateVMRequest):
    """Senkron VM oluşturma - optimize edilmiş"""
    vm_name = create_vm_request.name
    if not vm_name.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(400, "VM adı sadece harf, rakam, tire ve alt çizgi içerebilir.")
    
    try:
        # VM oluşturma komutu
        command = [config.MULTIPASS_BIN, "launch", "--name", vm_name]
        
        # Ek yapılandırmayı ekle
        for key, value in create_vm_request.config.items():
            if key in ['cpus', 'memory', 'disk']:
                command.extend([f"--{key}", value])
            else:
                command.extend([f"--{key}", value])
        
        # Asenkron subprocess çağrısı
        loop = asyncio.get_event_loop()
        output = await loop.run_in_executor(executor, run_multipass_command, command)
        return StatusResponse(status="created", message=f"Sanal makine '{vm_name}' başarıyla oluşturuldu.")
    
    except subprocess.CalledProcessError as e:
        error_msg = e.stderr.strip() if e.stderr else str(e)
        if "already exists" in error_msg:
            raise HTTPException(409, f"'{vm_name}' adında bir VM zaten var.")
        elif "invalid" in error_msg.lower():
            raise HTTPException(400, f"Geçersiz parametre: {error_msg}")
        else:
            raise HTTPException(500, f"VM oluşturma hatası: {error_msg}")
            
    except Exception as e:
        raise HTTPException(500, f"VM oluşturma hatası: {str(e)}")

@app.post("/vms/create-async", response_model=StatusResponse, summary="Yeni VM Oluştur (Asenkron)")
async def create_vm_async(create_vm_request: CreateVMRequest):
    """Asenkron VM oluşturma - arka planda çalışır, durumu /vms/status/{vm_name} ile kontrol edilir"""
    vm_name = create_vm_request.name
    if not vm_name.replace('-', '').replace('_', '').isalnum():
        raise HTTPException(400, "VM adı sadece harf, rakam, tire ve alt çizgi içerebilir.")
    
    # Arka planda VM oluşturmaya başla
    asyncio.create_task(async_create_vm_background(vm_name, create_vm_request.config))
    
    return {
        "status": "started", 
        "message": f"'{vm_name}' oluşturma işlemi başlatıldı."
    }

@app.get("/vms/status/{vm_name}", response_model=Dict, summary="VM Oluşturma Durumunu Kontrol Et")
def get_vm_creation_status(vm_name: str):
    """VM oluşturma durumunu kontrol eder"""
    if vm_name not in vm_creation_status:
        return {"status": "unknown", "message": f"'{vm_name}' için oluşturma işlemi bulunamadı."}
    
    return vm_creation_status[vm_name]

@app.get("/", summary="API Durumu")
def root():
    """API'nin temel bilgilerini döndürür"""
    return {
        "message": "Multipass VM Management API çalışıyor",
        "version": "3.1.0",
        "multipass_bin": config.MULTIPASS_BIN
    }

@app.get("/health", summary="Sağlık Kontrolü")
def health_check():
    """API'nin ve Multipass'ın çalışıp çalışmadığını kontrol eder"""
    try:
        # Multipass'ın çalışıp çalışmadığını kontrol et
        version_output = run_multipass_command(["multipass", "version"], timeout=10)
        return {
            "status": "healthy", 
            "multipass": "available", 
            "version": version_output.strip(),
            "multipass_bin": config.MULTIPASS_BIN
        }
    except Exception as e:
        return {
            "status": "unhealthy", 
            "multipass": "unavailable",
            "error": str(e),
            "multipass_bin": MULTIPASS_BIN
        }

# --- AI Chat Endpoints ---

@app.post("/chat")
async def chat_endpoint(request: LegacyChatRequest):
    """Kullanıcıdan gelen mesajı işler ve gerekli komutları çalıştırır."""
    try:
        # Eğer doğrudan multipass komutu gelmişse
        if request.message.strip().startswith('multipass '):
            command = request.message.strip()[len('multipass '):].strip()  # "multipass " kısmını kaldır
            result = await execute_vm_action_direct(command)
            
            if 'error' in result:
                return {
                    "response": f"❌ Hata: {result['error']}",
                    "execution_results": [{
                        "success": False,
                        "operation": "direct_command",
                        "details": result.get('error')
                    }]
                }
            
            return {
                "response": f"✅ Komut başarıyla çalıştırıldı!\n\nSonuç: {result.get('message', 'Başarılı')}",
                "execution_results": [{
                    "success": True,
                    "operation": "direct_command",
                    "details": result.get('message')
                }]
            }
    
        # AI ile işlem yapma kısmı
        async with httpx.AsyncClient(timeout=config.REQUEST_TIMEOUT) as client:
            # Legacy formatı Ollama formatına çevir
            ollama_request = {
                "model": config.OLLAMA_MODEL,
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
            
            # Mesajı Ollama'ya gönder
            response = await client.post(f"{config.OLLAMA_URL}/api/generate", json=ollama_request)
            response.raise_for_status()
            ai_response = response.json()
            ai_message = ai_response.get("response", "")

            # AI yanıtından komut çıkar ve çalıştır
            command = extract_multipass_command(ai_message)
            execution_results = []
            
            if command:
                print(f"AI'dan çıkarılan komut: {command}")  # Debug
                execution_result = await execute_vm_action_direct(command)
                print(f"Çalıştırma sonucu: {execution_result}")  # Debug
                
                execution_results.append({
                    "success": execution_result.get("success", False),
                    "operation": "multipass_command", 
                    "command": command,
                    "details": execution_result.get("message") or execution_result.get("error", "Bilinmeyen sonuç")
                })
                
                # Sonucu AI mesajına ekle
                # Asenkron oluşturma işlemi için özel durum kontrolü
                is_async_creation = execution_result.get("status") == "started"

                if execution_result.get("success") or is_async_creation:
                    result_text = execution_result.get("message", "İşlem başarılı")
                    ai_message += f"\n\n✅ **İşlem Sonucu:** {result_text}"
                else:
                    error_text = execution_result.get("error", "Bilinmeyen hata")
                    ai_message += f"\n\n❌ **Hata:** {error_text}"
            else:
                print("AI mesajından komut çıkarılamadı")  # Debug
                print(f"AI mesajı: {ai_message}")  # Debug
            
            # Legacy format için response döndür
            return {
                "response": ai_message,
                "execution_results": execution_results
            }

    except httpx.RequestError as e:
        raise HTTPException(503, f"Ollama sunucusuna ulaşılamadı: {str(e)}")
    except httpx.HTTPStatusError as e:
        error_text = e.response.text if hasattr(e, 'response') and e.response else str(e)
        raise HTTPException(e.response.status_code if hasattr(e, 'response') and e.response else 500, 
                         f"Ollama API hatası: {error_text}")
    except Exception as e:
        raise HTTPException(500, f"Beklenmeyen bir hata oluştu: {str(e)}")

@app.get("/vms/list-ai", response_model=AIVMListResponse)
async def list_vms_ai():
    """VM listesini AI proxy formatında döndürür (frontend uyumluluğu için)."""
    try:
        # Mevcut list_vms fonksiyonunu kullan
        vm_list_response = list_vms()
        return {"success": True, "vms": vm_list_response.list}
    except Exception as e:
        error_message = str(e)
        return {"success": False, "vms": [], "error": f"VM listesi alınamadı: {error_message}"}

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=config.PROXY_SERVER_PORT)