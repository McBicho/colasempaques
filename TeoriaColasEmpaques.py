# -*- coding: utf-8 -*-
"""
=====================================================================
 SIMULADOR DE BODEGA Y FLUJO DEL OPERARIO 1
 Control tridimensional: INVENTARIO · ESPACIO · RIESGO
 + Carga de stock inicial real desde reporte SAP MB5B
---------------------------------------------------------------------
 Requisitos (requirements.txt): streamlit, plotly, pandas, openpyxl
 Ejecutar:   streamlit run streamlit_app.py
=====================================================================
"""

import io
import math
import pandas as pd
import streamlit as st
import plotly.graph_objects as go
from datetime import datetime, timedelta
from collections import defaultdict

# =====================================================================
# 1. PARÁMETROS Y CONSTANTES
# =====================================================================
T1_START, T1_END = 0, 585        # Turno 1: 07:00 - 16:45
T2_START, T2_END = 570, 1155     # Turno 2: 16:30 - 02:15 (solapamiento 570-585)
PROD_START, PROD_END = 0, 1155   # Ventana de producción
MIN_STORE_DEF = 3.0              # min/pallet por defecto: guardar/descargar camión
MIN_SUPPLY_DEF = 3.0             # min/pallet por defecto: abastecer línea
LUNCH_START, LUNCH_END = 300, 345   # Almuerzo Operario 1: 12:00 - 12:45 (45 min)
BASE_DATE = datetime(2024, 1, 1, 7, 0)

UNITS_PER_PALLET = {
    "Balde 19L": 240, "Tapa 19L": 800,
    "Balde 2.5USG": 400, "Tapa 2.5USG": 1160,
    "Botella 1L": 1584, "Caja 12x1L": 750,
    "Botella 4L": 480, "Cilindro": 4,
    "Tapa Lt": 800, "Tapa Gl": 800,
    "Otros (genérico)": 1,
}
ALL_ITEMS = list(UNITS_PER_PALLET.keys())

ITEM_CATEGORY = {
    "Balde 19L": "balde", "Balde 2.5USG": "balde",
    "Tapa 19L": "tapa_balde", "Tapa 2.5USG": "tapa_balde",
    "Botella 1L": "bot1l", "Caja 12x1L": "caja",
    "Botella 4L": "bot4l", "Cilindro": "cilindro",
    "Tapa Lt": "tapa_lg", "Tapa Gl": "tapa_lg",
    "Otros (genérico)": "otros",
}
ITEMS_BY_CAT = defaultdict(list)
for _it, _c in ITEM_CATEGORY.items():
    ITEMS_BY_CAT[_c].append(_it)

RACK_COUNT, RACK_BAYS, RACK_DEEP, RACK_HIGH = 2, 18, 3, 4
RACK_TOTAL = RACK_COUNT * RACK_BAYS * RACK_DEEP * RACK_HIGH   # 432
RACK_L1 = RACK_COUNT * RACK_BAYS * RACK_DEEP                  # 108
RACK_UPPER = RACK_TOTAL - RACK_L1                            # 324

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
    "otros":      [("Mezanine 2 (Colapso)", 1.0), ("Sótano (Lejana)", 1.0), ("ILB", 1.0)],
}
PLACEMENT_ORDER = ["cilindro", "bot4l", "tapa_balde", "balde", "bot1l", "caja", "tapa_lg", "otros"]

# Item representativo por categoría (para volcar el stock real al motor dinámico)
CAT_REP = {
    "cilindro": "Cilindro", "balde": "Balde 19L", "tapa_balde": "Tapa 19L",
    "bot1l": "Botella 1L", "bot4l": "Botella 4L", "caja": "Caja 12x1L",
    "tapa_lg": "Tapa Gl", "otros": "Otros (genérico)",
}
CAT_OPTIONS = ["cilindro", "balde", "tapa_balde", "bot1l", "bot4l",
               "caja", "tapa_lg", "otros", "(ignorar)"]

# Mapeo por defecto Tipo SAP -> (categoría, unidades por pallet). EDITABLE en la app.
# OJO: las unidades por pallet de liners/tapas son ESTIMADAS; verifícalas con tu maestro.
SAP_DEFAULT_MAP = {
    "Cilindro":      ("cilindro", 4),
    "Base 19L":      ("balde", 240),
    "Base 2.5 USG":  ("balde", 400),
    "Botella 1L":    ("bot1l", 1584),
    "Botella 4L":    ("bot4l", 480),
    "Caja":          ("caja", 750),
    "Tapa 19L":      ("tapa_balde", 800),
    "Tapa 2.5 USG":  ("tapa_balde", 1160),
    "Tapa Gln":      ("tapa_lg", 18900),
    "Tapa Lt":       ("tapa_lg", 31500),
    # --- Tipos excluidos por defecto (fuera del alcance de esta bodega) ---
    "Adhesivo":      ("(ignorar)", 999999),
    "IBC CART":      ("(ignorar)", 999999),
    "MISC":          ("(ignorar)", 999999),
    "Pallet":        ("(ignorar)", 999999),
    "DoyPack":       ("(ignorar)", 999999),
    "Botella 0.5L":  ("(ignorar)", 999999),
    "Bombona":       ("(ignorar)", 999999),
    "Etiquetas":     ("(ignorar)", 999999),
    "Pote 1Lb":      ("(ignorar)", 999999),
    "Contenedor":    ("(ignorar)", 999999),
    "Manga Sachet":  ("(ignorar)", 999999),
    "Cinta":         ("(ignorar)", 999999),
    "Funda Pote":    ("(ignorar)", 999999),
    "Tapa Bot Cilíndrica": ("(ignorar)", 999999),
    "Tapa Bombona":  ("(ignorar)", 999999),
}
# Búsqueda normalizada (sin distinguir mayúsculas/espacios)
SAP_DEFAULT_NORM = {k.strip().lower(): v for k, v in SAP_DEFAULT_MAP.items()}

CONS_DAILY = {
    "Balde 19L": 13, "Tapa 19L": 7,
    "Balde 2.5USG": 8, "Tapa 2.5USG": 3,
    "Botella 1L": 10, "Tapa Lt": 0.5,
    "Botella 4L": 12, "Tapa Gl": 0.3,
    "Cilindro": 146,
}
LINE_ITEMS = {
    "Línea Baldes 1":   ["Balde 19L", "Tapa 19L"],
    "Línea Baldes 2":   ["Balde 2.5USG", "Tapa 2.5USG"],
    "Línea Botellas 1L":["Botella 1L", "Tapa Lt", "Caja 12x1L"],
    "Línea Botellas 4L":["Botella 4L", "Tapa Gl"],
    "Línea Cilindros":  ["Cilindro"],
}

# Composición (proporción) por viaje. Se escala al total de pallets/viaje indicado en la app.
IDE_MIX = {"Balde 19L": 9.5, "Tapa 19L": 5.5, "Balde 2.5USG": 6.0,
           "Tapa 2.5USG": 2.0, "Tapa Lt": 0.5, "Tapa Gl": 0.5}
SMASAC_MIX = {"Botella 1L": 7.0, "Botella 4L": 7.5, "Caja 12x1L": 1.5}
IDE_BASE = sum(IDE_MIX.values())        # 24
SMASAC_BASE = sum(SMASAC_MIX.values())  # 16
# Defaults editables en la interfaz
IDE_TRIPS_DEF, SMASAC_TRIPS_DEF, REYEMSA_TRIPS_DEF = 4, 4, 4
IDE_PALLETS_DEF, SMASAC_PALLETS_DEF, REYEMSA_PALLETS_DEF = 15, 14, 50

INIT_MIX = {
    "Cilindro": 0.14, "Balde 19L": 0.13, "Balde 2.5USG": 0.09,
    "Tapa 19L": 0.07, "Tapa 2.5USG": 0.05, "Botella 1L": 0.12,
    "Botella 4L": 0.12, "Caja 12x1L": 0.06, "Tapa Lt": 0.05, "Tapa Gl": 0.05,
}

UMBRAL_AMARILLO = 70.0
UMBRAL_ROJO = 90.0
PALETTE = ["#2E86C1", "#28B463", "#CA6F1E", "#884EA0", "#C0392B", "#7D6608", "#117A65"]


# =====================================================================
# 2. MOTOR DE ZONIFICACIÓN
# =====================================================================
def allocate_zones(inv):
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
                take = min(remaining, free / fp)
                used[zone] += take * fp
                remaining -= take
                if remaining <= 1e-9:
                    break
            if remaining > 1e-9:
                fict += remaining
    return used, fict


def build_initial_inventory(pct):
    target_pos = (pct / 100.0) * TOTAL_POSITIONS
    inv = {it: 0.0 for it in ALL_ITEMS}
    for it, frac in INIT_MIX.items():
        fp = 0.5 if it == "Cilindro" else 1.0
        inv[it] = (target_pos * frac) / fp
    return inv


def inv_from_items(init_items):
    inv = {it: 0.0 for it in ALL_ITEMS}
    for k, v in init_items:
        inv[k] = inv.get(k, 0.0) + float(v)
    return inv


def trip_sched(n, lo=0, hi=PROD_END, offset=0):
    # Primer camión al inicio del turno (07:00) y luego repartidos hacia adelante.
    if n <= 0:
        return []
    return [int(min(hi, max(lo, lo + (hi - lo) * i / n + offset))) for i in range(n)]


# =====================================================================
# 3. SIMULACIÓN MINUTO A MINUTO
# =====================================================================
@st.cache_data(show_spinner=False)
def run_simulation(initial_pct, growth, operator_unloads_cyl=False,
                   buffer_pallets=4.0, init_items=None,
                   ide_trips=4, smasac_trips=4, reyemsa_trips=4,
                   min_store=3.0, min_supply=3.0,
                   ide_pallets=15.0, smasac_pallets=14.0, reyemsa_pallets=50.0,
                   lunch_break=True):
    inv = inv_from_items(init_items) if init_items is not None else build_initial_inventory(initial_pct)

    # Los consumos de CONS_DAILY son POR TURNO. Las líneas de baldes y botellas operan en
    # AMBOS turnos; la línea de cilindros opera SOLO en el turno día (T1).
    lunch_minutes = (LUNCH_END - LUNCH_START) if lunch_break else 0
    day_min = (T1_END - T1_START + 1) - lunch_minutes      # min productivos turno día
    night_min = (PROD_END - T1_END)                        # min productivos turno noche
    caja_shift = (CONS_DAILY["Botella 1L"] * UNITS_PER_PALLET["Botella 1L"]
                  / 12 / UNITS_PER_PALLET["Caja 12x1L"])    # cajas por turno (prop. a Bot. 1L)
    per_shift = dict(CONS_DAILY); per_shift["Caja 12x1L"] = caja_shift
    rate_day = {it: v * growth / day_min for it, v in per_shift.items()}
    rate_night = {it: (0.0 if it == "Cilindro" else v * growth / night_min)
                  for it, v in per_shift.items()}

    arrivals = defaultdict(list)
    ide_f = (ide_pallets / IDE_BASE) * growth
    sma_f = (smasac_pallets / SMASAC_BASE) * growth
    for tt in trip_sched(ide_trips, offset=0):
        adds = {k: v * ide_f for k, v in IDE_MIX.items()}
        arrivals[tt].append((adds, sum(adds.values()), True))
    for tt in trip_sched(smasac_trips, offset=90):
        adds = {k: v * sma_f for k, v in SMASAC_MIX.items()}
        arrivals[tt].append((adds, sum(adds.values()), True))
    for tt in trip_sched(reyemsa_trips, offset=45):
        adds = {"Cilindro": reyemsa_pallets * growth}
        arrivals[tt].append((adds, reyemsa_pallets * growth, operator_unloads_cyl))

    supply_backlog = unload_due = 0.0
    t_supply = t_unload = t_idle = t_lunch = 0.0
    overloaded = False
    line_short = {ln: False for ln in LINE_ITEMS}
    quiebre_line = {ln: 0 for ln in LINE_ITEMS}
    quiebre_overload = 0
    quiebre_events = []

    # Abastecimiento por línea (solo las que atiende el Operario 1)
    LINES_OP = ["Línea Baldes 1", "Línea Baldes 2", "Línea Botellas 1L", "Línea Botellas 4L"]
    LINE_KEYS = {"Línea Baldes 1": "sup_b1", "Línea Baldes 2": "sup_b2",
                 "Línea Botellas 1L": "sup_1l", "Línea Botellas 4L": "sup_4l"}
    line_rate = {ln: sum(rate_day.get(it, 0.0) for it in LINE_ITEMS[ln]) for ln in LINES_OP}
    line_backlog = {ln: 0.0 for ln in LINES_OP}
    cum_supplied = {ln: 0.0 for ln in LINES_OP}

    rec = {"minute": [], "tiempo": [], "total_pct": [], "fict": [],
           "s_min": [], "u_min": [], "i_min": [], "l_min": [],
           "sup_b1": [], "sup_b2": [], "sup_1l": [], "sup_4l": []}
    for z in ZONE_LIST:
        rec[z] = []
    for t in range(PROD_START, PROD_END + 1):
        in_t1 = (t <= T1_END)
        on_lunch = lunch_break and (LUNCH_START <= t < LUNCH_END)
        if t in arrivals:
            for adds, pallets, op_handled in arrivals[t]:
                for k, v in adds.items():
                    inv[k] = inv.get(k, 0.0) + v
                if in_t1 and op_handled:
                    unload_due += pallets

        if not on_lunch:                      # las líneas no producen durante el almuerzo
            rates = rate_day if in_t1 else rate_night    # cilindros solo consumen de día
            for it, r in rates.items():
                if r <= 0:
                    continue
                inv[it] -= r
                if inv[it] < 0:
                    inv[it] = 0.0
            for ln, items in LINE_ITEMS.items():
                ceros = [it for it in items if rates.get(it, 0.0) > 0 and inv.get(it, 0.0) <= 1e-9]
                short = len(ceros) > 0
                if short and not line_short[ln]:
                    quiebre_line[ln] += 1
                    quiebre_events.append({
                        "Hora": (BASE_DATE + timedelta(minutes=t)).strftime("%H:%M"),
                        "Línea": ln, "Causa": "Sin stock en bodega",
                        "Detalle": ", ".join(ceros)})
                line_short[ln] = short

        s_min = u_min = i_min = l_min = 0.0
        if in_t1:
            if on_lunch:
                l_min = 1.0
                t_lunch += 1.0
            else:
                for ln in LINES_OP:
                    line_backlog[ln] += line_rate[ln]
                supply_backlog = sum(line_backlog.values())
                time_left = 1.0  # 1 minuto disponible por iteración
                # 1) Prioridad absoluta: abastecer líneas (min_supply por pallet)
                cap_s = time_left / max(min_supply, 1e-9)
                served_s = min(supply_backlog, cap_s)
                if supply_backlog > 1e-12:        # repartir lo servido entre líneas según su cola
                    for ln in LINES_OP:
                        s_ln = served_s * (line_backlog[ln] / supply_backlog)
                        line_backlog[ln] -= s_ln
                        cum_supplied[ln] += s_ln
                supply_backlog -= served_s
                time_left -= served_s * min_supply
                # 2) Descargar/guardar camiones (min_store por pallet)
                cap_u = time_left / max(min_store, 1e-9)
                served_u = min(unload_due, cap_u)
                unload_due -= served_u
                time_left -= served_u * min_store
                s_min, u_min, i_min = served_s * min_supply, served_u * min_store, time_left
                t_supply += s_min; t_unload += u_min; t_idle += i_min
            if supply_backlog > buffer_pallets and not overloaded:
                quiebre_overload += 1; overloaded = True
                quiebre_events.append({
                    "Hora": (BASE_DATE + timedelta(minutes=t)).strftime("%H:%M"),
                    "Línea": "Abastecimiento (operario)", "Causa": "Sobrecarga del operario",
                    "Detalle": f"cola {supply_backlog:.1f} pallets > tolerancia {buffer_pallets:.0f}"})
            elif supply_backlog <= buffer_pallets:
                overloaded = False

        used, fict = allocate_zones(inv)
        rec["minute"].append(t)
        rec["tiempo"].append(BASE_DATE + timedelta(minutes=t))
        rec["total_pct"].append(100.0 * sum(used.values()) / TOTAL_POSITIONS)
        rec["fict"].append(fict)
        rec["s_min"].append(s_min); rec["u_min"].append(u_min)
        rec["i_min"].append(i_min); rec["l_min"].append(l_min)
        for ln, key in LINE_KEYS.items():
            rec[key].append(cum_supplied[ln])
        for z in ZONE_LIST:
            rec[z].append(100.0 * used[z] / ZONE_CAP[z])

    df = pd.DataFrame(rec)
    t1_minutes = T1_END - T1_START + 1
    work_minutes = max(1.0, t1_minutes - t_lunch)
    summary = {
        "peak_total": float(df["total_pct"].max()),
        "final_total": float(df["total_pct"].iloc[-1]),
        "max_fict": float(df["fict"].max()), "final_fict": float(df["fict"].iloc[-1]),
        "t_supply": t_supply, "t_unload": t_unload, "t_idle": t_idle, "t_lunch": t_lunch,
        "utilizacion": 100.0 * (t_supply + t_unload) / work_minutes,
        "unload_backlog": unload_due, "supply_backlog": supply_backlog,
        "quiebre_line": quiebre_line, "quiebre_overload": quiebre_overload,
        "quiebres_total": sum(quiebre_line.values()) + quiebre_overload,
        "quiebre_events": quiebre_events, "growth": growth,
    }
    return df, summary


# =====================================================================
# 4. LECTURA DEL REPORTE SAP MB5B
# =====================================================================
def _find_col(cols, *keys):
    for c in cols:
        cl = str(c).lower()
        if all(k in cl for k in keys):
            return c
    return None


def parse_mb5b(uploaded):
    """Lee el reporte MB5B (CSV o Excel) y devuelve un DataFrame
    con columnas: Material, Texto, Tipo, Stock (ud)."""
    raw = uploaded.getvalue()
    name = uploaded.name.lower()
    df = None
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(raw))
    else:
        for sep in [None, ";", "\t", ","]:
            try:
                tmp = pd.read_csv(io.BytesIO(raw), sep=sep, engine="python", thousands=",")
                if tmp.shape[1] >= 3:
                    df = tmp
                    break
            except Exception:
                continue
    if df is None:
        raise ValueError("No se pudo leer el archivo. Expórtalo como CSV o XLSX.")

    df.columns = [str(c).strip() for c in df.columns]
    col_tipo = _find_col(df.columns, "tipo")
    col_stock = _find_col(df.columns, "stock", "inicial")
    col_mat = _find_col(df.columns, "material")
    col_desc = _find_col(df.columns, "texto") or _find_col(df.columns, "breve")
    if col_tipo is None or col_stock is None:
        raise ValueError("Faltan columnas 'Tipo' y/o 'Stock inicial'.")

    stock = df[col_stock]
    if stock.dtype == object:
        stock = pd.to_numeric(
            stock.astype(str).str.replace(",", "", regex=False).str.replace(" ", "", regex=False),
            errors="coerce")
    out = pd.DataFrame({
        "Material": df[col_mat].astype(str) if col_mat else "",
        "Texto": df[col_desc].astype(str) if col_desc else "",
        "Tipo": df[col_tipo].astype(str).str.strip(),
        "Stock (ud)": stock.fillna(0).astype(float),
    })
    out = out[out["Tipo"].str.lower() != "nan"]
    return out


def default_mapping(tipos):
    rows = []
    for tp in tipos:
        cat, udp = SAP_DEFAULT_NORM.get(str(tp).strip().lower(), ("otros", 1))
        rows.append({"Tipo": tp, "Categoría": cat, "Ud x pallet": float(udp)})
    return pd.DataFrame(rows)


def items_from_mapping(raw, mapping):
    """raw: df POR MATERIAL/SKU (Tipo, Stock (ud)). mapping: df Tipo, Categoría, Ud x pallet.
    El saldo de cada SKU (sobrante < 1 pallet) se redondea HACIA ARRIBA a un pallet entero,
    porque dos SKU distintos no comparten una posición de pallet."""
    m = mapping.set_index("Tipo")
    agg = defaultdict(float)
    det = {}
    for _, r in raw.iterrows():
        tp = r["Tipo"]
        stock = float(r["Stock (ud)"])
        if tp in m.index:
            cat = m.loc[tp, "Categoría"]; udp = float(m.loc[tp, "Ud x pallet"])
        else:
            cat, udp = "otros", 1.0
        if cat != "(ignorar)" and udp > 0 and stock > 0:
            pallets = int(math.ceil(stock / udp - 1e-9))   # saldo -> pallet entero
        else:
            pallets = 0
        if cat != "(ignorar)" and pallets > 0:
            agg[CAT_REP[cat]] += pallets
        d = det.setdefault(tp, {"Stock (ud)": 0.0, "SKUs": 0, "Pallets": 0})
        d["Stock (ud)"] += stock; d["SKUs"] += 1; d["Pallets"] += pallets
        d["Categoría"] = cat; d["Ud x pallet"] = udp
    detalle = pd.DataFrame([
        {"Tipo": tp, "Stock (ud)": v["Stock (ud)"], "SKUs": v["SKUs"],
         "Categoría": v["Categoría"], "Ud x pallet": v["Ud x pallet"], "Pallets": v["Pallets"]}
        for tp, v in det.items()])
    init_items = tuple(sorted((k, float(v)) for k, v in agg.items()))
    return init_items, detalle


# =====================================================================
# 5. GRÁFICOS PLOTLY
# =====================================================================
def fig_abastecimiento_animado(df):
    lines = ["Línea Baldes 1", "Línea Baldes 2", "Línea Botellas 1L", "Línea Botellas 4L"]
    cols = ["sup_b1", "sup_b2", "sup_1l", "sup_4l"]
    colors = ["#28B463", "#1E8449", "#2E86C1", "#5DADE2"]
    dT1 = df[df["minute"] <= T1_END]
    marks = list(range(0, T1_END + 1, 30))
    if marks[-1] != T1_END:
        marks.append(T1_END)
    frames = []
    for mk in marks:
        row = dT1[dT1["minute"] <= mk].iloc[-1]
        yv = [float(row[c]) for c in cols]
        ts = (BASE_DATE + timedelta(minutes=int(mk))).strftime("%H:%M")
        frames.append(go.Frame(name=ts, data=[go.Bar(
            x=lines, y=yv, marker_color=colors,
            text=[f"{v:.1f}" for v in yv], textposition="outside")]))
    maxy = max(1.0, float(dT1[cols].iloc[-1].max()) * 1.25)
    fig = go.Figure(data=list(frames[0].data), frames=frames)
    fig.update_layout(
        height=460, yaxis_title="Pallets abastecidos (acumulado)", yaxis_range=[0, maxy],
        title="Avanza la barra de tiempo o pulsa ▶ para ver el abastecimiento acumulándose",
        margin=dict(l=10, r=10, t=70, b=10),
        updatemenus=[dict(type="buttons", showactive=False, x=0, y=1.25, xanchor="left",
            buttons=[
                dict(label="▶ Reproducir", method="animate",
                     args=[None, dict(frame=dict(duration=250, redraw=True), fromcurrent=True)]),
                dict(label="⏸ Pausa", method="animate",
                     args=[[None], dict(frame=dict(duration=0, redraw=False), mode="immediate")])])],
        sliders=[dict(active=0, x=0, y=0, len=1.0, currentvalue=dict(prefix="Hora: "),
            steps=[dict(method="animate", label=f.name,
                        args=[[f.name], dict(frame=dict(duration=0, redraw=True), mode="immediate")])
                   for f in frames])])
    return fig


def fig_ocupacion(df):
    fig = go.Figure()
    for i, z in enumerate(ZONE_LIST):
        fig.add_trace(go.Scatter(x=df["tiempo"], y=df[z], mode="lines", name=z,
                                 line=dict(width=1.6, color=PALETTE[i % len(PALETTE)])))
    fig.add_trace(go.Scatter(x=df["tiempo"], y=df["total_pct"], mode="lines",
                             name="TOTAL bodega", line=dict(width=4, color="#1B2631")))
    fig.add_hline(y=UMBRAL_AMARILLO, line_dash="dot", line_color="#E1B12C",
                  annotation_text="70%", annotation_position="right")
    fig.add_hline(y=UMBRAL_ROJO, line_dash="dot", line_color="#C0392B",
                  annotation_text="90%", annotation_position="right")
    fig.update_layout(height=460, hovermode="x unified", yaxis_title="Ocupación (%)",
                      xaxis_title="Hora", margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    fig.update_yaxes(range=[0, max(105, df["total_pct"].max() + 5)])
    return fig


def fig_operario_tiempo(df):
    d = df[df["minute"] <= T1_END].copy().set_index("tiempo")
    g = d[["s_min", "u_min", "i_min", "l_min"]].resample("30min").sum()
    fig = go.Figure()
    fig.add_trace(go.Bar(x=g.index, y=g["s_min"], name="Abastecer líneas", marker_color="#28B463"))
    fig.add_trace(go.Bar(x=g.index, y=g["u_min"], name="Descargar camiones", marker_color="#2E86C1"))
    fig.add_trace(go.Bar(x=g.index, y=g["i_min"], name="Tiempo muerto", marker_color="#BDC3C7"))
    fig.add_trace(go.Bar(x=g.index, y=g["l_min"], name="Almuerzo", marker_color="#F39C12"))
    fig.update_layout(barmode="stack", height=420, yaxis_title="Minutos / 30 min",
                      xaxis_title="Hora (Turno 1)", margin=dict(l=10, r=10, t=30, b=10),
                      legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0))
    return fig


def fig_operario_dona(S):
    fig = go.Figure(go.Pie(
        labels=["Abastecer líneas", "Descargar camiones", "Tiempo muerto", "Almuerzo"],
        values=[S["t_supply"], S["t_unload"], S["t_idle"], S["t_lunch"]], hole=0.55,
        marker_colors=["#28B463", "#2E86C1", "#BDC3C7", "#F39C12"], textinfo="label+percent"))
    fig.update_layout(height=420, showlegend=False, margin=dict(l=10, r=10, t=10, b=10),
                      annotations=[dict(text="Operario 1<br>Turno 1", showarrow=False, font_size=13)])
    return fig


def fig_zonas_final(df):
    last = df.iloc[-1]
    vals = [last[z] for z in ZONE_LIST]
    colors = ["#C0392B" if v > UMBRAL_ROJO else "#E1B12C" if v > UMBRAL_AMARILLO else "#28B463"
              for v in vals]
    fig = go.Figure(go.Bar(x=vals, y=ZONE_LIST, orientation="h", marker_color=colors,
                           text=[f"{v:.0f}%" for v in vals], textposition="outside"))
    fig.update_layout(height=360, xaxis_title="Ocupación al cierre (%)",
                      margin=dict(l=10, r=10, t=10, b=10))
    fig.update_xaxes(range=[0, 115])
    fig.add_vline(x=UMBRAL_AMARILLO, line_dash="dot", line_color="#E1B12C")
    fig.add_vline(x=UMBRAL_ROJO, line_dash="dot", line_color="#C0392B")
    return fig


# =====================================================================
# 6. INTERFAZ
# =====================================================================
def main():
    st.set_page_config(page_title="Simulador Bodega · Operario 1", page_icon="📦", layout="wide")
    st.title("📦 Simulador de Bodega — Operario 1")
    st.caption("Control tridimensional: **Inventario · Espacio · Riesgo** · Simulación minuto a minuto (24 h).")

    with st.sidebar:
        st.header("⚙️ Parámetros")
        fuente = st.radio("Stock inicial", ["Slider (sintético)", "Reporte SAP MB5B"], index=0)
        initial_pct = st.slider("Ocupación inicial (%)", 0, 100, 45, step=5,
                                disabled=(fuente != "Slider (sintético)"))
        uploaded = None
        if fuente == "Reporte SAP MB5B":
            uploaded = st.file_uploader("Sube el MB5B (CSV o Excel)", type=["csv", "xlsx", "xls"])
        crecimiento = st.slider("Crecimiento macro (%)", 0, 100, 0, step=5,
                                help="Escala llegadas de camiones y consumo de líneas.")
        growth = 1.0 + crecimiento / 100.0
        st.metric("Factor aplicado", f"x{growth:.2f}")
        operator_cyl = st.checkbox("Operario 1 descarga cilindros (REYEMSA)", False)
        buffer_pallets = st.slider("Tolerancia de espera de líneas (pallets)", 1.0, 10.0, 4.0, 0.5)
        st.markdown("**⏱️ Tiempos del Operario 1 (min/pallet)**")
        min_store = st.number_input("Guardar / descargar camión", 0.5, 30.0, MIN_STORE_DEF, 0.5)
        min_supply = st.number_input("Abastecer línea", 0.5, 30.0, MIN_SUPPLY_DEF, 0.5)
        incluir_almuerzo = st.checkbox("Incluir almuerzo Operario 1 (12:00–12:45)", True)
        st.markdown("**🚚 Camiones (viajes/día y pallets/viaje)**")
        ci1, ci2 = st.columns(2)
        ide_trips = ci1.number_input("IDE · viajes", 0, 12, IDE_TRIPS_DEF, 1)
        ide_pallets = ci2.number_input("IDE · pallets/viaje", 1.0, 80.0, float(IDE_PALLETS_DEF), 1.0)
        cs1, cs2 = st.columns(2)
        smasac_trips = cs1.number_input("SMASAC · viajes", 0, 12, SMASAC_TRIPS_DEF, 1)
        smasac_pallets = cs2.number_input("SMASAC · pallets/viaje", 1.0, 80.0, float(SMASAC_PALLETS_DEF), 1.0)
        cr1, cr2 = st.columns(2)
        reyemsa_trips = cr1.number_input("REYEMSA · viajes", 0, 12, REYEMSA_TRIPS_DEF, 1)
        reyemsa_pallets = cr2.number_input("REYEMSA · pallets/viaje", 1.0, 120.0, float(REYEMSA_PALLETS_DEF), 1.0)
        entrada_dia = (ide_trips * ide_pallets + smasac_trips * smasac_pallets
                       + reyemsa_trips * reyemsa_pallets) * growth
        st.caption(f"Entrada total ≈ **{entrada_dia:.0f} pallets/día** (con el factor x{growth:.2f}).")

    # ---- Construcción del stock inicial ----
    init_items = None
    if fuente == "Reporte SAP MB5B":
        if uploaded is None:
            st.info("⬅️ Sube tu reporte MB5B en la barra lateral para analizar el día con tu stock "
                    "real. Mientras tanto se usa el stock sintético del slider.")
        else:
            raw = None
            try:
                raw = parse_mb5b(uploaded)
            except Exception as e:
                st.error(f"No se pudo procesar el archivo: {e}")
            if raw is not None and len(raw):
                grouped = raw.groupby("Tipo", as_index=False)["Stock (ud)"].sum()
                st.subheader("🗂️ Mapeo de materiales (Tipo SAP → categoría de bodega)")
                st.caption("Ajusta la **categoría destino** y las **unidades por pallet** de cada tipo. "
                           "El saldo de cada SKU (sobrante menor a 1 pallet) se cuenta como **1 pallet "
                           "entero**. Las unidades por pallet de tapas/liners son estimadas: verifícalas "
                           "con tu maestro. Usa '(ignorar)' para excluir un tipo de la bodega.")
                base_map = default_mapping(grouped["Tipo"].tolist())
                edited = st.data_editor(
                    base_map, hide_index=True, use_container_width=True, key="mapeo",
                    column_config={
                        "Tipo": st.column_config.TextColumn("Tipo SAP", disabled=True),
                        "Categoría": st.column_config.SelectboxColumn("Categoría destino",
                                                                      options=CAT_OPTIONS, required=True),
                        "Ud x pallet": st.column_config.NumberColumn("Ud x pallet", min_value=0.0,
                                                                     step=1.0, format="%.0f"),
                    })
                init_items, detalle = items_from_mapping(raw, edited)
                inv0 = inv_from_items(init_items)
                used0, fict0 = allocate_zones(inv0)
                occ0 = 100.0 * sum(used0.values()) / TOTAL_POSITIONS
                cA, cB, cC = st.columns(3)
                cA.metric("Pallets totales (stock real)", f"{sum(v for _, v in init_items):.0f}")
                cB.metric("Ocupación inicial calculada", f"{occ0:.0f}%")
                cC.metric("Pallets sin ubicación (inicio)", f"{fict0:.0f}",
                          help="Si >0, tu stock ya supera la capacidad física al iniciar el día.")
                with st.expander("Ver detalle de conversión a pallets"):
                    st.dataframe(detalle, hide_index=True, use_container_width=True)
                st.divider()

    # ---- Simulación ----
    df, S = run_simulation(initial_pct, growth, operator_cyl, buffer_pallets, init_items,
                           int(ide_trips), int(smasac_trips), int(reyemsa_trips),
                           float(min_store), float(min_supply),
                           float(ide_pallets), float(smasac_pallets), float(reyemsa_pallets),
                           bool(incluir_almuerzo))

    if S["max_fict"] > 0.5:
        st.error(f"🚨 **RIESGO DE SEGURIDAD** — Zona Ficticia usada (máx. {S['max_fict']:.0f} pallets "
                 f"sin ubicación). La bodega desborda su capacidad física.")
    elif S["peak_total"] > UMBRAL_ROJO:
        st.error(f"🔴 Ocupación pico **{S['peak_total']:.0f}%** — supera el 90% (crítico).")
    elif S["peak_total"] > UMBRAL_AMARILLO:
        st.warning(f"🟡 Ocupación pico **{S['peak_total']:.0f}%** — supera el 70% (atención).")
    else:
        st.success(f"🟢 Ocupación pico **{S['peak_total']:.0f}%** — dentro del nivel óptimo.")

    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("Ocupación pico", f"{S['peak_total']:.0f}%")
    c2.metric("Ocupación al cierre", f"{S['final_total']:.0f}%")
    c3.metric("Quiebres de stock", f"{S['quiebres_total']}")
    c4.metric("Pallets Zona Ficticia", f"{S['max_fict']:.0f}")
    c5.metric("Utilización Operario 1", f"{S['utilizacion']:.0f}%")
    st.divider()

    st.subheader("1 · Ocupación de la bodega por zonas a lo largo del tiempo")
    st.plotly_chart(fig_ocupacion(df), use_container_width=True)

    st.subheader("2 · Estado del Operario 1 (Turno 1)")
    g1, g2 = st.columns([1.4, 1])
    with g1:
        st.plotly_chart(fig_operario_tiempo(df), use_container_width=True)
    with g2:
        st.plotly_chart(fig_operario_dona(S), use_container_width=True)
    if S["unload_backlog"] > 1:
        st.warning(f"⚠️ El operario cerró el Turno 1 con **{S['unload_backlog']:.0f} pallets sin "
                   f"descargar**. No es por falta de tiempo total: corresponde a camiones que "
                   f"llegaron demasiado cerca del fin de turno (16:45) como para alcanzar a guardarlos. "
                   f"El tiempo muerto previo ocurrió *antes* de que esos camiones llegaran, así que no "
                   f"era aprovechable (no se puede descargar un camión que aún no está en el andén). "
                   f"Esos pallets los termina el Turno 2.")
    st.divider()

    st.subheader("2b · Abastecimiento a líneas (acumulado, animado)")
    st.caption("Pallets que el Operario 1 lleva a cada línea durante el Turno 1. Pulsa ▶ o mueve la "
               "barra de tiempo para ver cómo se acumula a lo largo del día.")
    st.plotly_chart(fig_abastecimiento_animado(df), use_container_width=True)
    st.divider()

    col_a, col_b = st.columns(2)
    with col_a:
        st.subheader("3 · Ocupación por zona al cierre")
        st.plotly_chart(fig_zonas_final(df), use_container_width=True)
    with col_b:
        st.subheader("4 · Quiebres de stock: conteo y causas")
        rows = [{"Origen": ln, "Quiebres": n} for ln, n in S["quiebre_line"].items()]
        rows.append({"Origen": "Sobrecarga del operario", "Quiebres": S["quiebre_overload"]})
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        ev = S.get("quiebre_events", [])
        if ev:
            st.markdown("**¿Cuándo y por qué?**")
            st.dataframe(pd.DataFrame(ev), use_container_width=True, hide_index=True)
        else:
            st.success("Sin quiebres en este escenario.")
        st.metric("Backlog de abastecimiento al cierre", f"{S['supply_backlog']:.1f} pallets")

    with st.expander("📘 Acerca del modelo y supuestos"):
        st.markdown(f"""
**Carga del MB5B.** El stock se lee en **unidades** y se convierte a **pallets** con la tabla de
mapeo (editable). Cada `Tipo` se asigna a una categoría de bodega y se ubica con las mismas reglas
de zonificación. Los `Tipo` no reconocidos caen por defecto en **otros** (desborde Mezanine 2 →
Sótano → ILB → Zona Ficticia). Las unidades por pallet de tapas/liners son estimaciones a calibrar.

**Consumo por turno.** Los valores de consumo son **por turno**. Las líneas de baldes y botellas
operan en ambos turnos (el consumo diario es el doble); la línea de **cilindros solo opera en el
turno día**. El Operario 1 abastece el consumo de su turno (día) dentro del T1.

**Turnos.** T1 (Operario 1): 07:00–16:45. T2: 16:30–02:15 (solapamiento 15 min). La producción
consume durante la ventana de turnos, salvo el almuerzo (12:00–12:45), en que las líneas se
detienen. Los camiones empiezan a llegar al inicio del turno (07:00) y se reparten hacia adelante.

**Zonas (posiciones), total = {TOTAL_POSITIONS}.** Rack = {RACK_TOTAL} (nivel 1 = {RACK_L1} solo
tapas de balde). Pampa: cilindros 2 de alto (0.5 pos/pallet). ILB: tapas Lt/Gl 3 de alto.

**Operario 1.** Tiempos de guardado y de abastecimiento configurables (por defecto 3 min/pallet
cada uno). Prioridad absoluta: abastecer líneas (salvo cilindros) interrumpe la descarga. Quiebre =
línea sin stock en bodega o backlog de abastecimiento sobre la tolerancia.
""")


if __name__ == "__main__":
    main()
