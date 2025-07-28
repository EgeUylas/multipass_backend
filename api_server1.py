import json
import time
import subprocess
import os
import shutil
import threading
from typing import Dict
from concurrent.futures import ThreadPoolExecutor
from fastapi import FastAPI, HTTPException, Body
import uvicorn
from dotenv import load_dotenv

load_dotenv()

app = FastAPI(
    title="Multipass VM Yönetim API'si",
    description="Multipass sanal makinelerini yönetmek için kullanılan yerel API.",
    version="2.0.0"
)

vm_creation_status: Dict[str, Dict] = {}
executor = ThreadPoolExecutor(max_workers=3)
MULTIPASS_BIN = os.getenv("MULTIPASS_BIN", "").strip()

def resolve_multipass_path() -> str:
    if MULTIPASS_BIN and os.path.exists(MULTIPASS_BIN):
        return MULTIPASS_BIN
    which = shutil.which("multipass")
    if which:
        return which
    return ""

def run_multipass_command(command: list, timeout=300) -> str:
    mp_path = resolve_multipass_path()
    if not mp_path:
        raise FileNotFoundError("Multipass komutu bulunamadı. Multipass'ın kurulu ve erişilebilir olduğundan emin olun.")
    if command[0] == "multipass":
        command[0] = mp_path
    try:
        print(f"Çalıştırılan Komut: {' '.join(command)}")
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=timeout
        )
        if result.returncode != 0:
            error_msg = result.stderr.strip() if result.stderr else "Bilinmeyen hata"
            print(f"Komut hatası: {error_msg}")
            raise HTTPException(
                status_code=400,
                detail=f"Multipass komutu başarısız oldu: {error_msg}"
            )
        print(f"Komut başarılı: {result.stdout}")
        return result.stdout

    except subprocess.TimeoutExpired:
        raise HTTPException(
            status_code=408,
            detail=f"Komut zaman aşımına uğradı ({timeout} saniye)"
        )
    except FileNotFoundError:
        raise HTTPException(
            status_code=500,
            detail="Multipass komutu bulunamadı. Multipass'ın yüklü ve PATH'de olduğundan emin olun."
        )

def async_create_vm(command: list, vm_name: str):
    vm_creation_status[vm_name] = {"status": "creating", "message": f'VM "{vm_name}" oluşturuluyor...'}
    try:
        run_multipass_command(command, timeout=600)
        vm_creation_status[vm_name] = {"status": "completed", "message": f'VM "{vm_name}" başarıyla oluşturuldu.'}
    except Exception as e:
        vm_creation_status[vm_name] = {"status": "error", "message": f"Oluşturulamadı: {str(e)}"}

@app.post("/vms/create", summary="Yeni VM Oluştur (Asenkron)")
def create_vm(config: Dict = Body(...)):
    vm_name = config.get("name")
    if not vm_name:
        raise HTTPException(status_code=400, detail="Geçerli bir VM 'name' parametresi zorunludur.")

    command = ["multipass", "launch", "--name", vm_name]
    allowed_params = {
        "mem": "--memory",
        "memory": "--memory",
        "disk": "--disk",
        "cpus": "--cpus"
    }
    for key, value in config.items():
        if key in allowed_params and value:
            command.extend([allowed_params[key], str(value)])
    if "image" in config and config["image"]:
        command.append(str(config["image"]))

    # Artık launch işlemi thread'de başlasın
    threading.Thread(target=async_create_vm, args=(command, vm_name), daemon=True).start()

    return {
        "status": "started",
        "message": f'VM "{vm_name}" oluşturuluyor...',
        "vm_name": vm_name
    }

@app.get("/vms/status/{vm_name}", summary="VM Oluşturma Durumunu Kontrol Et")
def get_vm_creation_status(vm_name: str):
    return vm_creation_status.get(vm_name, {"status": "unknown", "message": "VM bulunamadı."})

# Diğer endpointler aynı kalabilir...
