#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
# MasterPortxx Downloader v1.2
# RG35XX H / Knulli
#
# Compatível com a arquitetura gráfica já usada no app.py
# anterior: graphic.UserInterface + input.
#
# Pacote:
#   SERVER_URL/games.json
#   SERVER_URL/0001/manifest.json
#   SERVER_URL/0001/MasterPortxx_0001.mpx
#   ...
#
# A pasta "data/" do ZIP é extraída DIRETAMENTE em PORTS_DIR.
# ============================================================

import os
APP_DIR = os.path.dirname(os.path.abspath(__file__))
import json
# KNULLI/PortMaster input bridge: GPTOKEYB converts the physical gamepad into SDL keyboard events.
import ctypes
import atexit
import shlex
try:
    import sdl2
except Exception:
    sdl2 = None

class SDLInputBridge:
    def __init__(self):
        self.codeName = ""
        self.value = 0
        self._gptokeyb = None
        self._gptk_path = os.path.join(APP_DIR, "masterportxx.gptk")

    def _key_name(self, sym):
        if sdl2 is None:
            return None
        m = {
            getattr(sdl2, "SDLK_UP", 1073741906): "DY+",
            getattr(sdl2, "SDLK_DOWN", 1073741905): "DY-",
            getattr(sdl2, "SDLK_LEFT", 1073741904): "DX-",
            getattr(sdl2, "SDLK_RIGHT", 1073741903): "DX+",
            getattr(sdl2, "SDLK_x", ord("x")): "A",
            getattr(sdl2, "SDLK_z", ord("z")): "B",
            getattr(sdl2, "SDLK_c", ord("c")): "X",
            getattr(sdl2, "SDLK_a", ord("a")): "Y",
            getattr(sdl2, "SDLK_RETURN", 13): "START",
            getattr(sdl2, "SDLK_ESCAPE", 27): "B",
            getattr(sdl2, "SDLK_BACKSPACE", 8): "B",
        }
        return m.get(sym)

    def check(self):
        self.codeName = ""
        self.value = 0
        if sdl2 is None:
            return
        ev = sdl2.SDL_Event()
        while sdl2.SDL_PollEvent(ctypes.byref(ev)):
            if ev.type == sdl2.SDL_KEYDOWN:
                name = self._key_name(ev.key.keysym.sym)
                if name:
                    self.codeName = name
                    self.value = 1
                    return
            elif ev.type == sdl2.SDL_KEYUP:
                name = self._key_name(ev.key.keysym.sym)
                if name:
                    self.codeName = name
                    self.value = -1
                    return
            elif ev.type == sdl2.SDL_QUIT:
                self.codeName = "B"
                self.value = 1
                return

    def key(self, keyCodeName, keyValue=99):
        if self.codeName == keyCodeName:
            return self.value == keyValue if keyValue != 99 else True
        return False

    def slide_key(self):
        return bool(self.codeName)

    def reset_input(self):
        self.codeName = ""
        self.value = 0

    def start_gptokeyb(self):
        if self._gptokeyb is not None and self._gptokeyb.poll() is None:
            return True
        try:
            gptk = "\n".join([
                "# MasterPortxx Knulli controls",
                "start = enter", "guide = enter",
                "a = x", "b = z", "x = c", "y = a",
                "up = up", "down = down", "left = left", "right = right",
                "up = repeat", "down = repeat", "left = repeat", "right = repeat",
                "left_analog_up = up", "left_analog_down = down",
                "left_analog_left = left", "left_analog_right = right",
                "left_analog_up = repeat", "left_analog_down = repeat",
                "left_analog_left = repeat", "left_analog_right = repeat",
                "left_analog_as_mouse = false", "right_analog_as_mouse = false",
                "deadzone_x = 12000", "deadzone_y = 12000", "deadzone_triggers = 3000", ""
            ])
            with open(self._gptk_path, "w", encoding="utf-8") as f:
                f.write(gptk)
            try:
                os.chmod("/dev/uinput", 0o666)
            except Exception:
                pass
            gpt = os.environ.get("GPTOKEYB", "").strip()
            if gpt:
                cmd = shlex.split(gpt)
            else:
                candidates = [
                    "/opt/system/Tools/PortMaster/gptokeyb",
                    "/opt/tools/PortMaster/gptokeyb",
                    "/roms/ports/PortMaster/gptokeyb",
                ]
                exe = next((p for p in candidates if os.path.exists(p)), None)
                if not exe:
                    print("GPTOKEYB não encontrado")
                    return False
                cmd = [exe]
            try:
                ctypes.CDLL(None).prctl(15, b"MasterPortxx", 0, 0, 0)
            except Exception:
                pass
            cmd += ["MasterPortxx", "-c", self._gptk_path]
            self._gptokeyb = subprocess.Popen(cmd, cwd=APP_DIR, env=os.environ.copy())
            print("GPTOKEYB iniciado:", " ".join(cmd))
            return True
        except Exception as e:
            print("Falha ao iniciar gptokeyb:", repr(e))
            return False

    def stop_gptokeyb(self):
        p = self._gptokeyb
        self._gptokeyb = None
        if p is not None:
            try:
                if p.poll() is None:
                    p.terminate()
                    try:
                        p.wait(timeout=1)
                    except Exception:
                        p.kill()
            except Exception:
                pass

input = SDLInputBridge()
atexit.register(input.stop_gptokeyb)
import subprocess
import zipfile
import hashlib
import shutil
import tempfile
import sys
from graphic import UserInterface

ui = UserInterface()

APP_DIR = os.path.dirname(os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")

DEFAULT_SERVER_URL = "http://192.168.1.100:8000"
DEFAULT_PORTS_DIR = "/userdata/roms/ports"
DEFAULT_APP_UPDATE_URL = "https://raw.githubusercontent.com/seumedeiros/MasterPortxx/main/app.py"

SERVER_URL = DEFAULT_SERVER_URL
PORTS_DIR = DEFAULT_PORTS_DIR
APP_UPDATE_URL = DEFAULT_APP_UPDATE_URL
APP_PATH = os.path.join(APP_DIR, "app.py")
APP_BACKUP_PATH = os.path.join(APP_DIR, "app.bkp")
APP_VERSION = "v1.1.4"

# GitHub ROM catalog
GITHUB_API_BASE = "https://api.github.com/repos/seumedeiros/MasterPortxx/contents"
ROMS_REMOTE_PATH = "ROMs"
ROMS_LOCAL_BASE = "/userdata/roms"

games = []
selected = 0
message = ""
busy = False


# ============================================================
# CONFIG
# ============================================================

def load_config():
    global SERVER_URL, PORTS_DIR, APP_UPDATE_URL

    SERVER_URL = DEFAULT_SERVER_URL
    PORTS_DIR = DEFAULT_PORTS_DIR
    APP_UPDATE_URL = DEFAULT_APP_UPDATE_URL

    # 1) config ao lado do app
    paths = [CONFIG_PATH]

    # 2) config global, se existir
    paths.append("/userdata/system/configs/masterportxx_config.json")

    for path in paths:
        if not os.path.exists(path):
            continue

        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)

            if isinstance(data, dict):
                if data.get("server_url"):
                    SERVER_URL = str(data["server_url"]).rstrip("/")

                if data.get("ports_dir"):
                    PORTS_DIR = str(data["ports_dir"])

                if data.get("app_update_url"):
                    APP_UPDATE_URL = str(data["app_update_url"]).strip()

            # o primeiro config encontrado vence
            return

        except:
            continue


# ============================================================
# ATUALIZAÇÃO DO APLICATIVO
# ============================================================

def download_raw_file(url, destination):
    try:
        result = subprocess.call(
            [
                "/usr/bin/wget",
                "-q",
                "-O",
                destination,
                url
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return result == 0 and os.path.isfile(destination) and os.path.getsize(destination) > 0
    except Exception:
        return False


def update_app():
    """Baixa um novo app.py, valida, cria app.bkp e substitui atomicamente."""
    # O temporário precisa ficar no mesmo filesystem do app.
    # No RG35XX H/Knulli, /tmp e /userdata podem estar em filesystems
    # diferentes, o que faz os.replace() falhar com EXDEV.
    temp_path = os.path.join(
        APP_DIR,
        ".masterportxx_app_update_%d.py" % os.getpid()
    )

    try:
        show_status(
            "ATUALIZANDO APP",
            [
                "Baixando novo app.py...",
                "",
                APP_UPDATE_URL
            ]
        )

        if not download_raw_file(APP_UPDATE_URL, temp_path):
            raise RuntimeError("Não foi possível baixar o novo app.py.")

        if os.path.getsize(temp_path) < 50:
            raise RuntimeError("O arquivo baixado é muito pequeno.")

        # Valida a sintaxe antes de tocar no app atual.
        import py_compile
        try:
            py_compile.compile(temp_path, doraise=True)
        except Exception as exc:
            raise RuntimeError("O novo app.py possui erro de sintaxe.") from exc

        # Valida se parece realmente ser Python do MasterPortxx.
        with open(temp_path, "r", encoding="utf-8") as f:
            new_code = f.read()

        if "def main(" not in new_code or "MasterPortxx" not in new_code:
            raise RuntimeError("O arquivo baixado não parece ser um app.py válido do MasterPortxx.")

        show_status(
            "ATUALIZANDO APP",
            [
                "Nova versão validada.",
                "",
                "Criando app.bkp..."
            ]
        )

        # Só cria/substitui o backup depois que o novo arquivo passou na validação.
        shutil.copy2(APP_PATH, APP_BACKUP_PATH)

        # Substituição atômica: se falhar, o app atual continua intacto.
        os.replace(temp_path, APP_PATH)

        show_status(
            "ATUALIZAÇÃO CONCLUÍDA",
            [
                "app.py atualizado.",
                "",
                "Backup salvo como:",
                "app.bkp",
                "",
                "O programa será fechado.",
                "Abra novamente para usar a nova versão."
            ]
        )

        import time
        time.sleep(2)
        sys.exit(0)

    except SystemExit:
        raise
    except Exception as exc:
        show_status(
            "FALHA NA ATUALIZAÇÃO",
            [
                str(exc),
                "",
                "O app atual não foi alterado.",
                "",
                "A/B = Voltar"
            ]
        )
        wait_message(
            "FALHA NA ATUALIZAÇÃO",
            [
                str(exc),
                "",
                "O app atual não foi alterado.",
                "",
                "A/B = Voltar"
            ]
        )
        return False
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


def restore_app_backup():
    if not os.path.isfile(APP_BACKUP_PATH):
        wait_message("SEM BACKUP", ["app.bkp não encontrado.", "", "A/B = Voltar"])
        return

    show_status(
        "RESTAURAR BACKUP",
        [
            "Restaurar o app.bkp?",
            "",
            "A = Sim",
            "B = Cancelar"
        ]
    )

    while True:
        input.check()
        if input.key("B"):
            return
        if input.key("A"):
            try:
                # Também mantemos o temporário no mesmo filesystem do app.
                restore_temp = os.path.join(APP_DIR, ".masterportxx_restore_%d.py" % os.getpid())
                shutil.copy2(APP_BACKUP_PATH, restore_temp)
                import py_compile
                py_compile.compile(restore_temp, doraise=True)
                shutil.copy2(APP_PATH, APP_PATH + ".before_restore")
                os.replace(restore_temp, APP_PATH)
                show_status("BACKUP RESTAURADO", ["app.py voltou para a versão anterior.", "", "O programa será fechado."])
                import time
                time.sleep(2)
                sys.exit(0)
            except SystemExit:
                raise
            except Exception as exc:
                wait_message("ERRO", [str(exc), "", "A/B = Voltar"])
                return


# ============================================================
# CATÁLOGO / UI
# ============================================================

section = 0  # 0 = Ports, 1 = ROMs
rom_systems = []
rom_files = []
rom_system_selected = 0
rom_file_selected = 0
rom_system_path = ""


def draw_header(title=""):
    ui.draw_text((25, 20), "MASTERPORTXX " + APP_VERSION)
    if title:
        ui.draw_text((25, 52), title)


def draw_tabs(active):
    # Dois blocos simples para deixar claro onde estamos.
    ports_outline = (0, 255, 120) if active == 0 else (100, 100, 100)
    roms_outline = (0, 255, 120) if active == 1 else (100, 100, 100)
    ui.draw_rectangle([20, 68, 220, 105], outline=ports_outline)
    ui.draw_rectangle([230, 68, 430, 105], outline=roms_outline)
    ui.draw_text((65, 76), "[Ports]" if active == 0 else "Ports")
    ui.draw_text((280, 76), "[ROMs]" if active == 1 else "ROMs")


def draw_ports_menu():
    draw_header()
    draw_tabs(0)

    if not games:
        ui.draw_text((40, 145), "Nenhum port encontrado.")
        ui.draw_text((40, 185), "Verifique o servidor.")
    else:
        visible = 5
        first = max(0, selected - 2)
        last = min(len(games), first + visible)
        if last - first < visible:
            first = max(0, last - visible)

        y = 125
        for i in range(first, last):
            game = games[i]
            package_id = str(game.get("id", "")).zfill(4)
            title = str(game.get("title", package_id))
            if i == selected:
                ui.draw_rectangle([20, y - 6, ui.screen_width - 20, y + 28], outline=(0, 255, 120))
            ui.draw_text((35, y), "%s - %s" % (package_id, title[:30]))
            y += 40

    ui.draw_text((20, ui.screen_height - 58), "A = Baixar    X = Atualizar app")
    ui.draw_text((20, ui.screen_height - 32), "DY = Navegar    DX = Seção    B = Sair")
    ui.draw_paint()


def draw_rom_systems():
    draw_header("SISTEMAS")
    draw_tabs(1)

    if not rom_systems:
        ui.draw_text((40, 145), "Nenhum sistema encontrado.")
        ui.draw_text((40, 185), "Crie pastas dentro de ROMs")
        ui.draw_text((40, 215), "no GitHub.")
    else:
        visible = 5
        first = max(0, rom_system_selected - 2)
        last = min(len(rom_systems), first + visible)
        if last - first < visible:
            first = max(0, last - visible)

        y = 125
        for i in range(first, last):
            system = rom_systems[i]
            name = system["name"]
            if i == rom_system_selected:
                ui.draw_rectangle([20, y - 6, ui.screen_width - 20, y + 28], outline=(0, 255, 120))
            ui.draw_text((35, y), name[:34])
            y += 40

    ui.draw_text((20, ui.screen_height - 58), "A = Abrir sistema")
    ui.draw_text((20, ui.screen_height - 32), "DY = Navegar    DX = Seção    B = Voltar")
    ui.draw_paint()


def draw_rom_files():
    draw_header(rom_system_path.split("/")[-1])
    draw_tabs(1)

    if not rom_files:
        ui.draw_text((40, 145), "Nenhuma ROM encontrada.")
    else:
        visible = 5
        first = max(0, rom_file_selected - 2)
        last = min(len(rom_files), first + visible)
        if last - first < visible:
            first = max(0, last - visible)

        y = 125
        for i in range(first, last):
            item = rom_files[i]
            name = item["name"]
            if i == rom_file_selected:
                ui.draw_rectangle([20, y - 6, ui.screen_width - 20, y + 28], outline=(0, 255, 120))
            ui.draw_text((35, y), name[:34])
            y += 40

    ui.draw_text((20, ui.screen_height - 58), "A = Baixar ROM")
    ui.draw_text((20, ui.screen_height - 32), "DY = Navegar    B = Voltar")
    ui.draw_paint()


def draw_menu():
    # Limpa o frame antes de redesenhar para evitar que textos
    # do estado anterior fiquem sobrepostos ao menu atual.
    ui.draw_start()

    if section == 0:
        draw_ports_menu()
    elif section == 1 and rom_system_path:
        draw_rom_files()
    else:
        draw_rom_systems()


def show_status(title, lines):
    ui.draw_start()
    draw_header()
    ui.draw_text((25, 65), title)
    y = 110
    for line in lines:
        text = str(line)
        while len(text) > 55:
            ui.draw_text((30, y), text[:55])
            text = text[55:]
            y += 24
        ui.draw_text((30, y), text)
        y += 28
    ui.draw_paint()


def wait_message(title, lines):
    show_status(title, lines)
    while True:
        input.check()
        if input.key("A") or input.key("B"):
            return


# ============================================================
# NETWORK
# ============================================================

def wget_to_file(url, destination):
    try:
        result = subprocess.call(
            [
                "/usr/bin/wget",
                "-q",
                "-O",
                destination,
                url
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )

        if result != 0:
            return False

        if not os.path.exists(destination):
            return False

        return os.path.getsize(destination) > 0

    except:
        return False


def fetch_json(url):
    tmp = os.path.join(
        "/tmp",
        "masterportxx_json_%d.json" % os.getpid()
    )

    try:
        if not wget_to_file(url, tmp):
            return None

        with open(tmp, "r", encoding="utf-8") as f:
            return json.load(f)

    except:
        return None

    finally:
        try:
            os.remove(tmp)
        except:
            pass


# ============================================================
# ROMS - GITHUB CONTENTS API
# ============================================================

def github_api_json(path):
    path = str(path).strip("/")
    url = GITHUB_API_BASE + ("/" + path if path else "")
    return fetch_json(url)


def load_rom_systems():
    global rom_systems
    try:
        data = github_api_json(ROMS_REMOTE_PATH)
        if not isinstance(data, list):
            return False
        rom_systems = []
        for item in data:
            if not isinstance(item, dict):
                continue
            if item.get("type") != "dir":
                continue
            name = str(item.get("name", "")).strip()
            path = str(item.get("path", "")).strip("/")
            if name and path:
                rom_systems.append({"name": name, "path": path})
        rom_systems.sort(key=lambda x: x["name"].lower())
        return True
    except Exception:
        return False


def load_rom_files(remote_path):
    global rom_files
    try:
        data = github_api_json(remote_path)
        if not isinstance(data, list):
            return False
        rom_files = []
        for item in data:
            if not isinstance(item, dict):
                continue
            item_type = item.get("type")
            if item_type != "file":
                continue
            name = str(item.get("name", "")).strip()
            download_url = str(item.get("download_url", "")).strip()
            path = str(item.get("path", "")).strip("/")
            if name and download_url and path:
                rom_files.append({"name": name, "path": path, "download_url": download_url})
        rom_files.sort(key=lambda x: x["name"].lower())
        return True
    except Exception:
        return False


def rom_destination(system_name, filename):
    # O nome da pasta do GitHub é preservado no destino.
    safe_system = os.path.basename(system_name.strip("/"))
    safe_file = os.path.basename(filename)
    if not safe_system or safe_system in (".", ".."):
        raise RuntimeError("Nome de sistema inválido.")
    if not safe_file or safe_file in (".", ".."):
        raise RuntimeError("Nome de ROM inválido.")
    return os.path.join(ROMS_LOCAL_BASE, safe_system, safe_file)


def download_rom(item):
    if not rom_system_path:
        raise RuntimeError("Nenhum sistema selecionado.")

    system_name = rom_system_path.split("/")[-1]
    filename = item["name"]
    destination = rom_destination(system_name, filename)
    destination_dir = os.path.dirname(destination)
    os.makedirs(destination_dir, exist_ok=True)
    # O temporário fica junto da ROM para evitar EXDEV entre /tmp e /userdata.
    temp_path = os.path.join(destination_dir, ".masterportxx_rom_%d.tmp" % os.getpid())

    try:
        show_status("BAIXANDO ROM", [system_name, "", filename, "", "GitHub..."])
        if not download_raw_file(item["download_url"], temp_path):
            raise RuntimeError("Não foi possível baixar a ROM.")

        os.makedirs(os.path.dirname(destination), exist_ok=True)

        if os.path.exists(destination):
            show_status("ROM EXISTENTE", [filename, "", "A = Sobrescrever", "B = Cancelar"])
            while True:
                input.check()
                if input.key("B"):
                    return "cancelado"
                if input.key("A"):
                    break

        os.replace(temp_path, destination)
        return "ok"
    finally:
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except Exception:
            pass


# ============================================================
# HASH
# ============================================================

def sha256_file(path):
    h = hashlib.sha256()

    with open(path, "rb") as f:
        while True:
            block = f.read(1024 * 1024)

            if not block:
                break

            h.update(block)

    return h.hexdigest()


# ============================================================
# MANIFEST
# ============================================================

def load_manifest(package_id, temp_dir):
    url = SERVER_URL + "/" + package_id + "/manifest.json"
    path = os.path.join(temp_dir, "manifest.json")

    if not wget_to_file(url, path):
        raise RuntimeError("Não foi possível baixar manifest.json.")

    with open(path, "r", encoding="utf-8") as f:
        manifest = json.load(f)

    if manifest.get("format") != "MasterPortxx":
        raise RuntimeError("Formato de pacote inválido.")

    if str(manifest.get("package_id")) != package_id:
        raise RuntimeError("Package ID não corresponde à pasta.")

    parts = manifest.get("parts", [])

    if not parts:
        raise RuntimeError("Manifesto sem partes.")

    total_parts = int(manifest.get("total_parts", len(parts)))

    if len(parts) != total_parts:
        raise RuntimeError("Quantidade de partes inconsistente.")

    return manifest


# ============================================================
# DOWNLOAD + RECONSTRUÇÃO
# ============================================================

def download_package(game):
    package_id = str(game.get("id", "")).zfill(4)
    title = str(game.get("title", package_id))

    if len(package_id) != 4 or not package_id.isdigit():
        raise RuntimeError("ID inválido: " + package_id)

    temp_dir = tempfile.mkdtemp(prefix="masterportxx_")

    try:
        show_status(
            "Preparando...",
            [
                title,
                "ID: " + package_id,
                "",
                "Baixando manifesto..."
            ]
        )

        manifest = load_manifest(package_id, temp_dir)

        parts = sorted(
            manifest["parts"],
            key=lambda p: int(p["index"])
        )

        total_parts = len(parts)

        zip_path = os.path.join(temp_dir, "payload.zip")

        # Cria o ZIP reconstruído
        with open(zip_path, "wb") as final_zip:

            for position, part in enumerate(parts, 1):

                filename = str(part["file"])

                # Segurança: o nome deve ser somente um arquivo
                if (
                    not filename
                    or "/" in filename
                    or "\\" in filename
                    or filename in (".", "..")
                ):
                    raise RuntimeError("Nome de parte inválido.")

                expected_size = int(part["size_bytes"])
                expected_hash = str(part["sha256"]).lower()

                part_path = os.path.join(temp_dir, filename)

                show_status(
                    "BAIXANDO",
                    [
                        title,
                        "",
                        "Parte %d/%d" % (position, total_parts),
                        filename,
                        "",
                        "Tamanho: %d MB" %
                        (expected_size // (1024 * 1024))
                    ]
                )

                url = (
                    SERVER_URL
                    + "/"
                    + package_id
                    + "/"
                    + filename
                )

                if not wget_to_file(url, part_path):
                    raise RuntimeError(
                        "Falha ao baixar " + filename
                    )

                actual_size = os.path.getsize(part_path)

                if actual_size != expected_size:
                    raise RuntimeError(
                        "Tamanho incorreto em " + filename
                    )

                show_status(
                    "VERIFICANDO",
                    [
                        title,
                        "",
                        "Parte %d/%d" % (position, total_parts),
                        "SHA-256..."
                    ]
                )

                actual_hash = sha256_file(part_path).lower()

                if actual_hash != expected_hash:
                    raise RuntimeError(
                        "SHA-256 inválido em " + filename
                    )

                with open(part_path, "rb") as part_file:
                    shutil.copyfileobj(
                        part_file,
                        final_zip,
                        length=1024 * 1024
                    )

                try:
                    os.remove(part_path)
                except:
                    pass

        expected_zip_size = int(manifest["zip_size_bytes"])
        actual_zip_size = os.path.getsize(zip_path)

        if actual_zip_size != expected_zip_size:
            raise RuntimeError(
                "Tamanho final do ZIP incorreto."
            )

        # Testa o ZIP antes de extrair
        show_status(
            "TESTANDO ZIP",
            [
                title,
                "",
                "Verificando integridade..."
            ]
        )

        with zipfile.ZipFile(zip_path, "r") as zf:

            bad = zf.testzip()

            if bad:
                raise RuntimeError(
                    "ZIP corrompido: " + bad
                )

            names = zf.namelist()

            if not any(
                name == "data/"
                or name.startswith("data/")
                for name in names
            ):
                raise RuntimeError(
                    "ZIP não contém a pasta data/."
                )

            os.makedirs(PORTS_DIR, exist_ok=True)

            # Confere conflitos antes de alterar o PortMaster
            conflicts = []

            for name in names:

                if not name.startswith("data/"):
                    continue

                relative = name[5:]

                if not relative:
                    continue

                target = os.path.realpath(
                    os.path.join(PORTS_DIR, relative)
                )

                base = os.path.realpath(PORTS_DIR)

                if (
                    target != base
                    and not target.startswith(base + os.sep)
                ):
                    raise RuntimeError(
                        "Path traversal detectado."
                    )

                if os.path.exists(target):
                    conflicts.append(relative)

            if conflicts:

                show_status(
                    "ARQUIVOS EXISTENTES",
                    [
                        title,
                        "",
                        "%d arquivo(s) já existem." %
                        len(conflicts),
                        "",
                        "A = Sobrescrever",
                        "B = Cancelar"
                    ]
                )

                while True:
                    input.check()

                    if input.key("B"):
                        return "cancelado"

                    if input.key("A"):
                        break

            # Extração segura
            show_status(
                "INSTALANDO",
                [
                    title,
                    "",
                    "Extraindo em:",
                    PORTS_DIR
                ]
            )

            for name in names:

                if not name.startswith("data/"):
                    continue

                relative = name[5:]

                if not relative:
                    continue

                target = os.path.realpath(
                    os.path.join(PORTS_DIR, relative)
                )

                base = os.path.realpath(PORTS_DIR)

                if (
                    target != base
                    and not target.startswith(base + os.sep)
                ):
                    raise RuntimeError(
                        "Path traversal detectado."
                    )

                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    continue

                parent = os.path.dirname(target)

                if parent:
                    os.makedirs(parent, exist_ok=True)

                with zf.open(name) as src:
                    with open(target, "wb") as dst:
                        shutil.copyfileobj(
                            src,
                            dst,
                            length=1024 * 1024
                        )

        return "ok"

    finally:
        shutil.rmtree(
            temp_dir,
            ignore_errors=True
        )


# ============================================================
# MAIN
# ============================================================

def load_games():
    global games
    data = fetch_json(SERVER_URL + "/games.json")
    if not isinstance(data, dict):
        return False
    items = data.get("games", [])
    if not isinstance(items, list):
        return False
    games = []
    for game in items:
        if not isinstance(game, dict):
            continue
        package_id = str(game.get("id", "")).zfill(4)
        if not package_id.isdigit():
            continue
        games.append({
            "id": package_id,
            "title": str(game.get("title", package_id)),
            "description": str(game.get("description", ""))
        })
    return True


def main():
    global selected, section, rom_system_selected, rom_file_selected, rom_system_path

    load_config()
    input.start_gptokeyb()

    show_status("MASTERPORTXX", ["Conectando...", "", SERVER_URL])

    if not load_games():
        wait_message("ERRO DE CONEXÃO", [
            "Não foi possível carregar", "games.json.", "", "Servidor:", SERVER_URL, "", "A/B = Voltar"
        ])
        return

    # Carrega as pastas de ROMs do GitHub. Se não existir ROMs ainda,
    # o restante do aplicativo continua funcionando normalmente.
    load_rom_systems()

    selected = 0
    section = 0
    rom_system_selected = 0
    rom_file_selected = 0
    rom_system_path = ""

    while True:
        draw_menu()
        input.check()

        if input.key("B"):
            if section == 1 and rom_system_path:
                rom_system_path = ""
                rom_files.clear()
                rom_file_selected = 0
                continue
            break

        # X atualiza o app em qualquer tela principal.
        if input.key("X"):
            update_app()
            continue

        # Y restaura o app.bkp em qualquer tela principal.
        if input.key("Y") and not rom_system_path:
            restore_app_backup()
            continue

        # Esquerda/direita alterna entre Ports e ROMs.
        if not rom_system_path and input.key("DX", 1):
            section = 1
            continue
        if not rom_system_path and input.key("DX", -1):
            section = 0
            continue

        if section == 0:
            if input.key("DY", 1):
                selected = (selected + 1) % max(1, len(games))
            elif input.key("DY", -1):
                selected = (selected - 1) % max(1, len(games))
            elif input.key("A") and games:
                game = games[selected]
                try:
                    result = download_package(game)
                    if result == "cancelado":
                        continue
                    wait_message("DOWNLOAD CONCLUÍDO", [
                        game["title"], "", "Pacote %s instalado." % game["id"],
                        "", "Destino:", PORTS_DIR, "", "A/B = Voltar"
                    ])
                except Exception as e:
                    wait_message("ERRO", [game["title"], "", str(e), "", "A/B = Voltar"])

        elif section == 1 and not rom_system_path:
            if input.key("DY", 1) and rom_systems:
                rom_system_selected = (rom_system_selected + 1) % len(rom_systems)
            elif input.key("DY", -1) and rom_systems:
                rom_system_selected = (rom_system_selected - 1) % len(rom_systems)
            elif input.key("A") and rom_systems:
                rom_system_path = rom_systems[rom_system_selected]["path"]
                rom_file_selected = 0
                if not load_rom_files(rom_system_path):
                    wait_message("ERRO", ["Não foi possível carregar", rom_system_path, "", "A/B = Voltar"])
                    rom_system_path = ""

        else:
            if input.key("DY", 1) and rom_files:
                rom_file_selected = (rom_file_selected + 1) % len(rom_files)
            elif input.key("DY", -1) and rom_files:
                rom_file_selected = (rom_file_selected - 1) % len(rom_files)
            elif input.key("A") and rom_files:
                item = rom_files[rom_file_selected]
                try:
                    result = download_rom(item)
                    if result == "cancelado":
                        continue
                    wait_message("ROM INSTALADA", [
                        item["name"], "", "Sistema:", rom_system_path.split("/")[-1],
                        "", "Destino:", ROMS_LOCAL_BASE, "", "A/B = Voltar"
                    ])
                except Exception as e:
                    wait_message("ERRO", [item["name"], "", str(e), "", "A/B = Voltar"])


if __name__ == "__main__":
    main()
