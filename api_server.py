# api_server.py (GELİŞTİRİLMİŞ VERSİYON)
# Timeout sorunları çözüldü, daha detaylı bilgiler eklendi

from fastapi import FastAPI, HTTPException, Body, BackgroundTasks
import subprocess
import json
import uvicorn
import time
import asyncio
from typing import Dict
import threading
from concurrent.futures import ThreadPoolExecutor

app = FastAPI(
    title="Multipass VM Yönetim API'si",
    description="Multipass sanal makinelerini yönetmek için kullanılan yerel API.",
    version="2.0.0"
)

# CORS ayarları
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Tüm kaynaklara izin ver (geliştirme için)
    allow_credentials=True,
    allow_methods=["*"],  # Tüm metodlara izin ver
    allow_headers=["*"],  # Tüm başlıklara izin ver
)

# VM oluşturma durumlarını takip etmek için
vm_creation_status = {}
executor = ThreadPoolExecutor(max_workers=3)

def format_bytes(byte_val):
    """Byte değerini okunabilir bir formata (KB, MB, GB) dönüştürür."""
    if byte_val is None:
        return "N/A"
    try:
        b = int(byte_val)
        if b < 1024:
            return f"{b}B"
        elif b < 1024**2:
            return f"{b/1024:.1f}KB"
        elif b < 1024**3:
            return f"{b/1024**2:.1f}MB"
        else:
            return f"{b/1024**3:.1f}GB"
    except (ValueError, TypeError):
        return "N/A"

def run_multipass_command(command: list, timeout=300):
    """Verilen multipass komutunu çalıştırır ve çıktıyı döner."""
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

def create_vm_background(vm_name: str, config: Dict):
    """Arka planda VM oluşturur"""
    try:
        vm_creation_status[vm_name] = {"status": "creating", "message": "VM oluşturuluyor..."}
        
        # Güvenli karakter kontrolü
        if not vm_name.replace('-', '').replace('_', '').isalnum():
            vm_creation_status[vm_name] = {"status": "error", "message": "VM adı sadece harf, rakam, tire ve alt çizgi içerebilir."}
            return

        # Multipass için geçerli parametreler
        allowed_params = {
            "mem": "--memory",
            "disk": "--disk", 
            "cpus": "--cpus"
        }

        # Temel komutu oluştur
        command = ["multipass", "launch", "--name", vm_name]

        # Parametreleri ekle
        for key, value in config.items():
            if key in allowed_params and value:
                command.extend([allowed_params[key], str(value)])
        
        # VM oluşturma komutunu çalıştır (uzun timeout)
        result = run_multipass_command(command, timeout=600)  # 10 dakika
        
        # VM'in oluştuğunu doğrula
        time.sleep(3)
        try:
            info_result = run_multipass_command(["multipass", "info", vm_name, "--format", "json"])
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

@app.post("/vms/create", summary="Yeni VM Oluştur (Asenkron)")
def create_vm(config: Dict = Body(...)):
    """
    JSON formatında gönderilen konfigürasyona göre yeni bir sanal makine oluşturur.
    Oluşturma işlemi arka planda çalışır, durumu /vms/status/{vm_name} ile kontrol edilir.
    """
    vm_name = config.get("name")
    if not vm_name:
        raise HTTPException(status_code=400, detail="Geçerli bir VM 'name' parametresi zorunludur.")

    # Arka planda VM oluşturmaya başla
    executor.submit(create_vm_background, vm_name, config)
    
    return {
        "status": "started", 
        "message": f"'{vm_name}' oluşturma işlemi başlatıldı. Durumu kontrol etmek için GET /vms/status/{vm_name} kullanın.",
        "vm_name": vm_name
    }

@app.get("/vms/status/{vm_name}", summary="VM Oluşturma Durumunu Kontrol Et")
def get_vm_creation_status(vm_name: str):
    """VM oluşturma durumunu kontrol eder"""
    if vm_name not in vm_creation_status:
        return {"status": "unknown", "message": f"'{vm_name}' için oluşturma işlemi bulunamadı."}
    
    return vm_creation_status[vm_name]

@app.get("/vms/list", summary="Tüm VM'leri Detaylı Listele")
def list_vms():
    """Tüm multipass sanal makinelerini detaylı bilgileriyle birlikte JSON formatında listeler."""
    try:
        # Temel VM listesini al
        list_result_json = run_multipass_command(["multipass", "list", "--format", "json"])
        vm_list_data = json.loads(list_result_json)
        
        detailed_vms = []
        for vm_summary in vm_list_data.get("list", []):
            vm_name = vm_summary.get("name")
            if not vm_name:
                continue

            try:
                # Her VM için detaylı bilgi al
                info_result_json = run_multipass_command(["multipass", "info", vm_name, "--format", "json"], timeout=30)
                vm_info_data = json.loads(info_result_json)

                # Dinamik anahtarlı VM detayını al (örn: info['ege'])
                vm_details = vm_info_data.get("info", {}).get(vm_name)

                if vm_details:
                    # Detaylı bilgileri güvenli bir şekilde çıkar
                    memory_info = vm_details.get("memory", {})
                    disk_info = vm_details.get("disks", {}).get("sda1", {})

                    detailed_vm = {
                        "name": vm_name,
                        "state": vm_details.get("state", "N/A"),
                        "ipv4": vm_details.get("ipv4", []),
                        "release": vm_details.get("release", "N/A"),
                        "cpus": vm_details.get("cpu_count", "N/A"),
                        "image_hash": vm_details.get("image_hash", "N/A"),
                        "memory": f"{format_bytes(memory_info.get('used'))} / {format_bytes(memory_info.get('total'))}",
                        "disk": f"{format_bytes(disk_info.get('used'))} / {format_bytes(disk_info.get('total'))}",
                    }
                    detailed_vms.append(detailed_vm)
                else:
                    # Detay alınamazsa temel bilgiyi kullan
                    detailed_vms.append(vm_summary)

            except Exception as e:
                print(f"'{vm_name}' için detay alınamadı: {e}")
                # Hata durumunda temel bilgiyi kullan
                detailed_vms.append(vm_summary)
        
        return {"list": detailed_vms, "total": len(detailed_vms)}

    except Exception as e:
        print(f"VM listesi alınırken genel bir hata oluştu: {e}")
        raise HTTPException(status_code=500, detail="VM listesi alınırken sunucuda bir hata oluştu.")

@app.get("/vms/info/{vm_name}", summary="VM Detaylarını Al")
def get_vm_info(vm_name: str):
    """Belirtilen sanal makinenin detaylı bilgisini JSON formatında döndürür."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
    
    result_json = run_multipass_command(["multipass", "info", vm_name, "--format", "json"])
    return json.loads(result_json)

@app.post("/vms/start/{vm_name}", summary="VM'i Başlat")
def start_vm(vm_name: str):
    """Belirtilen sanal makineyi başlatır."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
    
    run_multipass_command(["multipass", "start", vm_name])
    return {"status": "başlatıldı", "message": f"'{vm_name}' başlatıldı."}

@app.post("/vms/stop/{vm_name}", summary="VM'i Durdur")
def stop_vm(vm_name: str):
    """Belirtilen sanal makineyi durdurur."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
    
    run_multipass_command(["multipass", "stop", vm_name])
    return {"status": "durduruldu", "message": f"'{vm_name}' durduruldu."}

@app.delete("/vms/delete/{vm_name}", summary="VM'i Sil")
def delete_vm(vm_name: str, purge: bool = False):
    """Belirtilen sanal makineyi siler. 'purge=true' ile kalıcı olarak siler."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
         
    run_multipass_command(["multipass", "delete", vm_name])
    
    if purge:
        run_multipass_command(["multipass", "purge"])
        return {"status": "silindi_ve_temizlendi", "message": f"'{vm_name}' kalıcı olarak silindi."}
    
    return {"status": "silindi", "message": f"'{vm_name}' silindi. Geri getirmek için 'recover' komutunu kullanabilirsiniz."}

@app.post("/vms/recover/{vm_name}", summary="Silinen VM'i Geri Getir")
def recover_vm(vm_name: str):
    """Silinmiş ancak purge edilmemiş VM'i geri getirir."""
    if not vm_name.replace('-', '').replace('_', '').isalnum():
         raise HTTPException(status_code=400, detail="Geçersiz VM adı.")
    
    run_multipass_command(["multipass", "recover", vm_name])
    return {"status": "geri_getirildi", "message": f"'{vm_name}' başarıyla geri getirildi."}

@app.get("/health", summary="API Durumu")
def health_check():
    """API'nin çalışıp çalışmadığını kontrol eder."""
    try:
        # Multipass'ın çalışıp çalışmadığını kontrol et
        version_output = run_multipass_command(["multipass", "version"])
        return {"status": "healthy", "multipass": "available", "version": version_output.strip()}
    except:
        return {"status": "unhealthy", "multipass": "unavailable"}

if __name__ == "__main__":
    print("API sunucusunu başlatmak için terminalde şu komutu çalıştırın:")
    print("uvicorn api_server:app --reload --host 0.0.0.0 --port 8000")