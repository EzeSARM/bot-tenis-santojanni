import os
import time
import re
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta

# ==========================================
# CONFIGURACIÓN Y CREDENCIALES - SANTOJANNI
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

NOMBRE_POLIDEPORTIVO = "Polideportivo Santojanni"
SERVICIO_ID = "3125"

CANCHAS = [
    {"nombre": "Cancha 1", "sede_id": "2255"},
    {"nombre": "Cancha 2", "sede_id": "2256"},
    {"nombre": "Cancha 3", "sede_id": "2257"},
    {"nombre": "Cancha 4", "sede_id": "2258"}
]

DIAS_A_CONSULTAR = 30

DIAS_SEMANA = {
    "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
    "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo"
}

LAST_UPDATE_ID = None
TURNOS_NOTIFICADOS = set()  # Memoria de turnos ya informados

# ==========================================
# SERVIDOR WEB PARA RAILWAY / KOYEB (HEALTH CHECK)
# ==========================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(f"🤖 Bot {NOMBRE_POLIDEPORTIVO} Activo 24/7".encode('utf-8'))

    def log_message(self, format, *args):
        return

def iniciar_servidor_web():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌐 Servidor web iniciado en puerto {port}")
    server.serve_forever()

# ==========================================
# FUNCIONES DE TELEGRAM Y SIGECI
# ==========================================
def enviar_mensaje_telegram(mensaje, chat_id=None):
    target_chat_id = chat_id or TELEGRAM_CHAT_ID
    if not TELEGRAM_TOKEN or not target_chat_id:
        print("❌ Error: Faltan las variables de entorno TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": target_chat_id,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"❌ Error enviando a Telegram: {e}")
        return False

def crear_sesion_sigeci():
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Accept": "application/json, text/javascript, */*; q=0.01",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={SERVICIO_ID}&flow=primeros"
    })
    try:
        session.get(f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={SERVICIO_ID}&flow=primeros", timeout=10)
    except Exception:
        pass
    return session

def extraer_horas_validas(lista_datos):
    """Filtra y valida únicamente cadenas con formato de hora real HH:MM."""
    horas = []
    if not isinstance(lista_datos, list):
        return horas

    for item in lista_datos:
        if not isinstance(item, str):
            continue

        item_str = item.strip()
        hora_detectada = None

        if "T" in item_str:
            try:
                dt = datetime.strptime(item_str.split(".")[0], "%Y-%m-%dT%H:%M:%S")
                hora_detectada = dt.strftime("%H:%M")
            except ValueError:
                pass
        elif ":" in item_str:
            # Extraer coincidencia de hora tipo 08:00 o 8:00
            match = re.search(r'\b(\d{1,2}):(\d{2})\b', item_str)
            if match:
                h, m = int(match.group(1)), int(match.group(2))
                if 0 <= h <= 23 and 0 <= m <= 59:
                    hora_detectada = f"{h:02d}:{m:02d}"

        if hora_detectada:
            horas.append(f"{hora_detectada} hs")

    return sorted(list(set(horas)))

def consultar_turnos_cancha(session, sede_id, fecha_str):
    url = "https://formulario-sigeci.buenosaires.gob.ar/getHorasDisp"
    params = {
        "day": fecha_str,
        "sedeId": sede_id,
        "servicioId": SERVICIO_ID
    }
    try:
        response = session.get(url, params=params, timeout=8)
        if response.status_code == 200:
            try:
                datos = response.json()
                # Verificar que no sea una lista vacía ni un objeto de error
                if datos and isinstance(datos, list):
                    return extraer_horas_validas(datos)
            except Exception:
                return []
    except Exception as e:
        print(f"Error al consultar sede {sede_id} para la fecha {fecha_str}: {e}")
    return []

def obtener_estado_turnos():
    """Escanea las 4 canchas y retorna los turnos divididos entre semana y fin de semana."""
    global TURNOS_NOTIFICADOS
    session = crear_sesion_sigeci()
    url_reserva = f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={SERVICIO_ID}&flow=primeros"

    hoy = datetime.now()
    fechas_a_consultar = [(hoy + timedelta(days=i)).strftime("%Y-%m-%d") for i in range(DIAS_A_CONSULTAR)]

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Escaneando turnos en {NOMBRE_POLIDEPORTIVO}...")

    lineas_todas = []
    lineas_nuevas_semana = []
    lineas_nuevas_finde = []
    turnos_visibles_actualmente = set()

    for cancha in CANCHAS:
        for fecha in fechas_a_consultar:
            horas = consultar_turnos_cancha(session, cancha["sede_id"], fecha)
            if horas:
                dt_fecha = datetime.strptime(fecha, "%Y-%m-%d")
                dia_nombre = DIAS_SEMANA.get(dt_fecha.strftime("%A"), dt_fecha.strftime("%A"))
                fecha_corta = dt_fecha.strftime("%d/%m")
                es_fin_de_semana = dt_fecha.weekday() in [5, 6]  # 5 = Sábado, 6 = Domingo

                horas_nuevas_cancha = []
                for h in horas:
                    clave_unica = f"{cancha['sede_id']}|{fecha}|{h}"
                    turnos_visibles_actualmente.add(clave_unica)
                    if clave_unica not in TURNOS_NOTIFICADOS:
                        horas_nuevas_cancha.append(h)

                linea_formateada = f"🎾 <b>{cancha['nombre']}</b> - 📅 <b>{dia_nombre} {fecha_corta}:</b> {', '.join(horas)}"
                lineas_todas.append(linea_formateEl problema de los "falsos positivos" en el sistema SIGECI ocurre porque la API `/getHorasDisp` devuelve **todos los slots horarios teóricos o configurados** para una sede en una fecha determinada, **sin verificar si la fecha tiene días hábiles/habilitados o si la sede realmente tiene disponibilidad real ese día**.

En la interfaz web de SIGECI, el flujo consulta primero el endpoint `/getDiasDisp` para saber qué días tienen fechas con cupo real (marcando los días habilitados en el calendario). Al hacer directamente un llamado a `/getHorasDisp` sin validar primero si la fecha está habilitada mediante `/getDiasDisp`, el servidor responde con horarios existentes en la base de datos pero inhabilitados para la reserva.

Para corregirlo, debes consultar `/getDiasDisp` antes de pedir los horarios de cada cancha y filtrar las fechas.

### Código corregido (`main.py`)

```python
import os
import time
import threading
import requests
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime, timedelta

# ==========================================
# CONFIGURACIÓN Y CREDENCIALES - SANTOJANNI
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

NOMBRE_POLIDEPORTIVO = "Polideportivo Santojanni"
SERVICIO_ID = "3125"

CANCHAS = [
    {"nombre": "Cancha 1", "sede_id": "2255"},
    {"nombre": "Cancha 2", "sede_id": "2256"},
    {"nombre": "Cancha 3", "sede_id": "2257"},
    {"nombre": "Cancha 4", "sede_id": "2258"}
]

DIAS_A_CONSULTAR = 30

DIAS_SEMANA = {
    "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
    "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo"
}

LAST_UPDATE_ID = None
TURNOS_NOTIFICADOS = set()  # Memoria de turnos ya informados

# ==========================================
# SERVIDOR WEB PARA RAILWAY / KOYEB (HEALTH CHECK)
# ==========================================
class SimpleHTTPRequestHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200)
        self.send_header('Content-type', 'text/html; charset=utf-8')
        self.end_headers()
        self.wfile.write(f"🤖 Bot {NOMBRE_POLIDEPORTIVO} Activo 24/7".encode('utf-8'))

    def log_message(self, format, *args):
        return

def iniciar_servidor_web():
    port = int(os.getenv("PORT", 8080))
    server = HTTPServer(('0.0.0.0', port), SimpleHTTPRequestHandler)
    print(f"🌐 Servidor web iniciado en puerto {port}")
    server.serve_forever()

# ==========================================
# FUNCIONES DE TELEGRAM Y SIGECI
# =================
