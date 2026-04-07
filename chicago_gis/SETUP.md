# Chicago Overdose Hotspot GIS — Setup Guide

## What this does
Real GIS analysis using live Chicago open data:
- Downloads real polygon boundaries for all 77 community areas
- Fetches real point data (narcotics crimes, vacant buildings, encampments, 311 calls, liquor licenses)
- Runs actual point-in-polygon spatial joins using GeoPandas + Shapely
- Produces a composite risk score per community area
- Outputs an interactive choropleth + heatmap in your browser

---

## Step 1 — Install Python dependencies

Open a terminal in this folder and run:

```bash
pip install -r requirements.txt
```

If you're on a Mac with Apple Silicon (M1/M2):
```bash
conda install -c conda-forge geopandas folium
pip install requests
```

---

## Step 2 — You do NOT need to manually download any data

The script downloads everything automatically from the Chicago Data Portal API when you run it.

Data sources it pulls:

| Layer | Source | Dataset ID |
|---|---|---|
| Community area polygons | City of Chicago | cauq-8yn6 |
| Narcotics crimes (opioid proxy) | Chicago Crimes | ijzp-q8t2 |
| Vacant & abandoned buildings | Chicago Buildings | kc9i-wq85 |
| Homeless encampments (311) | Chicago 311 | v6vf-nfxy |
| Drug-activity 311 calls | Chicago 311 | v6vf-nfxy |
| Active liquor licenses | Chicago Business Licenses | r5kz-chrr |

Community area boundaries are cached to `data/community_areas.geojson`
after the first run so you don't re-download them.

---

## Step 3 — Run the script

```bash
python run_map.py
```

This will take 1–3 minutes depending on your internet speed.
You'll see live progress as each dataset downloads and each spatial join runs.

---

## Step 4 — Open your map

```
output/chicago_overdose_gis.html   ← open this in Chrome or Firefox
output/risk_scores.csv             ← table of scores you can open in Excel
```

---

## Folder structure

```
chicago_gis/
├── run_map.py          ← main GIS script
├── requirements.txt    ← Python dependencies
├── SETUP.md            ← this file
├── data/               ← auto-created, caches downloaded data
└── output/             ← auto-created, your map and CSV go here
```

---

## What the composite risk score means

| Weight | Layer | Why |
|---|---|---|
| 40% | Narcotics crimes | Strongest available proxy for opioid activity in Chicago open data |
| 20% | Vacant buildings | Abandoned properties strongly correlated with drug use sites |
| 15% | Encampments (311) | Unsheltered populations at highest overdose risk |
| 15% | Drug 311 calls | Community-reported drug activity |
| 10% | Liquor licenses | Outlet density correlated with substance use rates |

All layers are min-max normalized to 0–100 before weighting.

---

## Notes on the data

- **Narcotics crimes** are used as the opioid proxy because Chicago does not publish
  a public point-level opioid overdose dataset. The CDPH does publish
  community-area-level opioid death rates — if you want to add those, download:
  https://data.cityofchicago.org/Health-Human-Services/Public-Health-Statistics-Opioid-Analgesic-Overdose/6qhv-d3ke
  and merge on community area name.

- **Encampments** come from 311 service requests of type "Homeless Encampment."
  These are *reported* encampments, not a complete census.

- All data is fetched for recent years (2021–present) to reflect current conditions.
