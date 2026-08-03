import hashlib
from datetime import datetime, date, time, timedelta

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
        return pd.read_sql(
            "SELECT * FROM eventos_trazabilidad ORDER BY id ASC", conn
        )


def verificar_integridad(df):
    esperado = GENESIS
    problemas = []
    for _, fila in df.iterrows():
        ev = fila.to_dict()
        if str(ev["hash_previo"]) != esperado:
            problemas.append({"id": ev["id"], "lote": ev["lote_id"], "problema": "cadena rota"})
        if calcular_hash(ev) != ev["hash_evento"]:
            problemas.append({"id": ev["id"], "lote": ev["lote_id"], "problema": "datos alterados"})
        esperado = ev["hash_evento"]
    return problemas


def cargar_datos_ejemplo():
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM eventos_trazabilidad")
        if cur.fetchone()[0] > 0:
            return False
    base = datetime(2026, 7, 10, 8, 0)
    demo = [
        ("L-001", "Finca El Oro", "cosecha", "Cuadrilla A", base, 28.0, 0, "-", "GlobalGAP"),
        ("L-001", "Finca El Oro", "empaque", "Empacadora Norte", base + timedelta(hours=6), 13.0, 960, "L", "GlobalGAP"),
        ("L-001", "Finca El Oro", "transporte", "Reefer 12", base + timedelta(hours=22), 13.2, 960, "L", "GlobalGAP"),
        ("L-002", "Finca La Union", "cosecha", "Cuadrilla B", base + timedelta(days=1), 27.5, 0, "-", "Rainforest Alliance"),
        ("L-002", "Finca La Union", "empaque", "Empacadora Sur", base + timedelta(days=1, hours=5), 12.8, 1080, "XL", "Rainforest Alliance"),
        ("L-002", "Finca La Union", "transporte", "Reefer 07", base + timedelta(days=1, hours=20), 16.5, 1080, "XL", "Rainforest Alliance"),
        ("L-003", "Finca El Oro", "cosecha", "Cuadrilla A", base + timedelta(days=2), 29.0, 0, "-", "GlobalGAP"),
        ("L-003", "Finca El Oro", "empaque", "Empacadora Norte", base + timedelta(days=2, hours=7), 13.1, 840, "M", "GlobalGAP"),
    ]
    for r in demo:
        insertar_evento({
            "lote_id": r[0], "finca_origen": r[1], "tipo_evento": r[2], "actor": r[3],
            "fecha_evento": r[4], "temperatura": r[5], "cajas": r[6], "calibre": r[7],
            "certificacion": r[8],
        })
    return True


def kpis(df):
    total_lotes = df["lote_id"].nunique()
    total_eventos = len(df)
    completos = 0
    tiempos = []
    for lote, g in df.groupby("lote_id"):
        tipos = set(g["tipo_evento"])
        if {"cosecha", "empaque", "transporte"}.issubset(tipos):
            completos += 1
        cos = g[g["tipo_evento"] == "cosecha"]["fecha_evento"]
        tra = g[g["tipo_evento"] == "transporte"]["fecha_evento"]
        if not cos.empty and not tra.empty:
            horas = (tra.min() - cos.min()).total_seconds() / 3600
            tiempos.append(horas)
    pct_completos = (completos / total_lotes * 100) if total_lotes else 0
    frio = df[df["tipo_evento"].isin(["empaque", "transporte"])]
    rupturas = int((frio["temperatura"].astype(float) > UMBRAL_FRIO).sum())
    tiempo_prom = round(sum(tiempos) / len(tiempos), 1) if tiempos else 0
    return {
        "total_lotes": total_lotes,
        "total_eventos": total_eventos,
        "pct_completos": round(pct_completos, 0),
        "rupturas": rupturas,
        "tiempo_prom": tiempo_prom,
    }


st.set_page_config(page_title="Trazabilidad del banano", layout="wide")
init_db()

st.title("Trazabilidad inteligente del banano")
st.caption("PoC - Blockchain (hash encadenado) + Business Intelligence + Software")

tab_reg, tab_traza, tab_int, tab_bi = st.tabs(
    ["Registrar evento", "Trazabilidad del lote", "Verificar integridad", "Dashboard BI"]
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
            temperatura = st.number_input("Temperatura (C)", value=13.0, step=0.1, format="%.1f")
            cajas = st.number_input("Cajas", value=0, step=1, min_value=0)
            calibre = st.selectbox("Calibre", ["-", "M", "L", "XL"])
        certificacion = st.selectbox("Certificacion", CERTIFICACIONES)
        enviar = st.form_submit_button("Registrar y sellar con hash")
    if enviar:
        insertar_evento({
            "lote_id": lote_id, "finca_origen": finca_origen, "tipo_evento": tipo_evento,
            "actor": actor, "fecha_evento": datetime.combine(f_dia, f_hora),
            "temperatura": temperatura, "cajas": int(cajas), "calibre": calibre,
            "certificacion": certificacion,
        })
        st.success("Evento registrado y encadenado correctamente.")
    st.divider()
    if st.button("Cargar datos de ejemplo"):
        if cargar_datos_ejemplo():
            st.success("Datos de ejemplo cargados.")
        else:
            st.info("Ya existen eventos; no se cargaron datos de ejemplo.")

with tab_traza:
    st.subheader("Historial trazable por lote")
    df = leer_eventos()
    if df.empty:
        st.info("Aun no hay eventos registrados.")
    else:
        lote_sel = st.selectbox("Selecciona un lote", sorted(df["lote_id"].unique()))
        g = df[df["lote_id"] == lote_sel].copy()
        st.dataframe(
            g[["id", "tipo_evento", "actor", "fecha_evento", "temperatura", "cajas", "calibre", "hash_evento"]],
            use_container_width=True, hide_index=True,
        )

with tab_int:
    st.subheader("Verificacion de integridad de la cadena")
    df = leer_eventos()
    if df.empty:
        st.info("Aun no hay eventos registrados.")
    else:
        problemas = verificar_integridad(df)
        if not problemas:
            st.success(f"Cadena integra: {len(df)} eventos verificados, ninguno alterado.")
        else:
            st.error(f"Se detectaron {len(problemas)} inconsistencias:")
            st.dataframe(pd.DataFrame(problemas), use_container_width=True, hide_index=True)

with tab_bi:
    st.subheader("Indicadores de trazabilidad")
    df = leer_eventos()
    if df.empty:
        st.info("Aun no hay eventos registrados.")
    else:
        k = kpis(df)
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Lotes", k["total_lotes"])
        c2.metric("Lotes con trazabilidad completa", f"{k['pct_completos']:.0f}%")
        c3.metric("Rupturas de cadena de frio", k["rupturas"])
        c4.metric("Tiempo cosecha-carga (h)", k["tiempo_prom"])
        st.divider()
        col_a, col_b = st.columns(2)
        with col_a:
            st.caption("Cajas por finca de origen")
            por_finca = df.groupby("finca_origen")["cajas"].sum()
            st.bar_chart(por_finca)
        with col_b:
            st.caption("Eventos por tipo")
            por_tipo = df["tipo_evento"].value_counts()
            st.bar_chart(por_tipo)