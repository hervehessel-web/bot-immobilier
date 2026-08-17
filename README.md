
# Chasseur de Pépites Immobilières — V3 Web

Application Streamlit autonome pour analyser des opérations d'achat-rénovation-revente en Belgique.

## Fonctionnalités

- budget, rayon, travaux, marge et bénéfice configurables
- ajout manuel d'un bien
- import CSV
- coût total estimé
- intérêts de financement simplifiés
- droits d'enregistrement paramétrables
- frais d'acquisition et de revente
- imprévus travaux
- bénéfice et marge
- prix maximum d'achat conseillé
- écart de négociation
- score pépite /100
- classement automatique
- simulateur d'offre
- export CSV

## Lancer en local

1. Installer Python 3.11 ou plus récent.
2. Ouvrir un terminal dans ce dossier.
3. Installer les dépendances :

```bash
pip install -r requirements.txt
```

4. Lancer :

```bash
streamlit run app.py
```

L'application s'ouvrira dans le navigateur.

## Déployer sur Streamlit Community Cloud

1. Créer un dépôt GitHub.
2. Copier dans le dépôt :
   - `app.py`
   - `requirements.txt`
   - `.streamlit/config.toml`
3. Aller sur Streamlit Community Cloud.
4. Créer une nouvelle app à partir du dépôt GitHub.
5. Sélectionner `app.py` comme fichier principal.
6. Déployer.

## Format CSV

Colonnes obligatoires :

- `prix`
- `travaux`
- `valeur_apres_travaux`
- `distance_km`
- `surface_m2`

Colonnes recommandées :

- `id`
- `titre`
- `ville`
- `terrain_m2`
- `url`

Un fichier `modele_annonces.csv` est inclus.

## Important

Cette application n'effectue pas de scraping de portails immobiliers. Pour automatiser la collecte d'annonces dans une version future, utiliser uniquement des API, flux, exports ou intégrations autorisés par les plateformes concernées.

Les calculs sont indicatifs et ne remplacent pas un notaire, un fiscaliste, une banque, un expert immobilier ou des devis de travaux.
