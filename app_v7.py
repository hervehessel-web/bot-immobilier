
import streamlit as st
import pandas as pd
import numpy as np
import requests
from io import StringIO, BytesIO
from datetime import datetime, timezone
import hashlib
import json
import re
import hmac
import base64
import zipfile as pyzipfile
from urllib.parse import urlencode
from pathlib import Path

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
st.session_state.setdefault("pipeline_status", {})
st.session_state.setdefault("v7_shortlist_threshold", 80)
st.session_state.setdefault("v7_market_price_m2", {})
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
    V6: local-first Statbel loader.
    Put the official Statbel file in the GitHub repository.
    Supported names/formats: statbel_immobilier.xlsx, vastgoed_2010_9999.xlsx,
    statbel_immobilier.csv, statbel_immobilier.txt.
    """
    candidates = [
        Path("statbel_immobilier.xlsx"),
        Path("vastgoed_2010_9999.xlsx"),
        Path("statbel_immobilier.csv"),
        Path("statbel_immobilier.txt"),
    ]

    for path in candidates:
        if not path.exists():
            continue

        if path.suffix.lower() == ".xlsx":
            return pd.read_excel(path, engine="openpyxl")

        last_error = None
        for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
            for sep in ("\\t", ";", ",", "|"):
                try:
                    df = pd.read_csv(path, encoding=enc, sep=sep, low_memory=False)
                    if df.shape[1] >= 4 and len(df) > 0:
                        return df
                except Exception as e:
                    last_error = e
        raise RuntimeError(f"Fichier Statbel local trouvé mais illisible : {last_error}")

    raise FileNotFoundError(
        "Fichier Statbel absent du dépôt GitHub. "
        "Ajoutez le fichier officiel sous le nom 'statbel_immobilier.xlsx' "
        "ou 'vastgoed_2010_9999.xlsx', puis redémarrez l'application."
    )

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
    st.caption("V6 : données Statbel chargées localement depuis GitHub — plus de dépendance au téléchargement direct depuis Streamlit Cloud.")
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



# =========================================================
# V7 — CHASSEUR INTELLIGENT
# =========================================================

def decision_v6(row):
    score = float(row.get("score", 0) or 0)
    profit = float(row.get("benefice", 0) or 0)
    margin = float(row.get("marge", -999) or -999)
    pmax = float(row.get("prix_max_absolu", 0) or 0)
    price = float(row.get("prix", 0) or 0)

    if score >= 80 and profit >= st.session_state.profit and margin >= st.session_state.margin and price <= pmax:
        return "ACHETER", "Le bien respecte les objectifs de marge, de bénéfice et de prix."
    if score >= 60 and pmax > 0:
        return "NÉGOCIER", f"Prix maximum calculé : {pmax:,.0f} €."
    return "ÉCARTER", "Le rapport prix / travaux / valeur de sortie est insuffisant selon les hypothèses."

def stress_metrics_v6(row, works_factor, resale_factor, months_delta):
    clone = row.copy()
    clone["travaux"] = float(row["travaux"]) * works_factor
    clone["valeur_apres_travaux"] = float(row["valeur_apres_travaux"]) * resale_factor

    old_months = st.session_state.months
    try:
        st.session_state.months = max(1, int(old_months + months_delta))
        return calc(clone)
    finally:
        st.session_state.months = old_months

st.divider()
st.header("🧠 V7 — Chasseur intelligent")

v6tabs = st.tabs([
    "🔗 Nouvelle annonce",
    "🚦 Décision",
    "🧪 Stress test",
    "✅ Checklist",
    "📋 Tableau global",
    "🗑️ Gestion des recherches"
])

v6_new, v6_dec, v6_stress, v6_check, v6_global, v6_manage = v6tabs

with v6_new:
    st.subheader("Analyser une nouvelle annonce")
    st.caption("Colle le lien pour le conserver. La V6 ne contourne pas les protections des portails : complète les données visibles dans l'annonce.")

    with st.form("v6_add_form"):
        url = st.text_input("URL de l'annonce")
        c1,c2,c3 = st.columns(3)
        title = c1.text_input("Titre / référence", "Maison à rénover")
        city = c2.text_input("Commune", st.session_state.city)
        dist = c3.number_input("Distance (km)", 0.0, 500.0, 10.0, 1.0)

        c1,c2,c3,c4 = st.columns(4)
        price = c1.number_input("Prix affiché (€)", 0, 2_000_000, 100000, 5000)
        surf = c2.number_input("Surface habitable (m²)", 1.0, 3000.0, 150.0, 5.0)
        land = c3.number_input("Terrain (m²)", 0.0, 100000.0, 800.0, 50.0)
        beds = c4.number_input("Chambres", 0, 30, 3, 1)

        c1,c2,c3 = st.columns(3)
        peb = c1.selectbox("PEB", ["A++","A+","A","B","C","D","E","F","G","Inconnu"], index=8, key="v6_peb")
        works = c2.number_input("Travaux réalistes (€)", 0, 1_000_000, 70000, 5000)
        resale = c3.number_input("Valeur de revente réaliste (€)", 0, 3_000_000, 240000, 5000)

        notes = st.text_area("Notes", placeholder="Toiture, humidité, électricité, urbanisme, servitudes, etc.")

        if st.form_submit_button("Ajouter et analyser", type="primary"):
            rid = f"V6-{len(st.session_state.manual_rows)+1:03d}"
            new_row = {
                "id": rid, "titre": title, "ville": city, "distance_km": dist,
                "prix": price, "surface_m2": surf, "terrain_m2": land,
                "chambres": beds, "peb": peb, "travaux": works,
                "valeur_apres_travaux": resale, "url": url,
                "source": "V6", "date_publication": "",
                "toiture":0,"electricite":0,"chauffage":0,"menuiseries":0,
                "cuisine":0,"sdb":0,"sols_peinture":0,"facade":0,
                "autres_travaux":works
            }
            st.session_state.manual_rows.append(new_row)
            st.session_state.notes[rid] = notes

            m = calc(pd.Series(new_row))
            pmax = solve_price(pd.Series(new_row), st.session_state.profit, st.session_state.margin)

            st.success("Bien ajouté.")
            a,b,c,d = st.columns(4)
            a.metric("Coût total", f"{m['cout_total']:,.0f} €")
            b.metric("Bénéfice", f"{m['benefice']:,.0f} €")
            c.metric("Marge", f"{m['marge']:.1f}%")
            d.metric("Prix max", f"{pmax:,.0f} €")

with v6_dec:
    st.subheader("Acheter, négocier ou écarter")
    if res.empty:
        st.info("Ajoute d'abord un bien.")
    else:
        labels = (res["titre"].astype(str)+" — "+res["ville"].astype(str)+" — "+res["id"].astype(str)).tolist()
        pick = st.selectbox("Bien", labels, key="v6_dec_pick")
        r = res.iloc[labels.index(pick)]

        decision, reason = decision_v6(r)
        if decision == "ACHETER":
            st.success(f"✅ **ACHETER** — {reason}")
        elif decision == "NÉGOCIER":
            st.warning(f"🤝 **NÉGOCIER** — {reason}")
        else:
            st.error(f"⛔ **ÉCARTER** — {reason}")

        a,b,c,d = st.columns(4)
        a.metric("Score", f"{r.score:.0f}/100")
        b.metric("Bénéfice", f"{r.benefice:,.0f} €")
        c.metric("Marge", f"{r.marge:.1f}%")
        d.metric("Prix max", f"{r.prix_max_absolu:,.0f} €")

        a,b,c = st.columns(3)
        a.metric("Offre prudente", f"{r.offre_prudente:,.0f} €")
        b.metric("Offre cible", f"{r.offre_cible:,.0f} €")
        c.metric("Maximum absolu", f"{r.prix_max_absolu:,.0f} €")

with v6_stress:
    st.subheader("Stress test")
    st.caption("Teste la solidité du projet si les travaux augmentent, la revente baisse et la durée s'allonge.")

    if res.empty:
        st.info("Ajoute d'abord un bien.")
    else:
        labels = (res["titre"].astype(str)+" — "+res["ville"].astype(str)+" — "+res["id"].astype(str)).tolist()
        pick = st.selectbox("Bien", labels, key="v6_stress_pick")
        r = res.iloc[labels.index(pick)]

        scenarios = [
            ("Optimiste",0.90,1.03,-2),
            ("Réaliste",1.00,1.00,0),
            ("Défavorable",1.20,0.95,3),
            ("Très défavorable",1.30,0.90,6),
        ]

        rows = []
        for name,wf,rf,md in scenarios:
            sm = stress_metrics_v6(r,wf,rf,md)
            rows.append({
                "Scénario":name,
                "Travaux (€)":float(r.travaux)*wf,
                "Revente (€)":float(r.valeur_apres_travaux)*rf,
                "Durée (mois)":max(1,st.session_state.months+md),
                "Bénéfice (€)":sm["benefice"],
                "Marge (%)":sm["marge"]
            })

        sdf = pd.DataFrame(rows)
        st.dataframe(sdf, use_container_width=True, hide_index=True)

        bad = sdf[sdf["Scénario"]=="Défavorable"].iloc[0]
        if bad["Bénéfice (€)"] >= 0 and bad["Marge (%)"] >= max(10, st.session_state.margin-5):
            st.success("✅ Le projet résiste au scénario défavorable.")
        else:
            st.error("⚠️ Le projet devient fragile en scénario défavorable.")

with v6_check:
    st.subheader("Checklist avant offre")
    items = [
        "Structure / fissures","Toiture / charpente","Humidité / infiltrations",
        "Électricité / conformité","Chauffage / chaudière","Châssis / vitrages",
        "Égouttage / fosse septique","PEB / isolation","Urbanisme / infractions",
        "Servitudes / accès","Amiante si bien ancien","Cadastre / parcelle",
        "Devis travaux","Comparables de revente","Financement validé",
        "Frais d'acquisition vérifiés"
    ]
    done = 0
    for item in items:
        if st.checkbox(item, key="v6_"+item):
            done += 1
    ratio = done/len(items)
    st.progress(ratio)
    st.write(f"**{done}/{len(items)} vérifications terminées.**")
    if ratio < .5:
        st.warning("Dossier encore trop incomplet pour une offre ferme.")
    elif ratio < .85:
        st.info("Dossier avancé, mais quelques contrôles restent ouverts.")
    else:
        st.success("Dossier bien préparé pour une négociation ou une offre.")

with v6_global:
    st.subheader("Tableau global de décision")
    if res.empty:
        st.info("Aucun bien analysable.")
    else:
        rows = []
        for _,r in res.iterrows():
            d,_ = decision_v6(r)
            rows.append({
                "Décision":d,
                "Score":r.get("score",0),
                "Bien":r.get("titre",""),
                "Ville":r.get("ville",""),
                "Prix (€)":r.get("prix",0),
                "Travaux (€)":r.get("travaux",0),
                "Revente (€)":r.get("valeur_apres_travaux",0),
                "Bénéfice (€)":r.get("benefice",0),
                "Marge (%)":r.get("marge",0),
                "Offre cible (€)":r.get("offre_cible",0),
                "Maximum (€)":r.get("prix_max_absolu",0),
            })
        ddf = pd.DataFrame(rows)
        order={"ACHETER":0,"NÉGOCIER":1,"ÉCARTER":2}
        ddf["_o"]=ddf["Décision"].map(order).fillna(9)
        ddf=ddf.sort_values(["_o","Score"],ascending=[True,False]).drop(columns="_o")
        st.dataframe(ddf,use_container_width=True,hide_index=True)
        st.download_button(
            "Télécharger décisions V6",
            ddf.to_csv(index=False).encode("utf-8"),
            "decisions_v6.csv",
            "text/csv"
        )


with v6_manage:
    st.subheader("🗑️ Gestion des recherches")
    st.caption("Supprime les biens ajoutés manuellement sans toucher aux données Statbel ni aux paramètres généraux du bot.")

    manual = st.session_state.get("manual_rows", [])

    if not manual:
        st.info("Aucune recherche manuelle à supprimer.")
    else:
        options = []
        by_label = {}
        for i, item in enumerate(manual):
            rid = str(item.get("id", f"bien-{i+1}"))
            title = str(item.get("titre", "Sans titre"))
            city = str(item.get("ville", ""))
            price = float(item.get("prix", 0) or 0)
            label = f"{title} — {city} — {price:,.0f} € — {rid}"
            options.append(label)
            by_label[label] = i

        selected = st.multiselect(
            "Sélectionne les recherches à supprimer",
            options,
            key="v61_delete_selected"
        )

        c1, c2 = st.columns(2)

        with c1:
            if st.button(
                "Supprimer la sélection",
                disabled=not selected,
                type="secondary",
                key="v61_delete_button"
            ):
                indexes = {by_label[x] for x in selected}
                removed_ids = [
                    str(manual[i].get("id", ""))
                    for i in indexes
                ]
                st.session_state.manual_rows = [
                    item for i, item in enumerate(manual)
                    if i not in indexes
                ]
                for rid in removed_ids:
                    st.session_state.get("notes", {}).pop(rid, None)

                st.success(f"{len(indexes)} recherche(s) supprimée(s).")
                st.rerun()

        with c2:
            confirm_all = st.checkbox(
                "Je confirme vouloir supprimer toutes les recherches manuelles",
                key="v61_confirm_delete_all"
            )
            if st.button(
                "Tout supprimer",
                disabled=not confirm_all,
                type="primary",
                key="v61_delete_all_button"
            ):
                st.session_state.manual_rows = []
                st.session_state.notes = {}
                st.success("Toutes les recherches manuelles ont été supprimées.")
                st.rerun()

        st.divider()
        st.write(f"**{len(manual)} recherche(s) manuelle(s) actuellement enregistrée(s).**")
        preview = pd.DataFrame(manual)
        cols = [c for c in ["id", "titre", "ville", "prix", "travaux", "valeur_apres_travaux", "url"] if c in preview.columns]
        if cols:
            st.dataframe(preview[cols], use_container_width=True, hide_index=True)


# =========================================================
# V7 — AUTOMATISATION AVANCÉE
# =========================================================
st.divider()
st.header("🚀 V7 — Automatisation avancée")
st.caption("Objectif : réduire au maximum la saisie manuelle tout en gardant une analyse contrôlable et explicable.")

v7tabs = st.tabs([
    "⚡ Capture rapide",
    "🔨 Travaux auto",
    "🎯 Shortlist",
    "🗂️ Pipeline",
    "💾 Sauvegarde",
    "📊 Analyse en masse"
])

v7_quick, v7_works, v7_short, v7_pipe, v7_backup, v7_batch = v7tabs

def v7_parse_listing_text(raw):
    """Extraction heuristique de quelques champs depuis du texte collé."""
    out = {}
    txt = raw.replace("\xa0", " ")

    # Price
    price_patterns = [
        r'(\d{2,3}(?:[ .]\d{3})+)\s*€',
        r'prix[^0-9]{0,20}(\d{2,3}(?:[ .]\d{3})+)',
    ]
    for pat in price_patterns:
        m = re.search(pat, txt, re.I)
        if m:
            out["prix"] = int(re.sub(r"[ .]", "", m.group(1)))
            break

    # Surface
    m = re.search(r'(\d{2,4}(?:[.,]\d+)?)\s*m[²2]\b', txt, re.I)
    if m:
        out["surface_m2"] = float(m.group(1).replace(",", "."))

    # Bedrooms
    for pat in [
        r'(\d{1,2})\s*chambre',
        r'chambres?\s*[:\-]?\s*(\d{1,2})',
    ]:
        m = re.search(pat, txt, re.I)
        if m:
            out["chambres"] = int(m.group(1))
            break

    # PEB
    m = re.search(r'\bPEB\s*[:\-]?\s*(A\+\+|A\+|A|B|C|D|E|F|G)\b', txt, re.I)
    if m:
        out["peb"] = m.group(1).upper()

    # Terrain
    m = re.search(r'(?:terrain|parcelle)[^0-9]{0,20}(\d{2,6}(?:[.,]\d+)?)\s*m[²2]', txt, re.I)
    if m:
        out["terrain_m2"] = float(m.group(1).replace(",", "."))

    return out

def v7_estimate_works(surface, level, roof=False, windows=False, heating=False, electric=False, damp=False):
    base_rates = {
        "Rafraîchissement": 350,
        "Rénovation légère": 650,
        "Rénovation moyenne": 1000,
        "Rénovation lourde": 1450,
        "Quasi reconstruction": 1900,
    }
    total = float(surface) * base_rates[level]
    extras = 0
    extras += 25000 if roof else 0
    extras += 18000 if windows else 0
    extras += 18000 if heating else 0
    extras += 14000 if electric else 0
    extras += 12000 if damp else 0
    return total + extras

def v7_status_for(rid):
    return st.session_state.pipeline_status.get(str(rid), "À analyser")

with v7_quick:
    st.subheader("⚡ Capture rapide d'une annonce")
    st.write("Copie-colle le texte d'une annonce. V7 tente d'en extraire le prix, la surface, le PEB, les chambres et le terrain.")

    raw = st.text_area(
        "Texte de l'annonce",
        height=220,
        placeholder="Colle ici le texte visible de l'annonce..."
    )

    parsed = v7_parse_listing_text(raw) if raw.strip() else {}

    if parsed:
        st.success("Informations détectées automatiquement :")
        st.json(parsed)
    elif raw.strip():
        st.info("Aucun champ fiable détecté automatiquement. Tu peux compléter manuellement.")

    with st.form("v7_quick_form"):
        c1,c2,c3 = st.columns(3)
        q_title = c1.text_input("Titre", "Annonce capturée")
        q_city = c2.text_input("Commune", st.session_state.city)
        q_url = c3.text_input("URL")

        c1,c2,c3,c4 = st.columns(4)
        q_price = c1.number_input("Prix (€)", 0, 2_000_000, int(parsed.get("prix", 100000)), 5000)
        q_surface = c2.number_input("Surface (m²)", 1.0, 3000.0, float(parsed.get("surface_m2", 150)), 5.0)
        q_land = c3.number_input("Terrain (m²)", 0.0, 100000.0, float(parsed.get("terrain_m2", 0)), 50.0)
        q_beds = c4.number_input("Chambres", 0, 30, int(parsed.get("chambres", 3)), 1)

        c1,c2,c3 = st.columns(3)
        peb_options = ["A++","A+","A","B","C","D","E","F","G","Inconnu"]
        detected_peb = parsed.get("peb", "Inconnu")
        peb_idx = peb_options.index(detected_peb) if detected_peb in peb_options else 9
        q_peb = c1.selectbox("PEB", peb_options, index=peb_idx)
        q_dist = c2.number_input("Distance (km)", 0.0, 500.0, 10.0, 1.0)
        q_resale = c3.number_input("Valeur de revente estimée (€)", 0, 3_000_000, 240000, 5000)

        level = st.selectbox(
            "Niveau de rénovation estimé",
            ["Rafraîchissement","Rénovation légère","Rénovation moyenne","Rénovation lourde","Quasi reconstruction"],
            index=2
        )

        c1,c2,c3,c4,c5 = st.columns(5)
        roof = c1.checkbox("Toiture")
        windows = c2.checkbox("Châssis")
        heating = c3.checkbox("Chauffage")
        electric = c4.checkbox("Électricité")
        damp = c5.checkbox("Humidité")

        auto_works = v7_estimate_works(q_surface, level, roof, windows, heating, electric, damp)
        st.metric("Travaux V7 estimés", f"{auto_works:,.0f} €")

        if st.form_submit_button("Ajouter cette annonce", type="primary"):
            rid = f"V7-{len(st.session_state.manual_rows)+1:03d}"
            row = {
                "id": rid, "titre": q_title, "ville": q_city, "distance_km": q_dist,
                "prix": q_price, "surface_m2": q_surface, "terrain_m2": q_land,
                "chambres": q_beds, "peb": q_peb, "travaux": auto_works,
                "valeur_apres_travaux": q_resale, "url": q_url,
                "source": "V7 capture", "date_publication": "",
                "toiture": 25000 if roof else 0,
                "electricite": 14000 if electric else 0,
                "chauffage": 18000 if heating else 0,
                "menuiseries": 18000 if windows else 0,
                "cuisine": 0, "sdb": 0, "sols_peinture": 0,
                "facade": 0, "autres_travaux": 12000 if damp else 0
            }
            st.session_state.manual_rows.append(row)
            st.session_state.pipeline_status[rid] = "À analyser"
            st.success("Annonce ajoutée au bot.")

with v7_works:
    st.subheader("🔨 Estimation rapide des travaux")
    c1,c2 = st.columns(2)
    surf = c1.number_input("Surface à rénover (m²)", 1.0, 3000.0, 150.0, 5.0, key="v7w_surf")
    level = c2.selectbox(
        "Niveau",
        ["Rafraîchissement","Rénovation légère","Rénovation moyenne","Rénovation lourde","Quasi reconstruction"],
        index=2,
        key="v7w_level"
    )

    c1,c2,c3,c4,c5 = st.columns(5)
    roof = c1.checkbox("Toiture à refaire", key="v7w_roof")
    windows = c2.checkbox("Châssis à refaire", key="v7w_windows")
    heating = c3.checkbox("Chauffage à refaire", key="v7w_heating")
    electric = c4.checkbox("Électricité à refaire", key="v7w_electric")
    damp = c5.checkbox("Humidité importante", key="v7w_damp")

    estimate = v7_estimate_works(surf, level, roof, windows, heating, electric, damp)
    st.metric("Budget travaux indicatif", f"{estimate:,.0f} €")
    st.caption("Estimation de présélection uniquement. Elle doit être remplacée par des devis dès que le bien devient sérieux.")

with v7_short:
    st.subheader("🎯 Shortlist automatique")
    threshold = st.slider(
        "Score minimum pour la shortlist",
        50, 100,
        int(st.session_state.v7_shortlist_threshold),
        5
    )
    st.session_state.v7_shortlist_threshold = threshold

    if res.empty:
        st.info("Aucun bien analysable.")
    else:
        shortlist = res[
            (res["score"] >= threshold) &
            (res["benefice"] > 0)
        ].sort_values(["score","benefice"], ascending=[False,False])

        st.metric("Biens en shortlist", len(shortlist))

        if shortlist.empty:
            st.info("Aucun bien ne dépasse le seuil actuel.")
        else:
            cols = [c for c in [
                "titre","ville","prix","travaux","valeur_apres_travaux",
                "benefice","marge","offre_cible","prix_max_absolu","score"
            ] if c in shortlist.columns]
            st.dataframe(shortlist[cols], use_container_width=True, hide_index=True)

with v7_pipe:
    st.subheader("🗂️ Pipeline de suivi")
    statuses = ["À analyser","À visiter","Visité","Offre à préparer","Offre envoyée","Négociation","Accepté","Écarté"]

    manual = st.session_state.get("manual_rows", [])
    if not manual:
        st.info("Aucun bien manuel dans le pipeline.")
    else:
        for i,item in enumerate(manual):
            rid = str(item.get("id", f"row-{i}"))
            c1,c2,c3,c4 = st.columns([3,2,2,2])
            c1.write(f"**{item.get('titre','Sans titre')}** — {item.get('ville','')}")
            c2.write(f"{float(item.get('prix',0) or 0):,.0f} €")
            current = v7_status_for(rid)
            new_status = c3.selectbox(
                "Statut",
                statuses,
                index=statuses.index(current) if current in statuses else 0,
                key=f"v7_status_{rid}",
                label_visibility="collapsed"
            )
            st.session_state.pipeline_status[rid] = new_status
            c4.write(str(item.get("url",""))[:45])

        pipe_df = pd.DataFrame([
            {
                "id": item.get("id",""),
                "Bien": item.get("titre",""),
                "Ville": item.get("ville",""),
                "Prix": item.get("prix",0),
                "Statut": v7_status_for(item.get("id","")),
            }
            for item in manual
        ])
        st.dataframe(pipe_df, use_container_width=True, hide_index=True)

with v7_backup:
    st.subheader("💾 Sauvegarder / restaurer le travail")
    backup = {
        "manual_rows": st.session_state.get("manual_rows", []),
        "pipeline_status": st.session_state.get("pipeline_status", {}),
        "notes": st.session_state.get("notes", {}),
        "comparables": st.session_state.get("comparables", []),
        "favorites": list(st.session_state.get("favorites", set())),
    }

    st.download_button(
        "Télécharger une sauvegarde JSON",
        json.dumps(backup, ensure_ascii=False, indent=2).encode("utf-8"),
        "chasseur_pepites_v7_backup.json",
        "application/json"
    )

    restore_file = st.file_uploader("Restaurer une sauvegarde JSON", type=["json"], key="v7_restore")
    if restore_file is not None:
        try:
            data = json.load(restore_file)
            if st.button("Restaurer cette sauvegarde", type="primary"):
                st.session_state.manual_rows = data.get("manual_rows", [])
                st.session_state.pipeline_status = data.get("pipeline_status", {})
                st.session_state.notes = data.get("notes", {})
                st.session_state.comparables = data.get("comparables", [])
                st.session_state.favorites = set(data.get("favorites", []))
                st.success("Sauvegarde restaurée.")
                st.rerun()
        except Exception as e:
            st.error(f"Fichier de sauvegarde invalide : {e}")

with v7_batch:
    st.subheader("📊 Analyse en masse")
    if res.empty:
        st.info("Aucun bien analysable.")
    else:
        stress = []
        for _,r in res.iterrows():
            bad = stress_metrics_v6(r, 1.20, 0.95, 3)
            decision, _ = decision_v6(r)
            stress.append({
                "Décision": decision,
                "Bien": r.get("titre",""),
                "Ville": r.get("ville",""),
                "Score": r.get("score",0),
                "Bénéfice réaliste (€)": r.get("benefice",0),
                "Marge réaliste (%)": r.get("marge",0),
                "Bénéfice défavorable (€)": bad["benefice"],
                "Marge défavorable (%)": bad["marge"],
                "Prix max (€)": r.get("prix_max_absolu",0),
            })

        batch = pd.DataFrame(stress)
        batch = batch.sort_values(
            ["Bénéfice défavorable (€)","Score"],
            ascending=[False,False]
        )
        st.dataframe(batch, use_container_width=True, hide_index=True)
        st.download_button(
            "Exporter l'analyse en masse",
            batch.to_csv(index=False).encode("utf-8"),
            "analyse_v7.csv",
            "text/csv"
        )
