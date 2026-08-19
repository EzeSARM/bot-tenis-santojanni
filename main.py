import os
import time
import requests
from datetime import datetime, timedelta

# ==========================================
# CONFIGURACIÓN GENERAL Y CREDENCIALES
# ==========================================
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

# Definición de los tres polideportivos con sus respectivas canchas y sedeIds
POLIDEPORTIVOS = [
    {
        "nombre": "Polideportivo Colegiales",
        "servicio_id": "3149",
        "canchas": [
            {"nombre": "Cancha 1", "sede_id": "2263"},
            {"nombre": "Cancha 2", "sede_id": "2279"}
        ]
    },
    {
        "nombre": "Polideportivo Onega",
        "servicio_id": "3137",
        "canchas": [
            {"nombre": "Cancha 1", "sede_id": "2289"},
            {"nombre": "Cancha 2", "sede_id": "2290"}
        ]
    },
    {
        "nombre": "Polideportivo Santojanni",
        "servicio_id": "3125",
        "canchas": [
            {"nombre": "Cancha 1", "sede_id": "2255"},
            {"nombre": "Cancha 2", "sede_id": "2256"},
            {"nombre": "Cancha 3", "sede_id": "2257"},
            {"nombre": "Cancha 4", "sede_id": "2258"}
        ]
    }
]

DIAS_A_CONSULTAR = 30
TURNOS_NOTIFICADOS = set()

DIAS_SEMANA = {
    "Monday": "Lunes", "Tuesday": "Martes", "Wednesday": "Miércoles",
    "Thursday": "Jueves", "Friday": "Viernes", "Saturday": "Sábado", "Sunday": "Domingo"
}


def enviar_mensaje_telegram(mensaje):
    """Envía un mensaje a Telegram con formato HTML."""
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("❌ Error: Faltan las variables TELEGRAM_TOKEN o TELEGRAM_CHAT_ID.")
        return False

    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": mensaje,
        "parse_mode": "HTML",
        "disable_web_page_preview": False
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        return res.status_code == 200
    except Exception as e:
        print(f"❌ Error al enviar mensaje a Telegram: {e}")
        return False


def crear_sesion_sigeci(servicio_id):
    """Inicializa la sesión HTTP para capturar cookies y tokens necesarios."""
    session = requests.Session()
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/151.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Accept-Language": "es-419,es;q=0.9,en;q=0.8",
        "X-Requested-With": "XMLHttpRequest",
        "Referer": f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={servicio_id}&flow=primeros"
    })
    
    url_inicio = f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={servicio_id}&flow=primeros"
    try:
        session.get(url_inicio, timeout=10)
    except Exception as e:
        print(f"⚠️ Aviso al inicializar sesión en prestación {servicio_id}: {e}")
        
    return session


def extraer_horas_validas(lista_datos):
    """Limpia y valida los datos de horarios devueltos por la API."""
    horas_validas = []
    if not isinstance(lista_datos, list):
        return horas_validas

    for item in lista_datos:
        if not isinstance(item, str):
            continue

        item_str = item.strip()

        if "T" in item_str:
            try:
                dt_hora = datetime.strptime(item_str.split(".")[0], "%Y-%m-%dT%H:%M:%S")
                horas_validas.append(dt_hora.strftime("%H:%M hs"))
            except ValueError:
                pass
        elif ":" in item_str and len(item_str) <= 8:
            try:
                partes = item_str.split(":")
                hora_str = f"{int(partes[0]):02d}:{int(partes[1]):02d} hs"
                horas_validas.append(hora_str)
            except ValueError:
                pass

    return sorted(list(set(horas_validas)))


def consultar_cancha(poli_nombre, servicio_id, cancha_info):
    global TURNOS_NOTIFICADOS

    nombre_cancha = cancha_info["nombre"]
    sede_id = cancha_info["sede_id"]

    session = crear_sesion_sigeci(servicio_id)
    url_reserva = f"https://formulario-sigeci.buenosaires.gob.ar/AgendarTramite?idPrestacion={servicio_id}"

    hoy = datetime.now()
    lineas_resumen = []
    turnos_nuevos_detectados = []
    turnos_visibles_hoy = set()
    hay_turnos_reales = False

    for i in range(DIAS_A_CONSULTAR):
        fecha_str = (hoy + timedelta(days=i)).strftime("%Y-%m-%d")

        api_url = "https://formulario-sigeci.buenosaires.gob.ar/getHorasDisp"
        params = {
            "day": fecha_str,
            "sedeId": sede_id,
            "servicioId": servicio_id
        }

        try:
            response = session.get(api_url, params=params, timeout=8)

            if response.status_code == 200:
                try:
                    datos = response.json()
                except Exception:
                    datos = []

                if datos and isinstance(datos, list):
                    horas_limpias = extraer_horas_validas(datos)

                    if horas_limpias:
                        hay_turnos_reales = True
                        try:
                            dt_fecha = datetime.strptime(fecha_str, "%Y-%m-%d")
                            dia_nombre = DIAS_SEMANA.get(dt_fecha.strftime("%A"), dt_fecha.strftime("%A"))
                            fecha_corta = dt_fecha.strftime("%d/%m")
                            texto_linea = f"📅 <b>{dia_nombre} {fecha_corta}:</b> {', '.join(horas_limpias)}"
                        except Exception:
                            texto_linea = f"📅 <b>{fecha_str}:</b> {', '.join(horas_limpias)}"

                        for h in horas_limpias:
                            clave_unica = f"{servicio_id}|{sede_id}|{fecha_str}|{h}"
                            turnos_visibles_hoy.add(clave_unica)

                            if clave_unica not in TURNOS_NOTIFICADOS:
                                turnos_nuevos_detectados.append(clave_unica)

                        lineas_resumen.append(texto_linea)
        except Exception:
            pass

        time.sleep(0.05)

    # Limpieza de memoria de turnos notificados que dejaron de estar disponibles
    turnos_a_remover = [
        t for t in TURNOS_NOTIFICADOS 
        if t.startswith(f"{servicio_id}|{sede_id}|") and t not in turnos_visibles_hoy
    ]
    for t in turnos_a_remover:
        TURNOS_NOTIFICADOS.remove(t)

    # Envío de alertas a Telegram
    if turnos_nuevos_detectados:
        resumen_turnos = "\n".join(lineas_resumen)
        mensaje = (
            "🔔 <b>¡NUEVO TURNO DISPONIBLE EN CABA!</b> 🔔\n\n"
            f"📍 <b>Lugar:</b> {poli_nombre}\n"
            f"🎾 <b>Cancha:</b> {nombre_cancha}\n\n"
            f"<b>Disponibilidad encontrada:</b>\n{resumen_turnos}\n\n"
            f"🔗 <a href='{url_reserva}'>RESERVAR AHORA EN SIGECI</a>"
        )
        if enviar_mensaje_telegram(mensaje):
            for t in turnos_nuevos_detectados:
                TURNOS_NOTIFICADOS.add(t)
            print(f"✅ ALERTA ENVIADA: {len(turnos_nuevos_detectados)} turnos en {poli_nombre} - {nombre_cancha}.")
    elif hay_turnos_reales:
        print(f"ℹ️ {poli_nombre} - {nombre_cancha}: Turnos detectados ya notificados.")
    else:
        print(f"ℹ️ {poli_nombre} - {nombre_cancha}: Sin disponibilidad.")


if __name__ == "__main__":
    print("🚀 Iniciando monitoreo unificado (Colegiales, Onega y Santojanni)...")

    enviar_mensaje_telegram(
        "🚀 <b>Bot Unificado Activo:</b> Monitoreando Colegiales, Onega y Santojanni."
    )

    while True:
        try:
            for poli in POLIDEPORTIVOS:
                for cancha in poli["canchas"]:
                    consultar_cancha(poli["nombre"], poli["servicio_id"], cancha)
                    time.sleep(0.5)
        except Exception as main_e:
            print(f"❌ Error en el bucle principal: {main_e}")

        # Consulta general cada 5 minutos
        time.sleep(300)
