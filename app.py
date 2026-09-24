#!/usr/bin/env python3
# -*- coding: utf-8 -*-

# ============================================================
# MasterPortxx Downloader v1.3.6
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
# ROMs (Packer v1.4):
#   GitHub/ROMs/index.json
#   GitHub/ROMs/<sistema>/catalog.json
#   GitHub/ROMs/<sistema>/0001/manifest.json
#   GitHub/ROMs/<sistema>/0001/MasterPortxx_0001.mpx
#
# Ports extraem data/ diretamente em PORTS_DIR.
# ROMs extraem data/ diretamente em /userdata/roms/<sistema>.
# ============================================================

import os
APP_DIR = os.path.dirname(os.path.abspath(__file__))
import json
# KNULLI/PortMaster input bridge: GPTOKEYB converts the physical gamepad into SDL keyboard events.
import ctypes
import atexit
import shlex
import glob
import select
import struct
import fcntl
try:
    import sdl2
except Exception:
    sdl2 = None

class MultiEvdevReader:
    """Lê os dispositivos evdev do Knulli sem assumir event1.

    D-pad pode aparecer como BTN_DPAD_* (EV_KEY) ou ABS_HAT0X/Y (EV_ABS).
    Também preserva os códigos usados pelo input.py antigo do RG35XX H.
    """
    EV_KEY = 0x01
    EV_ABS = 0x03
    KEY_UP = 103
    KEY_DOWN = 108
    KEY_LEFT = 105
    KEY_RIGHT = 106
    BTN_DPAD_UP = 544
    BTN_DPAD_DOWN = 545
    BTN_DPAD_LEFT = 546
    BTN_DPAD_RIGHT = 547
    ABS_X = 0
    ABS_Y = 1
    ABS_HAT0X = 16
    ABS_HAT0Y = 17

    def __init__(self):
        self.fds = []
        self.poller = select.poll()
        # Estado por eixo + filtro de duplicação entre evdev/SDL/GPTOKEYB.
        self._axis_state = {"DX": 0, "DY": 0}
        self._last_dpad = None
        self._last_dpad_time = 0.0
        self._dpad_lock_until = 0.0
        self._open_devices()

    def _open_devices(self):
        for path in sorted(glob.glob('/dev/input/event*')):
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK)
                self.fds.append((fd, path))
                self.poller.register(fd, select.POLLIN)
                print('EVDEV aberto:', path)
            except Exception as exc:
                print('EVDEV ignorado:', path, repr(exc))

    def close(self):
        for fd, _ in self.fds:
            try:
                self.poller.unregister(fd)
            except Exception:
                pass
            try:
                os.close(fd)
            except Exception:
                pass
        self.fds = []

    def _translate(self, typ, code, value):
        if typ == self.EV_KEY:
            if code in (self.BTN_DPAD_UP, self.KEY_UP): return 'DY+', value != 0
            if code in (self.BTN_DPAD_DOWN, self.KEY_DOWN): return 'DY-', value != 0
            if code in (self.BTN_DPAD_LEFT, self.KEY_LEFT): return 'DX-', value != 0
            if code in (self.BTN_DPAD_RIGHT, self.KEY_RIGHT): return 'DX+', value != 0
            # RG35XX H / input.py button codes
            button_map = {304:'A',305:'B',306:'Y',307:'X',308:'L1',309:'R1',314:'L2',315:'R2',310:'SELECT',311:'START',312:'MENUF',114:'V+',115:'V-'}
            if code in button_map: return button_map[code], value != 0
        elif typ == self.EV_ABS:
            # O D-pad/analógico do RG35XX H pode aparecer como eixo ABS.
            # Nesse caso, ao soltar o controle o eixo pode retornar por vários
            # valores intermediários. Esses valores NÃO podem virar vários
            # comandos de menu. O estado do eixo é tratado em read().
            if code == self.ABS_HAT0X:
                if value < 0: return 'DX-', True
                if value > 0: return 'DX+', True
                return None, False
            if code == self.ABS_HAT0Y:
                if value < 0: return 'DY+', True
                if value > 0: return 'DY-', True
                return None, False
        return None, False

    def drain(self):
        """Esvazia eventos pendentes dos dispositivos sem processá-los."""
        for fd, _ in self.fds:
            try:
                while True:
                    data = os.read(fd, 24)
                    if len(data) != 24:
                        break
            except (BlockingIOError, OSError):
                pass

    def read(self):
        # v1.4.0: o RG35XX-H Controller foi identificado no diagnóstico como
        # /dev/input/event1 e o D-pad usa exclusivamente ABS_HAT0X/Y.
        # Portanto NÃO interpretamos ABS_X/Y, não usamos SDL para D-pad e
        # não aplicamos debounce por tempo. Cada transição 0 -> direção gera
        # exatamente uma ação; direção -> 0 apenas libera o próximo toque.
        for fd, path in self.fds:
            if "event1" not in path:
                continue
            events = self.poller.poll(0)
            if not events:
                break
            try:
                while True:
                    data = os.read(fd, 24)
                    if len(data) != 24:
                        break
                    tv_sec, tv_usec, typ, code, value = struct.unpack('llHHI', data)
                    if typ == self.EV_KEY and value == 2:
                        continue
                    if typ == self.EV_ABS and value >= 0x80000000:
                        value -= 0x100000000
                    if typ == self.EV_ABS and code in (self.ABS_HAT0X, self.ABS_HAT0Y):
                        axis = "DX" if code == self.ABS_HAT0X else "DY"
                        direction = 0
                        if value < 0: direction = -1
                        elif value > 0: direction = 1
                        previous = self._axis_state[axis]
                        self._axis_state[axis] = direction
                        if direction == 0 or direction == previous:
                            continue
                        if axis == "DX":
                            return ("DX+" if direction > 0 else "DX-", 1, value)
                        return ("DY-" if direction > 0 else "DY+", 1, value)
                    name, pressed = self._translate(typ, code, value)
                    if name and name not in ("DX+","DX-","DY+","DY-"):
                        return name, (1 if pressed else -1), value
            except (BlockingIOError, OSError):
                pass
        return None, 0, 0


class SDLInputBridge:
    """KNULLI input bridge.

    Priority:
      1) Linux evdev para D-pad e controles físicos.
      2) SDL/GPTOKEYB somente como fallback para botões não-D-pad.

    This keeps the working gptokeyb path while also accepting the RG35XX H
    controller directly if gptokeyb does not inject keyboard events into SDL.
    """
    def __init__(self):
        self.codeName = ""
        self.value = 0
        self._gptokeyb = None
        self._gptk_path = os.path.join(APP_DIR, "masterportxx.gptk")
        self._controller = None
        self._controller_ready = False
        self._controller_index = -1
        self.held_buttons = set()
        # Estado dos eixos: evita que o retorno físico do analógico/D-pad
        # seja interpretado como vários novos comandos.
        self._axis_state = {"DX": 0, "DY": 0}
        self._sdl_axis_state = {"DX": 0, "DY": 0}
        self._controller_init()
        self._evdev = MultiEvdevReader()

    def _controller_init(self):
        if sdl2 is None:
            return
        try:
            # PortMaster/KNULLI already initializes SDL video through graphic.py.
            # Only initialize the controller subsystem if needed.
            init_sub = getattr(sdl2, "SDL_InitSubSystem", None)
            if init_sub is not None:
                rc = init_sub(sdl2.SDL_INIT_GAMECONTROLLER | sdl2.SDL_INIT_JOYSTICK)
                if rc != 0:
                    print("SDL controller subsystem init:", sdl2.SDL_GetError())

            num = sdl2.SDL_NumJoysticks()
            print("SDL joysticks:", num)
            for i in range(max(0, num)):
                try:
                    if sdl2.SDL_IsGameController(i):
                        controller = sdl2.SDL_GameControllerOpen(i)
                        if controller:
                            self._controller = controller
                            self._controller_index = i
                            self._controller_ready = True
                            print("SDL GameController aberto:", i)
                            return
                except Exception as exc:
                    print("Falha abrindo GameController", i, repr(exc))
        except Exception as exc:
            print("SDL GameController indisponível:", repr(exc))

    def _key_name(self, sym):
        if sdl2 is None:
            return None
        # D-pad NÃO entra pelo SDL/GPTOKEYB. O D-pad é tratado exclusivamente
        # pelo EVDEV, evitando que um único toque seja contado duas vezes.
        m = {
            getattr(sdl2, "SDLK_x", ord("x")): "A",
            getattr(sdl2, "SDLK_z", ord("z")): "B",
            getattr(sdl2, "SDLK_c", ord("c")): "X",
            getattr(sdl2, "SDLK_a", ord("a")): "Y",
            getattr(sdl2, "SDLK_RETURN", 13): "START",
            getattr(sdl2, "SDLK_ESCAPE", 27): "B",
            getattr(sdl2, "SDLK_BACKSPACE", 8): "B",
        }
        return m.get(sym)

    def _controller_button_name(self, button):
        if sdl2 is None:
            return None
        m = {
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_DPAD_UP", 11): "DY+",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_DPAD_DOWN", 12): "DY-",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_DPAD_LEFT", 13): "DX-",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_DPAD_RIGHT", 14): "DX+",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_A", 0): "A",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_B", 1): "B",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_X", 2): "X",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_Y", 3): "Y",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_START", 6): "START",
            getattr(sdl2, "SDL_CONTROLLER_BUTTON_BACK", 4): "SELECT",
        }
        return m.get(button)

    def _axis_to_name(self, axis, value):
        if sdl2 is None or abs(value) < 8000:
            return None
        if axis == getattr(sdl2, "SDL_CONTROLLER_AXIS_LEFTX", 0):
            return "DX+" if value > 0 else "DX-"
        if axis == getattr(sdl2, "SDL_CONTROLLER_AXIS_LEFTY", 1):
            return "DY+" if value > 0 else "DY-"
        return None

    def _poll_sdl(self):
        if sdl2 is None:
            return False
        ev = sdl2.SDL_Event()
        found = False
        while sdl2.SDL_PollEvent(ctypes.byref(ev)):
            if ev.type == sdl2.SDL_KEYDOWN:
                # SDL pode gerar KEYDOWN repetido enquanto a tecla/D-pad está
                # pressionada. Para navegação de menus queremos somente a
                # primeira borda de pressão.
                if getattr(ev.key, "repeat", 0):
                    continue
                name = self._key_name(ev.key.keysym.sym)
                if name:
                    self._set_event(name, 1)
                    return True
            elif ev.type == getattr(sdl2, "SDL_KEYUP", 769):
                name = self._key_name(ev.key.keysym.sym)
                if name:
                    self._set_event(name, -1)
                    return True
            elif ev.type == getattr(sdl2, "SDL_CONTROLLERBUTTONDOWN", 1617):
                name = self._controller_button_name(ev.cbutton.button)
                # D-pad fica exclusivamente no EVDEV para evitar que o mesmo
                # toque seja recebido duas vezes (EVDEV + SDL).
                if name in ("DY+", "DY-", "DX+", "DX-"):
                    continue
                if name:
                    self._set_event(name, 1)
                    return True
            elif ev.type == getattr(sdl2, "SDL_CONTROLLERBUTTONUP", 1618):
                name = self._controller_button_name(ev.cbutton.button)
                if name in ("DY+", "DY-", "DX+", "DX-"):
                    continue
                if name:
                    self._set_event(name, -1)
                    return True
            elif ev.type == getattr(sdl2, "SDL_CONTROLLERAXISMOTION", 1616):
                # Não usar eixos SDL para navegação neste hardware.
                # O EVDEV já fornece o D-pad e é a única fonte aceita para ele.
                continue
                axis = ev.caxis.axis
                raw = int(ev.caxis.value)
                axis_name = None
                direction = 0
                if axis == getattr(sdl2, "SDL_CONTROLLER_AXIS_LEFTX", 0):
                    axis_name = "DX"
                    if raw >= 8000: direction = 1
                    elif raw <= -8000: direction = -1
                elif axis == getattr(sdl2, "SDL_CONTROLLER_AXIS_LEFTY", 1):
                    axis_name = "DY"
                    if raw >= 8000: direction = 1
                    elif raw <= -8000: direction = -1

                if axis_name is not None:
                    previous = self._sdl_axis_state[axis_name]
                    self._sdl_axis_state[axis_name] = direction
                    # Centro: apenas atualiza o estado, não navega.
                    if direction == 0:
                        continue
                    # Mesma direção: não repete.
                    if direction == previous:
                        continue

                name = self._axis_to_name(axis, raw)
                if name:
                    self._set_event(name, 1)
                    return True
            elif ev.type == sdl2.SDL_QUIT:
                self._set_event("B", 1)
                return True
        return found

    def _set_event(self, name, value):
        self.codeName = name or ""
        self.value = value or 0

        if name:
            if value == 1:
                self.held_buttons.add(name)
            elif value == -1:
                self.held_buttons.discard(name)

    def is_held(self, name):
        return name in self.held_buttons

    def discard_pending(self):
        """Descarta eventos já enfileirados sem criar novas ações no menu."""
        try:
            self._evdev.drain()
        except Exception:
            pass

        if sdl2 is not None:
            try:
                ev = sdl2.SDL_Event()
                while sdl2.SDL_PollEvent(ctypes.byref(ev)):
                    pass
            except Exception:
                pass

        self.codeName = ""
        self.value = 0

    def wait_button_release(self, name):
        """Espera a soltura de A/B antes de aceitar outro comando."""
        import time
        if not self.is_held(name):
            return

        while self.is_held(name):
            self.check()
            if not self.codeName:
                time.sleep(0.01)

        self.codeName = ""
        self.value = 0

    def check(self):
        self.codeName = ""
        self.value = 0

        # No RG35XX H/Knulli, o caminho EVDEV é o que já foi comprovado
        # como correto para A/B/X/Y e para o D-pad. Ele tem prioridade para
        # evitar que eventos virtuais do GPTOKEYB/SDL alterem a identificação
        # física dos botões.
        try:
            name, value, raw_value = self._evdev.read()
            if name:
                self._set_event(name, value)
                print("INPUT:", name, value, "raw=", raw_value)
                if name in ("DX+", "DX-", "DY+", "DY-") and value == 1:
                    # Descarta o restante do pacote/burst do mesmo toque.
                    self._evdev.drain()
                return
        except Exception as exc:
            print("EVDEV read failed:", repr(exc))

        # Se não houver evento EVDEV disponível, aceita SDL/GPTOKEYB como
        # fallback. O mapeamento semântico continua sendo A/B/X/Y.
        if self._poll_sdl():
            return

    def key(self, keyCodeName, keyValue=99):
        # Compatibilidade: o evdev traduz o D-pad para DX+/DX-/DY+/DY-,
        # enquanto o restante do aplicativo consulta DX/DY com valor +/-1.
        if keyCodeName == "DX":
            # Navegação é por evento de pressionamento, nunca por estado mantido.
            if keyValue == 1:
                return self.codeName == "DX+" and self.value == 1
            if keyValue == -1:
                return self.codeName == "DX-" and self.value == 1
            return self.codeName in ("DX+", "DX-") and self.value == 1
        if keyCodeName == "DY":
            # Compatibilidade com o contrato antigo do MasterPortxx:
            # DY=+1 significa BAIXO e DY=-1 significa CIMA.
            # O evdev traduz fisicamente: baixo -> DY- e cima -> DY+.
            # Importante: somente value=1 (pressionar) altera a seleção.
            # value=-1 é a soltura e nunca move a lista.
            if keyValue == 1:
                return self.codeName == "DY-" and self.value == 1
            if keyValue == -1:
                return self.codeName == "DY+" and self.value == 1
            return self.codeName in ("DY+", "DY-") and self.value == 1
        if self.codeName == keyCodeName:
            return self.value == keyValue if keyValue != 99 else True
        return False

    def slide_key(self):
        return bool(self.codeName)

    def reset_input(self):
        self.codeName = ""
        self.value = 0

    def _write_gptk(self):
        gptk = "\n".join([
            "# MasterPortxx Knulli controls",
            "start = enter", "guide = enter",
            "a = x", "b = z", "x = c", "y = a",
            "up = up", "down = down", "left = left", "right = right",
            "left_analog_up = up", "left_analog_down = down",
            "left_analog_left = left", "left_analog_right = right",
            "deadzone_x = 12000", "deadzone_y = 12000", "deadzone_triggers = 3000",
            "deadzone_mode = scaled_radial", "deadzone = 2000", "deadzone_scale = 8",
            "repeat_delay = 300", "repeat_interval = 60", ""
        ])
        with open(self._gptk_path, "w", encoding="utf-8") as f:
            f.write(gptk)

    def start_gptokeyb(self):
        if self._gptokeyb is not None and self._gptokeyb.poll() is None:
            return True
        try:
            self._write_gptk()
            try:
                os.chmod("/dev/uinput", 0o666)
            except Exception:
                pass

            # get_controls normally provides this. Preserve it explicitly for SDL/gptokeyb.
            env = os.environ.copy()
            if env.get("sdl_controllerconfig") and not env.get("SDL_GAMECONTROLLERCONFIG"):
                env["SDL_GAMECONTROLLERCONFIG"] = env["sdl_controllerconfig"]
            if env.get("sdl_controllerconfig") and not env.get("SDL_GAMECONTROLLERCONFIG_FILE"):
                candidate = env["sdl_controllerconfig"]
                if os.path.isfile(candidate):
                    env["SDL_GAMECONTROLLERCONFIG_FILE"] = candidate

            gpt = env.get("GPTOKEYB", "").strip()
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

            # gptokeyb's first argument is the application name used by its kill switch.
            # python3 is the actual executable for this app; mapping itself is global.
            cmd += ["python3", "-c", self._gptk_path]
            self._gptokeyb = subprocess.Popen(cmd, cwd=APP_DIR, env=env)
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
        try:
            self._evdev.close()
        except Exception:
            pass
        if self._controller is not None and sdl2 is not None:
            try:
                sdl2.SDL_GameControllerClose(self._controller)
            except Exception:
                pass
            self._controller = None

input = SDLInputBridge()
atexit.register(input.stop_gptokeyb)
import subprocess
import zipfile
import hashlib
import shutil
import tempfile
import sys
import xml.etree.ElementTree as ET
from urllib.parse import quote
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
APP_VERSION = "v1.5.3"

# GitHub ROM catalog
GITHUB_API_BASE = "https://api.github.com/repos/seumedeiros/MasterPortxx/contents"
GITHUB_RAW_BASE = "https://raw.githubusercontent.com/seumedeiros/MasterPortxx/main"
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
        ui.draw_text((40, 185), "Publique ROMs/ com index.json")
        ui.draw_text((40, 215), "e catalog.json por sistema.")
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
            package_id = str(item.get("id", "0000")).zfill(4)
            name = str(item.get("title", item.get("name", package_id)))
            if i == rom_file_selected:
                ui.draw_rectangle([20, y - 6, ui.screen_width - 20, y + 28], outline=(0, 255, 120))
            ui.draw_text((35, y), "%s - %s" % (package_id, name[:29]))
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


def wait_message(title, lines, release_buttons=("A", "B")):
    show_status(title, lines)

    # Primeiro espera a soltura dos botões que possam ainda estar pressionados.
    # Isso permite que o evento de RELEASE seja lido e atualize o estado interno.
    for button in release_buttons:
        input.wait_button_release(button)

    # Só depois disso descartamos eventos antigos que possam ter ficado na fila
    # durante o download. Eles não podem virar uma nova escolha.
    input.discard_pending()

    while True:
        input.check()
        if input.key("A") or input.key("B"):
            input.wait_button_release("A")
            input.wait_button_release("B")
            input.discard_pending()
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


def github_raw_url(path):
    """Monta uma URL raw segura, escapando cada componente do caminho."""
    parts = [p for p in str(path).strip("/").split("/") if p]
    return GITHUB_RAW_BASE + "/" + "/".join(quote(p, safe="") for p in parts)


def load_rom_systems():
    """Carrega os sistemas a partir de ROMs/index.json gerado pelo Packer v1.4.

    Se o índice ainda não existir, usa a Contents API como fallback para
    manter compatibilidade com a estrutura antiga de ROMs/.
    """
    global rom_systems
    try:
        data = fetch_json(github_raw_url("ROMs/index.json"))
        if isinstance(data, dict) and isinstance(data.get("systems"), list):
            rom_systems = []
            for item in data["systems"]:
                if not isinstance(item, dict):
                    continue
                name = str(item.get("folder", "")).strip()
                title = str(item.get("title", name)).strip() or name
                if not name or name in (".", ".."): 
                    continue
                rom_systems.append({
                    "name": title,
                    "folder": name,
                    "path": "ROMs/" + name,
                    "package_count": int(item.get("package_count", 0) or 0)
                })
            rom_systems.sort(key=lambda x: x["name"].lower())
            return True
    except Exception:
        pass

    # Fallback para uma instalação que ainda tenha somente pastas no GitHub.
    try:
        data = github_api_json(ROMS_REMOTE_PATH)
        if not isinstance(data, list):
            return False
        rom_systems = []
        for item in data:
            if not isinstance(item, dict) or item.get("type") != "dir":
                continue
            name = str(item.get("name", "")).strip()
            path = str(item.get("path", "")).strip("/")
            if name and path:
                rom_systems.append({"name": name, "folder": name, "path": path, "package_count": 0})
        rom_systems.sort(key=lambda x: x["name"].lower())
        return True
    except Exception:
        return False


def load_rom_files(remote_path):
    """Carrega os pacotes ROM de ROMs/<sistema>/catalog.json."""
    global rom_files
    try:
        catalog_url = github_raw_url(str(remote_path).strip("/") + "/catalog.json")
        data = fetch_json(catalog_url)
        if not isinstance(data, dict) or not isinstance(data.get("packages"), list):
            return False

        rom_files = []
        for item in data["packages"]:
            if not isinstance(item, dict):
                continue
            package_id = str(item.get("id", "")).zfill(4)
            title = str(item.get("title", package_id)).strip() or package_id
            if len(package_id) != 4 or not package_id.isdigit():
                continue
            rom_files.append({
                "id": package_id,
                "name": title,
                "title": title,
                "path": str(remote_path).strip("/") + "/" + package_id,
                "type": "rom"
            })
        rom_files.sort(key=lambda x: x["name"].lower())
        return True
    except Exception:
        return False


def rom_destination(system_name, relative_name):
    """Retorna o destino final da ROM dentro de /userdata/roms/<sistema>."""
    safe_system = os.path.basename(str(system_name).strip("/"))
    if not safe_system or safe_system in (".", ".."):
        raise RuntimeError("Nome de sistema inválido.")

    relative_name = str(relative_name).replace("\\", "/").lstrip("/")
    if not relative_name or relative_name == "." or ".." in relative_name.split("/"):
        raise RuntimeError("Caminho de ROM inválido.")

    destination = os.path.realpath(os.path.join(ROMS_LOCAL_BASE, safe_system, relative_name))
    base = os.path.realpath(os.path.join(ROMS_LOCAL_BASE, safe_system))
    if destination != base and not destination.startswith(base + os.sep):
        raise RuntimeError("Path traversal detectado.")
    return destination


def load_rom_manifest(system_folder, package_id, temp_dir):
    package_id = str(package_id).zfill(4)
    if len(package_id) != 4 or not package_id.isdigit():
        raise RuntimeError("ID de ROM inválido: " + package_id)

    url = github_raw_url(f"ROMs/{system_folder}/{package_id}/manifest.json")
    path = os.path.join(temp_dir, "manifest.json")
    if not wget_to_file(url, path):
        raise RuntimeError("Não foi possível baixar o manifest da ROM.")

    try:
        with open(path, "r", encoding="utf-8") as f:
            manifest = json.load(f)
    except Exception as exc:
        raise RuntimeError("Manifest da ROM inválido.") from exc

    if manifest.get("format") != "MasterPortxx":
        raise RuntimeError("Formato de pacote ROM inválido.")
    if str(manifest.get("package_id", "")) != package_id:
        raise RuntimeError("Package ID não corresponde à pasta.")
    if str(manifest.get("package_type", manifest.get("type", ""))).lower() != "rom":
        raise RuntimeError("O pacote não é do tipo ROM.")
    if str(manifest.get("system_folder", "")) != str(system_folder):
        raise RuntimeError("O sistema do manifesto não corresponde à pasta.")

    parts = manifest.get("parts", [])
    total_parts = int(manifest.get("total_parts", len(parts)))
    if not parts or len(parts) != total_parts:
        raise RuntimeError("Quantidade de partes da ROM inconsistente.")
    if int(manifest.get("part_size_bytes", 0)) != 25 * 1024 * 1024:
        raise RuntimeError("A ROM não está usando partes de 25 MB.")
    return manifest


def download_rom(item):
    """Baixa, verifica, reconstrói e instala um pacote ROM v1.4."""
    if not rom_system_path:
        raise RuntimeError("Nenhum sistema selecionado.")

    system_folder = rom_system_path.split("/")[-1]
    package_id = str(item.get("id", "")).zfill(4)
    title = str(item.get("title", item.get("name", package_id)))
    temp_dir = tempfile.mkdtemp(prefix="masterportxx_rom_")

    try:
        show_status("PREPARANDO ROM", [system_folder, title, "", "Baixando manifesto..."])
        manifest = load_rom_manifest(system_folder, package_id, temp_dir)
        parts = sorted(manifest["parts"], key=lambda p: int(p["index"]))
        zip_path = os.path.join(temp_dir, "payload.zip")

        with open(zip_path, "wb") as final_zip:
            for position, part in enumerate(parts, 1):
                filename = str(part.get("file", ""))
                if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
                    raise RuntimeError("Nome de parte inválido.")

                expected_size = int(part["size_bytes"])
                expected_hash = str(part["sha256"]).lower()
                part_path = os.path.join(temp_dir, filename)

                show_status("BAIXANDO ROM", [
                    system_folder, title, "",
                    "Parte %d/%d" % (position, len(parts)),
                    filename,
                    "25 MB" if position < len(parts) else "Última parte"
                ])

                url = github_raw_url(f"ROMs/{system_folder}/{package_id}/{filename}")
                if not wget_to_file(url, part_path):
                    raise RuntimeError("Falha ao baixar " + filename)
                if os.path.getsize(part_path) != expected_size:
                    raise RuntimeError("Tamanho incorreto em " + filename)

                show_status("VERIFICANDO ROM", [
                    title, "",
                    "Parte %d/%d" % (position, len(parts)),
                    "SHA-256..."
                ])
                if sha256_file(part_path).lower() != expected_hash:
                    raise RuntimeError("SHA-256 inválido em " + filename)

                with open(part_path, "rb") as pf:
                    shutil.copyfileobj(pf, final_zip, length=1024 * 1024)
                try:
                    os.remove(part_path)
                except Exception:
                    pass

        expected_zip_size = int(manifest["zip_size_bytes"])
        if os.path.getsize(zip_path) != expected_zip_size:
            raise RuntimeError("Tamanho final do ZIP da ROM incorreto.")

        show_status("TESTANDO ROM", [title, "", "Verificando integridade do ZIP..."])
        with zipfile.ZipFile(zip_path, "r") as zf:
            bad = zf.testzip()
            if bad:
                raise RuntimeError("ZIP corrompido: " + bad)

            names = zf.namelist()
            members = [n for n in names if n == "data/" or n.startswith("data/")]
            if not members:
                raise RuntimeError("Pacote ROM sem pasta data/.")

            destination_base = os.path.realpath(os.path.join(ROMS_LOCAL_BASE, system_folder))
            os.makedirs(destination_base, exist_ok=True)
            conflicts = []

            for name in members:
                relative = name[5:]
                if not relative:
                    continue
                target = os.path.realpath(os.path.join(destination_base, relative))
                if target != destination_base and not target.startswith(destination_base + os.sep):
                    raise RuntimeError("Path traversal detectado na ROM.")
                if os.path.exists(target):
                    conflicts.append(relative)

            if conflicts:
                show_status("ROM EXISTENTE", [
                    title, "",
                    "%d arquivo(s) já existem." % len(conflicts),
                    "", "A = Sobrescrever", "B = Cancelar"
                ])
                while True:
                    input.check()
                    if input.key("B"):
                        return "cancelado"
                    if input.key("A"):
                        break

            show_status("INSTALANDO ROM", [
                title, "",
                "Sistema:", system_folder,
                "Destino:", destination_base
            ])

            for name in members:
                relative = name[5:]
                if not relative:
                    continue
                target = os.path.realpath(os.path.join(destination_base, relative))
                if target != destination_base and not target.startswith(destination_base + os.sep):
                    raise RuntimeError("Path traversal detectado na ROM.")
                if name.endswith("/"):
                    os.makedirs(target, exist_ok=True)
                    continue
                parent = os.path.dirname(target)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with zf.open(name, "r") as src_file, open(target, "wb") as dst_file:
                    shutil.copyfileobj(src_file, dst_file, length=1024 * 1024)

        # Atualiza o gamelist somente depois que todos os arquivos da ROM
        # e da imagem foram instalados com sucesso.
        try:
            update_rom_gamelist(system_folder, manifest)
        except Exception as exc:
            raise RuntimeError("ROM instalada, mas não foi possível atualizar gamelist.xml: " + str(exc))

        return "ok"
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


# ============================================================
# GAMELIST KNULLI — ROMS
# ============================================================

def _xml_text(element):
    return (element.text or "").strip() if element is not None else ""


def _safe_xml_write(root, path):
    tree = ET.ElementTree(root)
    try:
        ET.indent(tree, space="\t", level=0)
    except Exception:
        pass
    tmp = path + ".masterportxx.tmp"
    tree.write(tmp, encoding="utf-8", xml_declaration=True)
    os.replace(tmp, path)


def _manifest_rom_gamelist_data(system_folder, manifest):
    """Monta os dados da entrada sem exigir 'gamelist' no manifest.

    Packer v1.5.2 fornece image/marquee/thumbnail no próprio manifest.
    Para compatibilidade, também aceita manifestos antigos que tenham
    um bloco 'gamelist'.
    """
    rom_name = str(manifest.get("rom_name") or manifest.get("original_name") or "").strip()
    if not rom_name:
        raise RuntimeError("Manifesto ROM sem nome da ROM.")

    base_name = os.path.splitext(os.path.basename(rom_name))[0]
    old = manifest.get("gamelist")
    if not isinstance(old, dict):
        old = {}

    data = {
        "path": "./" + os.path.basename(rom_name),
        "name": str(old.get("name") or base_name),
    }

    # Packer v1.5.2 grava estes caminhos no manifest.
    image = manifest.get("image")
    marquee = manifest.get("marquee")
    thumbnail = manifest.get("thumbnail")

    # Compatibilidade com manifests que guardam a mídia dentro de gamelist.
    image = image or old.get("image")
    marquee = marquee or old.get("marquee")
    thumbnail = thumbnail or old.get("thumbnail")

    # Mesmo sem arquivo de imagem, escrevemos o caminho esperado no gamelist.
    # Isso permite ao usuário colocar a imagem manualmente depois.
    if not image:
        image = "./images/" + base_name + "-image.png"
    if not marquee:
        marquee = "./images/" + base_name + "-marquee.png"
    if not thumbnail:
        thumbnail = "./images/" + base_name + "-thumb.png"

    data["image"] = str(image)
    data["marquee"] = str(marquee)
    data["thumbnail"] = str(thumbnail)

    # Campos opcionais já existentes no manifest/gamelist.
    for key in ("desc", "rating", "releasedate", "developer", "publisher", "genre", "players", "lang", "region", "family", "video"):
        if old.get(key) is not None and str(old.get(key)).strip():
            data[key] = str(old[key])

    return data


def update_rom_gamelist(system_folder, manifest):
    """Cria/atualiza /userdata/roms/<sistema>/gamelist.xml.

    Se a ROM já existir, preserva playcount, lastplayed, gametime e outros
    campos que não são substituídos. Se não existir, cria uma nova entrada.
    """
    safe_system = os.path.basename(str(system_folder).strip("/"))
    if not safe_system or safe_system in (".", ".."):
        raise RuntimeError("Sistema inválido para gamelist.")

    system_dir = os.path.realpath(os.path.join(ROMS_LOCAL_BASE, safe_system))
    os.makedirs(system_dir, exist_ok=True)
    gamelist_path = os.path.join(system_dir, "gamelist.xml")
    data = _manifest_rom_gamelist_data(safe_system, manifest)

    try:
        if os.path.isfile(gamelist_path):
            tree = ET.parse(gamelist_path)
            root = tree.getroot()
        else:
            root = ET.Element("gameList")
    except Exception:
        # Não destrói o arquivo original se o XML existente estiver inválido.
        broken = gamelist_path + ".broken"
        try:
            shutil.copy2(gamelist_path, broken)
        except Exception:
            pass
        root = ET.Element("gameList")

    wanted = data["path"].replace("\\", "/").lstrip("./")
    wanted_base = os.path.basename(wanted)
    found = None

    for game in root.findall("game"):
        path_el = game.find("path")
        current = _xml_text(path_el).replace("\\", "/").lstrip("./")
        if current == wanted or os.path.basename(current) == wanted_base:
            found = game
            break

    if found is None:
        found = ET.SubElement(root, "game")
        ET.SubElement(found, "path")

    # Atualiza somente os campos controlados pelo MasterPortxx.
    # Estatísticas do usuário permanecem intactas.
    ordered = ["path", "name", "desc", "image", "marquee", "thumbnail", "video",
               "rating", "releasedate", "developer", "publisher", "genre",
               "players", "lang", "region", "family"]
    values = {"path": data["path"], **data}

    for tag in ordered:
        if tag not in values:
            continue
        child = found.find(tag)
        if child is None:
            child = ET.SubElement(found, tag)
        child.text = str(values[tag])

    _safe_xml_write(root, gamelist_path)
    return gamelist_path


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
                input.wait_button_release("B")
                input.discard_pending()
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
                    wait_message("ERRO", ["Não foi possível carregar", rom_system_path, "catalog.json", "A/B = Voltar"])
                    rom_system_path = ""
                else:
                    # O A usado para abrir o sistema não pode ser reutilizado
                    # para iniciar o primeiro jogo. Primeiro aguardamos a soltura,
                    # depois descartamos qualquer repetição que tenha ficado na fila.
                    input.wait_button_release("A")
                    input.discard_pending()

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
                        item.get("title", item.get("name", item.get("id", "ROM"))), "",
                        "Sistema:", rom_system_path.split("/")[-1],
                        "", "Destino:", os.path.join(ROMS_LOCAL_BASE, rom_system_path.split("/")[-1]),
                        "", "gamelist.xml atualizado",
                        "", "A = Continuar    B = Voltar"
                    ])
                except Exception as e:
                    wait_message("ERRO", [item["name"], "", str(e), "", "A/B = Voltar"])


if __name__ == "__main__":
    main()
