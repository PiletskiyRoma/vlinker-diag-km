from kivy.app import App
from kivy.uix.boxlayout import BoxLayout
from kivy.uix.spinner import Spinner
from kivy.uix.button import Button
from kivy.uix.textinput import TextInput
from kivy.uix.label import Label
from kivy.clock import Clock
import threading
import time
import json
import os

try:
    import serial
    import serial.tools.list_ports
    HAS_SERIAL = True
except ImportError:
    HAS_SERIAL = False


# =====================================================================
# ДРАЙВЕР SERIAL / STN ДЛЯ VGATE VLINKER MC+ (HS-CAN ТА MS-CAN)
# =====================================================================

class VlinkerSession:
    """Клас роботи через Vgate vLinker MC+ з апаратним мультиплексором HS/MS-CAN"""
    def __init__(self, com_port, log_callback, baudrate=115200):
        if not HAS_SERIAL:
            raise Exception("Бібліотека 'pyserial' не встановлена.")
        if not com_port:
            raise Exception("Не вказано Bluetooth/COM-порт для Vgate vLinker MC+.")
        self.com_port = com_port
        self.baudrate = int(baudrate)
        self.log = log_callback
        self.ser = None

    def open(self):
        try:
            self.ser = serial.Serial(self.com_port, baudrate=self.baudrate, timeout=1.5)
        except Exception as e:
            raise Exception(f"Не вдалося відкрити порт {self.com_port}: {e}")

        resp = self._send_at("ATZ")
        if not resp:
            raise Exception(f"Адаптер на {self.com_port} не відповідає на команду ATZ.")
        self._send_at("ATE0")
        self._send_at("ATL0")
        self._send_at("ATS0")
        self._send_at("ATH0")
        self._send_at("ATCAF1")

    def _send_at(self, cmd):
        if not self.ser:
            return ""
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\r").encode("ascii"))
        buf = bytearray()
        while True:
            b = self.ser.read(1)
            if not b or b == b">":
                break
            buf.extend(b)
        return buf.decode("ascii", errors="ignore").strip()

    def configure_ecu(self, tx_id, rx_id, is29, bus="HS"):
        if bus == "MS":
            self._send_at("STP 54" if is29 else "STP 53")
        else:
            self._send_at("ATSP7" if is29 else "ATSP6")

        if is29:
            tx_hex = f"{tx_id:08X}"
            rx_hex = f"{rx_id:08X}"
            self._send_at(f"ATCP {tx_hex[:2]}")
            self._send_at(f"ATSH {tx_hex[2:]}")
            self._send_at(f"ATCRA {rx_hex}")
        else:
            self._send_at(f"ATSH {tx_id:03X}")
            self._send_at(f"ATCRA {rx_id:03X}")

    def send_uds(self, payload):
        hex_cmd = "".join(f"{b:02X}" for b in payload)
        raw = self._send_at(hex_cmd)
        if not raw or "NO DATA" in raw or "ERROR" in raw or "?" in raw:
            return None

        cleaned = ""
        for line in raw.replace("\r", "\n").split("\n"):
            line = line.strip()
            if ":" in line:
                line = line.split(":", 1)[1].strip()
            cleaned += line

        try:
            bytes_list = [int(cleaned[i:i+2], 16) for i in range(0, len(cleaned) - 1, 2)]
            if len(bytes_list) >= 3 and bytes_list[0] == 0x7F and bytes_list[2] == 0x78:
                time.sleep(0.3)
                return None
            return bytes_list
        except ValueError:
            return None

    def close(self):
        if self.ser and self.ser.is_open:
            try:
                self.ser.close()
            except Exception:
                pass


# =====================================================================
# МУЛЬТИБРЕНДОВА БАЗА ДАНИХ (11 КОНЦЕРНІВ)
# =====================================================================

MB_GEN1_11BIT = [
    {"name": "CDI / MED (Двигун)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0x10, 0x01], [0x22, 0x10, 0x02], [0x22, 0xF4, 0xA6]], "scale": 0.1},
    {"name": "EGS / VGS (АКПП 7G-Tronic)", "tx": 0x7E1, "rx": 0x7E9, "is29": False, "bus": "HS", "dids": [[0x22, 0x02, 0x00], [0x22, 0x10, 0x05]], "scale": 1.0},
    {"name": "EZS / EIS (Замок запалювання)", "tx": 0x7E2, "rx": 0x7EA, "is29": False, "bus": "HS", "dids": [[0x22, 0x01, 0x00], [0x22, 0x10, 0x00]], "scale": 0.1},
]

MB_GEN2_29BIT = [
    {"name": "CDI / MED (Двигун)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0x10, 0x01], [0x22, 0xF4, 0xA6]], "scale": 0.1},
    {"name": "EZS (Замок запалювання FBS4)", "tx": 0x18DA10F1, "rx": 0x18DAF110, "is29": True, "bus": "HS", "dids": [[0x22, 0x01, 0x00]], "scale": 0.1},
]

VAG_UDS_BLOCKS = [
    {"name": "01 - Engine (Двигун EDC17 / MD1 / MED17)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6], [0x22, 0x10, 0x01]], "scale": 1.0},
    {"name": "02 - Auto Trans (АКПП DSG)", "tx": 0x7E1, "rx": 0x7E9, "is29": False, "bus": "HS", "dids": [[0x22, 0x10, 0x05], [0x22, 0x02, 0x00]], "scale": 1.0},
    {"name": "17 - Instruments (Панель приладів)", "tx": 0x714, "rx": 0x77E, "is29": False, "bus": "HS", "dids": [[0x22, 0x22, 0x03]], "scale": 1.0},
]

BMW_F_G_BLOCKS = [
    {"name": "DME / DDE (Двигун)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6], [0x22, 0x10, 0x01]], "scale": 1.0},
    {"name": "EGS (АКПП ZF 8HP)", "tx": 0x7E1, "rx": 0x7E9, "is29": False, "bus": "HS", "dids": [[0x22, 0x10, 0x05]], "scale": 1.0},
]

RENAULT_UDS_BLOCKS = [
    {"name": "ECM / Injection (Вприск)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6], [0x22, 0x20, 0x06]], "scale": 1.0},
    {"name": "TDB / Cluster (Панель приладів)", "tx": 0x743, "rx": 0x763, "is29": False, "bus": "HS", "dids": [[0x22, 0x01, 0x02]], "scale": 1.0},
]

PSA_UDS_BLOCKS = [
    {"name": "CMM / ECU (Двигун)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6]], "scale": 1.0},
    {"name": "BSI (Інтелектуальний блок кузова)", "tx": 0x752, "rx": 0x652, "is29": False, "bus": "HS", "dids": [[0x22, 0xD4, 0x00], [0x22, 0x01, 0x00]], "scale": 0.1},
]

FORD_UDS_BLOCKS = [
    {"name": "PCM (Двигун — HS-CAN)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6]], "scale": 1.0},
    {"name": "IPC (Панель приладів — MS-CAN)", "tx": 0x720, "rx": 0x728, "is29": False, "bus": "MS", "dids": [[0x22, 0x01, 0x02]], "scale": 1.0},
]

HYUNDAI_KIA_BLOCKS = [
    {"name": "EMS / ECU (Двигун)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6]], "scale": 1.0},
]

TOYOTA_LEXUS_BLOCKS = [
    {"name": "ECM / Engine (Двигун)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6]], "scale": 1.0},
]

FCA_29BIT_BLOCKS = [
    {"name": "PCM / ECM (Двигун)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6]], "scale": 1.0},
]

NISSAN_BLOCKS = [
    {"name": "ECM (Двигун)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6]], "scale": 1.0},
]

VOLVO_BLOCKS = [
    {"name": "ECM (Двигун — HS-CAN)", "tx": 0x7E0, "rx": 0x7E8, "is29": False, "bus": "HS", "dids": [[0x22, 0xF4, 0xA6]], "scale": 1.0},
]

BRAND_DATABASE = {
    "BENZ (Mercedes-Benz)": {"C-Class / E-Class": MB_GEN1_11BIT, "W205 / GLC": MB_GEN2_29BIT},
    "VAG (VW / Audi / Skoda / Seat)": {"MQB / PQ Platform": VAG_UDS_BLOCKS},
    "BMW": {"F / G Series": BMW_F_G_BLOCKS},
    "Renault (Renault / Dacia)": {"Megane / Duster / Logan": RENAULT_UDS_BLOCKS},
    "PSA (Peugeot / Citroen / Opel)": {"EMP2 Platform": PSA_UDS_BLOCKS},
    "Ford / Lincoln": {"Focus / Mondeo / Kuga": FORD_UDS_BLOCKS},
    "Hyundai / Kia": {"Tucson / Sportage / Santa Fe": HYUNDAI_KIA_BLOCKS},
    "Toyota / Lexus": {"Camry / RAV4 / Land Cruiser": TOYOTA_LEXUS_BLOCKS},
    "FCA (Fiat / Jeep / Dodge)": {"Ducato / Cherokee": FCA_29BIT_BLOCKS},
    "Nissan / Infiniti": {"Qashqai / X-Trail / Leaf": NISSAN_BLOCKS},
    "Volvo (P3 / SPA)": {"XC60 / XC90": VOLVO_BLOCKS}
}


# =====================================================================
# ІНТЕРФЕЙС KIVY ДЛЯ ANDROID / ПК
# =====================================================================

class DiagnosticAppUI(BoxLayout):
    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.orientation = 'vertical'
        self.padding = 20
        self.spacing = 12

        self.add_widget(Label(text="1. Автомобільний концерн / Бренд:", size_hint_y=None, height=30))
        self.brand_spinner = Spinner(text=list(BRAND_DATABASE.keys())[0], values=list(BRAND_DATABASE.keys()), size_hint_y=None, height=45)
        self.brand_spinner.bind(text=self.update_models)
        self.add_widget(self.brand_spinner)

        self.add_widget(Label(text="2. Модель / Платформа:", size_hint_y=None, height=30))
        self.model_spinner = Spinner(text="", values=[], size_hint_y=None, height=45)
        self.model_spinner.bind(text=self.update_ecus)
        self.add_widget(self.model_spinner)

        self.add_widget(Label(text="3. Електронний блок керування (ECU):", size_hint_y=None, height=30))
        self.ecu_spinner = Spinner(text="", values=[], size_hint_y=None, height=45)
        self.add_widget(self.ecu_spinner)

        self.add_widget(Label(text="Bluetooth порт (напр. /dev/rfcomm0 або COM5):", size_hint_y=None, height=30))
        self.port_input = TextInput(text="/dev/rfcomm0", multiline=False, size_hint_y=None, height=40)
        self.add_widget(self.port_input)

        btn_layout = BoxLayout(size_hint_y=None, height=50, spacing=10)
        self.read_btn = Button(text="Зчитати блок", background_color=(0, 0.47, 0.84, 1))
        self.read_btn.bind(on_press=self.start_single_read)
        btn_layout.add_widget(self.read_btn)
        self.add_widget(btn_layout)

        self.log_output = TextInput(text="Система готова до роботи через vLinker MC+...\n", readonly=True, background_color=(0.1, 0.1, 0.1, 1), foreground_color=(0.8, 1, 0.8, 1))
        self.add_widget(self.log_output)

        self.update_models(None, self.brand_spinner.text)

    def update_models(self, spinner, text):
        models = list(BRAND_DATABASE.get(text, {}).keys())
        self.model_spinner.values = models
        if models:
            self.model_spinner.text = models[0]
            self.update_ecus(None, models[0])

    def update_ecus(self, spinner, text):
        brand = self.brand_spinner.text
        ecus = BRAND_DATABASE.get(brand, {}).get(text, [])
        ecu_names = [f"{e['name']} [{e.get('bus','HS')}-CAN]" for e in ecus]
        self.ecu_spinner.values = ecu_names
        if ecu_names:
            self.ecu_spinner.text = ecu_names[0]

    def log(self, message):
        Clock.schedule_once(lambda dt: setattr(self.log_output, 'text', self.log_output.text + message + "\n"))

    def start_single_read(self, instance):
        port = self.port_input.text.strip()
        brand = self.brand_spinner.text
        model = self.model_spinner.text
        ecu_selected = self.ecu_spinner.text

        ecu_data = None
        for e in BRAND_DATABASE[brand][model]:
            if f"{e['name']} [{e.get('bus','HS')}-CAN]" == ecu_selected:
                ecu_data = e
                break

        if not ecu_data:
            self.log("[ПОМИЛКА] Блок не знайдено.")
            return

        self.log_output.text = ""
        self.log(f"Бренд: {brand} | Модель: {model}")
        threading.Thread(target=self.diagnostic_worker, args=(port, ecu_data), daemon=True).start()

    def diagnostic_worker(self, port, ecu):
        session = VlinkerSession(port, self.log)
        try:
            session.open()
            self.log(f"[OK] З'єднання з vLinker встановлено через {port}\n")
            
            bus_type = ecu.get("bus", "HS")
            self.log(f"--- Опитування блоку: {ecu['name']} [{bus_type}-CAN] ---")
            session.configure_ecu(ecu["tx"], ecu["rx"], ecu["is29"], bus=bus_type)

            # 1. Запит VIN
            vin_resp = session.send_uds([0x22, 0xF1, 0x90])
            if vin_resp and len(vin_resp) > 3 and vin_resp[0] == 0x62:
                vin_str = bytes(vin_resp[3:]).decode("ascii", errors="ignore").strip()
                self.log(f"  [+] VIN: {vin_str}")
            else:
                self.log("  [-] VIN не зчитано, спроба запиту номера блоку...")
                part_resp = session.send_uds([0x22, 0xF1, 0x87])
                if part_resp and len(part_resp) > 3 and part_resp[0] == 0x62:
                    part_str = bytes(part_resp[3:]).decode("ascii", errors="ignore").strip()
                    self.log(f"  [+] HW Номер: {part_str}")

            # 2. Сесія та DID
            session.send_uds([0x10, 0x03])
            for did in ecu["dids"]:
                resp = session.send_uds(did)
                if resp and len(resp) >= 6 and (resp[0] == 0x62 or resp[0] == 0x61):
                    offset = 3 if resp[0] == 0x62 else 2
                    raw_val = int.from_bytes(bytes(resp[offset:offset + 4]), byteorder="big")
                    km = int(raw_val * ecu["scale"])
                    did_hex = "".join(f"{b:02X}" for b in did)
                    self.log(f"  [PROBIG] Дід {did_hex} -> {km:,} км".replace(",", " "))
                    break

        except Exception as e:
            self.log(f"[ПОМИЛКА] {e}")
        finally:
            session.close()
            self.log("\n[OK] Роботу завершено, з'єднання закрито.")


class VLinkerDiagnosticApp(App):
    def build(self):
        self.title = "MultiBrand vLinker Diag"
        return DiagnosticAppUI()


if __name__ == "__main__":
    VLinkerDiagnosticApp().run()
