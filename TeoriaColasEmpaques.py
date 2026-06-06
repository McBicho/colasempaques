# -*- coding: utf-8 -*-
"""
=====================================================================
 SIMULADOR DE BODEGA Y FLUJO DEL OPERARIO 1
 Control tridimensional: INVENTARIO · ESPACIO · RIESGO
---------------------------------------------------------------------
 Tecnologías: Streamlit + Plotly + simulación paso a paso (minuto a minuto)
 Ejecutar con:   streamlit run app_bodega.py
=====================================================================

NOTAS DE MODELADO (supuestos explícitos donde el brief era ambiguo;
todos editables en la sección de constantes de abajo):

1. Capacidad de racks: "2 racks de 18 posiciones, 3 de fondo, 4 de alto"
   se interpreta como 2 x 18 x 3 x 4 = 432 pallets. El primer nivel
   (2*18*3 = 108) es exclusivo para tapas de balde; los 3 niveles
   superiores (324) admiten baldes, tapas de balde y botellas de 1L.

2. La producción (consumo de líneas) ocurre dentro de la ventana de
   turnos (07:00 a 02:15 = 1155 min). El consumo diario se reparte de
   forma uniforme en esa ventana.

3. El Operario 1 (Turno 1, 07:00-16:45) descarga camiones de IDE y
   SMASAC. Los cilindros de REYEMSA entran al inventario pero NO
   consumen tiempo del operario (manejados por un tercero), salvo que
   se active la casilla correspondiente.

4. Para separar el "tiempo del operario" del "drenaje de inventario"
   sin doble contabilidad: el inventario de la bodega baja de forma
   continua al ritmo de consumo (las líneas jalan pallets), y el tiempo
   del operario se contabiliza aparte a razón de 3 min por pallet movido.

5. La ocupación de cada zona se recalcula cada minuto colocando todo el
   inventario actual con el motor de zonificación (orden de prioridad).
"""

import streamlit as st
import pandas as pd
import numpy as np
import plotly.graph_objects as go
from plotly.subplots import make_subplots
from datetime import datetime, timedelta
from collections import defaultdict

# =====================================================================
# 1. PARÁMETROS Y CONSTANTES DEL MODELO
# =====================================================================

# ---- Tiempos (minutos contados desde las 07:00) --------------------
T1_START, T1_END = 0, 585        # Turno 1: 07:00 - 16:45  (9h45 = 585 min)
T2_START, T2_END = 570, 1155     # Turno 2: 16:30 - 02:15  (solapamiento 570-585)
PROD_START, PROD_END = 0, 1155   # Ventana de producción (consumo de líneas)
MIN_X_PALLET = 3                 # 3 minutos por pallet (inbound y outbound)
BASE_DATE = datetime(2024, 1, 1, 7, 0)

# ---- Catálogo: unidades por pallet ---------------------------------
UNITS_PER_PALLET = {
    "Balde 19L": 240, "Tapa 19L": 800,
    "Balde 2.5USG": 400, "Tapa 2.5USG": 1160,
    "Botella 1L": 1584, "Caja 12x1L": 750,
    "Botella 4L": 480, "Cilindro": 4,
    "Tapa Lt": 800, "Tapa Gl": 800,   # conversión no especificada; referencial
}
ALL_ITEMS = list(UNITS_PER_PALLET.keys())

# ---- Categorías de empaque (para zonificación) ---------------------
ITEM_CATEGORY = {
    "Balde 19L": "balde", "Balde 2.5USG": "balde",
    "Tapa 19L": "tapa_balde", "Tapa 2.5USG": "tapa_balde",
    "Botella 1L": "bot1l", "Caja 12x1L": "caja",
    "Botella 4L": "bot4l", "Cilindro": "cilindro",
    "Tapa Lt": "tapa_lg", "Tapa Gl": "tapa_lg",
}
ITEMS_BY_CAT = defaultdict(list)
for _it, _c in ITEM_CATEGORY.items():
    ITEMS_BY_CAT[_c].append(_it)

# ---- Matriz de capacidad por zona (en posiciones de piso) ----------
RACK_COUNT, RACK_BAYS, RACK_DEEP, RACK_HIGH = 2, 18, 3, 4
RACK_TOTAL = RACK_COUNT * RACK_BAYS * RACK_DEEP * RACK_HIGH   # 432
RACK_L1 = RACK_COUNT * RACK_BAYS * RACK_DEEP                  # 108 (nivel 1)
RACK_UPPER = RACK_TOTAL - RACK_L1                            # 324 (niveles 2-4)

ZONE_CAP = {
    "Rack Nivel 1 (Tapas Balde)": RACK_L1,
    "Rack Niveles Superiores": RACK_UPPER,
    "Pampa Cilindros": 166,
    "Mezanine 1 (Bot. 4L)": 146,
    "Mezanine 2 (Colapso)": 47,
    "Sótano (Lejana)": 250,
    "ILB": 60,
}
ZONE_LIST = list(ZONE_CAP.keys())
TOTAL_POSITIONS = sum(ZONE_CAP.values())   # 1101

# ---- Reglas de colocación (footprint = posiciones de piso por pallet)
# Cilindro: apilable 2 de alto  -> 0.5 posición por pallet
# Tapa Lt/Gl en ILB: apilable 3 de alto -> 1/3 de posición por pallet
PLACEMENT = {
    "tapa_balde": [("Rack Nivel 1 (Tapas Balde)", 1.0), ("Rack Niveles Superiores", 1.0),
                   ("Mezanine 2 (Colapso)", 1.0), ("Sótano (Lejana)", 1.0), ("ILB", 1.0)],
    "balde":      [("Rack Niveles Superiores", 1.0), ("Mezanine 2 (Colapso)", 1.0),
                   ("Sótano (Lejana)", 1.0), ("ILB", 1.0)],
    "bot1l":      [("Rack Niveles Superiores", 1.0), ("Mezanine 2 (Colapso)", 1.0),
                   ("Sótano (Lejana)", 1.0), ("ILB", 1.0)],
    "bot4l":      [("Mezanine 1 (Bot. 4L)", 1.0), ("Mezanine 2 (Colapso)", 1.0),
                   ("Sótano (Lejana)", 1.0), ("ILB", 1.0)],
    "caja":       [("Sótano (Lejana)", 1.0), ("ILB", 1.0), ("Mezanine 2 (Colapso)", 1.0)],
    "tapa_lg":    [("Sótano (Lejana)", 1.0), ("ILB", 1.0 / 3.0), ("Mezanine 2 (Colapso)", 1.0)],
    "cilindro":   [("Pampa Cilindros", 0.5)],
}
# Orden en que se colocan las categorías cada minuto (prioridad de zonas compartidas)
PLACEMENT_ORDER = ["cilindro", "bot4l", "tapa_balde", "balde", "bot1l", "caja", "tapa_lg"]

# ---- Consumo diario de líneas (pallets/día) ------------------------
CONS_DAILY = {
    "Balde 19L": 13, "Tapa 19L": 7,        # Línea Baldes 1
    "Balde 2.5USG": 8, "Tapa 2.5USG": 3,   # Línea Baldes 2
    "Botella 1L": 10, "Tapa Lt": 0.5,      # Línea Botellas 1L (+ cajas derivadas)
    "Botella 4L": 12, "Tapa Gl": 0.3,      # Línea Botellas 4L
    "Cilindro": 146,                       # Línea Cilindros (sin operario)
}
LINE_ITEMS = {
    "Línea Baldes 1":   ["Balde 19L", "Tapa 19L"],
    "Línea Baldes 2":   ["Balde 2.5USG", "Tapa 2.5USG"],
    "Línea Botellas 1L":["Botella 1L", "Tapa Lt", "Caja 12x1L"],
    "Línea Botellas 4L":["Botella 4L", "Tapa Gl"],
    "Línea Cilindros":  ["Cilindro"],
}

# ---- Entradas (inbound) por proveedor: total diario y nº de viajes --
IDE_DAILY = {"Balde 19L": 38, "Tapa 19L": 22, "Balde 2.5USG": 24,
             "Tapa 2.5USG": 8, "Tapa Lt": 2, "Tapa Gl": 2}      # = 96 pallets/día
SMASAC_DAILY = {"Botella 1L": 28, "Botella 4L": 30, "Caja 12x1L": 6}  # = 64 pallets/día
IDE_TRIPS, SMASAC_TRIPS, REYEMSA_TRIPS = 4, 4, 4
REYEMSA_CIL_PER_TRIP = 50                                        # 200 cilindros = 50 pallets

# ---- Mezcla para inventario inicial (fracción de posiciones) -------
INIT_MIX = {
    "Cilindro": 0.14, "Balde 19L": 0.13, "Balde 2.5USG": 0.09,
    "Tapa 19L": 0.07, "Tapa 2.5USG": 0.05, "Botella 1L": 0.12,
    "Botella 4L": 0.12, "Caja 12x1L": 0.06, "Tapa Lt": 0.05, "Tapa Gl": 0.05,
}

# ---- Umbrales de alerta --------------------------------------------
UMBRAL_AMARILLO = 70.0
UMBRAL_ROJO = 90.0


# =====================================================================
# 2. MOTOR DE ZONIFICACIÓN
# =====================================================================
def allocate_zones(inv):
    """Coloca TODO el inventario actual en zonas según prioridad.
    Devuelve (uso_por_zona_en_posiciones, pallets_en_zona_ficticia)."""
    used = {z: 0.0 for z in ZONE_CAP}
    fict = 0.0
    for cat in PLACEMENT_ORDER:
        plan = PLACEMENT[cat]
        for it in ITEMS_BY_CAT[cat]:
            remaining = inv.get(it, 0.0)
            if remaining <= 1e-9:
                continue
            for zone, fp in plan:
                free = ZONE_CAP[zone] - used[zone]
                if free <= 1e-9:
                    continue
                can_fit = free / fp
                take = min(remaining, can_fit)
                used[zone] += take * fp
                remaining -= take
                if remaining <= 1e-9:
                    break
            if remaining > 1e-9:        # no cupo en ninguna zona -> riesgo
                fict += remaining
    return used, fict


def build_initial_inventory(pct):
    """Inventario inicial aproximado para una ocupación objetivo (%)."""
    target_pos = (pct / 100.0) * TOTAL_POSITIONS
    inv = {it: 0.0 for it in ALL_ITEMS}
    for it, frac in INIT_MIX.items():
        fp = 0.5 if it == "Cilindro" else 1.0
        inv[it] = (target_pos * frac) / fp
    return inv


def trip_sched(n, lo=0, hi=PROD_END, offset=0):
    """Reparte n viajes de forma escalonada dentro de [lo, hi]."""
    return [int(min(hi, max(lo, lo + (hi - lo) * (i + 0.5) / n + offset)))
            for i in range(n)]


# =====================================================================
# 3. SIMULACIÓN MINUTO A MINUTO
# =====================================================================
@st.cache_data(show_spinner=False)
def run_simulation(initial_pct, growth, operator_unloads_cyl=False, buffer_pallets=4.0):
    inv = build_initial_inventory(initial_pct)

    # Tasas de consumo por minuto (pallets/min) durante la ventana de producción
    prod_min = PROD_END - PROD_START
    cons = {it: d * growth / prod_min for it, d in CONS_DAILY.items()}
    caja_daily = (CONS_DAILY["Botella 1L"] * UNITS_PER_PALLET["Botella 1L"]
                  / 12 / UNITS_PER_PALLET["Caja 12x1L"])         # cajas ~ proporcional a 1L
    cons["Caja 12x1L"] = caja_daily * growth / prod_min
    # Carga de abastecimiento del operario = todo el consumo excepto cilindros
    allowed_supply_rate = sum(r for it, r in cons.items() if it != "Cilindro")

    # Programación de llegadas de camiones
    arrivals = defaultdict(list)
    for tt in trip_sched(IDE_TRIPS, offset=0):
        adds = {k: v / IDE_TRIPS * growth for k, v in IDE_DAILY.items()}
        arrivals[tt].append(("IDE", adds, sum(adds.values()), True))
    for tt in trip_sched(SMASAC_TRIPS, offset=90):
        adds = {k: v / SMASAC_TRIPS * growth for k, v in SMASAC_DAILY.items()}
        arrivals[tt].append(("SMASAC", adds, sum(adds.values()), True))
    for tt in trip_sched(REYEMSA_TRIPS, offset=45):
        adds = {"Cilindro": REYEMSA_CIL_PER_TRIP * growth}
        arrivals[tt].append(("REYEMSA", adds, REYEMSA_CIL_PER_TRIP * growth,
                             operator_unloads_cyl))

    # Estado del operario
    supply_backlog = 0.0
    unload_due = 0.0
    t_supply = t_unload = t_idle = 0.0
    overloaded = False

    # Contadores de quiebres
    line_short = {ln: False for ln in LINE_ITEMS}
    quiebre_line = {ln: 0 for ln in LINE_ITEMS}
    quiebre_overload = 0

    # Registros
    rec = {"minute": [], "tiempo": [], "total_pct": [], "fict": [],
           "s_min": [], "u_min": [], "i_min": []}
    for z in ZONE_LIST:
        rec[z] = []

    op_cap = 1.0 / MIN_X_PALLET   # pallets que el operario puede mover por minuto

    for t in range(PROD_START, PROD_END + 1):
        in_t1 = (t <= T1_END)

        # --- Llegadas de camiones ---
        if t in arrivals:
            for _name, adds, pallets, op_handled in arrivals[t]:
                for k, v in adds.items():
                    inv[k] = inv.get(k, 0.0) + v
                if in_t1 and op_handled:
                    unload_due += pallets

        # --- Consumo de líneas (drena inventario) ---
        for it, r in cons.items():
            if r <= 0:
                continue
            inv[it] -= r
            if inv[it] < 0:
                inv[it] = 0.0
        # Detección de quiebre por desabasto de bodega
        for ln, items in LINE_ITEMS.items():
            short = any((it in cons and cons[it] > 0 and inv.get(it, 0.0) <= 1e-9)
                        for it in items)
            if short and not line_short[ln]:
                quiebre_line[ln] += 1
            line_short[ln] = short

        # --- Operario 1 (solo Turno 1) ---
        s_min = u_min = i_min = 0.0
        if in_t1:
            supply_backlog += allowed_supply_rate
            cap = op_cap
            served_s = min(cap, supply_backlog)
            supply_backlog -= served_s
            cap -= served_s
            served_u = min(cap, unload_due)
            unload_due -= served_u
            cap -= served_u
            idle = cap
            s_min, u_min, i_min = served_s * MIN_X_PALLET, served_u * MIN_X_PALLET, idle * MIN_X_PALLET
            t_supply += s_min
            t_unload += u_min
            t_idle += i_min
            # Quiebre por sobrecarga del operario (las líneas esperan demasiado)
            if supply_backlog > buffer_pallets and not overloaded:
                quiebre_overload += 1
                overloaded = True
            elif supply_backlog <= buffer_pallets:
                overloaded = False

        # --- Registro de ocupación ---
        used, fict = allocate_zones(inv)
        rec["minute"].append(t)
        rec["tiempo"].append(BASE_DATE + timedelta(minutes=t))
        total_used = sum(used.values())
        rec["total_pct"].append(100.0 * total_used / TOTAL_POSITIONS)
        rec["fict"].append(fict)
        rec["s_min"].append(s_min)
        rec["u_min"].append(u_min)
        rec["i_min"].append(i_min)
        for z in ZONE_LIST:
            rec[z].append(100.0 * used[z] / ZONE_CAP[z])

    df = pd.DataFrame(rec)

    t1_minutes = T1_END - T1_START + 1
    summary = {
        "peak_total": float(df["total_pct"].max()),
        "final_total": float(df["total_pct"].iloc[-1]),
        "max_fict": float(df["fict"].max()),
        "final_fict": float(df["fict"].iloc[-1]),
        "t_supply": t_supply,
        "t_unload": t_unload,
        "t_idle": t_idle,
        "utilizacion": 100.0 * (t_supply + t_unload) / t1_minutes,
        "unload_backlog": unload_due,
        "supply_backlog": supply_backlog,
        "quiebre_line": quiebre_line,
        "quiebre_overload": quiebre_overload,
        "quiebres_total": sum(quiebre_line.values()) + quiebre_overload,
        "growth": growth,
    }
    return df, summary


# =====================================================================
# 4. GRÁFICOS (Plotly)
# =====================================================================
PALETTE = ["#2E86C1", "#28B463", "#CA6F1E", "#884EA0",
           "#C0392B", "#7D6608", "#117A65"]


def fig_ocupacion(df):
    fig = go.Figure()
    for i, z in enumerate(ZONE_LIST):
        fig.add_trace(go.Scatter(
            x=df["tiempo"], y=df[z], mode="lines", name=z,
            line=dict(width=1.6, color=PALETTE[i % len(PALETTE)]),
            hovertemplate="%{y:.1f}%<extra>" + z + "</extra>"))
    fig.add_trace(go.Scatter(
        x=df["tiempo"], y=df["total_pct"], mode="lines", name="TOTAL bodega",
        line=dict(width=4, color="#1B2631"),
        hovertemplate="%{y:.1f}%<extra>TOTAL</extra>"))
    fig.add_hline(y=UMBRAL_AMARILLO, line_dash="dot", line_color="#E1B12C",
                  annotation_text="70% óptimo", annotation_position="right")
    fig.add_hline(y=UMBRAL_ROJO, line_dash="dot", line_color="#C0392B",
                  annotation_text="90% crítico", annotation_position="right")
    fig.update_layout(
        height=460, hovermode="x unified",
        yaxis_title="Ocupación (%)", xaxis_title="Hora",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=10, r=10, t=30, b=10))
    fig.update_yaxes(range=[0, max(105, df["total_pct"].max() + 5)])
    return fig


def fig_operario_tiempo(df):
    d = df[df["minute"] <= T1_END].copy().set_index("tiempo")
    g = d[["s_min", "u_min", "i_min"]].resample("30min").sum()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=g.index, y=g["s_min"], name="Abastecer líneas",
                         marker_color="#28B463"))
    fig.add_trace(go.Bar(x=g.index, y=g["u_min"], name="Descargar/guardar camiones",
                         marker_color="#2E86C1"))
    fig.add_trace(go.Bar(x=g.index, y=g["i_min"], name="Tiempo muerto",
                         marker_color="#BDC3C7"))
    fig.update_layout(
        barmode="stack", height=420,
        yaxis_title="Minutos por bloque de 30 min", xaxis_title="Hora (Turno 1)",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
        margin=dict(l=10, r=10, t=30, b=10))
    return fig


def fig_operario_dona(summary):
    fig = go.Figure(go.Pie(
        labels=["Abastecer líneas", "Descargar camiones", "Tiempo muerto"],
        values=[summary["t_supply"], summary["t_unload"], summary["t_idle"]],
        hole=0.55, marker_colors=["#28B463", "#2E86C1", "#BDC3C7"],
        textinfo="label+percent"))
    fig.update_layout(height=420, showlegend=False,
                      margin=dict(l=10, r=10, t=10, b=10),
                      annotations=[dict(text="Operario 1<br>Turno 1", showarrow=False,
                                        font_size=13)])
    return fig


def fig_zonas_final(df):
    last = df.iloc[-1]
    vals = [last[z] for z in ZONE_LIST]
    colors = ["#C0392B" if v > UMBRAL_ROJO else "#E1B12C" if v > UMBRAL_AMARILLO
              else "#28B463" for v in vals]
    fig = go.Figure(go.Bar(
        x=vals, y=ZONE_LIST, orientation="h", marker_color=colors,
        text=[f"{v:.0f}%" for v in vals], textposition="outside"))
    fig.update_layout(height=360, xaxis_title="Ocupación al cierre (%)",
                      margin=dict(l=10, r=10, t=10, b=10))
    fig.update_xaxes(range=[0, 110])
    fig.add_vline(x=UMBRAL_AMARILLO, line_dash="dot", line_color="#E1B12C")
    fig.add_vline(x=UMBRAL_ROJO, line_dash="dot", line_color="#C0392B")
    return fig


# =====================================================================
# 5. INTERFAZ STREAMLIT
# =====================================================================
def main():
    st.set_page_config(page_title="Simulador Bodega · Operario 1",
                       page_icon="📦", layout="wide")

    st.title("📦 Simulador de Bodega — Operario 1")
    st.caption("Control tridimensional: **Inventario · Espacio · Riesgo** · "
               "Simulación minuto a minuto (24 h) con foco en el Turno 1.")

    # ---------------- Sidebar: controles ----------------
    with st.sidebar:
        st.header("⚙️ Parámetros")
        initial_pct = st.slider("Ocupación inicial de la bodega (%)",
                                0, 100, 45, step=5,
                                help="Carga aproximada con la que arranca el día.")
        crecimiento = st.slider("Multiplicador de crecimiento macro (%)",
                                0, 100, 0, step=5,
                                help="Escala llegadas de camiones y consumo de líneas.")
        growth = 1.0 + crecimiento / 100.0
        st.metric("Factor aplicado", f"x{growth:.2f}")

        operator_cyl = st.checkbox("El Operario 1 también descarga cilindros (REYEMSA)",
                                   value=False)
        buffer_pallets = st.slider("Tolerancia de espera de líneas (pallets)",
                                   1.0, 10.0, 4.0, step=0.5,
                                   help="Backlog máximo antes de contar un quiebre por "
                                        "sobrecarga del operario.")
        st.divider()
        st.caption("El modelo recalcula automáticamente al mover cualquier control.")

    # ---------------- Ejecutar simulación ----------------
    df, S = run_simulation(initial_pct, growth, operator_cyl, buffer_pallets)

    # ---------------- Banner de alerta global ----------------
    if S["max_fict"] > 0.5:
        st.error(f"🚨 **RIESGO DE SEGURIDAD** — Se usó la Zona Ficticia "
                 f"(máx. {S['max_fict']:.0f} pallets sin ubicación). La bodega "
                 f"desborda su capacidad física.")
    elif S["peak_total"] > UMBRAL_ROJO:
        st.error(f"🔴 Ocupación pico **{S['peak_total']:.0f}%** — supera el 90% (crítico).")
    elif S["peak_total"] > UMBRAL_AMARILLO:
        st.warning(f"🟡 Ocupación pico **{S['peak_total']:.0f}%** — supera el 70% (atención).")
    else:
        st.success(f"🟢 Ocupación pico **{S['peak_total']:.0f}%** — dentro del nivel óptimo.")

    # ---------------- Métricas clave ----------------
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Ocupación pico", f"{S['peak_total']:.0f}%")
    c2.metric("Ocupación al cierre", f"{S['final_total']:.0f}%")
    c3.metric("Quiebres de stock", f"{S['quiebres_total']}")
    c4.metric("Pallets en Zona Ficticia", f"{S['max_fict']:.0f}",
              help="Métrica de riesgo de seguridad (máximo del día).")
    c5.metric("Utilización Operario 1", f"{S['utilizacion']:.0f}%")

    st.divider()

    # ---------------- Gráfico 1: ocupación por zonas ----------------
    st.subheader("1 · Ocupación de la bodega por zonas a lo largo del tiempo")
    st.plotly_chart(fig_ocupacion(df), use_container_width=True)

    # ---------------- Gráfico 2: estado del operario ----------------
    st.subheader("2 · Estado del Operario 1 (Turno 1)")
    g1, g2 = st.columns([1.4, 1])
    with g1:
        st.markdown("**Distribución de tiempo por bloques de 30 min**")
        st.plotly_chart(fig_operario_tiempo(df), use_container_width=True)
    with g2:
        st.markdown("**Resumen del turno**")
        st.plotly_chart(fig_operario_dona(S), use_container_width=True)
    if S["unload_backlog"] > 1:
        st.warning(f"⚠️ El operario terminó el turno con **{S['unload_backlog']:.0f} pallets "
                   f"sin descargar**: la prioridad de abastecimiento le impidió cerrar "
                   f"toda la recepción de camiones.")

    st.divider()

    # ---------------- Gráfico 3: zonas al cierre + quiebres ----------------
    g3, g4 = st.columns(2)
    with g3:
        st.subheader("3 · Ocupación por zona al cierre")
        st.plotly_chart(fig_zonas_final(df), use_container_width=True)
    with g4:
        st.subheader("4 · Detalle de quiebres de stock")
        rows = [{"Origen": ln, "Quiebres": n} for ln, n in S["quiebre_line"].items()]
        rows.append({"Origen": "Sobrecarga del operario", "Quiebres": S["quiebre_overload"]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        st.metric("Backlog de abastecimiento al cierre",
                  f"{S['supply_backlog']:.1f} pallets")

    # ---------------- Supuestos ----------------
    with st.expander("📘 Acerca del modelo y supuestos"):
        st.markdown(f"""
**Turnos.** Turno 1 (Operario 1): 07:00–16:45. Turno 2: 16:30–02:15
(solapamiento de 15 min). La producción consume inventario durante toda la
ventana de turnos ({PROD_END} min).

**Capacidad de zonas (posiciones de piso), total = {TOTAL_POSITIONS}:**
""")
        st.dataframe(pd.DataFrame(
            [{"Zona": z, "Capacidad": c} for z, c in ZONE_CAP.items()]),
            use_container_width=True, hide_index=True)
        st.markdown(f"""
- **Rack:** {RACK_COUNT}×{RACK_BAYS}×{RACK_DEEP}×{RACK_HIGH} = {RACK_TOTAL} pallets.
  Nivel 1 ({RACK_L1}) exclusivo para tapas de balde; niveles superiores ({RACK_UPPER})
  para baldes, tapas de balde y botellas de 1L.
- **Pampa Cilindros:** cilindros apilados 2 de alto (0.5 posición/pallet).
- **ILB:** tapas Lt/Gl apiladas 3 de alto (1/3 de posición/pallet).
- **Desborde:** zona óptima → Mezanine 2 (colapso) → Sótano (emergencia) → ILB →
  **Zona Ficticia** (dispara el indicador de riesgo de seguridad).

**Operario 1.** 3 min por pallet (descarga y abastecimiento). Regla de prioridad
absoluta: el abastecimiento de líneas (excepto cilindros) interrumpe la descarga.
Las cajas se consumen en proporción a las botellas de 1L.

**Quiebres de stock.** Se cuentan cuando una línea se queda sin inventario en
bodega, o cuando el backlog de abastecimiento supera la tolerancia configurada
(operario sobrecargado).
""")


# Streamlit ejecuta el script como __main__
if __name__ == "__main__":
    main()
