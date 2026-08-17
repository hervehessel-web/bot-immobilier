
import streamlit as st
import pandas as pd
import numpy as np
from io import StringIO

st.set_page_config(
    page_title="Chasseur de Pépites Belgique",
    page_icon="🏠",
    layout="wide",
    initial_sidebar_state="expanded"
)

DEFAULTS = {
    "budget_max": 150000,
    "radius": 80,
    "works_max": 100000,
    "min_margin": 20.0,
    "min_profit": 40000,
    "loan_rate": 3.8,
    "holding_months": 12,
    "financing_share": 100,
    "reg_rate": 12.5,
    "notary_rate": 1.5,
    "sale_rate": 3.0,
    "contingency_rate": 10.0,
    "target_city": "Houffalize",
}

for k, v in DEFAULTS.items():
    if k not in st.session_state:
        st.session_state[k] = v

st.title("🏠 Chasseur de Pépites Immobilières — Belgique")
st.caption("Application web autonome pour analyser des opérations achat → rénovation → revente.")

with st.sidebar:
    st.header("🎯 Critères de chasse")
    st.session_state.budget_max = st.number_input("Budget achat max (€)", min_value=0, max_value=2_000_000, value=int(st.session_state.budget_max), step=5000)
    st.session_state.radius = st.number_input("Rayon max (km)", min_value=1, max_value=300, value=int(st.session_state.radius), step=5)
    st.session_state.works_max = st.number_input("Travaux max (€)", min_value=0, max_value=1_000_000, value=int(st.session_state.works_max), step=5000)
    st.session_state.min_margin = st.number_input("Marge cible min (%)", min_value=0.0, max_value=100.0, value=float(st.session_state.min_margin), step=1.0)
    st.session_state.min_profit = st.number_input("Bénéfice cible min (€)", min_value=0, max_value=1_000_000, value=int(st.session_state.min_profit), step=5000)
    st.session_state.target_city = st.text_input("Ville centrale", value=st.session_state.target_city)

    st.header("🏦 Financement")
    st.session_state.loan_rate = st.number_input("Taux annuel (%)", min_value=0.0, max_value=15.0, value=float(st.session_state.loan_rate), step=0.1)
    st.session_state.holding_months = st.number_input("Durée de détention (mois)", min_value=1, max_value=60, value=int(st.session_state.holding_months), step=1)
    st.session_state.financing_share = st.slider("Part financée par crédit (%)", min_value=0, max_value=100, value=int(st.session_state.financing_share), step=5)

    st.header("🧾 Hypothèses de frais")
    st.session_state.reg_rate = st.number_input("Droits d'enregistrement (%)", min_value=0.0, max_value=21.0, value=float(st.session_state.reg_rate), step=0.5)
    st.session_state.notary_rate = st.number_input("Provision acte / frais achat (%)", min_value=0.0, max_value=10.0, value=float(st.session_state.notary_rate), step=0.1)
    st.session_state.sale_rate = st.number_input("Frais de revente (%)", min_value=0.0, max_value=15.0, value=float(st.session_state.sale_rate), step=0.5)
    st.session_state.contingency_rate = st.number_input("Imprévus travaux (%)", min_value=0.0, max_value=30.0, value=float(st.session_state.contingency_rate), step=1.0)

    if st.button("Réinitialiser les paramètres"):
        for k, v in DEFAULTS.items():
            st.session_state[k] = v
        st.rerun()

st.info("💡 V3 : ajoute des biens manuellement ou importe un CSV. Le moteur calcule coût total, marge, bénéfice, score et prix maximum d'achat.")

sample_csv = """id,titre,ville,distance_km,prix,surface_m2,terrain_m2,travaux,valeur_apres_travaux,url
DEMO-001,Maison à rénover,Houffalize,12,95000,145,900,60000,230000,https://example.com
DEMO-002,Maison 4 façades,Bastogne,22,125000,180,1100,75000,265000,https://example.com
DEMO-003,Maison à rénovation lourde,Vielsalm,35,85000,160,700,105000,225000,https://example.com
DEMO-004,Maison sous-évaluée,La Roche-en-Ardenne,28,110000,170,1200,55000,255000,https://example.com
"""

if "manual_rows" not in st.session_state:
    st.session_state.manual_rows = []

tab1, tab2, tab3 = st.tabs(["📥 Données", "🔥 Classement", "🧮 Simulateur"])

with tab1:
    st.subheader("Importer un fichier CSV")
    uploaded = st.file_uploader("Choisir un CSV", type=["csv"])
    st.download_button(
        "Télécharger le modèle CSV",
        data=sample_csv.encode("utf-8"),
        file_name="modele_annonces.csv",
        mime="text/csv"
    )

    st.subheader("Ajouter un bien manuellement")
    with st.form("manual_form", clear_on_submit=True):
        c1, c2, c3 = st.columns(3)
        titre = c1.text_input("Titre", "Maison à rénover")
        ville = c2.text_input("Ville", "Houffalize")
        distance_km = c3.number_input("Distance (km)", min_value=0.0, value=10.0, step=1.0)

        c1, c2, c3 = st.columns(3)
        prix = c1.number_input("Prix affiché (€)", min_value=0, value=100000, step=5000)
        surface_m2 = c2.number_input("Surface habitable (m²)", min_value=1.0, value=150.0, step=5.0)
        terrain_m2 = c3.number_input("Terrain (m²)", min_value=0.0, value=800.0, step=50.0)

        c1, c2, c3 = st.columns(3)
        travaux = c1.number_input("Travaux estimés (€)", min_value=0, value=60000, step=5000)
        valeur_apres = c2.number_input("Valeur après travaux (€)", min_value=0, value=230000, step=5000)
        url = c3.text_input("Lien annonce", "https://example.com")

        submitted = st.form_submit_button("Ajouter ce bien")
        if submitted:
            st.session_state.manual_rows.append({
                "id": f"MAN-{len(st.session_state.manual_rows)+1:03d}",
                "titre": titre,
                "ville": ville,
                "distance_km": distance_km,
                "prix": prix,
                "surface_m2": surface_m2,
                "terrain_m2": terrain_m2,
                "travaux": travaux,
                "valeur_apres_travaux": valeur_apres,
                "url": url
            })
            st.success("Bien ajouté.")

    if uploaded is not None:
        imported_df = pd.read_csv(uploaded)
    else:
        imported_df = pd.read_csv(StringIO(sample_csv))

    manual_df = pd.DataFrame(st.session_state.manual_rows)
    if not manual_df.empty:
        df = pd.concat([imported_df, manual_df], ignore_index=True)
    else:
        df = imported_df.copy()

    st.session_state["df_source"] = df
    st.dataframe(df, use_container_width=True, hide_index=True)

def compute_metrics(row, purchase_price=None):
    p = float(row["prix"] if purchase_price is None else purchase_price)
    w = float(row["travaux"])
    v = float(row["valeur_apres_travaux"])

    financed = (p + w) * st.session_state.financing_share / 100
    interest = financed * st.session_state.loan_rate / 100 * st.session_state.holding_months / 12 * 0.5
    reg = p * st.session_state.reg_rate / 100
    notary = p * st.session_state.notary_rate / 100
    contingency = w * st.session_state.contingency_rate / 100
    resale = v * st.session_state.sale_rate / 100

    total = p + reg + notary + w + contingency + interest + resale
    profit = v - total
    margin = profit / total * 100 if total else -999
    return total, profit, margin

def max_purchase_price(row):
    lo, hi = 0.0, max(float(st.session_state.budget_max), float(row["prix"]) * 1.5, 1.0)
    for _ in range(100):
        mid = (lo + hi) / 2
        _, profit, margin = compute_metrics(row, mid)
        if profit >= st.session_state.min_profit and margin >= st.session_state.min_margin:
            lo = mid
        else:
            hi = mid
    return lo

def score_row(row, profit, margin, max_buy, price_m2):
    score = 0.0
    score += min(max((margin - st.session_state.min_margin) * 1.5, 0), 25)
    score += min(max((profit / max(st.session_state.min_profit, 1)) * 20, 0), 20)
    if max_buy > 0:
        score += min(max((max_buy - float(row["prix"])) / max_buy * 25, 0), 25)
    score += 10 if float(row["travaux"]) <= st.session_state.works_max else 0
    score += 10 if float(row["distance_km"]) <= st.session_state.radius else 0
    score += 5 if price_m2 <= 1500 else 0
    score += 5 if profit > 0 else 0
    return min(round(score, 1), 100)

df = st.session_state.get("df_source", pd.read_csv(StringIO(sample_csv)))
required = ["prix", "travaux", "valeur_apres_travaux", "distance_km", "surface_m2"]
missing = [c for c in required if c not in df.columns]

if missing:
    st.error("Colonnes obligatoires manquantes : " + ", ".join(missing))
    st.stop()

rows = []
for _, r in df.iterrows():
    total, profit, margin = compute_metrics(r)
    max_buy = max_purchase_price(r)
    price_m2 = float(r["prix"]) / float(r["surface_m2"]) if float(r["surface_m2"]) else np.nan
    ecart = max_buy - float(r["prix"])
    score = score_row(r, profit, margin, max_buy, price_m2)
    rows.append({
        **r.to_dict(),
        "prix_m2": price_m2,
        "cout_total": total,
        "benefice": profit,
        "marge": margin,
        "prix_max_achat": max_buy,
        "ecart_negociation": ecart,
        "score": score
    })

res = pd.DataFrame(rows)
qualified = res[
    (res["prix"] <= st.session_state.budget_max) &
    (res["travaux"] <= st.session_state.works_max) &
    (res["distance_km"] <= st.session_state.radius) &
    (res["benefice"] >= st.session_state.min_profit) &
    (res["marge"] >= st.session_state.min_margin)
].sort_values("score", ascending=False)

with tab2:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Biens analysés", len(res))
    c2.metric("Pépites retenues", len(qualified))
    c3.metric("Meilleur score", f"{qualified['score'].max():.0f}/100" if len(qualified) else "—")
    c4.metric("Bénéfice max", f"{qualified['benefice'].max():,.0f} €" if len(qualified) else "—")

    show = qualified if len(qualified) else res.sort_values("score", ascending=False)

    if len(qualified) == 0:
        st.warning("Aucun bien ne respecte tous les critères. Le tableau ci-dessous montre les meilleurs candidats malgré tout.")

    view_cols = ["titre","ville","distance_km","prix","travaux","valeur_apres_travaux","cout_total","benefice","marge","prix_max_achat","ecart_negociation","score"]
    st.dataframe(show[view_cols], use_container_width=True, hide_index=True)

    st.subheader("Fiches")
    for _, r in show.head(15).iterrows():
        level = "🔥 PÉPITE" if r.score >= 90 else ("🟢 TRÈS INTÉRESSANTE" if r.score >= 80 else ("🟡 À ANALYSER" if r.score >= 70 else "🟠 MOYENNE"))
        with st.expander(f"{level} • {r.titre} • {r.ville} • Score {r.score:.0f}/100"):
            a,b,c,d = st.columns(4)
            a.metric("Prix affiché", f"{r.prix:,.0f} €")
            b.metric("Prix max conseillé", f"{r.prix_max_achat:,.0f} €")
            c.metric("Écart", f"{r.ecart_negociation:,.0f} €")
            d.metric("Bénéfice", f"{r.benefice:,.0f} €")
            st.write(f"**Marge :** {r.marge:.1f}%  |  **Coût total :** {r.cout_total:,.0f} €  |  **Prix/m² :** {r.prix_m2:,.0f} €/m²")
            st.write(f"**Travaux :** {r.travaux:,.0f} €  |  **Valeur après travaux :** {r.valeur_apres_travaux:,.0f} €  |  **Distance :** {r.distance_km:.0f} km")
            if "url" in r and pd.notna(r.url):
                st.link_button("Voir l'annonce", str(r.url))

    st.download_button(
        "📤 Exporter le classement",
        res.sort_values("score", ascending=False).to_csv(index=False).encode("utf-8"),
        file_name="classement_pepites.csv",
        mime="text/csv"
    )

with tab3:
    st.subheader("Tester une offre")
    labels = (res["titre"].astype(str) + " — " + res["ville"].astype(str)).tolist()
    choice = st.selectbox("Bien", labels)
    idx = labels.index(choice)
    rr = res.iloc[idx]

    offer = st.number_input("Prix d'offre (€)", min_value=0, max_value=2_000_000, value=int(rr["prix"]), step=1000)
    total, profit, margin = compute_metrics(rr, offer)

    a,b,c,d = st.columns(4)
    a.metric("Coût total", f"{total:,.0f} €")
    b.metric("Bénéfice", f"{profit:,.0f} €")
    c.metric("Marge", f"{margin:.1f}%")
    d.metric("Prix max conseillé", f"{rr['prix_max_achat']:,.0f} €")

    if profit >= st.session_state.min_profit and margin >= st.session_state.min_margin:
        st.success("✅ Cette offre respecte tes objectifs.")
    else:
        st.error("❌ Cette offre est trop élevée pour tes objectifs.")

    st.write(f"Réduction conseillée par rapport au prix affiché : **{max(float(rr['prix']) - float(rr['prix_max_achat']), 0):,.0f} €**")

st.caption("Les calculs sont indicatifs : vérifie la valeur de revente, les travaux, l'urbanisme, la fiscalité, le financement et les frais réels avant décision.")
