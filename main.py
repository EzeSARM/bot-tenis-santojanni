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
                lineas_todas.append(linea_formateada)

                if horas_nuevas_cancha:
                    linea_nueva_formateada = f"🎾 <b>{cancha['nombre']}</b> - 📅 <b>{dia_nombre} {fecha_corta}:</b> {', '.join(horas_nuevas_cancha)}"
                    if es_fin_de_semana:
                        lineas_nuevas_finde.append(linea_nueva_formateada)
                    else:
                        lineas_nuevas_semana.append(linea_nueva_formateada)

            time.sleep(0.05)

    TURNOS_NOTIFICADOS = TURNOS_NOTIFICADOS.intersection(turnos_visibles_actualmente)

    return lineas_todas, lineas_nuevas_finde, lineas_nuevas_semana, turnos_visibles_actualmente, url_reserva

def procesar_mensajes_telegram():
    """Responde cuando el usuario consulta manualmente escribiendo al bot."""
    global LAST_UPDATE_ID, TURNOS_NOTIFICADOS

    if not TELEGRAM_TOKEN:
        return

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/getUpdates"
    params = {"timeout": 5, "offset": LAST_UPDATE_ID}

    try:
        response = requests.get(url, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            for update in data.get("result", []):
                LAST_UPDATE_ID = update["update_id"] + 1
                message = update.get("message", {})
                chat_id = str(message.get("chat", {}).get("id"))
                texto = message.get("text", "").strip().lower()

                if texto:
                    print(f"📩 Consulta manual recibida de Chat ID {chat_id}: '{texto}'")
                    enviar_mensaje_telegram("🔎 Consultando la disponibilidad en el SIGECI, aguarda un momento...", chat_id=chat_id)
                    
                    lineas_todas, _, _, turnos_visibles, url_reserva = obtener_estado_turnos()
                    
                    if lineas_todas:
                        TURNOS_NOTIFICADOS.update(turnos_visibles)
                        resumen = "\n".join(lineas_todas)
                        mensaje = (
                            f"🔔 <b>¡TURNOS DISPONIBLES EN {NOMBRE_POLIDEPORTIVO.upper()}!</b> 🔔\n\n"
                            f"{resumen}\n\n"
                            f"🔗 <a href='{url_reserva}'>RESERVAR AHORA EN SIGECI</a>"
                        )
                    else:
                        hora_actual = datetime.now().strftime("%H:%M:%S")
                        mensaje = (
                            f"❌ <b>Sin turnos disponibles en {NOMBRE_POLIDEPORTIVO}</b>\n\n"
                            f"<i>Última verificación: {hora_actual} hs (Próximos {DIAS_A_CONSULTAR} días).</i>"
                        )
                    
                    enviar_mensaje_telegram(mensaje, chat_id=chat_id)
    except Exception as e:
        print(f"⚠️ Error al verificar mensajes de Telegram: {e}")

def bucle_principal():
    global TURNOS_NOTIFICADOS
    print(f"🚀 Bot iniciado en {NOMBRE_POLIDEPORTIVO}. Monitoreando...")
    enviar_mensaje_telegram(f"🤖 <b>Bot Activo:</b> Monitoreando {NOMBRE_POLIDEPORTIVO}. Envíame cualquier mensaje para una consulta rápida.")

    ULTIMO_ESCANEO = 0
    INTERVALO_ESCANEO = 300  # Escaneo automático cada 5 minutos

    while True:
        # 1. Escuchar si el usuario escribió un mensaje directo
        procesar_mensajes_telegram()

        # 2. Escaneo automático silencioso periódicamente
        tiempo_actual = time.time()
        if tiempo_actual - ULTIMO_ESCANEO >= INTERVALO_ESCANEO:
            print("⏰ Ejecutando escaneo automático en segundo plano...")
            _, lineas_nuevas_finde, lineas_nuevas_semana, turnos_visibles, url_reserva = obtener_estado_turnos()
            
            # Prioridad 1: Notificación especial si hay turnos de FIN DE SEMANA
            if lineas_nuevas_finde:
                resumen_finde = "\n".join(lineas_nuevas_finde)
                bloque_semana = ""
                if lineas_nuevas_semana:
                    bloque_semana = "\n\n<b>Otros turnos en la semana:</b>\n" + "\n".join(lineas_nuevas_semana)

                mensaje_alerta = (
                    f"⭐ <b>¡ALERTA FIN DE SEMANA EN {NOMBRE_POLIDEPORTIVO.upper()}!</b> ⭐\n"
                    f"🔥 <i>¡SE DETECTARON TURNOS PARA SÁBADO/DOMINGO!</i> 🔥\n\n"
                    f"{resumen_finde}"
                    f"{bloque_semana}\n\n"
                    f"🔗 <a href='{url_reserva}'>RESERVAR AHORA EN SIGECI</a>"
                )
                enviar_mensaje_telegram(mensaje_alerta)
                TURNOS_NOTIFICADOS.update(turnos_visibles)
                print(f"✅ Notificación de fin de semana enviada ({len(lineas_nuevas_finde)} línea/s nueva/s).")

            # Prioridad 2: Notificación estándar si solo hay turnos de DÍAS DE SEMANA
            elif lineas_nuevas_semana:
                resumen_semana = "\n".join(lineas_nuevas_semana)
                mensaje_alerta = (
                    f"🚨 <b>¡NUEVOS TURNOS DETECTADOS EN {NOMBRE_POLIDEPORTIVO.upper()}!</b> 🚨\n\n"
                    f"{resumen_semana}\n\n"
                    f"🔗 <a href='{url_reserva}'>RESERVAR AHORA EN SIGECI</a>"
                )
                enviar_mensaje_telegram(mensaje_alerta)
                TURNOS_NOTIFICADOS.update(turnos_visibles)
                print(f"✅ Notificación enviada ({len(lineas_nuevas_semana)} línea/s nueva/s).")
            else:
                print("ℹ️ Sin turnos nuevos para notificar.")

            ULTIMO_ESCANEO = tiempo_actual

        time.sleep(2)

if __name__ == "__main__":
    t_web = threading.Thread(target=iniciar_servidor_web, daemon=True)
    t_web.start()
    
    bucle_principal()
