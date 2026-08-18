
import streamlit as st
import pandas as pd
import numpy as np
from pathlib import Path
from io import BytesIO
import json
import math
import re
import requests
import hmac
import base64
from urllib.parse import urlencode
from datetime import datetime

st.set_page_config(
    page_title="Chasseur de Pépites V8",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded",
)

# =========================================================
# CONFIG / STATE
# =========================================================
DEFAULTS = {
    "budget_max": 150000,
    "radius": 80,
    "works_max": 120000,
    "target_profit": 40000,
    "target_margin": 20.0,
    "loan_rate": 3.8,
    "holding_months": 12,
    "financing_share": 100,
    "registration_rate": 12.5,
    "purchase_cost_rate": 1.5,
    "resale_cost_rate": 3.0,
    "contingency_rate": 10.0,
    "holding_monthly": 350,
    "center_city": "Houffalize",
}

for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

st.session_state.setdefault("properties", [])
st.session_state.setdefault("comparables", [])
st.session_state.setdefault("selected_id", None)
st.session_state.setdefault("statbel_loaded", False)
st.session_state.setdefault("statbel_df", pd.DataFrame())
st.session_state.setdefault("hunter_rows", [])
st.session_state.setdefault("hunter_seen", set())

# =========================================================
# DATA HELPERS
# =========================================================
STATBEL_FILES = [
    Path("statbel_immobilier.xlsx"),
    Path("vastgoed_2010_9999.xlsx"),
    Path("statbel_immobilier.csv"),
]

@st.cache_data(show_spinner=False)
def load_statbel_local():
    for p in STATBEL_FILES:
        if not p.exists():
            continue
        if p.suffix.lower() == ".xlsx":
            return pd.read_excel(p, engine="openpyxl"), str(p)
        for enc in ("utf-8-sig","utf-8","cp1252","latin1"):
            for sep in (";", "\t", ",", "|"):
                try:
                    df = pd.read_csv(p, encoding=enc, sep=sep, low_memory=False)
                    if len(df) and df.shape[1] >= 4:
                        return df, str(p)
                except Exception:
                    pass
    return pd.DataFrame(), None

def norm(s):
    return re.sub(r"[^a-z0-9]+", " ", str(s).lower()).strip()

def find_col(df, groups):
    ncols = {c: norm(c) for c in df.columns}
    for words in groups:
        for col, n in ncols.items():
            if all(w in n for w in words):
                return col
    return None

def statbel_columns(df):
    return {
        "year": find_col(df, [["year"],["annee"],["jaar"]]),
        "geo": find_col(df, [["municip"],["commune"],["gemeente"],["nis"]]),
        "geo_name": find_col(df, [["municip","name"],["commune","nom"],["gemeente","naam"],["name"]]),
        "q50": find_col(df, [["q50"],["median"],["mediane"],["mediaan"]]),
        "property": find_col(df, [["property"],["nature"],["type","bien"],["vastgoed"]]),
        "transactions": find_col(df, [["transaction"],["nombre"],["aantal"]]),
    }

def statbel_reference(city):
    df = st.session_state.statbel_df
    if df is None or df.empty:
        return None

    cols = statbel_columns(df)
    search_cols = [c for c in [cols["geo_name"], cols["geo"]] if c]
    if not search_cols or not cols["q50"]:
        return None

    mask = pd.Series(False, index=df.index)
    for c in search_cols:
        mask = mask | df[c].astype(str).str.contains(city, case=False, na=False)

    sub = df[mask].copy()
    if sub.empty:
        return None

    q = pd.to_numeric(sub[cols["q50"]], errors="coerce")
    sub = sub[q.notna()].copy()
    sub["_q50"] = q[q.notna()]
    if sub.empty:
        return None

    if cols["year"]:
        years = pd.to_numeric(sub[cols["year"]], errors="coerce")
        if years.notna().any():
            latest = years.max()
            sub = sub[years == latest]

    # Prefer house-like categories where recognizable.
    if cols["property"]:
        prop = sub[cols["property"]].astype(str).str.lower()
        housemask = prop.str.contains("house|maison|woonhuis|woning|house with|villa", regex=True, na=False)
        if housemask.any():
            sub = sub[housemask]

    if sub.empty:
        return None

    row = sub.iloc[-1]
    return {
        "median": float(row["_q50"]),
        "year": str(row.get(cols["year"], "")) if cols["year"] else "",
        "property_type": str(row.get(cols["property"], "")) if cols["property"] else "",
        "transactions": row.get(cols["transactions"], "") if cols["transactions"] else "",
    }


# =========================================================
# V8.1 — URL / TEXT IMPORTER
# =========================================================
def parse_listing_text(raw):
    """Conservative extraction from visible listing text."""
    out = {}
    txt = re.sub(r"\s+", " ", raw.replace("\xa0", " "))

    price_patterns = [
        r'(?:prix|price|vraagprijs)[^0-9]{0,30}(\d{2,3}(?:[ .]\d{3})+)\s*€?',
        r'(\d{2,3}(?:[ .]\d{3})+)\s*€',
    ]
    for pat in price_patterns:
        m = re.search(pat, txt, re.I)
        if m:
            out["prix"] = int(re.sub(r"[ .]", "", m.group(1)))
            break

    # Surface: prefer explicit habitable/living surface wording.
    surface_patterns = [
        r'(?:surface habitable|living area|woonoppervlakte|bewoonbare oppervlakte)[^0-9]{0,30}(\d{2,4}(?:[.,]\d+)?)\s*m[²2]',
        r'(\d{2,4}(?:[.,]\d+)?)\s*m[²2]\s*(?:habitables?|habitable)',
    ]
    for pat in surface_patterns:
        m = re.search(pat, txt, re.I)
        if m:
            out["surface"] = float(m.group(1).replace(",", "."))
            break

    land_patterns = [
        r'(?:terrain|parcelle|grond|perceel)[^0-9]{0,30}(\d{2,6}(?:[.,]\d+)?)\s*m[²2]',
    ]
    for pat in land_patterns:
        m = re.search(pat, txt, re.I)
        if m:
            out["terrain"] = float(m.group(1).replace(",", "."))
            break

    bedroom_patterns = [
        r'(\d{1,2})\s*(?:chambres?|slaapkamers?|bedrooms?)',
        r'(?:chambres?|slaapkamers?|bedrooms?)[^0-9]{0,15}(\d{1,2})',
    ]
    for pat in bedroom_patterns:
        m = re.search(pat, txt, re.I)
        if m:
            out["chambres"] = int(m.group(1))
            break

    m = re.search(r'\bPEB\s*[:\-]?\s*(A\+\+|A\+|A|B|C|D|E|F|G)\b', txt, re.I)
    if m:
        out["peb"] = m.group(1).upper()

    return out

@st.cache_data(ttl=1800, show_spinner=False)
def fetch_listing_text(url):
    """Best-effort public page read. Never bypasses login, CAPTCHA or anti-bot controls."""
    if not re.match(r"^https?://", url.strip(), re.I):
        raise ValueError("Le lien doit commencer par http:// ou https://")

    headers = {
        "User-Agent": "Mozilla/5.0 (compatible; ChasseurPepitesV8.1/1.0)",
        "Accept-Language": "fr-BE,fr;q=0.9,en;q=0.7",
    }
    r = requests.get(url.strip(), headers=headers, timeout=12, allow_redirects=True)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "").lower()
    if "text/html" not in ctype and "text/plain" not in ctype:
        raise RuntimeError("Le lien ne renvoie pas une page d'annonce lisible.")

    html = r.text
    # Strip scripts/styles/tags conservatively.
    html = re.sub(r"(?is)<script.*?>.*?</script>", " ", html)
    html = re.sub(r"(?is)<style.*?>.*?</style>", " ", html)
    txt = re.sub(r"(?s)<[^>]+>", " ", html)
    txt = txt.replace("&nbsp;", " ").replace("&euro;", "€").replace("&#8364;", "€")
    txt = re.sub(r"\s+", " ", txt).strip()

    if len(txt) < 200:
        raise RuntimeError("La page est vide ou protégée contre la lecture automatique.")
    return txt[:200000]




def to_num(v, default=0.0):
    if v is None:
        return default
    if isinstance(v, (int, float, np.number)):
        try:
            if math.isnan(v):
                return default
        except Exception:
            pass
        return float(v)
    s = str(v).replace("\xa0", " ").replace("€", "").replace("m²", "")
    s = re.sub(r"[^\d,.\-]", "", s)
    if "," in s and "." not in s:
        s = s.replace(",", ".")
    elif "." in s and "," in s:
        s = s.replace(".", "").replace(",", ".")
    try:
        return float(s)
    except Exception:
        return default

# =========================================================
# V10 — CONNECTED SOURCES
# =========================================================
def secret(name, default=""):
    try:
        return st.secrets.get(name, default)
    except Exception:
        return default

def realo_ready():
    return bool(secret("REALO_API_PUBLIC_KEY")) and bool(secret("REALO_API_PRIVATE_KEY"))

def realo_request(method, path, params=None, body=""):
    public = str(secret("REALO_API_PUBLIC_KEY", ""))
    private = str(secret("REALO_API_PRIVATE_KEY", ""))
    base = str(secret("REALO_API_BASE_URL", "https://api.realo.com/1.0")).rstrip("/")
    if not public or not private:
        raise RuntimeError("Clés Realo absentes.")

    path = "/" + str(path).lstrip("/")
    url = base + path
    if params:
        q = urlencode(params, doseq=True)
        if q:
            url += "?" + q

    base_string = f"{method.upper()}&{url}&{body}"
    digest = hmac.new(
        private.encode("utf-8"),
        base_string.encode("utf-8"),
        digestmod="sha256"
    ).digest()
    signature = base64.b64encode(digest).decode("ascii")

    headers = {
        "Authorization": f'Realo key="{public}", signature="{signature}" version="1.0"',
        "Accept": "application/json",
        "Accept-Language": "fr-BE",
        "User-Agent": "ChasseurPepitesV10/1.0",
    }
    r = requests.request(method.upper(), url, headers=headers, data=body or None, timeout=25)
    r.raise_for_status()
    return r.json()

def flatten_generic_listing(item, source="Source"):
    """Map common listing fields to V10 schema without inventing missing values."""
    def first(*keys, default=None):
        for k in keys:
            if isinstance(item, dict) and k in item and item[k] not in (None, ""):
                return item[k]
        return default

    address = item.get("address", {}) if isinstance(item, dict) else {}
    if not isinstance(address, dict):
        address = {}

    title = first("title", "name", "description", default="Annonce")
    if isinstance(title, dict):
        title = title.get("fr") or title.get("FR") or title.get("nl") or next(iter(title.values()), "Annonce")

    price = first("price", "askingPrice", "asking_price", default=0)
    surface = first("surface", "livingArea", "habitableArea", "habitableSurface", default=0)
    land = first("landArea", "plotArea", "terrain", default=0)
    beds = first("bedrooms", "bedroomCount", default=0)
    url = first("url", "realoUrl", "link", default="")
    city = (
        first("city", "municipality", "locality", default="")
        or address.get("locality")
        or address.get("city")
        or ""
    )
    lid = first("id", "listingId", default="")
    if not lid:
        lid = abs(hash(f"{source}|{title}|{city}|{price}|{url}"))

    return {
        "id": f"{source.upper()}-{lid}",
        "titre": str(title),
        "ville": str(city),
        "prix": to_num(price, 0),
        "surface": max(to_num(surface, 0), 1.0),
        "terrain": to_num(land, 0),
        "chambres": int(to_num(beds, 0)),
        "peb": str(first("peb", "epc", default="Inconnu")),
        "travaux": 0.0,
        "travaux_confiance": "Faible",
        "revente": 0.0,
        "revente_confiance": "Faible",
        "comparables_count": 0,
        "url": str(url),
        "notes": f"Import automatique {source}",
        "statbel_median": None,
        "statbel_year": "",
        "statbel_type": "",
        "statbel_transactions": "",
        "status": "À compléter",
    }

def extract_items(payload):
    if isinstance(payload, list):
        return payload
    if not isinstance(payload, dict):
        return []
    for key in ("results", "data", "items", "listings", "properties", "annonces"):
        value = payload.get(key)
        if isinstance(value, list):
            return value
        if isinstance(value, dict):
            for sub in ("results", "items", "data"):
                if isinstance(value.get(sub), list):
                    return value[sub]
    return []


# =========================================================
# FINANCIAL ENGINE
# =========================================================
def financials(prop, purchase_price=None, works=None, resale=None, months=None):
    p = float(prop["prix"] if purchase_price is None else purchase_price)
    w = float(prop["travaux"] if works is None else works)
    v = float(prop["revente"] if resale is None else resale)
    m = int(st.session_state.holding_months if months is None else months)

    rights = p * st.session_state.registration_rate / 100
    purchase_cost = p * st.session_state.purchase_cost_rate / 100
    contingency = w * st.session_state.contingency_rate / 100
    financed = (p + w) * st.session_state.financing_share / 100
    interest = financed * st.session_state.loan_rate / 100 * m / 12 * 0.5
    holding = st.session_state.holding_monthly * m
    resale_cost = v * st.session_state.resale_cost_rate / 100

    total = p + rights + purchase_cost + w + contingency + interest + holding + resale_cost
    profit = v - total
    margin = 100 * profit / total if total else -999

    return {
        "achat": p,
        "droits": rights,
        "frais_achat": purchase_cost,
        "travaux": w,
        "imprevus": contingency,
        "interets": interest,
        "detention": holding,
        "frais_revente": resale_cost,
        "cout_total": total,
        "benefice": profit,
        "marge": margin,
    }

def solve_max_price(prop, profit_target=None, margin_target=None):
    tp = st.session_state.target_profit if profit_target is None else profit_target
    tm = st.session_state.target_margin if margin_target is None else margin_target
    lo, hi = 0.0, max(st.session_state.budget_max * 2, float(prop["prix"]) * 2, 1.0)

    for _ in range(100):
        mid = (lo + hi) / 2
        f = financials(prop, purchase_price=mid)
        if f["benefice"] >= tp and f["marge"] >= tm:
            lo = mid
        else:
            hi = mid
    return lo

def scenarios(prop):
    return {
        "Optimiste": financials(prop, works=prop["travaux"]*0.90, resale=prop["revente"]*1.03, months=max(1, st.session_state.holding_months-2)),
        "Réaliste": financials(prop),
        "Défavorable": financials(prop, works=prop["travaux"]*1.20, resale=prop["revente"]*0.95, months=st.session_state.holding_months+3),
        "Très défavorable": financials(prop, works=prop["travaux"]*1.30, resale=prop["revente"]*0.90, months=st.session_state.holding_months+6),
    }

# =========================================================
# DATA QUALITY
# =========================================================
def quality_score(prop):
    score = 0
    reasons = []

    # Price
    if prop.get("prix", 0) > 0:
        score += 15
    else:
        reasons.append("prix manquant")

    # Surface
    if prop.get("surface", 0) > 0:
        score += 10
    else:
        reasons.append("surface manquante")

    # Works confidence
    wc = prop.get("travaux_confiance", "Faible")
    score += {"Faible":5, "Moyenne":12, "Élevée":20}.get(wc, 5)
    if wc == "Faible":
        reasons.append("travaux peu sécurisés")

    # Resale confidence
    rc = prop.get("revente_confiance", "Faible")
    score += {"Faible":5, "Moyenne":15, "Élevée":25}.get(rc, 5)
    if rc == "Faible":
        reasons.append("revente peu sécurisée")

    # Statbel
    if prop.get("statbel_median"):
        score += 10
    else:
        reasons.append("pas de référence Statbel")

    # Comparables
    comps = prop.get("comparables_count", 0)
    if comps >= 3:
        score += 15
    elif comps >= 1:
        score += 8
    else:
        reasons.append("aucun comparable réel")

    # URL/source
    if prop.get("url"):
        score += 5

    return min(score, 100), reasons

def opportunity_score(prop):
    f = financials(prop)
    maxp = solve_max_price(prop)
    s = scenarios(prop)
    bad = s["Défavorable"]
    quality, _ = quality_score(prop)

    score = 0
    score += min(max((f["marge"] - 10) * 1.4, 0), 25)
    score += min(max(f["benefice"] / max(st.session_state.target_profit, 1) * 20, 0), 20)
    score += 20 if prop["prix"] <= maxp else max(0, 20 - ((prop["prix"]-maxp)/max(maxp,1))*100)
    score += 15 if bad["benefice"] > 0 else 0
    score += 10 if bad["marge"] >= max(10, st.session_state.target_margin-5) else 0
    score += quality * 0.10
    return min(round(score, 1), 100)

def decision(prop):
    f = financials(prop)
    maxp = solve_max_price(prop)
    bad = scenarios(prop)["Défavorable"]
    q, _ = quality_score(prop)
    score = opportunity_score(prop)

    if q < 55:
        return "À VÉRIFIER", "Données insuffisamment fiables pour prendre une décision."
    if score >= 80 and prop["prix"] <= maxp and bad["benefice"] > 0:
        return "PRIORITÉ VISITE", "Bonne rentabilité et résistance correcte au scénario défavorable."
    if score >= 60:
        return "À NÉGOCIER", f"Potentiel intéressant, mais viser environ {maxp:,.0f} € maximum."
    return "À ÉCARTER", "Rentabilité ou marge de sécurité insuffisante."

def add_or_replace(prop):
    props = st.session_state.properties
    for i, p in enumerate(props):
        if p["id"] == prop["id"]:
            props[i] = prop
            return
    props.append(prop)

# =========================================================
# SIDEBAR — ONLY ESSENTIAL SETTINGS
# =========================================================
with st.sidebar:
    st.markdown("## ⚙️ Réglages")
    st.caption("À régler une fois, puis laisser le moteur travailler.")

    st.session_state.center_city = st.text_input("Zone principale", st.session_state.center_city)
    st.session_state.budget_max = st.number_input("Budget achat max (€)", 0, 2_000_000, int(st.session_state.budget_max), 5000)
    st.session_state.target_profit = st.number_input("Bénéfice minimum (€)", 0, 500_000, int(st.session_state.target_profit), 5000)
    st.session_state.target_margin = st.number_input("Marge minimum (%)", 0.0, 100.0, float(st.session_state.target_margin), 1.0)

    with st.expander("Réglages avancés"):
        st.session_state.loan_rate = st.number_input("Taux crédit (%)", 0.0, 15.0, float(st.session_state.loan_rate), 0.1)
        st.session_state.holding_months = st.number_input("Durée projet (mois)", 1, 60, int(st.session_state.holding_months), 1)
        st.session_state.registration_rate = st.number_input("Droits d'enregistrement (%)", 0.0, 21.0, float(st.session_state.registration_rate), 0.5)
        st.session_state.purchase_cost_rate = st.number_input("Provision frais achat (%)", 0.0, 10.0, float(st.session_state.purchase_cost_rate), 0.1)
        st.session_state.resale_cost_rate = st.number_input("Frais revente (%)", 0.0, 15.0, float(st.session_state.resale_cost_rate), 0.5)
        st.session_state.contingency_rate = st.number_input("Imprévus travaux (%)", 0.0, 30.0, float(st.session_state.contingency_rate), 1.0)
        st.session_state.holding_monthly = st.number_input("Coût détention/mois (€)", 0, 5000, int(st.session_state.holding_monthly), 50)

    df_statbel, statbel_file = load_statbel_local()
    if not df_statbel.empty:
        st.session_state.statbel_df = df_statbel
        st.session_state.statbel_loaded = True
        st.success("Statbel chargé")
    else:
        st.warning("Statbel non trouvé")

# =========================================================
# HEADER
# =========================================================
st.title("🏠 Chasseur de Pépites — V10")
st.caption("Simple à lire. Données traçables. Décision prudente.")

top = st.columns(4)
top[0].metric("Biens suivis", len(st.session_state.properties))
top[1].metric("Objectif bénéfice", f"{st.session_state.target_profit:,.0f} €")
top[2].metric("Objectif marge", f"{st.session_state.target_margin:.0f}%")
top[3].metric("Statbel", "OK" if st.session_state.statbel_loaded else "Absent")

# =========================================================
# FOUR MAIN SCREENS
# =========================================================
tab_hunt, tab_find, tab_analyze, tab_decide, tab_my = st.tabs([
    "🔎 1. CHASSER",
    "🏠 2. AJOUTER",
    "🔍 3. ANALYSER",
    "🚦 4. DÉCIDER",
    "⭐ 5. MES BIENS",
])


# ---------------------------------------------------------
# 1 HUNT — CONNECTED SOURCES
# ---------------------------------------------------------
with tab_hunt:
    st.subheader("🔎 Lancer la chasse")

    c1,c2,c3,c4 = st.columns(4)
    hunt_budget = c1.number_input("Prix max (€)", 0, 2_000_000, int(st.session_state.budget_max), 5000, key="v10_hunt_budget")
    hunt_radius = c2.number_input("Rayon (km)", 1, 300, int(st.session_state.radius), 5, key="v10_hunt_radius")
    hunt_profit = c3.number_input("Bénéfice cible (€)", 0, 500_000, int(st.session_state.target_profit), 5000, key="v10_hunt_profit")
    hunt_score = c4.slider("Score minimum", 0, 100, 60, 5, key="v10_hunt_score")

    st.markdown("### Sources")
    s1,s2 = st.columns(2)

    if realo_ready():
        s1.success("✅ Realo connecté")
    else:
        s1.warning("⚠️ Realo non connecté")

    if st.session_state.statbel_loaded:
        s2.success("✅ Statbel chargé")
    else:
        s2.warning("⚠️ Statbel absent")

    if not realo_ready():
        st.info(
            "Aucune source automatique d'annonces n'est encore connectée. "
            "Le bouton de chasse ne fabriquera pas de résultats. "
            "Dès que les clés Realo sont ajoutées dans Streamlit Secrets, la recherche automatique peut être activée."
        )

    with st.expander("⚙️ Paramètres Realo avancés", expanded=False):
        st.caption(
            "Le chemin exact de recherche dépend des droits activés sur ton compte Realo. "
            "Tu peux le définir dans les Secrets Streamlit."
        )
        st.code(
            'REALO_API_PUBLIC_KEY = "..."\\n'
            'REALO_API_PRIVATE_KEY = "..."\\n'
            'REALO_API_BASE_URL = "https://api.realo.com/1.0"\\n'
            'REALO_SEARCH_PATH = "/listings"'
        )

    if st.button("🚀 LANCER LA CHASSE", type="primary", use_container_width=True):
        if not realo_ready():
            st.error("Impossible de lancer une chasse automatique : aucune source d'annonces n'est connectée.")
        else:
            try:
                path = str(secret("REALO_SEARCH_PATH", "/listings"))
                params = {
                    "q": st.session_state.center_city,
                    "price_max": int(hunt_budget),
                    "radius": int(hunt_radius),
                }
                with st.spinner("Recherche Realo en cours…"):
                    payload = realo_request("GET", path, params=params)
                    items = extract_items(payload)
                    imported = [flatten_generic_listing(x, "Realo") for x in items if isinstance(x, dict)]

                    # Enrich Statbel reference only.
                    for p in imported:
                        stat = statbel_reference(p["ville"]) if st.session_state.statbel_loaded and p["ville"] else None
                        if stat:
                            p["statbel_median"] = stat["median"]
                            p["statbel_year"] = stat["year"]
                            p["statbel_type"] = stat["property_type"]
                            p["statbel_transactions"] = stat["transactions"]

                    known = {p["id"] for p in st.session_state.hunter_rows}
                    added = 0
                    for p in imported:
                        if p["id"] not in known and p["prix"] <= hunt_budget:
                            st.session_state.hunter_rows.append(p)
                            known.add(p["id"])
                            added += 1

                st.success(f"{len(items)} résultat(s) reçu(s), {added} nouvelle(s) annonce(s) ajoutée(s).")
            except Exception as e:
                st.error(f"Recherche Realo impossible : {e}")

    st.markdown("### Résultats")
    hunter = st.session_state.hunter_rows

    if not hunter:
        st.info("Aucune annonce automatique disponible pour le moment.")
    else:
        rows = []
        for p in hunter:
            quality, _ = quality_score(p)
            complete = p["travaux"] > 0 and p["revente"] > 0
            if complete:
                f = financials(p)
                score = opportunity_score(p)
                dec, _ = decision(p)
                benefit = f["benefice"]
                margin = f["marge"]
                pmax = solve_max_price(p)
            else:
                score = quality * 0.4
                dec = "À COMPLÉTER"
                benefit = 0
                margin = 0
                pmax = 0

            rows.append({
                "Décision": dec,
                "Score": round(score,1),
                "Qualité": quality,
                "Bien": p["titre"],
                "Ville": p["ville"],
                "Prix": p["prix"],
                "Surface": p["surface"],
                "Travaux": p["travaux"],
                "Revente": p["revente"],
                "Bénéfice": benefit,
                "Marge %": margin,
                "Prix max": pmax,
                "ID": p["id"],
            })

        rdf = pd.DataFrame(rows)
        rdf = rdf[rdf["Prix"] <= hunt_budget].sort_values(["Score","Qualité"], ascending=[False,False])

        st.dataframe(rdf.drop(columns=["ID"]), use_container_width=True, hide_index=True)

        if not rdf.empty:
            labels = {
                f"{r['Bien']} — {r['Ville']} — {r['Prix']:,.0f} €": r["ID"]
                for _, r in rdf.iterrows()
            }
            chosen = st.selectbox("Annonce", list(labels.keys()), key="v10_hunt_pick")
            pid = labels[chosen]
            prop = next(p for p in hunter if p["id"] == pid)

            if prop["travaux"] <= 0 or prop["revente"] <= 0:
                st.warning(
                    "Cette annonce est réelle mais incomplète pour le calcul financier. "
                    "Ajoute-la à MES BIENS puis renseigne travaux et valeur de revente."
                )

            if st.button("⭐ Ajouter à MES BIENS", type="primary", key="v10_add"):
                add_or_replace(prop)
                st.session_state.selected_id = prop["id"]
                st.success("Annonce ajoutée à MES BIENS.")

# ---------------------------------------------------------
# 1 ADD
# ---------------------------------------------------------
with tab_find:
    st.subheader("Ajouter une annonce")
    st.write("Colle un lien puis clique sur **Importer l'annonce**. Si le portail bloque la lecture, colle simplement le texte de l'annonce.")

    st.session_state.setdefault("v81_url", "")
    st.session_state.setdefault("v81_imported", {})
    st.session_state.setdefault("v81_raw_text", "")

    url_input = st.text_input("Lien de l'annonce", value=st.session_state.v81_url, key="v81_url_widget")
    cimp1, cimp2 = st.columns([1, 3])

    if cimp1.button("🔎 Importer l'annonce", type="primary"):
        st.session_state.v81_url = url_input
        try:
            with st.spinner("Lecture de l'annonce…"):
                page_text = fetch_listing_text(url_input)
                parsed = parse_listing_text(page_text)
                st.session_state.v81_raw_text = page_text
                st.session_state.v81_imported = parsed
            if parsed:
                st.success("Annonce lue. Vérifie les champs détectés avant de lancer l'analyse.")
            else:
                st.warning("La page a été lue, mais aucun champ suffisamment fiable n'a été détecté. Utilise le mode texte ci-dessous.")
        except Exception as e:
            st.session_state.v81_imported = {}
            st.warning(
                "Lecture automatique impossible. Le portail peut bloquer Streamlit ou nécessiter JavaScript/CAPTCHA. "
                "Aucune donnée n'a été inventée."
            )
            st.caption(f"Détail technique : {e}")

    with st.expander("📋 Mode secours — coller le texte de l'annonce", expanded=False):
        pasted = st.text_area(
            "Texte copié depuis l'annonce",
            value="",
            height=180,
            placeholder="Sélectionne le texte de l'annonce dans ton navigateur, copie-le, puis colle-le ici."
        )
        if st.button("Extraire les informations du texte"):
            parsed = parse_listing_text(pasted)
            st.session_state.v81_raw_text = pasted
            st.session_state.v81_imported = parsed
            if parsed:
                st.success("Informations détectées. Vérifie-les dans le formulaire ci-dessous.")
            else:
                st.error("Je n'ai pas trouvé de champs suffisamment fiables dans ce texte.")

    imported = st.session_state.v81_imported
    if imported:
        st.info(
            "Détecté automatiquement : " +
            ", ".join(f"{k} = {v}" for k, v in imported.items())
        )

    with st.form("add_property", clear_on_submit=False):
        url = st.text_input("Lien conservé dans la fiche", value=url_input)
        c1,c2,c3 = st.columns(3)
        title = c1.text_input("Nom / référence", "Maison à rénover")
        city = c2.text_input("Commune", st.session_state.center_city)
        price = c3.number_input("Prix demandé (€)", 0, 2_000_000, int(imported.get("prix", 100000)), 5000)

        c1,c2,c3,c4 = st.columns(4)
        surface = c1.number_input("Surface habitable (m²)", 1.0, 3000.0, float(imported.get("surface", 150.0)), 5.0)
        land = c2.number_input("Terrain (m²)", 0.0, 100000.0, float(imported.get("terrain", 800.0)), 50.0)
        beds = c3.number_input("Chambres", 0, 30, int(imported.get("chambres", 3)), 1)
        peb_opts = ["A++","A+","A","B","C","D","E","F","G","Inconnu"]
        imported_peb = imported.get("peb", "Inconnu")
        peb = c4.selectbox("PEB", peb_opts, index=peb_opts.index(imported_peb) if imported_peb in peb_opts else 9)

        st.markdown("### Travaux")
        c1,c2 = st.columns([2,1])
        works = c1.number_input("Budget travaux réaliste (€)", 0, 1_000_000, 70000, 5000)
        works_conf = c2.selectbox(
            "Fiabilité travaux",
            ["Faible","Moyenne","Élevée"],
            index=0,
            help="Faible = estimation rapide. Moyenne = visite détaillée. Élevée = devis/professionnels."
        )

        st.markdown("### Revente")
        c1,c2 = st.columns([2,1])
        resale = c1.number_input("Valeur après rénovation (€)", 0, 3_000_000, 240000, 5000)
        resale_conf = c2.selectbox(
            "Fiabilité revente",
            ["Faible","Moyenne","Élevée"],
            index=0,
            help="Faible = intuition. Moyenne = Statbel + annonces comparables. Élevée = plusieurs comparables réellement cohérents."
        )

        comps = st.number_input("Nombre de comparables réellement vérifiés", 0, 20, 0, 1)
        notes = st.text_area("Notes importantes", placeholder="Toiture, humidité, urbanisme, électricité, servitudes…")

        submitted = st.form_submit_button("Analyser ce bien", type="primary")

    if submitted:
        stat = statbel_reference(city) if st.session_state.statbel_loaded else None
        pid = f"P-{datetime.now().strftime('%Y%m%d%H%M%S%f')}"
        prop = {
            "id": pid,
            "titre": title,
            "ville": city,
            "prix": float(price),
            "surface": float(surface),
            "terrain": float(land),
            "chambres": int(beds),
            "peb": peb,
            "travaux": float(works),
            "travaux_confiance": works_conf,
            "revente": float(resale),
            "revente_confiance": resale_conf,
            "comparables_count": int(comps),
            "url": url,
            "notes": notes,
            "statbel_median": stat["median"] if stat else None,
            "statbel_year": stat["year"] if stat else "",
            "statbel_type": stat["property_type"] if stat else "",
            "statbel_transactions": stat["transactions"] if stat else "",
            "status": "À analyser",
        }
        add_or_replace(prop)
        st.session_state.selected_id = pid

        q, reasons = quality_score(prop)
        st.success("Bien ajouté.")
        c1,c2,c3 = st.columns(3)
        c1.metric("Qualité des données", f"{q}/100")
        c2.metric("Score opportunité", f"{opportunity_score(prop)}/100")
        c3.metric("Prix max", f"{solve_max_price(prop):,.0f} €")
        if reasons:
            st.warning("À améliorer : " + ", ".join(reasons))

# ---------------------------------------------------------
# SELECTOR
# ---------------------------------------------------------
def selected_property():
    props = st.session_state.properties
    if not props:
        return None
    ids = [p["id"] for p in props]
    if st.session_state.selected_id not in ids:
        st.session_state.selected_id = ids[-1]
    return next(p for p in props if p["id"] == st.session_state.selected_id)

def property_selector(key):
    props = st.session_state.properties
    if not props:
        return None
    labels = {
        f"{p['titre']} — {p['ville']} — {p['prix']:,.0f} €": p["id"]
        for p in props
    }
    current = st.session_state.selected_id
    current_label = next((k for k,v in labels.items() if v == current), list(labels)[-1])
    choice = st.selectbox("Bien", list(labels.keys()), index=list(labels.keys()).index(current_label), key=key)
    st.session_state.selected_id = labels[choice]
    return selected_property()

# ---------------------------------------------------------
# 2 ANALYZE
# ---------------------------------------------------------
with tab_analyze:
    prop = property_selector("analyze_select")
    if prop is None:
        st.info("Ajoute d'abord un bien dans l'écran 1.")
    else:
        f = financials(prop)
        q, reasons = quality_score(prop)
        stat = prop.get("statbel_median")

        st.subheader(f"{prop['titre']} — {prop['ville']}")

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Prix demandé", f"{prop['prix']:,.0f} €")
        c2.metric("Travaux", f"{prop['travaux']:,.0f} €")
        c3.metric("Coût total", f"{f['cout_total']:,.0f} €")
        c4.metric("Revente estimée", f"{prop['revente']:,.0f} €")

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Bénéfice", f"{f['benefice']:,.0f} €")
        c2.metric("Marge", f"{f['marge']:.1f}%")
        c3.metric("Prix maximum", f"{solve_max_price(prop):,.0f} €")
        c4.metric("Qualité données", f"{q}/100")

        st.markdown("### Contrôle des données")
        c1,c2,c3 = st.columns(3)
        c1.write(f"**Travaux :** {prop['travaux_confiance']}")
        c2.write(f"**Revente :** {prop['revente_confiance']}")
        c3.write(f"**Comparables :** {prop['comparables_count']}")

        if stat:
            st.info(
                f"Référence Statbel trouvée pour {prop['ville']} : "
                f"Q50 ≈ **{stat:,.0f} €**"
                + (f" ({prop.get('statbel_year')})" if prop.get("statbel_year") else "")
                + ". À utiliser comme contrôle de marché, pas comme estimation exacte du bien."
            )
        else:
            st.warning("Aucune référence Statbel clairement détectée pour cette commune.")

        if reasons:
            st.warning("Analyse encore fragile : " + ", ".join(reasons))
        else:
            st.success("Les principaux champs de qualité sont renseignés.")

        st.markdown("### Décomposition du coût")
        cost_df = pd.DataFrame([
            ["Achat", f["achat"]],
            ["Droits d'enregistrement", f["droits"]],
            ["Provision frais achat", f["frais_achat"]],
            ["Travaux", f["travaux"]],
            ["Imprévus travaux", f["imprevus"]],
            ["Intérêts", f["interets"]],
            ["Coûts de détention", f["detention"]],
            ["Frais de revente", f["frais_revente"]],
        ], columns=["Poste","Montant (€)"])
        st.dataframe(cost_df, use_container_width=True, hide_index=True)

# ---------------------------------------------------------
# 3 DECIDE
# ---------------------------------------------------------
with tab_decide:
    prop = property_selector("decide_select")
    if prop is None:
        st.info("Ajoute d'abord un bien.")
    else:
        dec, why = decision(prop)
        f = financials(prop)
        maxp = solve_max_price(prop)
        target = solve_max_price(prop, st.session_state.target_profit*1.15, st.session_state.target_margin+3)
        prudent = solve_max_price(prop, st.session_state.target_profit*1.30, st.session_state.target_margin+5)
        q, reasons = quality_score(prop)
        score = opportunity_score(prop)

        if dec == "PRIORITÉ VISITE":
            st.success(f"## 🟢 {dec}")
        elif dec in ("À NÉGOCIER","À VÉRIFIER"):
            st.warning(f"## 🟠 {dec}")
        else:
            st.error(f"## 🔴 {dec}")
        st.write(why)

        c1,c2,c3,c4 = st.columns(4)
        c1.metric("Score Pépite", f"{score}/100")
        c2.metric("Qualité données", f"{q}/100")
        c3.metric("Bénéfice réaliste", f"{f['benefice']:,.0f} €")
        c4.metric("Marge réaliste", f"{f['marge']:.1f}%")

        st.markdown("### Prix à proposer")
        c1,c2,c3 = st.columns(3)
        c1.metric("🟢 Offre prudente", f"{prudent:,.0f} €")
        c2.metric("🎯 Offre cible", f"{target:,.0f} €")
        c3.metric("🚫 Maximum absolu", f"{maxp:,.0f} €")

        st.markdown("### Stress test")
        scen = scenarios(prop)
        rows = []
        for name, sf in scen.items():
            rows.append({
                "Scénario": name,
                "Bénéfice (€)": sf["benefice"],
                "Marge (%)": sf["marge"],
            })
        st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)

        bad = scen["Défavorable"]
        if bad["benefice"] > 0 and bad["marge"] >= max(10, st.session_state.target_margin-5):
            st.success(f"Scénario défavorable encore rentable : {bad['benefice']:,.0f} € / {bad['marge']:.1f}%.")
        else:
            st.error(f"Scénario défavorable fragile : {bad['benefice']:,.0f} € / {bad['marge']:.1f}%.")

        if reasons:
            st.warning("Avant offre : " + ", ".join(reasons))

# ---------------------------------------------------------
# 4 MY PROPERTIES
# ---------------------------------------------------------
with tab_my:
    st.subheader("Mes biens")

    props = st.session_state.properties
    if not props:
        st.info("Aucun bien suivi.")
    else:
        rows = []
        for p in props:
            f = financials(p)
            dec, _ = decision(p)
            rows.append({
                "Décision": dec,
                "Score": opportunity_score(p),
                "Qualité": quality_score(p)[0],
                "Bien": p["titre"],
                "Ville": p["ville"],
                "Prix": p["prix"],
                "Travaux": p["travaux"],
                "Revente": p["revente"],
                "Bénéfice": f["benefice"],
                "Marge %": f["marge"],
                "Prix max": solve_max_price(p),
                "Statut": p.get("status","À analyser"),
                "ID": p["id"],
            })
        table = pd.DataFrame(rows)
        order = {"PRIORITÉ VISITE":0,"À NÉGOCIER":1,"À VÉRIFIER":2,"À ÉCARTER":3}
        table["_o"] = table["Décision"].map(order).fillna(9)
        table = table.sort_values(["_o","Score"], ascending=[True,False]).drop(columns="_o")
        st.dataframe(table.drop(columns=["ID"]), use_container_width=True, hide_index=True)

        st.markdown("### Modifier le suivi")
        selected = property_selector("my_select")
        if selected:
            statuses = ["À analyser","À visiter","Visité","Offre à préparer","Offre envoyée","Négociation","Accepté","Écarté"]
            current = selected.get("status","À analyser")
            new_status = st.selectbox("Statut", statuses, index=statuses.index(current) if current in statuses else 0)
            selected["status"] = new_status

            c1,c2 = st.columns(2)
            if c1.button("Supprimer ce bien"):
                st.session_state.properties = [p for p in st.session_state.properties if p["id"] != selected["id"]]
                st.session_state.selected_id = None
                st.rerun()

            if c2.button("Tout supprimer"):
                st.session_state.properties = []
                st.session_state.selected_id = None
                st.rerun()

        st.markdown("### Sauvegarde")
        backup = json.dumps(st.session_state.properties, ensure_ascii=False, indent=2)
        st.download_button("Télécharger mes biens", backup.encode("utf-8"), "mes_biens_v8.json", "application/json")

        restore = st.file_uploader("Restaurer mes biens", type=["json"])
        if restore is not None:
            try:
                data = json.load(restore)
                if isinstance(data, list) and st.button("Restaurer cette sauvegarde"):
                    st.session_state.properties = data
                    st.session_state.selected_id = None
                    st.rerun()
            except Exception as e:
                st.error(f"Sauvegarde invalide : {e}")

st.divider()
st.caption(
    "V10 sépare clairement deux notions : la rentabilité estimée et la qualité des données. "
    "Une bonne rentabilité avec de mauvaises données ne doit pas déclencher une décision d'achat."
)
