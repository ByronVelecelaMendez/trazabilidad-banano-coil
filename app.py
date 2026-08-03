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


def linea_humana(ev):
    fecha = ev["fecha_evento"]
    fstr = fecha.strftime("%d/%m %H:%M") if isinstance(fecha, datetime) else str(fecha)
    return f"{ICON.get(ev['tipo_evento'], '•')} {ev['tipo_evento'].capitalize()} · {ev['finca_origen']} · {fstr} · {float(ev['temperatura']):.1f}°C · {int(ev['cajas'])} cajas"


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


def verificar_integridad(df, sim_id=None):
    esperado = GENESIS
    estados = []
    ok = True
    for _, fila in df.iterrows():
        ev = fila.to_dict()
        roto = str(ev["hash_previo"]) != esperado
        alterado = (calcular_hash(ev) != ev["hash_evento"]) or (ev["id"] == sim_id)
        if roto or alterado:
            ok = False
        estados.append({"ev": ev, "roto": roto, "alterado": alterado})
        esperado = ev["hash_evento"]
    return ok, estados


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


def bloque_html(estado, es_ultimo):
    ev = estado["ev"]
    malo = estado["roto"] or estado["alterado"]
    borde = "#C0392B" if malo else "#2E7D32"
    fondo = "#FCEDEB" if malo else "#EDF7ED"
    chip = "#C0392B" if malo else "#2E7D32"
    icono = "SELLO ROTO" if malo else "SELLO VÁLIDO"
    nota = ""
    if malo:
        nota = "<div style='color:#C0392B; font-size:12.5px; margin-top:6px; font-weight:600'>El sello ya no coincide con los datos: registro alterado.</div>"
    conector = ""
    if not es_ultimo:
        col = "#C0392B" if malo else "#C8A032"
        txt = "cadena rota aquí" if malo else "el sello se pasa al siguiente bloque"
        conector = f"<div style='text-align:center; margin:3px 0'><span style='color:{col}; font-size:18px'>&#9660;</span><div style='color:{col}; font-size:11px; font-weight:600'>{txt}</div></div>"
    return f"""
    <div style="border:1.5px solid {borde}; background:{fondo}; border-radius:12px; padding:13px 18px; max-width:680px; margin:0 auto;">
      <div style="display:flex; justify-content:space-between; align-items:center;">
        <span style="font-weight:700; color:#3A3222; font-size:14.5px">{linea_humana(ev)}</span>
        <span style="background:{chip}; color:#FFF; font-size:11px; font-weight:700; padding:4px 11px; border-radius:20px; white-space:nowrap">{icono}</span>
      </div>
      <div style="font-family:monospace; font-size:12.5px; color:#8A7A55; margin-top:8px; line-height:1.7">
        Sello de este bloque: <span style="color:#5A5040">{ev['hash_evento'][:22]}…</span>
      </div>
      {nota}
    </div>
    {conector}
    """


st.set_page_config(page_title="Trazabilidad del banano", layout="wide")

st.markdown(
    """
    <style>
    #MainMenu {visibility: hidden;}
    footer {visibility: hidden;}
    header {visibility: hidden;}
    .block-container {padding-top: 1.5rem; padding-bottom: 2rem; max-width: 1150px;}
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

    with st.expander("¿Qué es esto y cómo funciona? (léelo aquí)"):
        st.markdown(
            "Imagina que cada registro se cierra con un **sello único** (un código llamado *hash*). "
            "Ese sello se calcula con los datos del registro **más** el sello del registro anterior, "
            "por eso todos quedan **encadenados** como los eslabones de una cadena.\n\n"
            "Si alguien cambia un dato —por ejemplo, baja una temperatura para ocultar una falla— "
            "el sello de ese registro **deja de coincidir** con su contenido, y como los siguientes "
            "dependían de él, **la cadena se rompe y la manipulación queda a la vista**.\n\n"
            "Eso es lo que hace confiable la información: nadie puede modificarla sin que se note."
        )

    df = leer_eventos()
    if df.empty:
        st.info("Aún no hay eventos registrados.")
    else:
        st.markdown("**Prueba tú mismo:** elige un bloque para simular que alguien lo manipula y observa cómo se rompe la cadena.")
        c_sel, c_btn = st.columns([2, 1])
        with c_sel:
            opciones = ["(cadena original, sin alterar)"] + [f"Bloque {int(r.id)} - {r.tipo_evento} {r.lote_id}" for r in df.itertuples()]
            sel = st.selectbox("Simular manipulación de un bloque", opciones, label_visibility="collapsed")

        sim_id = None
        if not sel.startswith("("):
            sim_id = int(sel.split()[1])

        ok, estados = verificar_integridad(df, sim_id=sim_id)
        malos = sum(1 for e in estados if e["roto"] or e["alterado"])

        m1, m2, m3 = st.columns(3)
        m1.metric("Bloques verificados", len(estados))
        m2.metric("Bloques alterados", malos)
        m3.metric("Estado", "OK" if ok else "ALERTA")

        if ok:
            st.success(f"Cadena íntegra: los {len(estados)} bloques son auténticos y nadie los modificó.")
        else:
            st.error(f"Se detectó manipulación en {malos} bloque(s). La información ya no es confiable.")

        st.markdown("<div style='height:8px'></div>", unsafe_allow_html=True)
        for i, estado in enumerate(estados):
            st.markdown(bloque_html(estado, i == len(estados) - 1), unsafe_allow_html=True)

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