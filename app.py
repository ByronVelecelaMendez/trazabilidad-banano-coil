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
GOLD = "#B8901F"
ICON = {"cosecha": "🌱", "empaque": "📦", "transporte": "🚚"}
TIPOS_EVENTO = ["cosecha", "empaque", "transporte"]
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


def kpis(df):
    total_lotes = df["lote_id"].nunique()
    completos = 0
    tiempos = []
    for lote, g in df.groupby("lote_id"):
        if {"cosecha", "empaque", "transporte"}.issubset(set(g["tipo_evento"])):
            completos += 1
        cos = g[g["tipo_evento"] == "cosecha"]["fecha_evento"]
        tra = g[g["tipo_evento"] == "transporte"]["fecha_evento"]
        if not cos.empty and not tra.empty:
            tiempos.append((tra.min() - cos.min()).total_seconds() / 3600)
    pct = (completos / total_lotes * 100) if total_lotes else 0
    frio = df[df["tipo_evento"].isin(["empaque", "transporte"])]
    rupturas = int((frio["temperatura"].astype(float) > UMBRAL_FRIO).sum())
    tp = round(sum(tiempos) / len(tiempos), 1) if tiempos else 0
    return {"total_lotes": total_lotes, "pct_completos": round(pct, 0), "rupturas": rupturas, "tiempo_prom": tp}


def barra(data, campo_x, campo_y, titulo_y):
    return (
        alt.Chart(data)
        .mark_bar(color=GOLD, cornerRadiusTopLeft=5, cornerRadiusTopRight=5)
        .encode(
            x=alt.X(f"{campo_x}:N", title=None, sort="-y", axis=alt.Axis(labelAngle=0, labelColor="#5A5040")),
            y=alt.Y(f"{campo_y}:Q", title=titulo_y, axis=alt.Axis(labelColor="#5A5040", titleColor="#5A5040")),
            tooltip=[campo_x, campo_y],
        )
        .properties(height=320)
        .configure_view(strokeWidth=0)
        .configure_axis(grid=True, gridColor="#EDE4CE")
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
              f"<div style='font-weight:700; color:#3A3222; font-size:13px; margin-top:4px'>{tipo}</div>"
              f"<div style='color:#8A7A55; font-size:11px'>{ev['lote_id']} · Bloque {ev['id']}</div>"
              f"<div style='font-family:monospace; font-size:10px; color:#A99770; margin-top:6px'>{ev['hash_evento'][:10]}…</div>"
              f"<div style='background:{chip}; color:#FFF; font-size:10px; font-weight:700; padding:2px 8px; border-radius:20px; display:inline-block; margin-top:8px'>{estado}</div>"
              f"</div>")
    if es_ultimo:
        return bloque
    col = "#C0392B" if malo else "#C8A032"
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
    .block-container {padding-top: 1rem; padding-bottom: 1.5rem; max-width: 1150px;} .stExpander {margin-bottom: 0.4rem;} [data-testid="stVerticalBlock"] {gap: 0.7rem;}
    [data-testid="stMetric"] {
        background: #FAF4E6;
        border: 1px solid #E8DCBC;
        border-radius: 12px;
        padding: 14px 16px;
        min-height: 105px;
        display: flex;
        flex-direction: column;
        justify-content: center;
        box-shadow: 0 1px 2px rgba(120,90,20,0.05);
    }
    [data-testid="stMetricLabel"] p {color: #8A7A55; font-weight: 600; font-size: 14px;}
    [data-testid="stMetricValue"] {color: #6E5A1E; font-size: 26px;}
    h1, h2, h3 {color: #3A3222;}
    .stTabs [data-baseweb="tab-list"] {gap: 6px; border-bottom: 1px solid #E8DCBC;}
    hr {border-color: #E8DCBC; margin: 0.8rem 0;}
    [data-testid="stVerticalBlock"] {gap: 0.6rem;}
    [data-testid="stHorizontalBlock"] {gap: 0.8rem;}
    div[data-testid="stMarkdownContainer"] p {margin-bottom: 0.3rem;}
    .stAlert {padding: 0.6rem 0.9rem;}
    hr {margin: 0.5rem 0 !important;}
    </style>
    """,
    unsafe_allow_html=True,
)

init_db()

col_logo, col_title = st.columns([1, 8], vertical_alignment="center")
with col_logo:
    st.image("logo.png", width=110)
with col_title:
    st.markdown("<h1 style='margin:0'>Trazabilidad inteligente del banano</h1>", unsafe_allow_html=True)
    st.markdown(
        "<p style='color:#8A7A55; margin-top:4px; font-size:16px; font-weight:500'>Universidad Estatal Península de Santa Elena</p>",
        unsafe_allow_html=True,
    )

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

        st.markdown("##### Demostrador: intenta manipular un dato")
        col_l, col_r = st.columns([1, 1])
        with col_l:
            etiquetas = {f"Bloque {int(e['id'])} · {e['tipo_evento']} · {e['lote_id']}": int(e["id"]) for e in eventos}
            sel = st.selectbox("Elige un bloque", list(etiquetas.keys()))
            bid = etiquetas[sel]
            ev_sel = next(e for e in eventos if int(e["id"]) == bid)
            temp_orig = float(ev_sel["temperatura"])
        with col_r:
            nueva_temp = st.number_input("Cambia la temperatura (°C)", value=temp_orig, step=0.1, format="%.1f")

        ev_mod = dict(ev_sel)
        ev_mod["temperatura"] = nueva_temp
        sello_original = ev_sel["hash_evento"]
        sello_nuevo = calcular_hash(ev_mod)
        manipulado = sello_nuevo != sello_original

        cc1, cc2 = st.columns(2)
        with cc1:
            st.markdown(
                f"<div style='border:1px solid #E8DCBC; background:#FAF4E6; border-radius:10px; padding:12px'>"
                f"<div style='font-size:13px; color:#8A7A55; font-weight:600'>Sello guardado (original)</div>"
                f"<div style='font-family:monospace; font-size:12px; color:#5A5040; margin-top:6px; word-break:break-all'>{sello_original[:40]}…</div></div>",
                unsafe_allow_html=True,
            )
        with cc2:
            col_b = "#C0392B" if manipulado else "#2E7D32"
            bg_b = "#FCEDEB" if manipulado else "#EDF7ED"
            st.markdown(
                f"<div style='border:1px solid {col_b}; background:{bg_b}; border-radius:10px; padding:12px'>"
                f"<div style='font-size:13px; color:{col_b}; font-weight:600'>Sello recalculado (con tu cambio)</div>"
                f"<div style='font-family:monospace; font-size:12px; color:#5A5040; margin-top:6px; word-break:break-all'>{sello_nuevo[:40]}…</div></div>",
                unsafe_allow_html=True,
            )

        if manipulado:
            st.error("Los sellos NO coinciden. Cambiar un solo dato altera el sello por completo, y el sistema detecta la manipulación al instante.")
        else:
            st.info("Los sellos coinciden: no has cambiado ningún dato. Modifica la temperatura para ver qué ocurre.")

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

        st.markdown("<div style='height:14px'></div>", unsafe_allow_html=True)
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
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Lotes", k["total_lotes"], help="Número de lotes registrados.")
        c2.metric("Trazabilidad completa", f"{k['pct_completos']:.0f}%", help="Lotes con cosecha, empaque y transporte.")
        c3.metric("Rupturas de frío", k["rupturas"], delta=("Alerta" if k["rupturas"] else "OK"),
                  delta_color=("inverse" if k["rupturas"] else "normal"),
                  help="Eventos con temperatura sobre el rango seguro (~13 °C).")
        c4.metric("Tiempo cosecha a carga (h)", k["tiempo_prom"], help="Horas promedio entre cosecha y carga.")
        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Cajas por finca de origen")
            d1 = df.groupby("finca_origen")["cajas"].sum().reset_index()
            st.altair_chart(barra(d1, "finca_origen", "cajas", "Cajas"), use_container_width=True)
        with col_b:
            st.caption("Eventos por tipo")
            d2 = df["tipo_evento"].value_counts().reset_index()
            d2.columns = ["tipo_evento", "conteo"]
            st.altair_chart(barra(d2, "tipo_evento", "conteo", "Eventos"), use_container_width=True)