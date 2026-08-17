
import streamlit as st
import pandas as pd
import numpy as np
from io import StringIO

st.set_page_config(page_title="Chasseur de Pépites V4", page_icon="🔥", layout="wide")

D = dict(budget=150000, radius=80, works=120000, profit=40000, margin=20.0,
         rate=3.8, months=12, finance=100, reg=12.5, notary=1.5,
         resale=3.0, contingency=10.0, holding=350, city="Houffalize")
for k,v in D.items(): st.session_state.setdefault(k,v)

st.title("🔥 Chasseur de Pépites Immobilières — V4")
st.caption("Belgique • achat → rénovation → revente • analyse avancée")

with st.sidebar:
    st.header("🎯 Critères")
    st.session_state.city = st.text_input("Ville centrale", st.session_state.city)
    st.session_state.budget = st.number_input("Budget achat max (€)",0,2_000_000,int(st.session_state.budget),5000)
    st.session_state.radius = st.number_input("Rayon max (km)",1,300,int(st.session_state.radius),5)
    st.session_state.works_max = st.number_input("Travaux max (€)",0,1_000_000,int(st.session_state.works_max),5000)
    st.session_state.profit = st.number_input("Bénéfice cible (€)",0,1_000_000,int(st.session_state.profit),5000)
    st.session_state.margin = st.number_input("Marge cible (%)",0.0,100.0,float(st.session_state.margin),1.0)

    st.header("🏦 Financement")
    st.session_state.rate = st.number_input("Taux annuel (%)",0.0,15.0,float(st.session_state.rate),0.1)
    st.session_state.months = st.number_input("Durée détention (mois)",1,60,int(st.session_state.months),1)
    st.session_state.finance = st.slider("Part financée (%)",0,100,int(st.session_state.finance),5)
    st.session_state.holding = st.number_input("Coût mensuel de détention (€)",0,5000,int(st.session_state.holding),50)

    st.header("🧾 Frais")
    st.session_state.reg = st.number_input("Droits d'enregistrement (%)",0.0,21.0,float(st.session_state.reg),0.5)
    st.session_state.notary = st.number_input("Provision achat/notaire (%)",0.0,10.0,float(st.session_state.notary),0.1)
    st.session_state.resale = st.number_input("Frais revente (%)",0.0,15.0,float(st.session_state.resale),0.5)
    st.session_state.contingency = st.number_input("Imprévus travaux (%)",0.0,30.0,float(st.session_state.contingency),1.0)

sample = """id,titre,ville,distance_km,prix,surface_m2,terrain_m2,chambres,peb,travaux,toiture,electricite,chauffage,menuiseries,cuisine,sdb,sols_peinture,facade,autres_travaux,valeur_apres_travaux,url
DEMO-001,Maison à rénover,Houffalize,12,95000,145,900,3,G,60000,12000,8000,10000,7000,8000,6000,5000,2000,2000,230000,https://example.com
DEMO-002,Maison 4 façades,Bastogne,22,125000,180,1100,4,F,75000,15000,10000,12000,9000,9000,7000,6000,3000,4000,265000,https://example.com
DEMO-003,Maison rénovation lourde,Vielsalm,35,85000,160,700,3,G,105000,20000,12000,15000,12000,10000,8000,8000,5000,15000,225000,https://example.com
"""

if "manual_rows" not in st.session_state: st.session_state.manual_rows=[]
tabs = st.tabs(["📥 Données","🔥 Classement","🤝 Prix d'offre","🔨 Travaux","📊 Comparables"])
tdata,trank,toffer,tworks,tcomp=tabs

with tdata:
    uploaded=st.file_uploader("Importer un CSV",type=["csv"])
    st.download_button("Télécharger le modèle CSV",sample.encode(),"modele_annonces_v4.csv","text/csv")

    st.subheader("Ajouter un bien")
    with st.form("add"):
        a,b,c=st.columns(3)
        title=a.text_input("Titre","Maison à rénover")
        city=b.text_input("Ville","Houffalize")
        dist=c.number_input("Distance (km)",0.0,500.0,10.0,1.0)
        a,b,c,d=st.columns(4)
        price=a.number_input("Prix (€)",0,2_000_000,100000,5000)
        surf=b.number_input("Surface (m²)",1.0,3000.0,150.0,5.0)
        land=c.number_input("Terrain (m²)",0.0,100000.0,800.0,50.0)
        beds=d.number_input("Chambres",0,30,3,1)
        a,b,c=st.columns(3)
        peb=a.selectbox("PEB",["A++","A+","A","B","C","D","E","F","G","Inconnu"],index=8)
        resale_value=b.number_input("Valeur après travaux (€)",0,3_000_000,230000,5000)
        url=c.text_input("URL","https://example.com")
        st.markdown("**Travaux par poste**")
        vals={}
        names=[("toiture","Toiture"),("electricite","Électricité"),("chauffage","Chauffage"),
               ("menuiseries","Menuiseries"),("cuisine","Cuisine"),("sdb","Salle de bain"),
               ("sols_peinture","Sols / peinture"),("facade","Façade / isolation"),("autres_travaux","Autres")]
        cols=st.columns(3)
        for i,(key,label) in enumerate(names):
            vals[key]=cols[i%3].number_input(label+" (€)",0,500000,5000 if i>2 else 10000,1000,key=f"w_{key}")
        if st.form_submit_button("Ajouter"):
            total=sum(vals.values())
            st.session_state.manual_rows.append(dict(
                id=f"MAN-{len(st.session_state.manual_rows)+1:03d}",titre=title,ville=city,distance_km=dist,
                prix=price,surface_m2=surf,terrain_m2=land,chambres=beds,peb=peb,travaux=total,
                valeur_apres_travaux=resale_value,url=url,**vals
            ))
            st.success(f"Bien ajouté — travaux: {total:,.0f} €")

    base=pd.read_csv(uploaded) if uploaded else pd.read_csv(StringIO(sample))
    manual=pd.DataFrame(st.session_state.manual_rows)
    df=pd.concat([base,manual],ignore_index=True) if not manual.empty else base
    st.session_state.df=df
    st.dataframe(df,use_container_width=True,hide_index=True)

df=st.session_state.get("df",pd.read_csv(StringIO(sample)))
required=["prix","travaux","valeur_apres_travaux","distance_km","surface_m2"]
miss=[c for c in required if c not in df.columns]
if miss:
    st.error("Colonnes manquantes: "+", ".join(miss)); st.stop()

def calc(r,p=None):
    p=float(r.prix if p is None else p); w=float(r.travaux); v=float(r.valeur_apres_travaux)
    interest=(p+w)*st.session_state.finance/100*st.session_state.rate/100*st.session_state.months/12*0.5
    rights=p*st.session_state.reg/100
    notary=p*st.session_state.notary/100
    conting=w*st.session_state.contingency/100
    holding=st.session_state.holding*st.session_state.months
    resale=v*st.session_state.resale/100
    total=p+rights+notary+w+conting+interest+holding+resale
    profit=v-total
    margin=profit/total*100 if total else -999
    return dict(achat=p,droits=rights,notaire=notary,travaux=w,imprevus=conting,interets=interest,
                detention=holding,revente=resale,cout_total=total,benefice=profit,marge=margin)

def solve(r,tp,tm):
    lo,hi=0.0,max(st.session_state.budget*2,float(r.prix)*2,1)
    for _ in range(100):
        mid=(lo+hi)/2; m=calc(r,mid)
        if m["benefice"]>=tp and m["marge"]>=tm: lo=mid
        else: hi=mid
    return lo

peb_score={"A++":10,"A+":10,"A":10,"B":9,"C":8,"D":6,"E":4,"F":2,"G":0,"Inconnu":3}
out=[]
for _,r in df.iterrows():
    m=calc(r); pmax=solve(r,st.session_state.profit,st.session_state.margin)
    pt=solve(r,st.session_state.profit*1.15,st.session_state.margin+3)
    pp=solve(r,st.session_state.profit*1.30,st.session_state.margin+5)
    pm2=float(r.prix)/float(r.surface_m2)
    score=0
    score+=min(max((m["marge"]-st.session_state.margin)*1.4,0),25)
    score+=min(max((m["benefice"]/max(st.session_state.profit,1))*18,0),20)
    score+=20 if float(r.prix)<=pmax else max(0,20-((float(r.prix)-pmax)/max(pmax,1))*100)
    score+=10 if float(r.travaux)<=st.session_state.works_max else 0
    score+=10 if float(r.distance_km)<=st.session_state.radius else 0
    score+=peb_score.get(str(getattr(r,"peb","Inconnu")),3)*0.5
    score+=5 if pm2<1500 else 2
    score+=5 if float(getattr(r,"terrain_m2",0) or 0)>=500 else 2
    out.append({**r.to_dict(),**m,"prix_m2":pm2,"offre_prudente":pp,"offre_cible":pt,
                "prix_max_absolu":pmax,"negociation_requise":float(r.prix)-pmax,"score":min(round(score,1),100)})
res=pd.DataFrame(out)

qualified=res[(res.prix<=st.session_state.budget)&(res.distance_km<=st.session_state.radius)&
              (res.travaux<=st.session_state.works_max)&(res.benefice>=st.session_state.profit)&
              (res.marge>=st.session_state.margin)].sort_values("score",ascending=False)

with trank:
    a,b,c,d=st.columns(4)
    a.metric("Biens analysés",len(res)); b.metric("Pépites",len(qualified))
    c.metric("Meilleur score",f"{qualified.score.max():.0f}/100" if len(qualified) else "—")
    d.metric("Bénéfice max",f"{qualified.benefice.max():,.0f} €" if len(qualified) else "—")
    show=qualified if len(qualified) else res.sort_values("score",ascending=False)
    cols=["titre","ville","peb","chambres","surface_m2","terrain_m2","prix","travaux","valeur_apres_travaux",
          "benefice","marge","offre_cible","prix_max_absolu","score"]
    st.dataframe(show[[c for c in cols if c in show.columns]],use_container_width=True,hide_index=True)

with toffer:
    labels=(res.titre.astype(str)+" — "+res.ville.astype(str)).tolist()
    pick=st.selectbox("Bien",labels,key="offer")
    r=res.iloc[labels.index(pick)]
    a,b,c=st.columns(3)
    a.metric("🟢 Offre prudente",f"{r.offre_prudente:,.0f} €")
    b.metric("🎯 Offre cible",f"{r.offre_cible:,.0f} €")
    c.metric("🚫 Maximum absolu",f"{r.prix_max_absolu:,.0f} €")
    offer=st.number_input("Tester une offre (€)",0,2_000_000,int(r.prix),1000)
    m=calc(r,offer)
    a,b,c=st.columns(3)
    a.metric("Coût total",f"{m['cout_total']:,.0f} €"); b.metric("Bénéfice",f"{m['benefice']:,.0f} €"); c.metric("Marge",f"{m['marge']:.1f}%")
    st.success("✅ Compatible avec tes objectifs") if m["benefice"]>=st.session_state.profit and m["marge"]>=st.session_state.margin else st.error("❌ Offre trop élevée")
    cost=pd.DataFrame({"Poste":["Achat","Droits","Notaire","Travaux","Imprévus","Intérêts","Détention","Revente"],
                       "Montant (€)":[m["achat"],m["droits"],m["notaire"],m["travaux"],m["imprevus"],m["interets"],m["detention"],m["revente"]]})
    st.dataframe(cost,use_container_width=True,hide_index=True)

with tworks:
    labels=(res.titre.astype(str)+" — "+res.ville.astype(str)).tolist()
    pick=st.selectbox("Bien",labels,key="works_choice")
    r=res.iloc[labels.index(pick)]
    mapping={"Toiture":"toiture","Électricité":"electricite","Chauffage":"chauffage","Menuiseries":"menuiseries","Cuisine":"cuisine",
             "Salle de bain":"sdb","Sols / peinture":"sols_peinture","Façade / isolation":"facade","Autres":"autres_travaux"}
    wdf=pd.DataFrame([{"Poste":lab,"Montant (€)":float(r.get(col,0) or 0)} for lab,col in mapping.items()])
    st.dataframe(wdf,use_container_width=True,hide_index=True)
    st.metric("Total détaillé",f"{wdf['Montant (€)'].sum():,.0f} €")
    st.metric("Travaux utilisés",f"{r.travaux:,.0f} €")

with tcomp:
    if "comparables" not in st.session_state: st.session_state.comparables=[]
    st.subheader("Ajouter des comparables")
    with st.form("comp"):
        a,b,c,d=st.columns(4)
        cv=a.text_input("Ville","Houffalize"); cp=b.number_input("Prix (€)",0,3_000_000,250000,5000)
        cs=c.number_input("Surface (m²)",1.0,3000.0,150.0,5.0); ce=d.selectbox("État",["Rénové","Bon état","À rafraîchir","À rénover"])
        if st.form_submit_button("Ajouter comparable"):
            st.session_state.comparables.append({"ville":cv,"prix":cp,"surface_m2":cs,"etat":ce,"prix_m2":cp/cs})
    if st.session_state.comparables:
        cdf=pd.DataFrame(st.session_state.comparables)
        st.dataframe(cdf,use_container_width=True,hide_index=True)
        avg=cdf.prix_m2.mean(); st.metric("Prix/m² moyen",f"{avg:,.0f} €/m²")
        labels=(res.titre.astype(str)+" — "+res.ville.astype(str)).tolist()
        pick=st.selectbox("Comparer avec",labels,key="comp_pick"); r=res.iloc[labels.index(pick)]
        theo=avg*r.surface_m2
        a,b,c=st.columns(3)
        a.metric("Valeur par comparables",f"{theo:,.0f} €")
        b.metric("Valeur saisie",f"{r.valeur_apres_travaux:,.0f} €")
        c.metric("Écart",f"{r.valeur_apres_travaux-theo:,.0f} €")
    else:
        st.info("Ajoute au moins un comparable.")

st.download_button("📤 Exporter les analyses",res.sort_values("score",ascending=False).to_csv(index=False).encode(),"classement_pepites_v4.csv","text/csv")
st.caption("Estimations indicatives à vérifier avec comparables, devis, banque, urbanisme, fiscaliste/notaire et professionnels.")
