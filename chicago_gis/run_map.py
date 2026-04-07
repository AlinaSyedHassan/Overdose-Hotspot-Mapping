#!/usr/bin/env python3
"""
Chicago Overdose Hotspot GIS
================================
Real spatial analysis using Chicago Data Portal open data.

Layers:
  1. Narcotics crimes (opioid proxy)  — Chicago Crimes dataset
  2. Vacant & abandoned buildings     — Chicago Buildings dataset
  3. Homeless encampments             — Chicago 311 Service Requests
  4. Drug-related 311 calls           — Chicago 311 Service Requests
  5. Active liquor licenses           — Chicago Business Licenses

Spatial operations:
  - Point-in-polygon spatial joins (GeoPandas / Shapely)
  - Per-community-area count aggregation
  - Weighted composite risk scoring (normalized 0–100)
  - Kernel Density Estimation heatmap

Output:
  output/chicago_overdose_gis.html   — interactive Folium map
  output/risk_scores.csv             — table of scores by community area
"""

import os
import sys
import time
import requests
import pandas as pd
import geopandas as gpd
import numpy as np
import folium
from folium.plugins import HeatMap, MarkerCluster, MiniMap
from shapely.geometry import Point

# ─────────────────────────────────────────────────────────────────────────────
# CONFIGURATION
# ─────────────────────────────────────────────────────────────────────────────

DATA_DIR   = "data"
OUTPUT_DIR = "output"

os.makedirs(DATA_DIR,   exist_ok=True)
os.makedirs(OUTPUT_DIR, exist_ok=True)

# Chicago Data Portal — Socrata API base
SOCRATA = "https://data.cityofchicago.org/resource"

# Community areas GeoJSON (official City of Chicago polygon boundaries)
COMMUNITY_AREAS_URL = (
    "https://data.cityofchicago.org/api/geospatial/cauq-8yn6"
    "?method=export&type=GeoJSON"
)

# Dataset endpoints
CRIMES_URL   = f"{SOCRATA}/ijzp-q8t2.json"   # Chicago Crimes 2001–present
VACANT_URL   = f"{SOCRATA}/kc9i-wq85.json"   # Vacant & Abandoned Buildings Violations
REQUESTS_URL = f"{SOCRATA}/v6vf-nfxy.json"   # 311 Service Requests (2018–present)
LICENSES_URL = f"{SOCRATA}/r5kz-chrr.json"   # Business Licenses

# ─────────────────────────────────────────────────────────────────────────────
# DATA FETCHING  (Socrata SoQL API)
# ─────────────────────────────────────────────────────────────────────────────

def fetch(url: str, params: dict, label: str, limit: int = 50_000) -> pd.DataFrame:
    """Fetch JSON from Socrata with a single page request."""
    params = {**params, "$limit": limit, "$offset": 0}
    print(f"  → Fetching {label}...")
    r = requests.get(url, params=params, timeout=90)
    r.raise_for_status()
    df = pd.DataFrame(r.json())
    print(f"    {len(df):,} records")
    return df


def to_geodataframe(df: pd.DataFrame, lat="latitude", lon="longitude") -> gpd.GeoDataFrame:
    """Convert DataFrame with lat/lon columns to a GeoDataFrame (WGS-84)."""
    df = df.dropna(subset=[lat, lon]).copy()
    df[lat] = pd.to_numeric(df[lat], errors="coerce")
    df[lon] = pd.to_numeric(df[lon], errors="coerce")
    df = df.dropna(subset=[lat, lon])
    # Remove obvious bad coordinates
    df = df[(df[lat].between(41.6, 42.1)) & (df[lon].between(-87.95, -87.5))]
    gdf = gpd.GeoDataFrame(
        df,
        geometry=gpd.points_from_xy(df[lon], df[lat]),
        crs="EPSG:4326"
    )
    return gdf


# ── 1. Community area polygons ────────────────────────────────────────────────

def load_community_areas() -> gpd.GeoDataFrame:
    cache = os.path.join(DATA_DIR, "community_areas.geojson")
    if os.path.exists(cache):
        print("  → Loading community areas from cache...")
        gdf = gpd.read_file(cache)
    else:
        print("  → Downloading community areas GeoJSON...")
        r = requests.get(COMMUNITY_AREAS_URL, timeout=60)
        r.raise_for_status()
        with open(cache, "w") as f:
            f.write(r.text)
        gdf = gpd.read_file(cache)

    gdf = gdf.to_crs("EPSG:4326")

    # Normalise the area number column (varies between downloads)
    num_col = next(
        (c for c in gdf.columns if "area_num" in c.lower()), None
    )
    if num_col:
        gdf["area_num"] = pd.to_numeric(gdf[num_col], errors="coerce").astype("Int64")

    # Normalise the community name column
    name_col = next(
        (c for c in gdf.columns if "community" in c.lower()), None
    )
    if name_col and name_col != "community":
        gdf = gdf.rename(columns={name_col: "community"})

    gdf["community"] = gdf["community"].str.title()
    print(f"    {len(gdf)} community areas loaded")
    return gdf[["area_num", "community", "geometry"]]


# ── 2. Narcotics crimes (opioid proxy) ───────────────────────────────────────

def load_drug_crimes() -> gpd.GeoDataFrame:
    df = fetch(
        CRIMES_URL,
        {
            "$where": (
                "primary_type='NARCOTICS' "
                "AND year >= '2021' "
                "AND latitude IS NOT NULL"
            ),
            "$select": "latitude,longitude,year,description",
        },
        "Narcotics crimes (2021–present)",
        limit=50_000,
    )
    return to_geodataframe(df)


# ── 3. Vacant & abandoned buildings ─────────────────────────────────────────

def load_vacant_buildings() -> gpd.GeoDataFrame:
    df = fetch(
        VACANT_URL,
        {
            "$where": "latitude IS NOT NULL",
            "$select": "latitude,longitude,address,violation_date",
        },
        "Vacant & abandoned buildings",
        limit=30_000,
    )
    return to_geodataframe(df)


# ── 4. 311 — Homeless encampments ────────────────────────────────────────────

def load_encampments() -> gpd.GeoDataFrame:
    df = fetch(
        REQUESTS_URL,
        {
            "$where": (
                "sr_type='Homeless Encampment' "
                "AND latitude IS NOT NULL"
            ),
            "$select": "latitude,longitude,sr_type,created_date",
        },
        "311 Homeless Encampment requests",
        limit=20_000,
    )
    return to_geodataframe(df)


# ── 5. 311 — Drug-activity calls ─────────────────────────────────────────────

def load_drug_calls() -> gpd.GeoDataFrame:
    df = fetch(
        REQUESTS_URL,
        {
            "$where": (
                "sr_type='Drug Activity' "
                "AND latitude IS NOT NULL"
            ),
            "$select": "latitude,longitude,sr_type,created_date",
        },
        "311 Drug Activity calls",
        limit=20_000,
    )
    # Fallback: if Drug Activity returns nothing try a broader query
    if len(df) < 10:
        print("    (Drug Activity sparse — using broader narcotics-complaint filter)")
        df = fetch(
            REQUESTS_URL,
            {
                "$where": (
                    "(sr_type LIKE '%Drug%' OR sr_type LIKE '%Narcotic%') "
                    "AND latitude IS NOT NULL"
                ),
                "$select": "latitude,longitude,sr_type,created_date",
            },
            "311 Drug-related calls (broad)",
            limit=20_000,
        )
    return to_geodataframe(df)


# ── 6. Active liquor licenses ────────────────────────────────────────────────

def load_liquor() -> gpd.GeoDataFrame:
    df = fetch(
        LICENSES_URL,
        {
            "$where": (
                "(license_description LIKE '%LIQUOR%' "
                " OR license_description LIKE '%TAVERN%' "
                " OR license_description LIKE '%PACKAGED GOODS%') "
                "AND license_status='AAI' "
                "AND latitude IS NOT NULL"
            ),
            "$select": "latitude,longitude,doing_business_as_name,license_description",
        },
        "Active liquor licenses",
        limit=10_000,
    )
    return to_geodataframe(df)


# ─────────────────────────────────────────────────────────────────────────────
# SPATIAL ANALYSIS
# ─────────────────────────────────────────────────────────────────────────────

def spatial_count(areas: gpd.GeoDataFrame,
                  points: gpd.GeoDataFrame,
                  col: str) -> gpd.GeoDataFrame:
    """
    Spatial join: count how many points fall within each community area polygon.
    This is a real GIS operation — point-in-polygon via Shapely / PyGEOS.
    """
    if len(points) == 0:
        areas[col] = 0
        return areas

    joined = gpd.sjoin(
        points[["geometry"]],
        areas[["area_num", "geometry"]],
        how="left",
        predicate="within",
    )
    counts = joined.groupby("area_num").size().reset_index(name=col)
    areas = areas.merge(counts, on="area_num", how="left")
    areas[col] = areas[col].fillna(0).astype(int)
    return areas


def normalize_col(series: pd.Series) -> pd.Series:
    """Min-max normalize a series to 0–100."""
    mn, mx = series.min(), series.max()
    if mx == mn:
        return pd.Series(np.zeros(len(series)), index=series.index)
    return (series - mn) / (mx - mn) * 100


def compute_composite(areas: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """
    Weighted composite risk score (0–100):
      40%  narcotics crime count  (strongest opioid proxy in open data)
      20%  vacant building count
      15%  encampment count
      15%  drug 311 call count
      10%  liquor license count
    """
    areas["score"] = (
        0.40 * normalize_col(areas["drug_count"])   +
        0.20 * normalize_col(areas["vacant_count"]) +
        0.15 * normalize_col(areas["encamp_count"]) +
        0.15 * normalize_col(areas["calls_count"])  +
        0.10 * normalize_col(areas["liquor_count"])
    ).round(1)
    return areas


# ─────────────────────────────────────────────────────────────────────────────
# MAP BUILDING
# ─────────────────────────────────────────────────────────────────────────────

def risk_color(score: float) -> str:
    if score <= 25:  return "#2ecc71"
    if score <= 50:  return "#f1c40f"
    if score <= 75:  return "#e67e22"
    return "#e74c3c"


def risk_label(score: float) -> str:
    if score <= 25:  return "Low"
    if score <= 50:  return "Moderate"
    if score <= 75:  return "High"
    return "Critical"


def add_point_cluster(m, gdf: gpd.GeoDataFrame, name: str,
                       color: str, show: bool = False):
    """Add a clustered point layer to the map."""
    mc = MarkerCluster(name=name, show=show)
    for _, row in gdf.iterrows():
        folium.CircleMarker(
            location=[row.geometry.y, row.geometry.x],
            radius=4,
            color=color,
            fill=True,
            fill_color=color,
            fill_opacity=0.75,
            weight=0.5,
        ).add_to(mc)
    mc.add_to(m)


def build_map(areas: gpd.GeoDataFrame,
              drug_gdf:   gpd.GeoDataFrame,
              vacant_gdf: gpd.GeoDataFrame,
              encamp_gdf: gpd.GeoDataFrame,
              calls_gdf:  gpd.GeoDataFrame,
              liquor_gdf: gpd.GeoDataFrame) -> str:

    m = folium.Map(
        location=[41.845, -87.68],
        zoom_start=11,
        tiles="CartoDB dark_matter",
    )

    # ── Choropleth — real polygon boundaries, colored by composite score ──────
    folium.GeoJson(
        data=areas.__geo_interface__,
        name="Composite Risk Score (Choropleth)",
        style_function=lambda feat: {
            "fillColor":   risk_color(feat["properties"].get("score", 0)),
            "color":       "white",
            "weight":      0.8,
            "fillOpacity": 0.55,
        },
        highlight_function=lambda feat: {
            "fillOpacity": 0.80,
            "weight":      2,
            "color":       "white",
        },
        tooltip=folium.GeoJsonTooltip(
            fields=[
                "community", "score",
                "drug_count", "vacant_count",
                "encamp_count", "calls_count", "liquor_count",
            ],
            aliases=[
                "Community Area", "Risk Score",
                "Narcotics Crimes", "Vacant Buildings",
                "Encampments", "Drug 311 Calls", "Liquor Licenses",
            ],
            localize=True,
            sticky=True,
        ),
    ).addTo(m) if False else None   # placeholder — use below

    # GeoJson must be called as a method, not chained with addTo in older folium
    gj = folium.GeoJson(
        data=areas.__geo_interface__,
        name="Composite Risk Score (Choropleth)",
        style_function=lambda feat: {
            "fillColor":   risk_color(feat["properties"].get("score", 0)),
            "color":       "white",
            "weight":      0.8,
            "fillOpacity": 0.55,
        },
        highlight_function=lambda feat: {
            "fillOpacity": 0.80,
            "weight":      2,
        },
        tooltip=folium.GeoJsonTooltip(
            fields=[
                "community", "score",
                "drug_count", "vacant_count",
                "encamp_count", "calls_count", "liquor_count",
            ],
            aliases=[
                "Community Area", "Risk Score (0–100)",
                "Narcotics Crimes", "Vacant Buildings",
                "Encampments (311)", "Drug 311 Calls", "Liquor Licenses",
            ],
            sticky=True,
        ),
    )
    gj.add_to(m)

    # ── Heat map — drug crime point density ───────────────────────────────────
    heat_pts = [
        [row.geometry.y, row.geometry.x]
        for _, row in drug_gdf.iterrows()
    ]
    HeatMap(
        heat_pts,
        name="Drug Crime Heat Map",
        radius=20,
        blur=18,
        max_zoom=14,
        gradient={
            "0.2": "#4575b4",
            "0.45": "#74add1",
            "0.6": "#ffffbf",
            "0.75": "#f46d43",
            "1.0": "#d73027",
        },
        show=True,
    ).add_to(m)

    # ── Point layers (clustered, toggled off by default) ──────────────────────
    add_point_cluster(m, drug_gdf,   "Narcotics Crimes",    "#e74c3c", show=False)
    add_point_cluster(m, vacant_gdf, "Vacant Buildings",    "#9b59b6", show=False)
    add_point_cluster(m, encamp_gdf, "Encampments (311)",   "#3498db", show=False)
    add_point_cluster(m, calls_gdf,  "Drug 311 Calls",      "#f39c12", show=False)
    add_point_cluster(m, liquor_gdf, "Liquor Licenses",     "#1abc9c", show=False)

    # ── Legend ────────────────────────────────────────────────────────────────
    legend_html = """
    <div style="
        position:fixed; bottom:30px; right:12px; z-index:9999;
        background:rgba(13,17,23,0.93); padding:14px 16px;
        border-radius:10px; font-family:'Segoe UI',sans-serif;
        border:1px solid rgba(255,255,255,0.1);
        box-shadow:0 4px 20px rgba(0,0,0,0.6);">
      <div style="font-size:12px;font-weight:700;color:#e6edf3;
                  text-transform:uppercase;letter-spacing:.6px;margin-bottom:8px;">
        Composite Risk</div>
      <div style="display:flex;align-items:center;margin:4px 0;font-size:11px;color:#c9d1d9">
        <div style="width:13px;height:13px;border-radius:2px;background:#2ecc71;margin-right:8px"></div>Low (0–25)</div>
      <div style="display:flex;align-items:center;margin:4px 0;font-size:11px;color:#c9d1d9">
        <div style="width:13px;height:13px;border-radius:2px;background:#f1c40f;margin-right:8px"></div>Moderate (26–50)</div>
      <div style="display:flex;align-items:center;margin:4px 0;font-size:11px;color:#c9d1d9">
        <div style="width:13px;height:13px;border-radius:2px;background:#e67e22;margin-right:8px"></div>High (51–75)</div>
      <div style="display:flex;align-items:center;margin:4px 0;font-size:11px;color:#c9d1d9">
        <div style="width:13px;height:13px;border-radius:2px;background:#e74c3c;margin-right:8px"></div>Critical (76–100)</div>
      <hr style="border-color:rgba(255,255,255,.08);margin:8px 0">
      <div style="font-size:10px;color:#6e7681;">
        Real data · Chicago Data Portal<br>
        Spatial joins: GeoPandas / Shapely<br>
        Weighted score: 40% crimes · 20% vacant<br>
        15% encampments · 15% 311 · 10% liquor
      </div>
    </div>
    """
    m.get_root().html.add_child(folium.Element(legend_html))

    # ── Mini-map & scale bar ──────────────────────────────────────────────────
    MiniMap(toggle_display=True).add_to(m)
    folium.LayerControl(collapsed=False).add_to(m)

    out_path = os.path.join(OUTPUT_DIR, "chicago_overdose_gis.html")
    m.save(out_path)
    return out_path


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

def main():
    print("=" * 55)
    print("  Chicago Overdose Hotspot GIS")
    print("  Real data · Real spatial joins · GeoPandas")
    print("=" * 55)

    # ── Fetch all data ────────────────────────────────────────────────────────
    print("\n[1/6] Community area boundaries")
    areas = load_community_areas()

    print("\n[2/6] Narcotics crimes (opioid proxy)")
    drug_gdf = load_drug_crimes()

    print("\n[3/6] Vacant & abandoned buildings")
    vacant_gdf = load_vacant_buildings()

    print("\n[4/6] 311 — Homeless encampments")
    encamp_gdf = load_encampments()

    print("\n[5/6] 311 — Drug-activity calls")
    calls_gdf = load_drug_calls()

    print("\n[6/6] Active liquor licenses")
    liquor_gdf = load_liquor()

    # ── Spatial joins (real GIS: point-in-polygon) ────────────────────────────
    print("\n── Spatial joins ─────────────────────────────────────")
    print("  Counting points within each community area polygon...")

    areas = spatial_count(areas, drug_gdf,   "drug_count")
    areas = spatial_count(areas, vacant_gdf, "vacant_count")
    areas = spatial_count(areas, encamp_gdf, "encamp_count")
    areas = spatial_count(areas, calls_gdf,  "calls_count")
    areas = spatial_count(areas, liquor_gdf, "liquor_count")

    # ── Composite score ───────────────────────────────────────────────────────
    areas = compute_composite(areas)

    # ── Save CSV results ──────────────────────────────────────────────────────
    csv_path = os.path.join(OUTPUT_DIR, "risk_scores.csv")
    (
        areas[["community", "score", "drug_count", "vacant_count",
               "encamp_count", "calls_count", "liquor_count"]]
        .sort_values("score", ascending=False)
        .to_csv(csv_path, index=False)
    )
    print(f"  ✓ Risk scores saved → {csv_path}")

    # ── Print top 15 ─────────────────────────────────────────────────────────
    print("\n── Top 15 Highest-Risk Community Areas ───────────────")
    top = (
        areas[["community", "score", "drug_count", "vacant_count", "encamp_count"]]
        .sort_values("score", ascending=False)
        .head(15)
    )
    print(top.to_string(index=False))

    # ── Build map ─────────────────────────────────────────────────────────────
    print("\n── Building map ──────────────────────────────────────")
    out_path = build_map(areas, drug_gdf, vacant_gdf, encamp_gdf, calls_gdf, liquor_gdf)
    print(f"  ✓ Map saved → {out_path}")
    print("\nDone. Open output/chicago_overdose_gis.html in your browser.")
    print("=" * 55)


if __name__ == "__main__":
    main()
