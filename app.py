
import streamlit as st
import pandas as pd
import numpy as np
import requests
from io import StringIO, BytesIO
from datetime import datetime, timezone
import hashlib
import hmac
import base64
import zipfile as pyzipfile
from urllib.parse import urlencode

st.set_page_config(
    page_title="Chasseur de Pépites V5",
    page_icon="🤖",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ----------------------------
# SESSION / PARAMÈTRES
# ----------------------------
DEFAULTS = {
    "budget": 150000,
    "radius": 80,
    "works_max": 120000,
    "profit": 40000,
    "margin": 20.0,
    "rate": 3.8,
    "months": 12,
    "finance": 100,
    "reg": 12.5,
    "notary": 1.5,
    "resale": 3.0,
    "contingency": 10.0,
    "holding": 350,
    "city": "Houffalize",
}

for k, v in DEFAULTS.items():
    st.session_state.setdefault(k, v)

st.session_state.setdefault("manual_rows", [])
st.session_state.setdefault("feed_urls", [])
st.session_state.setdefault("favorites", set())
st.session_state.setdefault("seen_ids", set())
st.session_state.setdefault("notes", {})
st.session_state.setdefault("comparables", [])
st.session_state.setdefault("last_refresh", None)
st.session_state.setdefault("realo_df", pd.DataFrame())
st.session_state.setdefault("statbel_df", pd.DataFrame())

st.title("🤖 Chasseur de Pépites Immobilières — V5")
st.caption("Belgique • surveillance multi-sources autorisées • scoring • prix d'offre • alertes pépites")

with st.sidebar:
    st.header("🎯 Critères de chasse")
    st.session_state.city = st.text_input("Ville centrale", st.session_state.city)
    st.session_state.budget = st.number_input("Budget achat max (€)", 0, 2_000_000, int(st.session_state.budget), 5000)
    st.session_state.radius = st.number_input("Rayon max (km)", 1, 300, int(st.session_state.radius), 5)
    st.session_state.works_max = st.number_input("Travaux max (€)", 0, 1_000_000, int(st.session_state.works_max), 5000)
    st.session_state.profit = st.number_input("Bénéfice cible (€)", 0, 1_000_000, int(st.session_state.profit), 5000)
    st.session_state.margin = st.number_input("Marge cible (%)", 0.0, 100.0, float(st.session_state.margin), 1.0)

    st.header("🏦 Financement")
    st.session_state.rate = st.number_input("Taux annuel (%)", 0.0, 15.0, float(st.session_state.rate), 0.1)
    st.session_state.months = st.number_input("Durée détention (mois)", 1, 60, int(st.session_state.months), 1)
    st.session_state.finance = st.slider("Part financée (%)", 0, 100, int(st.session_state.finance), 5)
    st.session_state.holding = st.number_input("Coût mensuel de détention (€)", 0, 5000, int(st.session_state.holding), 50)

    st.header("🧾 Frais")
    st.session_state.reg = st.number_input("Droits d'enregistrement (%)", 0.0, 21.0, float(st.session_state.reg), 0.5)
    st.session_state.notary = st.number_input("Provision achat/notaire (%)", 0.0, 10.0, float(st.session_state.notary), 0.1)
    st.session_state.resale = st.number_input("Frais de revente (%)", 0.0, 15.0, float(st.session_state.resale), 0.5)
    st.session_state.contingency = st.number_input("Imprévus travaux (%)", 0.0, 30.0, float(st.session_state.contingency), 1.0)

    st.divider()
    st.caption("V5 n'effectue pas de scraping de portails. Utilise uniquement des API, exports ou flux dont tu as l'autorisation.")

# ----------------------------
# FORMAT DE DONNÉES
# ----------------------------
sample_csv = """id,titre,ville,distance_km,prix,surface_m2,terrain_m2,chambres,peb,travaux,toiture,electricite,chauffage,menuiseries,cuisine,sdb,sols_peinture,facade,autres_travaux,valeur_apres_travaux,url,source,date_publication
DEMO-001,Maison à rénover,Houffalize,12,95000,145,900,3,G,60000,12000,8000,10000,7000,8000,6000,5000,2000,2000,230000,https://example.com,Demo,2026-08-17
DEMO-002,Maison 4 façades,Bastogne,22,125000,180,1100,4,F,75000,15000,10000,12000,9000,9000,7000,6000,3000,4000,265000,https://example.com,Demo,2026-08-17
DEMO-003,Maison rénovation lourde,Vielsalm,35,85000,160,700,3,G,105000,20000,12000,15000,12000,10000,8000,8000,5000,15000,225000,https://example.com,Demo,2026-08-16
"""

def stable_id(row):
    raw = "|".join([
        str(row.get("source","")),
        str(row.get("url","")),
        str(row.get("titre","")),
        str(row.get("ville","")),
        str(row.get("prix",""))
    ])
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]

def normalize(df, source_name="Import"):
    df = df.copy()
    if "source" not in df.columns:
        df["source"] = source_name
    if "id" not in df.columns:
        df["id"] = [stable_id(r) for _, r in df.iterrows()]
    else:
        missing = df["id"].isna() | (df["id"].astype(str).str.strip() == "")
        for idx in df.index[missing]:
            df.at[idx, "id"] = stable_id(df.loc[idx])
    if "date_publication" not in df.columns:
        df["date_publication"] = ""
    return df

def fetch_feed(url):
    headers = {"User-Agent": "ChasseurPepitesV5/1.0"}
    r = requests.get(url, headers=headers, timeout=15)
    r.raise_for_status()
    content_type = r.headers.get("content-type","").lower()
    if "json" in content_type or url.lower().endswith(".json"):
        data = r.json()
        if isinstance(data, dict):
            for key in ["results","data","items","annonces","properties"]:
                if key in data and isinstance(data[key], list):
                    data = data[key]
                    break
        if not isinstance(data, list):
            raise ValueError("Le JSON doit contenir une liste d'annonces.")
        return pd.DataFrame(data)
    return pd.read_csv(StringIO(r.text))


# ----------------------------
# REALO + STATBEL
# ----------------------------
STATBEL_REAL_ESTATE_XLSX = "https://statbel.fgov.be/sites/default/files/files/opendata/immo/vastgoed_2010_9999.xlsx"
STATBEL_REAL_ESTATE_ZIP = "https://statbel.fgov.be/sites/default/files/files/opendata/immo/vastgoed_2010_9999.zip"

def get_realo_config():
    """Read credentials only from Streamlit Secrets."""
    public = st.secrets.get("REALO_API_PUBLIC_KEY", "") if hasattr(st, "secrets") else ""
    private = st.secrets.get("REALO_API_PRIVATE_KEY", "") if hasattr(st, "secrets") else ""
    base = st.secrets.get("REALO_API_BASE_URL", "https://api.realo.com/1.0") if hasattr(st, "secrets") else "https://api.realo.com/1.0"
    return str(public), str(private), str(base).rstrip("/")

def realo_signed_request(method, path, params=None, body=""):
    """
    Realo signature per official docs:
    METHOD & full URL & body, HMAC-SHA256(private), base64.
    """
    public, private, base = get_realo_config()
    if not public or not private:
        raise RuntimeError("Clés Realo absentes dans les Secrets Streamlit.")

    path = "/" + path.lstrip("/")
    url = base + path
    if params:
        query = urlencode(params, doseq=True)
        if query:
            url = url + "?" + query

    base_string = f"{method.upper()}&{url}&{body}"
    digest = hmac.new(
        private.encode("utf-8"),
        msg=base_string.encode("utf-8"),
        digestmod=hashlib.sha256
    ).digest()
    signature = base64.b64encode(digest).decode("ascii")

    headers = {
        "User-Agent": "ChasseurPepitesV5.1/1.0",
        "Content-Type": "application/json",
        "Accept-Language": "fr-BE",
        "Authorization": f'Realo key="{public}", signature="{signature}" version="1.0"',
    }
    resp = requests.request(method.upper(), url, headers=headers, data=body if body else None, timeout=25)
    resp.raise_for_status()
    return resp.json()

def flatten_realo_listing(item):
    """Best-effort mapping of a Realo listing to the V5.1 common schema."""
    address = item.get("address") or {}
    if not address and isinstance(item.get("estate"), dict):
        address = item["estate"].get("address") or {}

    title = item.get("title")
    if isinstance(title, dict):
        title = title.get("FR") or title.get("fr") or title.get("NL") or next(iter(title.values()), "")
    if not title:
        title = f"Bien Realo #{item.get('id','')}"

    price = item.get("price") or 0
    hab = (
        item.get("habitableArea") or item.get("habitableSurface") or item.get("livingArea")
        or (item.get("estate") or {}).get("habitableArea") if isinstance(item.get("estate"), dict) else 0
    )
    land = item.get("landArea") or item.get("plotArea") or 0
    bedrooms = item.get("bedrooms") or item.get("bedroomCount") or 0
    city = address.get("locality") or address.get("subLocality") or ""

    url = item.get("url") or item.get("realoUrl") or item.get("agencyUrl") or ""
    listed = item.get("listedAt") or item.get("createdAt") or ""

    return {
        "id": f"REALO-{item.get('id', stable_id(pd.Series(item)))}",
        "titre": title,
        "ville": city,
        "distance_km": 0,
        "prix": price,
        "surface_m2": hab or 0,
        "terrain_m2": land or 0,
        "chambres": bedrooms or 0,
        "peb": "Inconnu",
        "travaux": 0,
        "valeur_apres_travaux": 0,
        "url": url,
        "source": "Realo",
        "date_publication": str(listed)[:10] if listed else "",
    }

@st.cache_data(ttl=24*3600, show_spinner=False)
def load_statbel_real_estate():
    """
    Robust Statbel loader.
    1) Try XLSX with browser-like headers and validate the ZIP magic bytes ("PK").
    2) If Statbel returns HTML or an invalid XLSX, fall back to the official ZIP/TXT dataset.
    """
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/151.0 Safari/537.36",
        "Accept": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet,"
                  "application/zip,text/plain,*/*",
        "Accept-Language": "fr-BE,fr;q=0.9,en;q=0.8",
        "Referer": "https://statbel.fgov.be/",
    }

    # --- Attempt 1: XLSX ---
    r = requests.get(STATBEL_REAL_ESTATE_XLSX, headers=headers, timeout=40, allow_redirects=True)
    if r.ok and len(r.content) > 4 and r.content[:2] == b"PK":
        try:
            return pd.read_excel(BytesIO(r.content), engine="openpyxl")
        except Exception:
            pass

    # --- Attempt 2: ZIP/TXT official fallback ---
    rz = requests.get(STATBEL_REAL_ESTATE_ZIP, headers=headers, timeout=40, allow_redirects=True)
    if not rz.ok:
        raise RuntimeError(
            f"Statbel inaccessible (XLSX HTTP {r.status_code}, ZIP HTTP {rz.status_code})."
        )
    if len(rz.content) < 4 or rz.content[:2] != b"PK":
        ctype = rz.headers.get("content-type", "inconnu")
        preview = rz.text[:120].replace("\\n", " ") if "text" in ctype or "html" in ctype else ""
        raise RuntimeError(
            f"Statbel n'a pas renvoyé un vrai fichier ZIP (type={ctype}). {preview}"
        )

    with pyzipfile.ZipFile(BytesIO(rz.content)) as zf:
        names = [n for n in zf.namelist() if not n.endswith("/")]
        candidates = [
            n for n in names
            if n.lower().endswith((".txt", ".csv", ".tsv"))
        ]
        if not candidates:
            raise RuntimeError("Le ZIP Statbel ne contient aucun fichier TXT/CSV reconnu.")

        # Prefer the largest likely data file.
        candidates.sort(key=lambda n: zf.getinfo(n).file_size, reverse=True)
        raw = zf.read(candidates[0])

    # Statbel TXT exports are commonly UTF-8/Windows-1252 and tab/semicolon delimited.
    last_error = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        for sep in ("\\t", ";", ",", "|"):
            try:
                df = pd.read_csv(
                    BytesIO(raw),
                    encoding=enc,
                    sep=sep,
                    low_memory=False
                )
                if df.shape[1] >= 4 and len(df) > 0:
                    return df
            except Exception as e:
                last_error = e

    raise RuntimeError(f"Impossible de lire le fichier texte Statbel : {last_error}")

def statbel_guess_columns(df):
    cols = {str(c).lower(): c for c in df.columns}
    def pick(words):
        for low, orig in cols.items():
            if all(w in low for w in words):
                return orig
        return None
    return {
        "year": pick(["year"]) or pick(["annee"]) or pick(["jaar"]),
        "geo": pick(["municip"]) or pick(["commune"]) or pick(["gemeente"]),
        "q50": pick(["q50"]) or pick(["median"]),
        "property": pick(["property"]) or pick(["nature"]) or pick(["type"]),
    }

# ----------------------------
# CALCULS
# ----------------------------
def calc(row, purchase_price=None):
    p = float(row["prix"] if purchase_price is None else purchase_price)
    w = float(row["travaux"])
    v = float(row["valeur_apres_travaux"])

    interest = (p+w) * st.session_state.finance/100 * st.session_state.rate/100 * st.session_state.months/12 * 0.5
    rights = p * st.session_state.reg/100
    notary = p * st.session_state.notary/100
    conting = w * st.session_state.contingency/100
    holding = st.session_state.holding * st.session_state.months
    resale = v * st.session_state.resale/100

    total = p + rights + notary + w + conting + interest + holding + resale
    profit = v - total
    margin = profit / total * 100 if total else -999

    return {
        "achat": p, "droits": rights, "notaire": notary, "travaux_calc": w,
        "imprevus": conting, "interets": interest, "detention": holding,
        "revente": resale, "cout_total": total, "benefice": profit, "marge": margin
    }

def solve_price(row, target_profit, target_margin):
    lo, hi = 0.0, max(float(st.session_state.budget)*2, float(row["prix"])*2, 1.0)
    for _ in range(100):
        mid = (lo + hi) / 2
        m = calc(row, mid)
        if m["benefice"] >= target_profit and m["marge"] >= target_margin:
            lo = mid
        else:
            hi = mid
    return lo

PEB_SCORE = {"A++":10,"A+":10,"A":10,"B":9,"C":8,"D":6,"E":4,"F":2,"G":0,"Inconnu":3}

def analyze(df):
    rows = []
    for _, r in df.iterrows():
        m = calc(r)
        pmax = solve_price(r, st.session_state.profit, st.session_state.margin)
        target = solve_price(r, st.session_state.profit*1.15, st.session_state.margin+3)
        prudent = solve_price(r, st.session_state.profit*1.30, st.session_state.margin+5)
        pm2 = float(r["prix"])/float(r["surface_m2"]) if float(r["surface_m2"]) else np.nan
        peb = str(r.get("peb","Inconnu"))
        score = 0.0
        score += min(max((m["marge"]-st.session_state.margin)*1.4,0),25)
        score += min(max((m["benefice"]/max(st.session_state.profit,1))*18,0),20)
        score += 20 if float(r["prix"]) <= pmax else max(0,20-((float(r["prix"])-pmax)/max(pmax,1))*100)
        score += 10 if float(r["travaux"]) <= st.session_state.works_max else 0
        score += 10 if float(r["distance_km"]) <= st.session_state.radius else 0
        score += PEB_SCORE.get(peb,3)*0.5
        score += 5 if pm2 < 1500 else 2
        score += 5 if float(r.get("terrain_m2",0) or 0) >= 500 else 2

        rows.append({
            **r.to_dict(), **m,
            "prix_m2": pm2,
            "offre_prudente": prudent,
            "offre_cible": target,
            "prix_max_absolu": pmax,
            "negociation_requise": float(r["prix"]) - pmax,
            "score": min(round(score,1),100)
        })
    return pd.DataFrame(rows)

# ----------------------------
# INTERFACE
# ----------------------------
tabs = st.tabs([
    "🔌 Realo + Statbel",
    "📡 Surveillance",
    "📥 Données",
    "🔥 Pépites",
    "🤝 Prix d'offre",
    "🔨 Travaux",
    "📊 Comparables",
    "⭐ Favoris"
])

tintegrations, tsurv, tdata, trank, toffer, tworks, tcomp, tfav = tabs

with tintegrations:
    st.subheader("🔌 Realo")
    public, private, base = get_realo_config()
    if public and private:
        st.success("✅ Clés Realo détectées dans les Secrets Streamlit.")
        st.caption(f"API : {base}")
    else:
        st.warning("Clés Realo non configurées.")
        st.code(
            'REALO_API_PUBLIC_KEY = "votre_cle_publique"\n'
            'REALO_API_PRIVATE_KEY = "votre_cle_privee"\n'
            'REALO_API_BASE_URL = "https://api.realo.com/1.0"',
            language="toml"
        )
        st.caption("Ajoute ces lignes dans Streamlit → Manage app → Settings → Secrets. Ne mets jamais la clé privée dans GitHub.")

    st.markdown("**Test de connexion Realo**")
    test_id = st.number_input("ID d'une annonce Realo à tester", min_value=1, value=1, step=1)
    if st.button("Tester Realo"):
        try:
            data = realo_signed_request("GET", f"/listings/{test_id}")
            st.success("Connexion Realo réussie.")
            st.json(data)
        except Exception as e:
            st.error(f"Test Realo : {e}")

    st.markdown("**Recherche Realo (mode avancé)**")
    st.caption("L'accès exact aux endpoints de recherche dépend du produit/compte Realo. La V5.1 signe correctement les requêtes et permet d'adapter le chemin et les paramètres sans exposer les clés.")
    c1,c2 = st.columns(2)
    realo_path = c1.text_input("Chemin endpoint", "/listings")
    realo_query = c2.text_input("Paramètre q (optionnel)", "")
    if st.button("Lancer la recherche Realo"):
        try:
            params = {"q": realo_query} if realo_query.strip() else None
            payload = realo_signed_request("GET", realo_path, params=params)
            items = payload.get("data", payload) if isinstance(payload, dict) else payload
            if isinstance(items, dict):
                items = [items]
            if not isinstance(items, list):
                raise ValueError("Réponse Realo non reconnue comme liste d'annonces.")
            rdf = pd.DataFrame([flatten_realo_listing(x) for x in items if isinstance(x, dict)])
            st.session_state.realo_df = rdf
            st.success(f"{len(rdf)} élément(s) Realo importé(s).")
            st.dataframe(rdf, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Recherche Realo : {e}")

    st.divider()
    st.subheader("🇧🇪 Statbel — marché immobilier officiel")
    st.caption("Source officielle Statbel : ventes immobilières et quartiles Q25/Q50/Q75. La V5.1.1 essaie le XLSX puis bascule automatiquement sur le ZIP/TXT officiel si nécessaire.")
    if st.button("Charger / actualiser Statbel"):
        try:
            sdf = load_statbel_real_estate()
            st.session_state.statbel_df = sdf
            st.success(f"Statbel chargé : {len(sdf):,} lignes.")
        except Exception as e:
            st.error(f"Chargement Statbel : {e}")

    sdf = st.session_state.statbel_df
    if isinstance(sdf, pd.DataFrame) and not sdf.empty:
        st.write("Aperçu des données Statbel :")
        st.dataframe(sdf.head(50), use_container_width=True, hide_index=True)

        cols_guess = statbel_guess_columns(sdf)
        st.caption("La structure des colonnes peut évoluer ; la V5.1 affiche le fichier brut et tente une détection automatique.")
        geo_col = cols_guess["geo"]
        q50_col = cols_guess["q50"]

        if geo_col and q50_col:
            commune = st.text_input("Rechercher une commune Statbel", st.session_state.city, key="statbel_commune")
            subset = sdf[sdf[geo_col].astype(str).str.contains(commune, case=False, na=False)]
            if not subset.empty:
                st.dataframe(subset.tail(30), use_container_width=True, hide_index=True)
                numeric_q50 = pd.to_numeric(subset[q50_col], errors="coerce").dropna()
                if not numeric_q50.empty:
                    st.metric("Médiane Q50 repérée", f"{numeric_q50.iloc[-1]:,.0f} €")
            else:
                st.info("Aucune ligne trouvée pour cette commune avec la détection automatique.")
        else:
            st.info("Colonnes commune/Q50 non détectées automatiquement. Le fichier reste consultable et exportable.")

with tsurv:
    st.subheader("📡 Sources automatiques autorisées")
    st.write("Ajoute une URL directe vers un fichier CSV ou un endpoint JSON que tu es autorisé à utiliser.")

    with st.form("add_feed"):
        c1,c2 = st.columns([3,1])
        feed_url = c1.text_input("URL du flux CSV/JSON")
        feed_name = c2.text_input("Nom de la source", "Source")
        if st.form_submit_button("Ajouter la source") and feed_url:
            st.session_state.feed_urls.append({"name":feed_name.strip() or "Source","url":feed_url.strip()})
            st.success("Source ajoutée.")

    if st.session_state.feed_urls:
        for i, feed in enumerate(st.session_state.feed_urls):
            c1,c2,c3 = st.columns([2,5,1])
            c1.write(f"**{feed['name']}**")
            c2.code(feed["url"])
            if c3.button("Suppr.", key=f"del_feed_{i}"):
                st.session_state.feed_urls.pop(i)
                st.rerun()

    if st.button("🔄 Rafraîchir toutes les sources", type="primary"):
        frames = []
        errors = []
        for feed in st.session_state.feed_urls:
            try:
                fdf = fetch_feed(feed["url"])
                fdf = normalize(fdf, feed["name"])
                fdf["source"] = feed["name"]
                frames.append(fdf)
            except Exception as e:
                errors.append(f"{feed['name']}: {e}")

        if frames:
            st.session_state["remote_df"] = pd.concat(frames, ignore_index=True)
            st.session_state.last_refresh = datetime.now(timezone.utc).isoformat()
            st.success(f"{len(st.session_state.remote_df)} annonces récupérées.")
        if errors:
            st.error("Certaines sources n'ont pas pu être chargées :\n\n" + "\n".join(errors))
        if not frames and not errors:
            st.info("Ajoute au moins une source avant de rafraîchir.")

    if st.session_state.last_refresh:
        st.caption(f"Dernier rafraîchissement UTC : {st.session_state.last_refresh}")

    remote = st.session_state.get("remote_df", pd.DataFrame())
    if not remote.empty:
        current_ids = set(remote["id"].astype(str))
        new_ids = current_ids - st.session_state.seen_ids
        st.metric("Nouvelles annonces détectées", len(new_ids))
        remote["nouvelle"] = remote["id"].astype(str).isin(new_ids)
        st.dataframe(remote.head(100), use_container_width=True, hide_index=True)
        if st.button("Marquer comme vues"):
            st.session_state.seen_ids |= current_ids
            st.success("Annonces marquées comme vues.")

with tdata:
    st.subheader("Import manuel")
    uploaded = st.file_uploader("Importer un CSV", type=["csv"])
    st.download_button("Télécharger le modèle CSV", sample_csv.encode("utf-8"), "modele_annonces_v5.csv", "text/csv")

    st.subheader("Ajouter un bien manuellement")
    with st.form("manual_add"):
        a,b,c = st.columns(3)
        title = a.text_input("Titre", "Maison à rénover")
        city = b.text_input("Ville", "Houffalize")
        dist = c.number_input("Distance (km)", 0.0, 500.0, 10.0, 1.0)

        a,b,c,d = st.columns(4)
        price = a.number_input("Prix (€)", 0, 2_000_000, 100000, 5000)
        surf = b.number_input("Surface (m²)", 1.0, 3000.0, 150.0, 5.0)
        land = c.number_input("Terrain (m²)", 0.0, 100000.0, 800.0, 50.0)
        beds = d.number_input("Chambres", 0, 30, 3, 1)

        a,b,c = st.columns(3)
        peb = a.selectbox("PEB", ["A++","A+","A","B","C","D","E","F","G","Inconnu"], index=8)
        resale_value = b.number_input("Valeur après travaux (€)", 0, 3_000_000, 230000, 5000)
        url = c.text_input("URL", "https://example.com")

        st.markdown("**Travaux détaillés**")
        work_fields = [
            ("toiture","Toiture"),("electricite","Électricité"),("chauffage","Chauffage"),
            ("menuiseries","Menuiseries"),("cuisine","Cuisine"),("sdb","Salle de bain"),
            ("sols_peinture","Sols / peinture"),("facade","Façade / isolation"),("autres_travaux","Autres")
        ]
        cols = st.columns(3)
        vals = {}
        for i,(key,label) in enumerate(work_fields):
            vals[key] = cols[i%3].number_input(label+" (€)",0,500000,5000 if i>2 else 10000,1000,key=f"manual_{key}")

        if st.form_submit_button("Ajouter"):
            total = sum(vals.values())
            row = {
                "id": f"MAN-{len(st.session_state.manual_rows)+1:03d}",
                "titre": title, "ville": city, "distance_km": dist, "prix": price,
                "surface_m2": surf, "terrain_m2": land, "chambres": beds, "peb": peb,
                "travaux": total, "valeur_apres_travaux": resale_value, "url": url,
                "source": "Manuel", "date_publication": datetime.now().date().isoformat(),
                **vals
            }
            st.session_state.manual_rows.append(row)
            st.success(f"Bien ajouté — travaux : {total:,.0f} €")

# Agrégation de toutes les sources
frames = [pd.read_csv(StringIO(sample_csv))]
if uploaded:
    frames.append(normalize(pd.read_csv(uploaded), "CSV"))
if st.session_state.manual_rows:
    frames.append(pd.DataFrame(st.session_state.manual_rows))
remote = st.session_state.get("remote_df", pd.DataFrame())
if not remote.empty:
    frames.append(remote)
realo_df = st.session_state.get("realo_df", pd.DataFrame())
if isinstance(realo_df, pd.DataFrame) and not realo_df.empty:
    frames.append(realo_df)

df = pd.concat(frames, ignore_index=True, sort=False)
df = normalize(df)
df = df.drop_duplicates(subset=["id"], keep="last")

required = ["prix","travaux","valeur_apres_travaux","distance_km","surface_m2"]
missing = [c for c in required if c not in df.columns]
if missing:
    st.error("Colonnes obligatoires manquantes : " + ", ".join(missing))
    st.stop()

for c in required + ["terrain_m2","chambres"]:
    if c in df.columns:
        df[c] = pd.to_numeric(df[c], errors="coerce").fillna(0)

analysis_ready = df[(df["surface_m2"] > 0) & (df["valeur_apres_travaux"] > 0)].copy()
if analysis_ready.empty:
    st.warning("Aucune annonce n’a encore une surface et une valeur après travaux suffisantes pour le scoring.")
    res = pd.DataFrame(columns=list(df.columns)+["benefice","marge","score","offre_cible","prix_max_absolu"])
else:
    res = analyze(analysis_ready)

with tdata:
    st.subheader("Base consolidée")
    st.metric("Annonces uniques", len(df))
    st.dataframe(df, use_container_width=True, hide_index=True)

qualified = res[
    (res["prix"] <= st.session_state.budget) &
    (res["distance_km"] <= st.session_state.radius) &
    (res["travaux"] <= st.session_state.works_max) &
    (res["benefice"] >= st.session_state.profit) &
    (res["marge"] >= st.session_state.margin)
].sort_values("score", ascending=False)

with trank:
    st.subheader("🔥 Pépites détectées")
    a,b,c,d,e = st.columns(5)
    a.metric("Biens analysés", len(res))
    b.metric("Pépites", len(qualified))
    c.metric("Score max", f"{qualified.score.max():.0f}/100" if len(qualified) else "—")
    d.metric("Bénéfice max", f"{qualified.benefice.max():,.0f} €" if len(qualified) else "—")
    e.metric("Marge max", f"{qualified.marge.max():.1f}%" if len(qualified) else "—")

    show = qualified if len(qualified) else res.sort_values("score", ascending=False)
    if len(qualified) == 0:
        st.warning("Aucun bien ne respecte tous les seuils. Voici les meilleurs candidats.")

    cols = ["source","date_publication","titre","ville","peb","chambres","surface_m2","terrain_m2",
            "prix","travaux","valeur_apres_travaux","benefice","marge","offre_cible","prix_max_absolu","score"]
    st.dataframe(show[[c for c in cols if c in show.columns]], use_container_width=True, hide_index=True)

    st.subheader("Alertes")
    alerts = show[show["score"] >= 85]
    if alerts.empty:
        st.info("Aucune alerte forte actuellement.")
    else:
        for _, r in alerts.head(20).iterrows():
            st.success(f"🔥 {r['titre']} — {r['ville']} — Score {r['score']:.0f}/100 — bénéfice {r['benefice']:,.0f} € — marge {r['marge']:.1f}%")

with toffer:
    labels = (res["titre"].astype(str)+" — "+res["ville"].astype(str)+" — "+res["id"].astype(str)).tolist()
    pick = st.selectbox("Choisir un bien", labels, key="offer_choice_v5")
    r = res.iloc[labels.index(pick)]

    a,b,c = st.columns(3)
    a.metric("🟢 Offre prudente", f"{r.offre_prudente:,.0f} €")
    b.metric("🎯 Offre cible", f"{r.offre_cible:,.0f} €")
    c.metric("🚫 Maximum absolu", f"{r.prix_max_absolu:,.0f} €")

    offer = st.number_input("Tester une offre (€)", 0, 2_000_000, int(r.prix), 1000)
    m = calc(r, offer)

    a,b,c = st.columns(3)
    a.metric("Coût total", f"{m['cout_total']:,.0f} €")
    b.metric("Bénéfice", f"{m['benefice']:,.0f} €")
    c.metric("Marge", f"{m['marge']:.1f}%")

    if m["benefice"] >= st.session_state.profit and m["marge"] >= st.session_state.margin:
        st.success("✅ Offre compatible avec tes objectifs.")
    else:
        st.error("❌ Offre trop élevée pour tes objectifs.")

    cost_df = pd.DataFrame({
        "Poste":["Achat","Droits","Notaire","Travaux","Imprévus","Intérêts","Détention","Revente"],
        "Montant (€)":[m["achat"],m["droits"],m["notaire"],m["travaux_calc"],m["imprevus"],m["interets"],m["detention"],m["revente"]]
    })
    st.dataframe(cost_df, use_container_width=True, hide_index=True)

    note_key = str(r["id"])
    note = st.text_area("Notes / stratégie de négociation", st.session_state.notes.get(note_key,""), key=f"note_{note_key}")
    st.session_state.notes[note_key] = note

    if st.button("⭐ Ajouter/retirer des favoris"):
        if note_key in st.session_state.favorites:
            st.session_state.favorites.remove(note_key)
        else:
            st.session_state.favorites.add(note_key)
        st.rerun()

with tworks:
    labels = (res["titre"].astype(str)+" — "+res["ville"].astype(str)+" — "+res["id"].astype(str)).tolist()
    pick = st.selectbox("Choisir un bien", labels, key="works_choice_v5")
    r = res.iloc[labels.index(pick)]
    mapping = {
        "Toiture":"toiture","Électricité":"electricite","Chauffage":"chauffage",
        "Menuiseries":"menuiseries","Cuisine":"cuisine","Salle de bain":"sdb",
        "Sols / peinture":"sols_peinture","Façade / isolation":"facade","Autres":"autres_travaux"
    }
    wdf = pd.DataFrame([{"Poste":lab,"Montant (€)":float(r.get(col,0) or 0)} for lab,col in mapping.items()])
    st.dataframe(wdf, use_container_width=True, hide_index=True)
    st.metric("Total détaillé", f"{wdf['Montant (€)'].sum():,.0f} €")
    st.metric("Travaux utilisés", f"{r.travaux:,.0f} €")

with tcomp:
    st.subheader("📊 Comparables")
    with st.form("comp_v5"):
        a,b,c,d = st.columns(4)
        cv = a.text_input("Ville", "Houffalize")
        cp = b.number_input("Prix (€)", 0, 3_000_000, 250000, 5000)
        cs = c.number_input("Surface (m²)", 1.0, 3000.0, 150.0, 5.0)
        ce = d.selectbox("État", ["Rénové","Bon état","À rafraîchir","À rénover"])
        if st.form_submit_button("Ajouter comparable"):
            st.session_state.comparables.append({"ville":cv,"prix":cp,"surface_m2":cs,"etat":ce,"prix_m2":cp/cs})

    if st.session_state.comparables:
        cdf = pd.DataFrame(st.session_state.comparables)
        st.dataframe(cdf, use_container_width=True, hide_index=True)
        avg = cdf["prix_m2"].mean()
        st.metric("Prix/m² moyen", f"{avg:,.0f} €/m²")

        labels = (res["titre"].astype(str)+" — "+res["ville"].astype(str)+" — "+res["id"].astype(str)).tolist()
        pick = st.selectbox("Comparer avec", labels, key="comp_choice_v5")
        r = res.iloc[labels.index(pick)]
        theo = avg * r.surface_m2
        a,b,c = st.columns(3)
        a.metric("Valeur par comparables", f"{theo:,.0f} €")
        b.metric("Valeur saisie", f"{r.valeur_apres_travaux:,.0f} €")
        c.metric("Écart", f"{r.valeur_apres_travaux-theo:,.0f} €")
    else:
        st.info("Ajoute au moins un comparable.")

with tfav:
    st.subheader("⭐ Favoris")
    fav = res[res["id"].astype(str).isin(st.session_state.favorites)]
    if fav.empty:
        st.info("Aucun favori pour le moment.")
    else:
        cols = ["titre","ville","prix","travaux","valeur_apres_travaux","benefice","marge","offre_cible","prix_max_absolu","score","url"]
        st.dataframe(fav[[c for c in cols if c in fav.columns]], use_container_width=True, hide_index=True)

st.divider()
st.download_button(
    "📤 Exporter toutes les analyses",
    res.sort_values("score",ascending=False).to_csv(index=False).encode("utf-8"),
    "classement_pepites_v5.csv",
    "text/csv"
)
st.caption("V5 : collecte uniquement via sources autorisées (API/CSV/JSON/export). Les estimations doivent être vérifiées avant toute décision.")
