"""Decodificador de 4 bits a display de 7 segmentos, con enlace MQTT.

La ESP32 lee un DIP switch de 4 bits, muestra el valor (0 a F) en el display de
7 segmentos y publica cada cambio en un broker MQTT público. El panel web
(index.html + script.js) se suscribe a esas publicaciones para replicar el
display en pantalla y, en sentido contrario, puede fijar un dígito enviando un
número por el tópico de control.

Escrito para MicroPython sobre ESP32; el montaje de referencia es el
diagram.json del proyecto de Wokwi.
"""

from machine import Pin
import network
import time

from umqtt.simple import MQTTClient


# --- Identificación del equipo ---------------------------------------------
# El broker es público y todos los equipos del curso comparten el prefijo
# "clase/decoder", así que GRUPO es lo único que separa este montaje del de los
# demás. Tiene que ser idéntico al GRUPO definido en script.js: si no coinciden,
# el panel web queda escuchando un tópico por el que nadie habla.
GRUPO = "Sanjuanelo"

WIFI_SSID = "Wokwi-GUEST"          # red abierta que publica el simulador
WIFI_PASS = ""
MQTT_BROKER = "broker.hivemq.com"
MQTT_CLIENT_ID = "esp32_" + GRUPO  # el broker desconecta clientes con ID repetido

TOPIC_ESTADO     = f"clase/decoder/{GRUPO}/estado"     # ESP32 -> panel web
TOPIC_CONTROL    = f"clase/decoder/{GRUPO}/control"    # panel web -> ESP32
TOPIC_PRESENCIA  = f"clase/decoder/{GRUPO}/presencia"  # ESP32 -> panel web ("online"/"offline")

# --- Tiempos ---------------------------------------------------------------
WIFI_TIMEOUT_MS = 10000    # plazo máximo para conseguir IP
WIFI_REINTENTO_MS = 300    # pausa entre consultas de isconnected()
PERIODO_SONDEO_MS = 150    # periodo del bucle principal
LATIDO_MS = 10000          # cada cuánto se reconfirma "online" aunque nada cambie
RECONEXION_MS = 2000       # pausa antes de reintentar tras perder la conexión

# Plazo que el broker espera sin recibir nada antes de dar por muerta la sesión
# y publicar el last will. Con el valor 0 que trae umqtt por omisión el broker
# NO vigila nada: al detener la simulación de Wokwi el socket queda a medio
# cerrar, el "offline" nunca se publica y el panel web sigue creyendo que la
# placa está ahí. Debe ser mayor que LATIDO_MS.
KEEPALIVE_S = 30

# --- Cableado --------------------------------------------------------------
# DIP switch: la posición en la lista es el peso del bit. DIP_PINS[0] es el LSB
# y DIP_PINS[3] el MSB.
DIP_PINS = (33, 25, 26, 27)

# Display: mismo orden que los patrones de DIGIT_TABLE, de "a" hasta "g".
SEG_PINS = (23, 22, 16, 17, 18, 21, 19)

# El común del display va a 3V3, es decir ánodo común: un segmento se enciende
# cuando su pin queda en 0. Con un display de cátodo común basta poner esta
# bandera en True y escribir_patron deja de invertir los niveles.
COMMON_CATHODE = False

# Patrones de encendido para 0-F en el orden a, b, c, d, e, f, g, donde 1 es
# segmento encendido. La misma tabla está replicada en script.js para que el
# display dibujado en la web coincida con el físico.
DIGIT_TABLE = {
    0x0: (1, 1, 1, 1, 1, 1, 0),
    0x1: (0, 1, 1, 0, 0, 0, 0),
    0x2: (1, 1, 0, 1, 1, 0, 1),
    0x3: (1, 1, 1, 1, 0, 0, 1),
    0x4: (0, 1, 1, 0, 0, 1, 1),
    0x5: (1, 0, 1, 1, 0, 1, 1),
    0x6: (1, 0, 1, 1, 1, 1, 1),
    0x7: (1, 1, 1, 0, 0, 0, 0),
    0x8: (1, 1, 1, 1, 1, 1, 1),
    0x9: (1, 1, 1, 1, 0, 1, 1),
    0xA: (1, 1, 1, 0, 1, 1, 1),
    0xB: (0, 0, 1, 1, 1, 1, 1),
    0xC: (1, 0, 0, 1, 1, 1, 0),
    0xD: (0, 1, 1, 1, 1, 0, 1),
    0xE: (1, 0, 0, 1, 1, 1, 1),
    0xF: (1, 0, 0, 0, 1, 1, 1),
}

SEGMENTOS_APAGADOS = (0, 0, 0, 0, 0, 0, 0)

VALOR_MIN = 0
VALOR_MAX = 15   # una palabra de 4 bits no da para más


# Las entradas llevan pull-down interno para que un bit valga 0 con su llave
# abierta; sin él el pin queda flotando y la lectura cambia sola.
dip_inputs = [Pin(numero, Pin.IN, Pin.PULL_DOWN) for numero in DIP_PINS]
seg_outputs = [Pin(numero, Pin.OUT) for numero in SEG_PINS]


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------
def escribir_patron(patron):
    """Vuelca sobre los pines de segmento una tupla de 7 estados (a..g)."""
    for pin, encendido in zip(seg_outputs, patron):
        pin.value(encendido if COMMON_CATHODE else 1 - encendido)


def mostrar_digito(valor):
    """Dibuja el dígito hexadecimal correspondiente a valor (0-15).

    Un valor sin entrada en la tabla apaga el display en lugar de dejar el
    patrón anterior, que se leería como un dato válido.
    """
    escribir_patron(DIGIT_TABLE.get(valor, SEGMENTOS_APAGADOS))


def apagar_display():
    """Apaga los siete segmentos.

    Se llama al arrancar porque los pines nacen en 0 y, con ánodo común, ese 0
    es justamente el nivel de encendido: sin esta limpieza el display muestra
    un ocho durante todo el tiempo que tarde la conexión WiFi.
    """
    escribir_patron(SEGMENTOS_APAGADOS)


# ---------------------------------------------------------------------------
# DIP switch
# ---------------------------------------------------------------------------
def leer_bits_dip():
    """Devuelve los cuatro bits del DIP switch, del LSB al MSB.

    El resto del programa trabaja sobre esta lista en vez de volver a consultar
    los pines. El mensaje que se publica lleva el número y su representación
    binaria, y los dos campos tienen que describir el mismo instante: con dos
    lecturas separadas, mover una llave entre la primera y la segunda produce un
    payload que se contradice a sí mismo.
    """
    return [pin.value() for pin in dip_inputs]


def valor_de_bits(bits):
    """Arma el entero 0-15 que representan los bits recibidos (LSB primero)."""
    valor = 0
    for peso, bit in enumerate(bits):
        if bit:
            valor |= 1 << peso
    return valor


# ---------------------------------------------------------------------------
# Conectividad
# ---------------------------------------------------------------------------
def conectar_wifi():
    """Activa la interfaz estación y espera a que la red entregue una IP.

    La espera está acotada a propósito: si el SSID está mal escrito o la red no
    aparece, un bucle abierto dejaría la placa colgada aquí sin ninguna señal
    visible. Al vencer el plazo se lanza OSError para que el fallo salga en la
    consola del simulador.
    """
    wlan = network.WLAN(network.STA_IF)
    wlan.active(True)
    if not wlan.isconnected():
        wlan.connect(WIFI_SSID, WIFI_PASS)

    inicio = time.ticks_ms()
    while not wlan.isconnected():
        # ticks_diff y no una resta: el contador de milisegundos se desborda y
        # vuelve a empezar cada tanto.
        if time.ticks_diff(time.ticks_ms(), inicio) > WIFI_TIMEOUT_MS:
            raise OSError("Sin IP tras 10 s: revise el SSID y la red en Wokwi")
        time.sleep_ms(WIFI_REINTENTO_MS)
        print("Esperando IP...")

    print("WiFi conectado, IP:", wlan.ifconfig()[0])
    return wlan


def al_llegar_comando(topic, msg):
    """Atiende los mensajes de TOPIC_CONTROL y fija el display.

    umqtt entrega siempre el tópico junto al mensaje; aquí no se usa porque la
    única suscripción es TOPIC_CONTROL, pero el parámetro debe estar para que la
    firma del callback calce.

    El panel manda el número como texto plano, de "0" a "15". Cualquier otra
    cosa que caiga en el tópico (una prueba manual con mosquitto_pub, el mensaje
    de un equipo que copió el GRUPO) se descarta con un aviso en consola: esta
    función corre dentro del bucle principal y una excepción aquí detiene el
    programa completo.

    El valor recibido dura hasta que alguien mueva el DIP switch, porque el
    bucle principal vuelve a imponer la lectura física.
    """
    try:
        valor = int(msg.decode())
    except ValueError:
        print("Comando ignorado, no es un número:", msg)
        return

    if not VALOR_MIN <= valor <= VALOR_MAX:
        print("Comando fuera del rango 0-15:", valor)
        return

    mostrar_digito(valor)
    print("Display fijado desde la web:", valor)


def publicar_estado(client, bits, valor):
    """Publica el estado del DIP con el formato "bbbb,decimal".

    La cadena binaria va de MSB a LSB, que es como se lee el switch en el
    montaje y como la espera el panel: script.js parte el payload por la coma,
    usa el primer campo para los indicadores de bit y el segundo para el dígito.

    Se publica con retain=True para que el broker guarde este último valor:
    si alguien abre (o recarga) el panel web después de que el switch ya se
    movió, ve de inmediato la posición actual en vez de una pantalla vacía
    hasta el próximo cambio físico.
    """
    bits_msb_primero = "".join(str(bit) for bit in reversed(bits))
    payload = f"{bits_msb_primero},{valor}"
    client.publish(TOPIC_ESTADO, payload.encode(), retain=True)
    print("Publicado:", payload)


def conectar_mqtt():
    """Crea el cliente MQTT, deja anunciada la presencia y se suscribe.

    El last will (set_last_will) es un mensaje que el propio broker publica en
    lugar de la ESP32 si la conexión se corta de golpe (se apaga la placa, se
    cae el WiFi, se cierra la pestaña del simulador). Así el panel web puede
    distinguir "la placa está encendida pero nadie mueve el switch" de "la
    placa ya no está".

    El last will por sí solo no basta: el broker únicamente lo dispara cuando
    da la sesión por perdida, y para eso necesita el keepalive que se le pasa
    al constructor. El latido del bucle principal (cada LATIDO_MS) es lo que
    mantiene viva la sesión mientras la placa sí está corriendo.
    """
    client = MQTTClient(MQTT_CLIENT_ID, MQTT_BROKER, keepalive=KEEPALIVE_S)
    client.set_callback(al_llegar_comando)
    client.set_last_will(TOPIC_PRESENCIA, b"offline", retain=True, qos=0)
    client.connect()
    client.subscribe(TOPIC_CONTROL)
    client.publish(TOPIC_PRESENCIA, b"online", retain=True)
    print("MQTT conectado. Escuchando en:", TOPIC_CONTROL)
    return client


# ---------------------------------------------------------------------------
# Programa principal
# ---------------------------------------------------------------------------
def main():
    apagar_display()
    conectar_wifi()

    client = conectar_mqtt()

    # None y no 0, para que la primera vuelta publique el estado inicial aunque
    # el switch esté en cero.
    ultimo_valor = None
    ultimo_latido = time.ticks_ms()

    try:
        while True:
            try:
                # check_msg no bloquea: si no hay nada pendiente vuelve
                # enseguida y el bucle sigue atendiendo el DIP switch.
                client.check_msg()

                bits = leer_bits_dip()
                valor = valor_de_bits(bits)

                # Solo se publica en los cambios. Hacerlo en cada vuelta
                # llenaría el broker con el mismo dato varias veces por
                # segundo.
                if valor != ultimo_valor:
                    mostrar_digito(valor)
                    publicar_estado(client, bits, valor)
                    ultimo_valor = valor

                # Reconfirma "online" cada cierto tiempo aunque nada cambie:
                # algunos brokers tardan en notar una caída silenciosa de la
                # conexión, así que no conviene depender solo del last will.
                if time.ticks_diff(time.ticks_ms(), ultimo_latido) > LATIDO_MS:
                    client.publish(TOPIC_PRESENCIA, b"online", retain=True)
                    ultimo_latido = time.ticks_ms()

            except OSError as error:
                # Típico si el WiFi parpadea o el broker cierra el socket.
                # Se reintenta la conexión en vez de dejar morir el programa
                # (y, con él, la publicación de cambios del switch).
                print("Conexión MQTT perdida, reintentando:", error)
                try:
                    client.disconnect()
                except OSError:
                    pass
                time.sleep_ms(RECONEXION_MS)
                if not network.WLAN(network.STA_IF).isconnected():
                    conectar_wifi()
                client = conectar_mqtt()
                ultimo_valor = None       # fuerza republicar el estado actual
                ultimo_latido = time.ticks_ms()

            # Pausa corta: da tiempo a que se asienten los contactos del
            # switch y evita que el bucle acapare el procesador.
            time.sleep_ms(PERIODO_SONDEO_MS)
    finally:
        # Avisa que se apaga de forma ordenada y cierra la sesión, para no
        # dejar el ID de cliente colgado en el broker.
        try:
            client.publish(TOPIC_PRESENCIA, b"offline", retain=True)
        except OSError:
            pass
        client.disconnect()


if __name__ == "__main__":
    main()
