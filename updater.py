import os
import sys
import subprocess
import tempfile
import requests

def install_update(download_url):
    if not getattr(sys, "frozen", False):
        return "Лаунчер запущен из исходного кода. Автообновление доступно только для .exe версии."
    
    temp_dir = tempfile.gettempdir()
    new_exe_path = os.path.join(temp_dir, "ShinriTrialLauncher_new.exe")
    
    try:
        response = requests.get(download_url, stream=True, timeout=15)
        response.raise_for_status()
        with open(new_exe_path, 'wb') as f:
            for data in response.iter_content(chunk_size=8192):
                f.write(data)
    except Exception as e:
        return f"Ошибка скачивания: {e}"
        
    current_exe_path = sys.executable
    batch_path = os.path.join(temp_dir, "update_launcher.bat")
    
    with open(batch_path, "w", encoding="utf-8") as f:
        f.write(f"""@echo off
timeout /t 2 /nobreak > NUL
:loop
del "{current_exe_path}"
if exist "{current_exe_path}" goto loop
move "{new_exe_path}" "{current_exe_path}"
start "" "{current_exe_path}"
del "%~f0"
""")
        
    subprocess.Popen([batch_path], shell=True)
    return None