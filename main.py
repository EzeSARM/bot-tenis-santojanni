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
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Accept": "*/*",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={SERVICIO_ID}&flow=primeros"
    })
    try:
        session.get(f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={SERVICIO_ID}&flow=primeros", timeout=10)
    except Exception:
        pass
    return session

def obtener_dias_disponibles(session, sede_id):
    """Obtiene el listado de fechas reales habilitadas para la sede especificada."""
    url = "https://formulario-sigeci.buenosaires.gob.ar/getDiasDisp"
    params = {
        "sedeId": sede_id,
        "servicioId": SERVICIO_ID
    }
    try:
        response = session.get(url, params=params, timeout=8)
        if response.status_code == 200:
            datos = response.json()
            if isinstance(datos, list):
                dias_limpios = []
                for item in datos:
                    if isinstance(item, str):
                        fecha_corta = item.split("T")[0]
                        dias_limpios.append(fecha_corta)
                return set(dias_limpios)
    except Exception as e:
        print(f"⚠️ Error al consultar días disponibles para sede {sede_id}: {e}")
    return set()

def extraer_horas_validas(lista_datos):
    horas = []
    if not isinstance(lista_datos, list):
        return horas
    for item in lista_datos:
        if not isinstance(item, str):
            continue
        item_str = item.strip()
        if "T" in item_str:
            try:
                dt = datetime.strptime(item_str.split(".")[0], "%Y-%m-%dT%H:%M:%S")
                horas.append(dt.strftime("%H:%M hs"))
            except ValueError:
                pass
        elif ":" in item_str and len(item_str) <= 8:
            try:
                p = item_str.split(":")
                horas.append(f"{int(p[0]):02d}:{int(p[1]):02d} hs")
            except ValueError:
                pass
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
                return extraer_horas_validas(datos)
            except Exception:
                return []
    except Exception as e:
        print(f"Error al consultar sede {sede_id} para la fecha {fecha_str}: {e}")
    return []

def obtener_estado_turnos():
    """Escanea las 4 canchas consultando primero los días válidos en SIGECI."""
    global TURNOS_NOTIFICADOS
    session = crear_sesion_sigeci()
    url_reserva = f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={SERVICIO_ID}&flow=primeros"

    hoy = datetime.now()
    limite_fecha = hoy + timedelta(days=DIAS_A_CONSULTAR)

    print(f"[{datetime.now().strftime('%H:%M:%S')}] Escaneando turnos en {NOMBRE_POLIDEPORTIVO}...")

    lineas_todas = []
    lineas_nuevas_semana = []
    lineas_nuevas_finde = []
    turnos_visibles_actualmente = set()

    for cancha in CANCHAS:
        dias_disponibles = obtener_dias_disponibles(session, cancha["sede_id"])
        
        if not dias_disponibles:
            continue

        for fecha in sorted(list(dias_disponibles)):
            try:
                dt_fecha = datetime.strptime(fecha, "%Y-%m-%d")
            except ValueError:
                continue

            if hoy.date() <= dt_fecha.date() <= limite_fecha.date():
                horas = consultar_turnos_cancha(session, cancha["sede_id"], fecha)
                if horas:
                    dia_nombre = DIAS_SEMANA.get(dt_fecha.strftime("%A"), dt_fecha.strftime("%A"))
                    fecha_corta = dt_fecha.strftime("%d/%m")
                    es_fin_de_semana = dt_fecha.weekday()El error indica que al copiar y pegar el código en `main.py`, se pegó un texto explicativo sobre **SIGECI** dentro de la instrucción de Python en la **línea 177**.

Aparece esto en tu código:
```python
lineas_todas.append(linea_formateEl problema de los "falsos positivos" en el sistema SIGECI ocurre porque...
