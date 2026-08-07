import hashlib
from datetime import datetime, date, time, timedelta

import altair as alt
import pandas as pd
import psycopg2
import psycopg2.extras
import streamlit as st

try:
    _db = st.secrets["postgres"]
    DB_CONFIG = {
        "host": _db["host"],
        "port": int(_db["port"]),
        "dbname": _db["dbname"],
        "user": _db["user"],
        "password": _db["password"],
    }
except Exception:
    DB_CONFIG = {
        "host": "localhost",
        "port": 5433,
        "dbname": "trazabilidad",
        "user": "postgres",
        "password": "postgres",
    }

GENESIS = "0" * 64
UMBRAL_FRIO = 14.0
AZUL = "#1F3A6E"
ICON = {"cosecha": "🌱", "recepcion": "🏭", "lavado": "💧", "clasificacion": "📋", "empaque": "📦", "etiquetado": "🏷️", "paletizado": "🗄️", "carga": "🏗️", "transito": "🚚", "transporte": "🚚", "puerto": "⚓"}
TIPOS_EVENTO = ["cosecha", "recepcion", "lavado", "clasificacion", "empaque", "etiquetado", "paletizado", "carga", "transito", "puerto"]
CERTIFICACIONES = ["GlobalGAP", "Rainforest Alliance", "Fairtrade", "Ninguna"]


def get_conn():
    return psycopg2.connect(**DB_CONFIG)


def init_db():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS eventos_trazabilidad (
                id SERIAL PRIMARY KEY,
                lote_id VARCHAR(50) NOT NULL,
                finca_origen VARCHAR(100) NOT NULL,
                tipo_evento VARCHAR(50) NOT NULL,
                actor VARCHAR(100) NOT NULL,
                fecha_evento TIMESTAMP NOT NULL,
                temperatura NUMERIC(5,2) NOT NULL,
                cajas INTEGER NOT NULL,
                calibre VARCHAR(20) NOT NULL,
                certificacion VARCHAR(50) NOT NULL,
                hash_previo VARCHAR(64) NOT NULL,
                hash_evento VARCHAR(64) NOT NULL,
                creado_en TIMESTAMP DEFAULT NOW()
            )
            """
        )
        conn.commit()


def serializar(ev):
    fecha = ev["fecha_evento"]
    if isinstance(fecha, datetime):
        fecha = fecha.strftime("%Y-%m-%d %H:%M:%S")
    partes = [
        str(ev["lote_id"]).strip(),
        str(ev["finca_origen"]).strip(),
        str(ev["tipo_evento"]).strip(),
        str(ev["actor"]).strip(),
        str(fecha),
        f"{float(ev['temperatura']):.2f}",
        str(int(ev["cajas"])),
        str(ev["calibre"]).strip(),
        str(ev["certificacion"]).strip(),
        str(ev["hash_previo"]).strip(),
    ]
    return "|".join(partes)


def calcular_hash(ev):
    return hashlib.sha256(serializar(ev).encode("utf-8")).hexdigest()


def ultimo_hash():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT hash_evento FROM eventos_trazabilidad ORDER BY id DESC LIMIT 1")
        fila = cur.fetchone()
        return fila[0] if fila else GENESIS


def insertar_evento(datos):
    datos["hash_previo"] = ultimo_hash()
    datos["hash_evento"] = calcular_hash(datos)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO eventos_trazabilidad
            (lote_id, finca_origen, tipo_evento, actor, fecha_evento, temperatura,
             cajas, calibre, certificacion, hash_previo, hash_evento)
            VALUES (%(lote_id)s, %(finca_origen)s, %(tipo_evento)s, %(actor)s,
                    %(fecha_evento)s, %(temperatura)s, %(cajas)s, %(calibre)s,
                    %(certificacion)s, %(hash_previo)s, %(hash_evento)s)
            """,
            datos,
        )
        conn.commit()


def leer_eventos():
    with get_conn() as conn:
        return pd.read_sql("SELECT * FROM eventos_trazabilidad ORDER BY id ASC", conn)


ETAPAS_FRIAS = ["empaque", "etiquetado", "paletizado", "carga", "transito", "transporte", "puerto"]
ETAPAS_CLAVE = {"cosecha", "empaque", "transito"}
ETAPAS_CARGA = ["carga", "transito", "transporte"]

def kpis(df):
    total_lotes = df["lote_id"].nunique()
    completos = 0
    tiempos = []
    for lote, g in df.groupby("lote_id"):
        tipos = set(g["tipo_evento"])
        if ETAPAS_CLAVE.issubset(tipos):
            completos += 1
        cos = g[g["tipo_evento"] == "cosecha"]["fecha_evento"]
        car = g[g["tipo_evento"].isin(ETAPAS_CARGA)]["fecha_evento"]
        if not cos.empty and not car.empty:
            tiempos.append((car.min() - cos.min()).total_seconds() / 3600)
    pct = (completos / total_lotes * 100) if total_lotes else 0
    frio = df[df["tipo_evento"].isin(ETAPAS_FRIAS)]
    rupturas = int((frio["temperatura"].astype(float) > UMBRAL_FRIO).sum())
    tp = round(sum(tiempos) / len(tiempos), 1) if tiempos else 0
    return {"total_lotes": total_lotes, "pct_completos": round(pct, 0), "rupturas": rupturas, "tiempo_prom": tp, "completos": completos}


def tarjeta(icono, etiqueta, valor, color, subtexto):
    return (f"<div style='background:#FFFFFF; border:1px solid #DFD8C4; border-left:4px solid {color}; "
            f"border-radius:12px; padding:16px 18px; box-shadow:0 1px 3px rgba(30,50,90,0.06); height:132px; "
            f"display:flex; flex-direction:column; justify-content:center'>"
            f"<div style='display:flex; align-items:center; gap:8px'>"
            f"<span style='font-size:19px'>{icono}</span>"
            f"<span style='font-size:12px; color:#5B6472; font-weight:600; text-transform:uppercase; letter-spacing:0.3px'>{etiqueta}</span></div>"
            f"<div style='font-size:32px; font-weight:700; color:#26303F; margin-top:6px; line-height:1'>{valor}</div>"
            f"<div style='font-size:12px; color:{color}; font-weight:600; margin-top:5px'>{subtexto}</div></div>")


def barra(data, campo_x, campo_y, titulo_y):
    base = alt.Chart(data).encode(
        x=alt.X(f"{campo_x}:N", title=None, sort="-y",
                axis=alt.Axis(labelAngle=0, labelColor="#5B6472", labelFontSize=12)),
        y=alt.Y(f"{campo_y}:Q", title=titulo_y,
                axis=alt.Axis(labelColor="#5B6472", titleColor="#5B6472", grid=True, gridColor="#E4E0D3")),
        tooltip=[campo_x, campo_y],
    )
    barras = base.mark_bar(color=AZUL, cornerRadiusTopLeft=6, cornerRadiusTopRight=6, size=48)
    texto = base.mark_text(dy=-9, color=AZUL, fontWeight="bold", fontSize=13).encode(text=f"{campo_y}:Q")
    return (
        (barras + texto)
        .properties(height=270)
        .configure_view(strokeWidth=0)
        .configure_axis(domainColor="#DFD8C4")
    )


def bloque_mini(ev, malo, es_ultimo):
    borde = "#C0392B" if malo else "#2E7D32"
    fondo = "#FCEDEB" if malo else "#EDF7ED"
    chip = "#C0392B" if malo else "#2E7D32"
    estado = "ROTO" if malo else "OK"
    tipo = str(ev["tipo_evento"]).capitalize()
    icono = ICON.get(ev["tipo_evento"], "•")
    bloque = (f"<div style='border:1.5px solid {borde}; background:{fondo}; border-radius:12px; padding:12px 10px; width:150px; text-align:center; flex:0 0 auto;'>"
              f"<div style='font-size:26px; line-height:1'>{icono}</div>"
              f"<div style='font-weight:700; color:#26303F; font-size:13px; margin-top:4px'>{tipo}</div>"
              f"<div style='color:#5B6472; font-size:11px'>{ev['lote_id']} · Bloque {ev.get('n_bloque', ev['id'])}</div>"
              f"<div style='font-family:monospace; font-size:10px; color:#8A93A5; margin-top:6px'>{ev['hash_evento'][:10]}…</div>"
              f"<div style='background:{chip}; color:#FFF; font-size:10px; font-weight:700; padding:2px 8px; border-radius:20px; display:inline-block; margin-top:8px'>{estado}</div>"
              f"</div>")
    if es_ultimo:
        return bloque
    col = "#C0392B" if malo else "#1F3A6E"
    simbolo = "✕" if malo else "🔗"
    enlace = f"<div style='display:flex; align-items:center; color:{col}; font-size:18px; flex:0 0 auto; padding:0 2px'>{simbolo}</div>"
    return bloque + enlace


st.set_page_config(page_title="Trazabilidad del banano", layout="wide")

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .block-container {padding-top: 1rem; padding-bottom: 1.5rem; max-width: 1150px;}
    [data-testid="stVerticalBlock"] {gap: 0.6rem;}
    div[data-testid="stImage"] {margin-bottom: 0.5rem;}
    h1, h2, h3 {color: #26303F;}
    .stTabs [data-baseweb="tab-list"] {gap: 6px; border-bottom: 1px solid #DFD8C4;}
    hr {margin: 0.5rem 0 !important; border-color: #DFD8C4;}
    [data-testid="stTextInput"] input, [data-testid="stNumberInput"] input,
    [data-testid="stDateInput"] input, [data-testid="stTimeInput"] input,
    [data-baseweb="select"] > div, [data-baseweb="select"] > div > div,
    div[data-baseweb="input"], div[data-baseweb="base-input"] {
        background-color: #FFFFFF !important;
        border: 1px solid #C9D2E0 !important;
        border-radius: 8px !important;
    }
    [data-baseweb="select"] * {background-color: transparent !important;}
    [data-testid="stForm"] {border: 1px solid #DFD8C4; border-radius: 14px; background: #FBFAF6; padding: 20px 22px;}
    [data-testid="stForm"] label p {color: #1F3A6E !important; font-weight: 600; font-size: 13px;}
    [data-testid="stForm"] [data-testid="stVerticalBlock"] {gap: 0.5rem;}
    .stButton button, .stFormSubmitButton button {margin-top: 8px;}
    </style>
    """,
    unsafe_allow_html=True,
)

init_db()

st.image("banner.png", use_container_width=True)

tab_reg, tab_traza, tab_int, tab_bi = st.tabs(
    ["  Registrar evento  ", "  Trazabilidad del lote  ", "  Verificar integridad  ", "  Dashboard BI  "]
)

with tab_reg:
    st.subheader("Registrar un evento de la cadena")
    with st.form("form_evento", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        with c1:
            lote_id = st.text_input("Lote", "L-001")
            finca_origen = st.text_input("Finca de origen", "Finca El Oro")
            tipo_evento = st.selectbox("Tipo de evento", TIPOS_EVENTO)
        with c2:
            actor = st.text_input("Actor / responsable", "Empacadora Norte")
            f_dia = st.date_input("Fecha", date(2026, 7, 10))
            f_hora = st.time_input("Hora", time(8, 0))
        with c3:
            temperatura = st.number_input("Temperatura (°C)", value=13.0, step=0.1, format="%.1f")
            cajas = st.number_input("Cajas", value=0, step=1, min_value=0)
            calibre = st.selectbox("Calibre", ["-", "M", "L", "XL"])
        certificacion = st.selectbox("Certificación", CERTIFICACIONES)
        enviar = st.form_submit_button("Registrar y sellar con hash", type="primary")
    if enviar:
        insertar_evento({
            "lote_id": lote_id, "finca_origen": finca_origen, "tipo_evento": tipo_evento,
            "actor": actor, "fecha_evento": datetime.combine(f_dia, f_hora),
            "temperatura": temperatura, "cajas": int(cajas), "calibre": calibre,
            "certificacion": certificacion,
        })
        st.success("Evento registrado y encadenado correctamente.")

with tab_traza:
    st.subheader("Historial trazable por lote")
    df = leer_eventos()
    if df.empty:
        st.info("Aún no hay eventos registrados.")
    else:
        lote_sel = st.selectbox("Selecciona un lote", sorted(df["lote_id"].unique()))
        g = df[df["lote_id"] == lote_sel].copy()
        g["Hash"] = g["hash_evento"].str.slice(0, 18) + "…"
        vista = g[["id", "tipo_evento", "actor", "fecha_evento", "temperatura", "cajas", "calibre", "Hash"]]
        vista = vista.rename(columns={
            "id": "ID", "tipo_evento": "Evento", "actor": "Actor",
            "fecha_evento": "Fecha", "temperatura": "Temp. (°C)", "cajas": "Cajas", "calibre": "Calibre",
        })
        st.dataframe(vista, use_container_width=True, hide_index=True)

with tab_int:
    st.subheader("Verificación de integridad de la cadena")

    with st.expander("¿Cómo funciona? (explicación sencilla)"):
        st.markdown(
            "Cada registro se cierra con un sello único (un código llamado hash) que se calcula "
            "a partir de sus datos más el sello del registro anterior. Así todos quedan encadenados. "
            "Si alguien cambia un solo dato, el sello de ese bloque cambia por completo y deja de coincidir, "
            "rompiendo la cadena. Por eso nadie puede alterar la información sin que se note. "
            "Usa el demostrador de abajo para verlo con tus propios ojos."
        )

    df = leer_eventos()
    if df.empty:
        st.info("Aún no hay eventos registrados.")
    else:
        eventos = df.to_dict("records")
        for _n, _ev in enumerate(eventos, start=1):
            _ev["n_bloque"] = _n

        st.markdown("##### Demostrador: intenta manipular un dato")
        col_a, col_b = st.columns([1, 1])
        with col_a:
            etiquetas = {f"Bloque {e['n_bloque']} · {e['tipo_evento']} · {e['lote_id']}": int(e["id"]) for e in eventos}
            sel = st.selectbox("Elige un bloque", list(etiquetas.keys()))
            bid = etiquetas[sel]
            ev_sel = next(e for e in eventos if int(e["id"]) == bid)
        with col_b:
            campo = st.selectbox("¿Qué dato quieres manipular?", ["Temperatura", "Cajas", "Finca de origen", "Actor / responsable"])

        ev_mod = dict(ev_sel)
        if campo == "Temperatura":
            nuevo = st.number_input("Nuevo valor de temperatura (°C)", value=float(ev_sel["temperatura"]), step=0.1, format="%.1f")
            ev_mod["temperatura"] = nuevo
        elif campo == "Cajas":
            nuevo = st.number_input("Nuevo número de cajas", value=int(ev_sel["cajas"]), step=1, min_value=0)
            ev_mod["cajas"] = int(nuevo)
        elif campo == "Finca de origen":
            nuevo = st.text_input("Nueva finca de origen", value=str(ev_sel["finca_origen"]))
            ev_mod["finca_origen"] = nuevo
        else:
            nuevo = st.text_input("Nuevo actor / responsable", value=str(ev_sel["actor"]))
            ev_mod["actor"] = nuevo

        sello_original = ev_sel["hash_evento"]
        sello_nuevo = calcular_hash(ev_mod)
        manipulado = sello_nuevo != sello_original

        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown(
                f"<div style='border:1px solid #DFD8C4; background:#F3F0E7; border-radius:10px; padding:12px'>"
                f"<div style='font-size:13px; color:#5B6472; font-weight:600'>Sello guardado (original)</div>"
                f"<div style='font-family:monospace; font-size:12px; color:#26303F; margin-top:6px; word-break:break-all'>{sello_original[:40]}…</div></div>",
                unsafe_allow_html=True,
            )
        with cc2:
            col_c = "#C0392B" if manipulado else "#2E7D32"
            bg_c = "#FCEDEB" if manipulado else "#EDF7ED"
            st.markdown(
                f"<div style='border:1px solid {col_c}; background:{bg_c}; border-radius:10px; padding:12px'>"
                f"<div style='font-size:13px; color:{col_c}; font-weight:600'>Sello recalculado (con tu cambio)</div>"
                f"<div style='font-family:monospace; font-size:12px; color:#26303F; margin-top:6px; word-break:break-all'>{sello_nuevo[:40]}…</div></div>",
                unsafe_allow_html=True,
            )

        if manipulado:
            st.error(f"Modificaste el campo «{campo}». Los sellos ya NO coinciden: el sistema detecta la manipulación al instante.")
        else:
            st.info("Los sellos coinciden: aún no has cambiado el dato. Modifica el valor para ver qué ocurre.")

        st.markdown("<div style='height:6px'></div>", unsafe_allow_html=True)

        esperado = GENESIS
        estados = []
        ok = True
        for e in eventos:
            roto = str(e["hash_previo"]) != esperado
            alterado = (calcular_hash(e) != e["hash_evento"]) or (int(e["id"]) == bid and manipulado)
            if roto or alterado:
                ok = False
            estados.append({"ev": e, "malo": roto or alterado})
            esperado = e["hash_evento"]

        if ok:
            st.markdown(
                "<div style='background:#EDF7ED; border:1px solid #2E7D32; border-radius:12px; padding:16px; text-align:center'>"
                f"<span style='font-size:22px'>✅</span> <span style='color:#1E5E22; font-weight:700; font-size:17px'>Cadena verificada · {len(estados)} bloques auténticos</span></div>",
                unsafe_allow_html=True,
            )
        else:
            malos = sum(1 for e in estados if e["malo"])
            st.markdown(
                "<div style='background:#FCEDEB; border:1px solid #C0392B; border-radius:12px; padding:16px; text-align:center'>"
                f"<span style='font-size:22px'>⛔</span> <span style='color:#A5281B; font-weight:700; font-size:17px'>Cadena comprometida · {malos} bloque(s) manipulado(s)</span></div>",
                unsafe_allow_html=True,
            )

        st.markdown("<div style='height:12px'></div>", unsafe_allow_html=True)
        st.caption("Cadena de bloques")
        cadena = "<div style='display:flex; flex-wrap:wrap; align-items:center; gap:2px'>"
        for i, e in enumerate(estados):
            cadena += bloque_mini(e["ev"], e["malo"], i == len(estados) - 1)
        cadena += "</div>"
        st.markdown(cadena, unsafe_allow_html=True)

with tab_bi:
    st.subheader("Indicadores de trazabilidad")
    df = leer_eventos()
    if df.empty:
        st.info("Aún no hay eventos registrados.")
    else:
        k = kpis(df)
        col_frio = "#C0392B" if k["rupturas"] else "#2E7D32"
        sub_frio = "Requiere atención" if k["rupturas"] else "Sin incidencias"
        col_traza = "#2E7D32" if k["pct_completos"] >= 100 else "#1F3A6E"
        c1, c2, c3, c4 = st.columns(4)
        c1.markdown(tarjeta("📦", "Lotes", k["total_lotes"], "#1F3A6E", "En seguimiento activo"), unsafe_allow_html=True)
        c2.markdown(tarjeta("✔️", "Trazabilidad completa", f"{k['pct_completos']:.0f}%", col_traza, f"{k['completos']} de {k['total_lotes']} lotes completos"), unsafe_allow_html=True)
        c3.markdown(tarjeta("❄️", "Rupturas de frío", k["rupturas"], col_frio, sub_frio), unsafe_allow_html=True)
        c4.markdown(tarjeta("⏱️", "Tiempo cosecha a carga", f"{k['tiempo_prom']} h", "#1F3A6E", "Promedio por lote"), unsafe_allow_html=True)

        st.markdown("<div style='height:26px'></div>", unsafe_allow_html=True)
        st.markdown("<h3 style='color:#1F3A6E; font-size:19px; margin-bottom:4px'>Análisis visual</h3>", unsafe_allow_html=True)
        st.markdown("<div style='height:10px'></div>", unsafe_allow_html=True)
        col_a, col_b = st.columns(2, gap="large")
        with col_a:
            st.markdown("<p style='font-weight:600; color:#5B6472; font-size:14px; margin-bottom:6px'>Cajas por finca de origen</p>", unsafe_allow_html=True)
            d1 = df.groupby("finca_origen")["cajas"].sum().reset_index()
            st.altair_chart(barra(d1, "finca_origen", "cajas", "Cajas"), use_container_width=True)
        with col_b:
            st.markdown("<p style='font-weight:600; color:#5B6472; font-size:14px; margin-bottom:6px'>Temperatura promedio por etapa de la cadena</p>", unsafe_allow_html=True)
            orden = ["cosecha","recepcion","lavado","clasificacion","empaque","etiquetado","paletizado","carga","transito","transporte","puerto"]
            d2 = df.groupby("tipo_evento")["temperatura"].mean().reset_index()
            d2["temperatura"] = d2["temperatura"].astype(float).round(1)
            d2["orden"] = d2["tipo_evento"].apply(lambda x: orden.index(x) if x in orden else 99)
            d2 = d2.sort_values("orden")
            d2["estado"] = d2["temperatura"].apply(lambda t: "Ruptura de frio" if t > UMBRAL_FRIO else "En rango")
            linea = alt.Chart(pd.DataFrame({"y": [UMBRAL_FRIO]})).mark_rule(
                color="#C0392B", strokeDash=[5, 4]).encode(y="y:Q")
            barras = alt.Chart(d2).mark_bar(cornerRadiusTopLeft=5, cornerRadiusTopRight=5).encode(
                x=alt.X("tipo_evento:N", title=None, sort=list(d2["tipo_evento"]),
                        axis=alt.Axis(labelAngle=-40, labelColor="#5B6472", labelFontSize=11)),
                y=alt.Y("temperatura:Q", title="Temp. (C)",
                        axis=alt.Axis(labelColor="#5B6472", titleColor="#5B6472", grid=True, gridColor="#E4E0D3")),
                color=alt.Color("estado:N",
                    scale=alt.Scale(domain=["En rango", "Ruptura de frio"], range=["#1F3A6E", "#C0392B"]),
                    legend=alt.Legend(title=None, orient="top")),
                tooltip=["tipo_evento", "temperatura"],
            )
            chart2 = (barras + linea).properties(height=270).configure_view(strokeWidth=0).configure_axis(domainColor="#DFD8C4")
            st.altair_chart(chart2, use_container_width=True)