import os
import sys
import json
import sqlite3
from pathlib import Path

# Redirect HuggingFace/SentenceTransformers models cache to D drive if available
if os.environ.get("VERCEL") or not os.path.exists("D:\\"):
    os.environ["HF_HOME"] = "/tmp/hf_cache"
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = "/tmp/st_cache"
else:
    os.environ["HF_HOME"] = "D:\\hf_cache"
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = "D:\\st_cache"

ROOT = Path(__file__).resolve().parent
DB_FILE = ROOT / "data" / "healthcare_cost.db"
CHROMA_DIR = ROOT / "data" / "chroma_db"
HOSPITALS_JSON = ROOT / "data" / "hospitals.json"

# Import structures from server
sys.path.append(str(ROOT))
from server import PROCEDURES, BASE_COSTS, PROCEDURE_BASE_OVERRIDES, COMPONENT_WEIGHTS, CITY_MULTIPLIERS, money, detect_city_tier

def seed_sqlite():
    print("Seeding SQLite database...")
    DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    
    conn = sqlite3.connect(str(DB_FILE))
    cursor = conn.cursor()
    
    # 1. Procedures table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS procedures (
            procedure_id TEXT PRIMARY KEY,
            display_name TEXT,
            condition_label TEXT,
            icd10_code TEXT,
            specialty TEXT,
            complexity TEXT,
            synonyms_json TEXT,
            red_flag_terms_json TEXT
        )
    """)
    
    # 2. Procedure benchmarks table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS procedure_benchmarks (
            procedure_id TEXT,
            city_tier TEXT,
            component TEXT,
            min_inr INTEGER,
            max_inr INTEGER,
            source_type TEXT,
            source_confidence REAL
        )
    """)
    
    # 3. City tiers table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS city_tiers (
            city TEXT PRIMARY KEY,
            tier TEXT,
            cost_multiplier REAL
        )
    """)
    
    # 4. Multipliers table
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS multipliers (
            factor TEXT,
            component TEXT,
            condition TEXT,
            multiplier REAL
        )
    """)
    
    # Clean tables first
    cursor.execute("DELETE FROM procedures")
    cursor.execute("DELETE FROM procedure_benchmarks")
    cursor.execute("DELETE FROM city_tiers")
    cursor.execute("DELETE FROM multipliers")
    
    # Seed procedures & calculate benchmarks
    benchmarks_data = []
    procedures_data = []
    
    for proc in PROCEDURES:
        # Determine red flags specific to the procedure/specialty
        red_flags = []
        if proc.key in ("angioplasty", "bypass", "angiography", "pacemaker"):
            red_flags = ["chest pain"]
        elif proc.key in ("stroke_care",):
            red_flags = ["stroke", "paralysis"]
        elif proc.key in ("pneumonia", "asthma", "icu_day"):
            red_flags = ["breathless"]
        else:
            red_flags = []
            
        procedures_data.append((
            proc.key,
            proc.name,
            proc.condition,
            proc.icd10,
            proc.specialty,
            proc.complexity,
            json.dumps(proc.aliases),
            json.dumps(red_flags)
        ))
        
        # Calculate benchmarks for each city tier and component
        base_min, base_max = PROCEDURE_BASE_OVERRIDES.get(proc.key, BASE_COSTS[proc.complexity])
        
        for tier, tier_multiplier in CITY_MULTIPLIERS.items():
            # Sum up components first to handle rounding reconciliation
            total_min = money(base_min * tier_multiplier)
            total_max = money(base_max * tier_multiplier)
            
            temp_components = []
            for comp_key, weight in COMPONENT_WEIGHTS.items():
                min_val = money(total_min * weight)
                max_val = money(total_max * weight)
                temp_components.append({
                    "component": comp_key,
                    "min_val": min_val,
                    "max_val": max_val
                })
            
            # Reconcile rounding drift by adjusting the largest component
            sum_min = sum(tc["min_val"] for tc in temp_components)
            sum_max = sum(tc["max_val"] for tc in temp_components)
            biggest = max(temp_components, key=lambda tc: tc["max_val"])
            biggest["min_val"] += total_min - sum_min
            biggest["max_val"] += total_max - sum_max
            
            for tc in temp_components:
                benchmarks_data.append((
                    proc.key,
                    tier,
                    tc["component"],
                    tc["min_val"],
                    tc["max_val"],
                    "deterministic_seeded",
                    0.95
                ))
                
    cursor.executemany("""
        INSERT INTO procedures (procedure_id, display_name, condition_label, icd10_code, specialty, complexity, synonyms_json, red_flag_terms_json)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, procedures_data)
    
    cursor.executemany("""
        INSERT INTO procedure_benchmarks (procedure_id, city_tier, component, min_inr, max_inr, source_type, source_confidence)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, benchmarks_data)
    
    # Seed city tiers
    cities = [
        # metro
        ("mumbai", "metro", 1.18),
        ("delhi", "metro", 1.18),
        ("bengaluru", "metro", 1.18),
        ("bangalore", "metro", 1.18),
        ("chennai", "metro", 1.18),
        ("hyderabad", "metro", 1.18),
        ("kolkata", "metro", 1.18),
        ("pune", "metro", 1.18),
        # tier-1
        ("nagpur", "tier-1", 1.05),
        ("jaipur", "tier-1", 1.05),
        ("lucknow", "tier-1", 1.05),
        ("indore", "tier-1", 1.05),
        ("surat", "tier-1", 1.05),
        ("bhopal", "tier-1", 1.05),
        ("kochi", "tier-1", 1.05),
        # tier-2
        ("mysuru", "tier-2", 0.90)
    ]
    cursor.executemany("""
        INSERT INTO city_tiers (city, tier, cost_multiplier)
        VALUES (?, ?, ?)
    """, cities)
    
    # Seed multipliers
    multipliers_data = [
        ("age_65_plus", "procedure", "age >= 65", 1.15),
        ("age_65_plus", "room_stay", "age >= 65", 1.15),
        ("age_65_plus", "medicines_contingency", "age >= 65", 1.15),
        ("diabetes", "medicines_contingency", "diabetes", 1.20),
        ("hypertension", "diagnostics", "hypertension", 1.08),
        ("hypertension", "medicines_contingency", "hypertension", 1.08),
        ("kidney_disease", "diagnostics", "kidney_disease", 1.18),
        ("kidney_disease", "medicines_contingency", "kidney_disease", 1.18),
        ("private_room", "room_stay", "private", 1.35),
        ("icu_likely", "room_stay", "icu", 2.50),
    ]
    cursor.executemany("""
        INSERT INTO multipliers (factor, component, condition, multiplier)
        VALUES (?, ?, ?, ?)
    """, multipliers_data)
    
    conn.commit()
    conn.close()
    print("SQLite database seeded successfully.")

def seed_chromadb():
    print("Seeding ChromaDB...")
    # Load hospital dataset
    with HOSPITALS_JSON.open("r", encoding="utf-8") as f:
        hospitals_raw = json.load(f)
        
    import chromadb
    from sentence_transformers import SentenceTransformer
    
    model = SentenceTransformer("all-MiniLM-L6-v2")
    
    # Initialize chroma client
    client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    
    # Get or create collection
    try:
        client.delete_collection("hospital_specializations")
    except Exception:
        pass
    collection = client.create_collection("hospital_specializations")
    
    ids = []
    documents = []
    metadatas = []
    
    for idx, h in enumerate(hospitals_raw):
        h_name = h["name"]
        city = h["city"]
        specialties_str = ", ".join(h["specialties"])
        
        # Unique ID
        h_id = f"{city.lower()}_{h_name.lower().replace(' ', '_').replace('&', 'and').replace('-', '_')}"
        
        # Document text is name + specialties
        doc_text = f"{h_name} located in {city}. Specialties: {specialties_str}"
        
        cost_category = str(h.get("cost_category") or "mid").lower()
        price_index = {"budget": 0.78, "mid": 1.0, "premium": 1.24}.get(cost_category, 1.0)
        
        meta = {
            "name": h_name,
            "specialties": json.dumps(h["specialties"]),
            "city": city,
            "nabh": bool(h.get("nabh_accredited")),
            "rating": float(h.get("rating") or 4.0),
            "review_count": int(h.get("review_count") or 0),
            "synthetic_review_score": float(h.get("synthetic_review_score") or 0.7),
            "distance_km": float(h.get("distance_km") or 12.0),
            "price_index": price_index,
            "cost_category": cost_category
        }
        
        ids.append(h_id)
        documents.append(doc_text)
        metadatas.append(meta)
        
    print(f"Generating embeddings for {len(documents)} hospitals...")
    embeddings = model.encode(documents).tolist()
    
    collection.add(
        ids=ids,
        documents=documents,
        metadatas=metadatas,
        embeddings=embeddings
    )
    print("ChromaDB seeded successfully.")

if __name__ == "__main__":
    seed_sqlite()
    seed_chromadb()
