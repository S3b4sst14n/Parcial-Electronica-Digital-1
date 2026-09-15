
// Debe coincidir EXACTAMENTE con los tópicos de la ESP32 (main.py)
const GRUPO = "Sanjuanelo";

const TOPIC_ESTADO  = `clase/decoder/${GRUPO}/estado`;   // ESP32 -> Web
const TOPIC_CONTROL = `clase/decoder/${GRUPO}/control`;  // Web   -> ESP32

const VALOR_MIN = 0;
const VALOR_MAX = 15;   // palabra de 4 bits: 0000 (0) .. 1111 (F)


// Tabla de 7 segmentos (a, b, c, d, e, f, g) — idéntica a la
// DIGIT_TABLE de main_con_conectividad.py
const SEGMENTOS = ["a", "b", "c", "d", "e", "f", "g"];

const TABLA_7SEG = {
    0x0: [1, 1, 1, 1, 1, 1, 0],
    0x1: [0, 1, 1, 0, 0, 0, 0],
    0x2: [1, 1, 0, 1, 1, 0, 1],
    0x3: [1, 1, 1, 1, 0, 0, 1],
    0x4: [0, 1, 1, 0, 0, 1, 1],
    0x5: [1, 0, 1, 1, 0, 1, 1],
    0x6: [1, 0, 1, 1, 1, 1, 1],
    0x7: [1, 1, 1, 0, 0, 0, 0],
    0x8: [1, 1, 1, 1, 1, 1, 1],
    0x9: [1, 1, 1, 1, 0, 1, 1],
    0xA: [1, 1, 1, 0, 1, 1, 1],
    0xB: [0, 0, 1, 1, 1, 1, 1],
    0xC: [1, 0, 0, 1, 1, 1, 0],
    0xD: [0, 1, 1, 1, 1, 0, 1],
    0xE: [1, 0, 0, 1, 1, 1, 1],
    0xF: [1, 0, 0, 0, 1, 1, 1],
};

// Referencias al DOM
const $ = (id) => document.getElementById(id);

const elEstado      = $("estado");
const elEstadoTexto = $("estado_texto");
const elValorDec    = $("valor_dec");
const elValorHex    = $("valor_hex");
const elValorBin    = $("valor_bin");
const elOrigen      = $("origen");
const elSello       = $("sello");
const elLeds        = $("leds");
const elDip         = $("dip");
const elSuma        = $("suma");
const elPalabraBin  = $("palabra_bin");
const elPalabraDec  = $("palabra_dec");
const elPalabraHex  = $("palabra_hex");
const elAutoEnvio   = $("auto_envio");
const elLog         = $("log");

$("grupo_badge").textContent   = GRUPO;
$("topic_estado").textContent  = TOPIC_ESTADO;
$("topic_control").textContent = TOPIC_CONTROL;


// Estado de la aplicación
// bits[i] corresponde al bit de peso 2^i (bits[0] = LSB)
const bits = [0, 0, 0, 0];
let conectado = false;


// Utilidades de formato
const aBinario = (valor) => valor.toString(2).padStart(4, "0");
const aHex     = (valor) => "0x" + valor.toString(16).toUpperCase();
const hora     = () => new Date().toLocaleTimeString("es-CO", { hour12: false });

function valorDeBits() {
    return bits.reduce((acc, bit, i) => acc | (bit << i), 0);
}


// Registro de actividad
function registrar(texto, tipo = "info") {
    const vacio = elLog.querySelector(".log__vacio");
    if (vacio) vacio.remove();

    const item = document.createElement("li");
    item.className = `log__item log__item--${tipo}`;

    const marca = document.createElement("span");
    marca.className = "log__hora";
    marca.textContent = hora();

    const msg = document.createElement("span");
    msg.className = "log__msg";
    msg.textContent = texto;

    item.append(marca, msg);
    elLog.prepend(item);

    // Evitar que el registro crezca sin límite
    while (elLog.children.length > 40) {
        elLog.lastElementChild.remove();
    }
}

function logVacio() {
    elLog.replaceChildren();
    const li = document.createElement("li");
    li.className = "log__vacio";
    li.textContent = "Sin actividad todavía.";
    elLog.appendChild(li);
}


// Render del display de 7 segmentos y de los indicadores
function pintarDisplay(valor) {
    const patron = TABLA_7SEG[valor] || [0, 0, 0, 0, 0, 0, 0];
    SEGMENTOS.forEach((seg, i) => {
        $("seg-" + seg).classList.toggle("on", patron[i] === 1);
    });
}

function pintarMonitor(valor, binario) {
    pintarDisplay(valor);

    elValorDec.textContent = valor;
    elValorDec.classList.remove("lectura__dec--vacio");
    elValorHex.textContent = aHex(valor);
    elValorBin.textContent = binario;
    elSello.textContent = hora();

    // binario llega de MSB a LSB; los LEDs se muestran en ese mismo orden
    [...elLeds.children].forEach((led, i) => {
        led.classList.toggle("on", binario[i] === "1");
    });

    // Destello breve para que se note el cambio de valor
    elValorDec.classList.remove("flash");
    void elValorDec.offsetWidth;   // reinicia la animación
    elValorDec.classList.add("flash");
}

function limpiarMonitor() {
    SEGMENTOS.forEach((seg) => $("seg-" + seg).classList.remove("on"));
    elValorDec.textContent = "—";
    elValorDec.classList.add("lectura__dec--vacio");
    elValorHex.textContent = "—";
    elValorBin.textContent = "----";
    [...elLeds.children].forEach((led) => led.classList.remove("on"));
}


// LEDs del monitor (B3 → B0)
for (let peso = 3; peso >= 0; peso--) {
    const led = document.createElement("div");
    led.className = "led";

    const punto = document.createElement("span");
    punto.className = "led__dot";

    const nombre = document.createElement("span");
    nombre.className = "led__name";
    nombre.textContent = "B" + peso;

    led.append(punto, nombre);
    elLeds.appendChild(led);
}


// Teclado de 4 bits: cada botón alterna su dígito 0 ⇄ 1 (B3 → B0)
const botonesBit = [];

for (let peso = 3; peso >= 0; peso--) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.className = "bit";
    btn.dataset.peso = peso;
    btn.setAttribute("role", "switch");
    btn.setAttribute("aria-checked", "false");
    btn.setAttribute("aria-label", `Bit ${peso}, peso ${2 ** peso}`);

    const led = document.createElement("span");
    led.className = "bit__led";

    const digito = document.createElement("span");
    digito.className = "bit__digito";
    digito.textContent = "0";

    const nombre = document.createElement("span");
    nombre.className = "bit__name";
    nombre.textContent = `B${peso} · ${2 ** peso}`;

    btn.append(led, digito, nombre);
    btn.addEventListener("click", () => alternarBit(peso));

    elDip.appendChild(btn);
    botonesBit[peso] = btn;
}


// Descomposición por pesos: 8 + 4 + 2 + 1 = valor
const chipsPeso = [];

for (let peso = 3; peso >= 0; peso--) {
    if (peso < 3) {
        const op = document.createElement("span");
        op.className = "suma__op";
        op.textContent = "+";
        elSuma.appendChild(op);
    }

    const chip = document.createElement("span");
    chip.className = "suma__chip";
    chip.textContent = 2 ** peso;

    elSuma.appendChild(chip);
    chipsPeso[peso] = chip;
}

const elIgual = document.createElement("span");
elIgual.className = "suma__igual";
elSuma.appendChild(elIgual);


// Cambios en el teclado

function alternarBit(peso) {
    bits[peso] = bits[peso] ? 0 : 1;
    refrescarControl();
    if (elAutoEnvio.checked) enviarValor();
}

function ponerBits(nuevos) {
    nuevos.forEach((b, i) => { bits[i] = b; });
    refrescarControl();
    if (elAutoEnvio.checked) enviarValor();
}

function refrescarControl() {
    const valor = valorDeBits();

    elPalabraBin.textContent = aBinario(valor);
    elPalabraDec.textContent = valor;
    elPalabraHex.textContent = aHex(valor);
    elIgual.textContent = "= " + valor;

    for (let peso = 3; peso >= 0; peso--) {
        const activo = bits[peso] === 1;
        const btn = botonesBit[peso];

        btn.classList.toggle("on", activo);
        btn.setAttribute("aria-checked", String(activo));
        btn.querySelector(".bit__digito").textContent = bits[peso];

        chipsPeso[peso].classList.toggle("on", activo);
    }
}


// Envío de comandos (Frontend -> ESP32)
function enviarValor() {
    const valor = valorDeBits();
    if (valor < VALOR_MIN || valor > VALOR_MAX) return;   // validar rango

    if (!conectado || !client.isConnected()) {
        registrar("Sin conexión: no se envió " + aBinario(valor), "err");
        return;
    }

    const msg = new Paho.MQTT.Message(String(valor));
    msg.destinationName = TOPIC_CONTROL;

    // send() lanza si el cliente perdió el enlace entre dos latidos:
    // sin este try el error cortaría el resto del flujo de la interfaz.
    try {
        client.send(msg);
    } catch (e) {
        console.error("[enviarValor]", e);
        registrar("No se pudo enviar " + aBinario(valor), "err");
        fijarEstado("error", "Sin conexión");
        return;
    }

    pintarMonitor(valor, aBinario(valor));
    elOrigen.textContent = "panel web";
    registrar(`TX ${aBinario(valor)} → ${valor} (${aHex(valor)})`, "tx");
}


// Botones de acción y atajos de teclado
$("btn_clear").addEventListener("click", () => ponerBits([0, 0, 0, 0]));
$("btn_full").addEventListener("click", () => ponerBits([1, 1, 1, 1]));
$("btn_enviar").addEventListener("click", enviarValor);
$("btn_limpiar_log").addEventListener("click", logVacio);

document.addEventListener("keydown", (e) => {
    if (e.target.matches("input, textarea")) return;

    // Teclas 1..4 → bits B3, B2, B1, B0 (de izquierda a derecha en pantalla)
    const mapa = { "1": 3, "2": 2, "3": 1, "4": 0 };
    if (e.key in mapa) {
        alternarBit(mapa[e.key]);
    } else if (e.key === "0") {
        ponerBits([0, 0, 0, 0]);
    } else if (e.key === "Enter") {
        enviarValor();
    }
});


// Estado visual de la conexión
function fijarEstado(clase, texto) {
    elEstado.className = "estado estado--" + clase;
    elEstadoTexto.textContent = texto;

    conectado = clase === "ok";
    botonesBit.forEach((btn) => { btn.disabled = !conectado; });
    $("btn_clear").disabled  = !conectado;
    $("btn_full").disabled   = !conectado;
    $("btn_enviar").disabled = !conectado;
}


// Cliente MQTT sobre WebSockets seguros (puerto 8884 en HiveMQ)
const client = new Paho.MQTT.Client(
    "broker.hivemq.com",                    // hostname sin protocolo
    8884,                                   // puerto WebSocket seguro
    "web_" + GRUPO + "_" + Math.random()    // ID único por cliente
);

function conectar() {
    fijarEstado("conectando", "Conectando…");
    client.connect({
        useSSL: true,   // requerido para el puerto 8884
        onSuccess: () => {
            fijarEstado("ok", "Conectado");
            client.subscribe(TOPIC_ESTADO);
            registrar("Conectado a broker.hivemq.com", "info");
        },
        onFailure: (err) => {
            fijarEstado("error", "Sin conexión");
            registrar("Error de conexión: " + err.errorMessage, "err");
            setTimeout(conectar, 5000);   // reintento automático
        },
    });
}

client.onConnectionLost = (respuesta) => {
    fijarEstado("error", "Conexión perdida");
    if (respuesta.errorCode !== 0) {
        registrar("Conexión perdida: " + respuesta.errorMessage, "err");
    }
    setTimeout(conectar, 5000);
};


// Mensajes entrantes (ESP32 -> Frontend): "bbbb,decimal"
client.onMessageArrived = (message) => {
    try {
        const datos = message.payloadString.split(",");
        if (datos.length !== 2) return;

        const binario = datos[0].trim();
        const valor = parseInt(datos[1], 10);

        // Validar antes de mostrar en el DOM
        const binarioValido = /^[01]{4}$/.test(binario);
        const valorValido = !isNaN(valor) && valor >= VALOR_MIN && valor <= VALOR_MAX;

        if (binarioValido && valorValido) {
            pintarMonitor(valor, binario);
            elOrigen.textContent = "DIP switch físico";
            registrar(`RX ${binario} → ${valor} (${aHex(valor)})`, "rx");
        }
    } catch (e) {
        // Nunca dejar el catch vacío
        console.error("[onMessageArrived]", e);
    }
};


// Arranque

limpiarMonitor();
refrescarControl();
logVacio();
conectar();
