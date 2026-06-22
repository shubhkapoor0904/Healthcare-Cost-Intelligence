from __future__ import annotations

import json
import math
import os
import re
import time
import sqlite3
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
FRONTEND_FILE = ROOT / "frontend" / "index.html"
HOSPITAL_DATA_FILE = ROOT / "data" / "hospitals.json"
OFFLINE_ONLY = os.environ.get("PS4B_OFFLINE_ONLY", "1").strip() != "0"

DB_FILE = ROOT / "data" / "healthcare_cost.db"
CHROMA_DIR = ROOT / "data" / "chroma_db"

_sqlite_conn = None
_chroma_client = None
_chroma_collection = None
_embedding_model = None
_procedure_embeddings_cache = {}
_procedures_cache = []

def cosine_similarity(v1, v2):
    dot = sum(a * b for a, b in zip(v1, v2))
    norm1 = sum(a * a for a in v1) ** 0.5
    norm2 = sum(b * b for b in v2) ** 0.5
    return float(dot / max(1e-9, norm1 * norm2))

def init_services():
    global _sqlite_conn, _chroma_client, _chroma_collection, _embedding_model, _procedure_embeddings_cache, _procedures_cache
    
    _sqlite_conn = sqlite3.connect(str(DB_FILE), check_same_thread=False)
    _sqlite_conn.row_factory = sqlite3.Row
    
    cursor = _sqlite_conn.cursor()
    try:
        cursor.execute("SELECT COUNT(*) FROM procedures")
        count = cursor.fetchone()[0]
        if count == 0:
            raise Exception("Database empty")
    except Exception:
        print("Database not found or empty. Running seeding...")
        import seed_db
        seed_db.seed_sqlite()
        seed_db.seed_chromadb()
        
    cursor.execute("SELECT * FROM procedures")
    for row in cursor.fetchall():
        proc = dict(row)
        proc["synonyms"] = json.loads(proc["synonyms_json"])
        proc["red_flag_terms"] = json.loads(proc["red_flag_terms_json"])
        _procedures_cache.append(proc)
        
    print("Loading sentence-transformers model...")
    os.environ["HF_HOME"] = "D:\\hf_cache"
    os.environ["SENTENCE_TRANSFORMERS_HOME"] = "D:\\st_cache"
    from sentence_transformers import SentenceTransformer
    _embedding_model = SentenceTransformer("all-MiniLM-L6-v2")
    
    import chromadb
    print("Connecting to ChromaDB...")
    _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    _chroma_collection = _chroma_client.get_collection("hospital_specializations")
    
    print("Precomputing procedure embeddings...")
    proc_texts = []
    proc_ids = []
    for p in _procedures_cache:
        text = f"{p['display_name']} - {p['condition_label']}. Synonyms: {', '.join(p['synonyms'])}"
        proc_texts.append(text)
        proc_ids.append(p["procedure_id"])
        
    embeddings = _embedding_model.encode(proc_texts, show_progress_bar=False)
    for p_id, emb in zip(proc_ids, embeddings):
        _procedure_embeddings_cache[p_id] = emb
    print("Initialization completed successfully.")


def billing_guard() -> dict[str, Any]:
    return {
        "offline_first": True,
        "offline_only": OFFLINE_ONLY,
        "external_calls_allowed": False,
        "mode": "offline-first-deterministic",
        "guarantee": "This hackathon MVP has no paid/network API dependency for core functionality. Hospital data, ratings, clinical mapping, cost estimates, ranking, confidence, and explanations are produced locally.",
    }


@dataclass(frozen=True)
class Procedure:
    key: str
    name: str
    condition: str
    icd10: str
    specialty: str
    complexity: str
    aliases: tuple[str, ...]


PROCEDURES: list[Procedure] = [
    Procedure("knee_replacement", "Knee replacement", "Osteoarthritis of knee", "M17.1", "Orthopedics", "major", ("knee pain", "knee replacement", "arthritis knee", "joint replacement")),
    Procedure("angioplasty", "Coronary angioplasty", "Coronary artery disease", "I25.1", "Cardiology", "major", ("chest pain", "blocked artery", "angioplasty", "stent", "heart pain")),
    Procedure("appendectomy", "Appendectomy", "Acute appendicitis", "K35.8", "General Surgery", "moderate", ("appendix", "appendicitis", "right lower abdomen")),
    Procedure("c_section", "Caesarean delivery", "Pregnancy requiring surgical delivery", "O82.9", "Obstetrics", "moderate", ("c section", "cesarean", "delivery", "pregnancy surgery")),
    Procedure("normal_delivery", "Normal delivery", "Pregnancy", "O80", "Obstetrics", "low", ("normal delivery", "delivery", "labor", "pregnancy")),
    Procedure("cataract", "Cataract surgery", "Senile cataract", "H25.9", "Ophthalmology", "low", ("cataract", "cloudy vision", "lens surgery")),
    Procedure("gallbladder", "Laparoscopic cholecystectomy", "Gallstones", "K80.2", "General Surgery", "moderate", ("gall bladder", "gallbladder", "gallstone", "upper abdomen pain")),
    Procedure("hernia", "Hernia repair", "Inguinal hernia", "K40.9", "General Surgery", "moderate", ("hernia", "groin swelling", "abdominal wall")),
    Procedure("mri_brain", "MRI brain", "Neurological evaluation", "R90.8", "Radiology", "low", ("mri brain", "headache scan", "brain scan")),
    Procedure("dialysis", "Hemodialysis session", "Chronic kidney disease", "N18.9", "Nephrology", "moderate", ("dialysis", "kidney failure", "creatinine")),
    Procedure("chemotherapy", "Chemotherapy cycle", "Malignant neoplasm", "C80.1", "Oncology", "major", ("cancer", "chemo", "chemotherapy", "tumor")),
    Procedure("radiotherapy", "Radiotherapy fraction", "Malignant neoplasm", "C80.1", "Oncology", "major", ("radiation therapy", "radiotherapy", "cancer radiation")),
    Procedure("hip_replacement", "Hip replacement", "Osteoarthritis of hip", "M16.9", "Orthopedics", "major", ("hip pain", "hip replacement", "hip arthritis")),
    Procedure("fracture_fixation", "Fracture fixation", "Fracture of limb", "T14.2", "Orthopedics", "moderate", ("fracture", "broken bone", "plate surgery")),
    Procedure("tonsillectomy", "Tonsillectomy", "Chronic tonsillitis", "J35.0", "ENT", "low", ("tonsil", "tonsillitis", "throat infection")),
    Procedure("sinus_surgery", "Functional endoscopic sinus surgery", "Chronic sinusitis", "J32.9", "ENT", "moderate", ("sinus", "blocked nose", "fess")),
    Procedure("hysterectomy", "Hysterectomy", "Uterine disorder", "N85.9", "Gynecology", "major", ("hysterectomy", "uterus removal", "fibroid")),
    Procedure("ivf_cycle", "IVF cycle", "Female infertility", "N97.9", "Fertility", "major", ("ivf", "fertility", "test tube baby")),
    Procedure("endoscopy", "Upper GI endoscopy", "Digestive disorder", "K30", "Gastroenterology", "low", ("endoscopy", "gastric", "stomach scope")),
    Procedure("colonoscopy", "Colonoscopy", "Bowel evaluation", "K63.9", "Gastroenterology", "low", ("colonoscopy", "colon", "blood in stool")),
    Procedure("angiography", "Coronary angiography", "Ischemic heart disease evaluation", "I25.9", "Cardiology", "moderate", ("angiography", "heart test", "coronary test")),
    Procedure("bypass", "CABG bypass surgery", "Coronary artery disease", "I25.1", "Cardiology", "major", ("bypass", "cabg", "heart bypass")),
    Procedure("pacemaker", "Pacemaker implantation", "Cardiac rhythm disorder", "I49.9", "Cardiology", "major", ("pacemaker", "slow heartbeat", "rhythm")),
    Procedure("prostate", "Prostate surgery", "Prostate enlargement", "N40", "Urology", "moderate", ("prostate", "urine problem", "turp")),
    Procedure("kidney_stone", "Kidney stone removal", "Urinary calculus", "N20.0", "Urology", "moderate", ("kidney stone", "renal stone", "ureteroscopy")),
    Procedure("dengue", "Dengue inpatient care", "Dengue fever", "A90", "Internal Medicine", "moderate", ("dengue", "platelet", "fever")),
    Procedure("pneumonia", "Pneumonia admission", "Pneumonia", "J18.9", "Pulmonology", "moderate", ("pneumonia", "breathing infection", "lung infection")),
    Procedure("asthma", "Asthma emergency care", "Asthma", "J45.9", "Pulmonology", "low", ("asthma", "wheezing", "breathlessness")),
    Procedure("icu_day", "ICU day care", "Critical illness", "Z99.8", "Critical Care", "major", ("icu", "ventilator", "critical care")),
    Procedure("nicu", "NICU day care", "Neonatal intensive care", "P07.3", "Neonatology", "major", ("nicu", "newborn icu", "premature baby")),
    Procedure("physiotherapy", "Physiotherapy package", "Rehabilitation need", "Z50.1", "Rehabilitation", "low", ("physio", "physiotherapy", "rehab")),
    Procedure("dental_implant", "Dental implant", "Tooth loss", "K08.1", "Dental", "moderate", ("dental implant", "tooth implant", "missing tooth")),
    Procedure("root_canal", "Root canal treatment", "Dental pulp disease", "K04.0", "Dental", "low", ("root canal", "tooth pain", "rct")),
    Procedure("lasik", "LASIK", "Refractive error", "H52.1", "Ophthalmology", "low", ("lasik", "laser eye", "spectacles removal")),
    Procedure("thyroidectomy", "Thyroidectomy", "Thyroid disorder", "E04.9", "Endocrine Surgery", "major", ("thyroid surgery", "thyroidectomy", "goiter")),
    Procedure("diabetes_admission", "Diabetes stabilization admission", "Diabetes mellitus", "E11.9", "Endocrinology", "moderate", ("diabetes", "sugar high", "insulin admission")),
    Procedure("stroke_care", "Stroke acute care", "Stroke", "I64", "Neurology", "major", ("stroke", "paralysis", "slurred speech")),
    Procedure("migraine", "Migraine evaluation", "Migraine", "G43.9", "Neurology", "low", ("migraine", "headache", "severe headache")),
    Procedure("depression", "Psychiatry consultation package", "Depressive episode", "F32.9", "Psychiatry", "low", ("depression", "anxiety", "mental health")),
    Procedure("skin_biopsy", "Skin biopsy", "Skin lesion", "D48.5", "Dermatology", "low", ("skin biopsy", "mole", "skin lesion")),
    Procedure("burn_care", "Burn wound care", "Burn injury", "T30.0", "Plastic Surgery", "moderate", ("burn", "burns", "wound dressing")),
    Procedure("varicose_vein", "Varicose vein laser treatment", "Varicose veins", "I83.9", "Vascular Surgery", "moderate", ("varicose", "leg veins", "vein laser")),
    Procedure("transplant_eval", "Transplant evaluation", "Organ failure evaluation", "Z01.8", "Transplant Medicine", "major", ("transplant", "organ transplant", "liver transplant")),
    Procedure("liver_cirrhosis", "Liver cirrhosis admission", "Cirrhosis", "K74.6", "Hepatology", "major", ("cirrhosis", "liver failure", "ascites")),
    Procedure("biopsy_guided", "Image-guided biopsy", "Suspicious lesion", "R93.8", "Interventional Radiology", "moderate", ("biopsy", "guided biopsy", "tumor biopsy")),
    Procedure("maternity_package", "Maternity package", "Pregnancy care", "Z34.9", "Obstetrics", "moderate", ("maternity", "pregnancy package", "antenatal")),
    Procedure("vaccination", "Vaccination package", "Immunization", "Z23", "Pediatrics", "low", ("vaccine", "vaccination", "immunization")),
    Procedure("pediatric_fever", "Pediatric fever care", "Fever in child", "R50.9", "Pediatrics", "low", ("child fever", "pediatric fever", "baby fever")),
    Procedure("sleep_study", "Sleep study", "Sleep apnea", "G47.3", "Pulmonology", "low", ("sleep study", "snoring", "sleep apnea")),
    Procedure("obesity_surgery", "Bariatric surgery", "Obesity", "E66.9", "Bariatric Surgery", "major", ("weight loss surgery", "bariatric", "obesity surgery")),
]


CITY_MULTIPLIERS = {
    "metro": 1.18,
    "tier-1": 1.05,
    "tier-2": 0.9,
    "tier-3": 0.78,
}


BASE_COSTS = {
    "low": (18000, 65000),
    "moderate": (65000, 220000),
    "major": (210000, 650000),
}

# Procedure-specific overrides for procedures where the shared
# complexity bucket is unrealistic.  (key -> (min_inr, max_inr))
PROCEDURE_BASE_OVERRIDES: dict[str, tuple[int, int]] = {
    "bypass":       (350000, 900000),
    "chemotherapy": (80000,  250000),
    "dialysis":     (15000,  45000),
    "nicu":         (40000,  180000),
    "ivf_cycle":    (120000, 350000),
}


COMPONENT_WEIGHTS = {
    "procedure": 0.48,
    "doctor_fees": 0.14,
    "room_stay": 0.18,
    "diagnostics": 0.08,
    "medicines_contingency": 0.12,
}


def load_hospitals() -> list[dict[str, Any]]:
    with HOSPITAL_DATA_FILE.open("r", encoding="utf-8") as f:
        rows = json.load(f)

    normalized = []
    for idx, row in enumerate(rows):
        cost_category = str(row.get("cost_category") or "mid").lower()
        price_index = {"budget": 0.78, "mid": 1.0, "premium": 1.24}.get(cost_category, 1.0)
        normalized.append({
            "id": row.get("id") or f"hospital_{idx + 1:03d}",
            "name": str(row["name"]),
            "city": str(row["city"]),
            "city_tier": str(row.get("city_tier") or "tier-2"),
            "specialties": list(row.get("specialties") or []),
            "rating": float(row.get("rating") or 4.0),
            "review_count": int(row.get("review_count") or 0),
            "reviews": int(row.get("review_count") or 0),
            "nabh_accredited": bool(row.get("nabh_accredited")),
            "nabh": bool(row.get("nabh_accredited")),
            "cost_category": cost_category,
            "price_index": price_index,
            "base_price": price_index,
            "approximate_location": row.get("approximate_location"),
            "distance_km": float(row.get("distance_km") or 12.0),
            "synthetic_review_score": float(row.get("synthetic_review_score") or 0.7),
            "sentiment": float(row.get("synthetic_review_score") or 0.7),
        })
    return normalized


HOSPITALS = load_hospitals()


def money(value: float) -> int:
    return int(round(value / 1000.0) * 1000)


def detect_city_tier(city: str) -> str:
    city_norm = city.strip().lower()
    cursor = _sqlite_conn.cursor()
    cursor.execute("SELECT tier FROM city_tiers WHERE city = ?", (city_norm,))
    row = cursor.fetchone()
    if row:
        return row["tier"]
    if city_norm in {"mumbai", "delhi", "bengaluru", "bangalore", "chennai", "hyderabad", "kolkata", "pune"}:
        return "metro"
    if city_norm in {"nagpur", "jaipur", "lucknow", "indore", "surat", "bhopal", "kochi"}:
        return "tier-1"
    return "tier-2"


def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def map_clinical_query(query: str) -> dict[str, Any]:
    q = query.lower()
    q_tokens = tokenize(q)
    
    query_vector = _embedding_model.encode([q], show_progress_bar=False)[0]
    
    candidates = []
    for proc in _procedures_cache:
        exact_alias_match = 0.0
        display_name_l = proc["display_name"].lower()
        if display_name_l in q:
            exact_alias_match = 1.0
        else:
            for alias in proc["synonyms"]:
                alias_l = alias.lower()
                if alias_l in q:
                    exact_alias_match = 1.0
                    break
                    
        max_overlap = 0.0
        for alias in proc["synonyms"]:
            alias_l = alias.lower()
            alias_tokens = tokenize(alias_l)
            overlap = len(q_tokens & alias_tokens) / max(1, len(alias_tokens))
            if overlap > max_overlap:
                max_overlap = overlap
                
        specialty_match = 0.0
        if proc["specialty"].lower() in q:
            specialty_match = 0.2
            
        token_overlap_score = min(1.0, max_overlap + specialty_match)
        
        red_flag_match = 0.0
        for term in proc["red_flag_terms"]:
            if term.lower() in q:
                red_flag_match = 1.0
                break
                
        normalized_rule_score = exact_alias_match * 0.5 + token_overlap_score * 0.4 + red_flag_match * 0.1
        
        cached_emb = _procedure_embeddings_cache[proc["procedure_id"]]
        embedding_score = cosine_similarity(query_vector, cached_emb)
        
        clinical_match_score = 0.55 * embedding_score + 0.45 * normalized_rule_score
        candidates.append((clinical_match_score, proc))
        
    candidates.sort(key=lambda x: x[0], reverse=True)
    
    best_score, best_proc = candidates[0]
    second_score = candidates[1][0] if len(candidates) > 1 else 0.0
    
    if best_score < 0.55:
        return {
            "condition": "Unknown",
            "procedure_key": "__no_match__",
            "procedure": "No matching procedure found",
            "icd10_code": "R69",
            "specialty": "General",
            "complexity": "low",
            "severity": "unknown",
            "comorbidity_flags": [],
            "ambiguity_score": 1.0,
            "clarifying_question": (
                "We couldn't confidently map your query to a known procedure. "
                "Try being more specific about the symptom, diagnosis, or procedure."
            ),
            "example_suggestions": [
                "knee replacement surgery, Nagpur",
                "chest pain, diabetic, Mumbai",
                "cataract surgery for age 68, Pune",
            ],
        }
        
    ambiguity_score = round(1.0 - best_score, 2)
    clarifying_question = None
    
    if (best_score - second_score) < 0.08:
        second_proc = candidates[1][1]
        clarifying_question = f"Did you mean {best_proc['display_name']} or {second_proc['display_name']}?"
    elif best_score < 0.72:
        clarifying_question = "Can you add duration, severity, and any known diagnosis?"
        
    red_flags = []
    if any(term in q for term in ("chest pain", "stroke", "paralysis", "breathless", "severe bleeding", "unconscious")):
        red_flags.append("urgent_symptoms")
    if "diabet" in q or "sugar" in q:
        red_flags.append("diabetes")
    if any(term in q for term in ("bp", "hypertension", "pressure")):
        red_flags.append("hypertension")
    if "kidney" in q or "creatinine" in q or "renal" in q:
        red_flags.append("kidney_disease")
        
    severity = "moderate"
    if best_proc["complexity"] == "major" or "urgent_symptoms" in red_flags:
        severity = "high"
    elif best_proc["complexity"] == "low":
        severity = "low"
        
    return {
        "condition": best_proc["condition_label"],
        "procedure_key": best_proc["procedure_id"],
        "procedure": best_proc["display_name"],
        "icd10_code": best_proc["icd10_code"],
        "specialty": best_proc["specialty"],
        "complexity": best_proc["complexity"],
        "severity": severity,
        "comorbidity_flags": red_flags,
        "ambiguity_score": ambiguity_score,
        "clarifying_question": clarifying_question,
    }


def estimate_cost(mapped: dict[str, Any], profile: dict[str, Any], city: str, room_type: str) -> dict[str, Any]:
    city_tier = detect_city_tier(city)
    proc_key = mapped["procedure_key"]
    
    labels = {
        "procedure": "Procedure / surgery",
        "doctor_fees": "Doctor fees",
        "room_stay": "Room / stay",
        "diagnostics": "Diagnostics",
        "medicines_contingency": "Medicines + contingency",
    }
    whys = {
        "procedure": "Core treatment, OT, equipment, implants where applicable.",
        "doctor_fees": "Surgeon/consultant, anaesthesia, specialist review.",
        "room_stay": "Expected stay adjusted for selected room and ICU risk.",
        "diagnostics": "Pre-op tests, imaging, monitoring, repeat checks.",
        "medicines_contingency": "Medicines, consumables, risk buffer for complications.",
    }
    
    if proc_key == "__no_match__":
        components = [{
            "key": key,
            "label": labels[key],
            "min_inr": 0,
            "max_inr": 0,
            "why": whys[key]
        } for key in COMPONENT_WEIGHTS.keys()]
        return {
            "city_tier": city_tier,
            "room_type": room_type,
            "total_min_inr": 0,
            "total_max_inr": 0,
            "components": components,
            "adjustments": [],
            "coverage": 0.0,
        }
        
    cursor = _sqlite_conn.cursor()
    cursor.execute("""
        SELECT component, min_inr, max_inr 
        FROM procedure_benchmarks 
        WHERE procedure_id = ? AND city_tier = ?
    """, (proc_key, city_tier))
    rows = cursor.fetchall()
    
    # If benchmarks are empty (unseeded), fallback
    if not rows:
        low, high = PROCEDURE_BASE_OVERRIDES.get(proc_key, BASE_COSTS[mapped["complexity"]])
        tier_multiplier = CITY_MULTIPLIERS.get(city_tier, 0.9)
        total_min_raw = money(low * tier_multiplier)
        total_max_raw = money(high * tier_multiplier)
        db_rows = []
        for key, weight in COMPONENT_WEIGHTS.items():
            db_rows.append({
                "component": key,
                "min_inr": money(total_min_raw * weight),
                "max_inr": money(total_max_raw * weight)
            })
        rows = db_rows

    age = int(profile.get("age") or 40)
    comorbidities = [c.lower() for c in (profile.get("comorbidities") or [])]
    query_flags = [f.lower() for f in (mapped.get("comorbidity_flags") or [])]
    
    comp_multipliers = {key: 1.0 for key in COMPONENT_WEIGHTS.keys()}
    adjustments_added = set()
    adjustments = []
    
    city_mult = CITY_MULTIPLIERS.get(city_tier, 0.9)
    adjustments.append({"label": f"{city_tier} city tier", "multiplier": city_mult})
    
    cursor.execute("SELECT factor, component, condition, multiplier FROM multipliers")
    for m in cursor.fetchall():
        factor = m["factor"]
        comp = m["component"]
        condition = m["condition"]
        val = m["multiplier"]
        
        applies = False
        if factor == "age_65_plus" and age >= 65:
            applies = True
            label = "Age over 65"
        elif factor == "diabetes" and ("diabetes" in comorbidities or "diabetes" in query_flags):
            applies = True
            label = "Diabetes care buffer"
        elif factor == "hypertension" and ("hypertension" in comorbidities or "hypertension" in query_flags):
            applies = True
            label = "Hypertension monitoring"
        elif factor == "kidney_disease" and ("kidney_disease" in comorbidities or "kidney_disease" in query_flags or "kidney" in comorbidities or "kidney" in query_flags):
            applies = True
            label = "Kidney monitoring"
        elif factor == "private_room" and room_type == "private":
            applies = True
            label = "Private room"
        elif factor == "icu_likely" and room_type == "icu":
            applies = True
            label = "ICU stay probability"
            
        if applies:
            if comp in comp_multipliers:
                comp_multipliers[comp] *= val
            adj_key = (factor, val)
            if adj_key not in adjustments_added:
                adjustments_added.add(adj_key)
                adjustments.append({"label": label, "multiplier": val})
                
    complexity_map = {"low": 1, "moderate": 2, "major": 3}
    complexity_level = complexity_map.get(mapped["complexity"], 1)
    
    unique_comorbidities = set(comorbidities) | set(query_flags)
    unique_comorbidities.discard("urgent_symptoms")
    comorbidity_count = len(unique_comorbidities)
    
    components = []
    total_min = 0
    total_max = 0
    
    # Calculate a narrow uncertainty band (7.5% to 12.5%) based on complexity & comorbidities
    uncertainty_fraction = 0.06 + (0.015 * complexity_level) + (0.01 * comorbidity_count)
    
    for row in rows:
        comp_key = row["component"]
        c_min = row["min_inr"]
        c_max = row["max_inr"]
        
        mult = comp_multipliers.get(comp_key, 1.0)
        c_mid = (c_min + c_max) / 2.0
        expected_cost = c_mid * mult
        
        adj_min = money(expected_cost * (1.0 - uncertainty_fraction))
        adj_max = money(expected_cost * (1.0 + uncertainty_fraction))
        
        components.append({
            "key": comp_key,
            "label": labels[comp_key],
            "min_inr": adj_min,
            "max_inr": adj_max,
            "why": whys[comp_key],
        })
        total_min += adj_min
        total_max += adj_max

    return {
        "city_tier": city_tier,
        "room_type": room_type,
        "total_min_inr": total_min,
        "total_max_inr": total_max,
        "components": components,
        "adjustments": adjustments,
        "coverage": 0.95 if proc_key in {p.key for p in PROCEDURES} else 0.40,
    }


def discover_hospitals(mapped: dict[str, Any], city: str, cost: dict[str, Any], budget: int | None) -> list[dict[str, Any]]:
    city_norm = city.strip().title()
    
    query_text = f"{mapped['procedure']} {mapped['specialty']} {mapped['condition']}"
    query_vector = _embedding_model.encode([query_text], show_progress_bar=False)[0].tolist()
    
    results = _chroma_collection.query(
        query_embeddings=[query_vector],
        where={"city": city_norm},
        n_results=8,
        include=["metadatas", "embeddings", "documents"]
    )
    
    if not results["ids"] or len(results["ids"][0]) < 3:
        results = _chroma_collection.query(
            query_embeddings=[query_vector],
            n_results=12,
            include=["metadatas", "embeddings", "documents"]
        )
        
    hospitals_list = []
    if results["ids"] and len(results["ids"][0]) > 0:
        for idx in range(len(results["ids"][0])):
            h_id = results["ids"][0][idx]
            meta = results["metadatas"][0][idx]
            emb = results["embeddings"][0][idx]
            
            clinical = cosine_similarity(query_vector, emb)
            rating_score = max(0.0, min(1.0, (meta["rating"] - 3.5) / 1.3))
            accreditation = 1.0 if meta["nabh"] else 0.45
            
            distance_km = meta["distance_km"]
            price_index = meta["price_index"]
            hospital_mid = ((cost["total_min_inr"] + cost["total_max_inr"]) / 2) * price_index
            
            if budget:
                affordability = max(0.05, min(1.0, budget / hospital_mid))
            else:
                affordability = {"budget": 0.95, "mid": 0.78, "premium": 0.55}.get(meta["cost_category"], 0.75)
                
            h_specialties = json.loads(meta["specialties"])
            
            hospitals_list.append({
                "name": meta["name"],
                "city": meta["city"],
                "distance_km": distance_km,
                "rating": meta["rating"],
                "review_count": meta["review_count"],
                "nabh_accredited": meta["nabh"],
                "cost_category": meta["cost_category"],
                "price_index": price_index,
                "specialties": h_specialties,
                "clinical_fit": clinical,
                "rating_score": rating_score,
                "accreditation": accreditation,
                "affordability": affordability
            })
            
    if not hospitals_list:
        return []
        
    ranked = []
    for h in hospitals_list:
        req_spec = mapped["specialty"].lower()
        h_specs_lower = [s.lower() for s in h["specialties"]]
        if req_spec in h_specs_lower:
            clinical_fit_score = 1.0
        else:
            clinical_fit_score = h["clinical_fit"]
            
        score = (
            0.40 * clinical_fit_score
            + 0.20 * h["rating_score"]
            + 0.15 * h["accreditation"]
            + 0.25 * h["affordability"]
        )
        
        strengths, tradeoff = ranking_explanation_factors(
            h,
            clinical=clinical_fit_score,
            rating_score=h["rating_score"],
            accreditation=h["accreditation"],
            affordability=h["affordability"]
        )
        
        min_cost = cost["total_min_inr"] * h["price_index"]
        max_cost = cost["total_max_inr"] * h["price_index"]
        estimated_cost_inr = money((min_cost + max_cost) / 2)
        uncertainty_inr = money((max_cost - min_cost) / 2)

        ranked.append({
            "name": h["name"],
            "city": h["city"],
            "distance_km": h["distance_km"],
            "rating": h["rating"],
            "review_count": h["review_count"],
            "reviews": h["review_count"],
            "nabh": h["nabh_accredited"],
            "nabh_accredited": h["nabh_accredited"],
            "cost_category": h["cost_category"],
            "approximate_location": None,
            "specialties": h["specialties"],
            "estimated_cost_inr": estimated_cost_inr,
            "uncertainty_inr": uncertainty_inr,
            "key_strengths": strengths,
            "tradeoff": tradeoff,
            "subscores": {
                "clinical": round(clinical_fit_score, 2),
                "rating": round(h["rating_score"], 2),
                "accreditation": round(h["accreditation"], 2),
                "affordability": round(h["affordability"], 2),
            },
            "score": round(score, 3),
            "reason": make_reason(h, mapped, strengths, tradeoff),
        })
        
    return sorted(ranked, key=lambda item: item["score"], reverse=True)[:8]


def clinical_match(mapped: dict[str, Any], hospital: dict[str, Any]) -> float:
    requested = mapped["specialty"].lower()
    specialties = [s.lower() for s in hospital["specialties"]]
    if requested in specialties:
        return 1.0
    requested_tokens = tokenize(requested)
    specialty_tokens = tokenize(" ".join(specialties))
    token_score = len(requested_tokens & specialty_tokens) / max(1, len(requested_tokens))
    return max(0.35, min(0.82, token_score))


def ranking_explanation_factors(
    hospital: dict[str, Any],
    *,
    clinical: float,
    rating_score: float,
    accreditation: float,
    affordability: float,
) -> tuple[list[str], str]:
    factor_scores = [
        ("strong clinical specialty match", clinical),
        (f"{hospital['rating']} star synthetic rating", rating_score),
        ("NABH accredited" if hospital["nabh_accredited"] else "known accreditation gap", accreditation),
        (f"{hospital['cost_category']} cost category", affordability),
    ]
    strengths = [label for label, _ in sorted(factor_scores, key=lambda item: item[1], reverse=True)[:2]]
    weakest_label, weakest_score = sorted(factor_scores, key=lambda item: item[1])[0]
    if weakest_score >= 0.75:
        tradeoff = "No major tradeoff in static dataset; verify availability and exact package price."
    else:
        tradeoff = f"Tradeoff: {weakest_label} is the weakest scoring factor."
    return strengths, tradeoff


def make_reason(hospital: dict[str, Any], mapped: dict[str, Any], strengths: list[str], tradeoff: str) -> str:
    return (
        f"Ranked for {mapped['procedure']} because of {strengths[0]} and {strengths[1]}. "
        f"{tradeoff} Static dataset only; verify live availability before deciding."
    )


def confidence(
    mapped: dict[str, Any],
    cost: dict[str, Any],
    hospitals: list[dict[str, Any]],
    profile: dict[str, Any] | None = None,
    room_type: str = "general",
) -> dict[str, Any]:
    # FIX-2: Richer confidence scoring — penalise for missing hospitals,
    #        ambiguous queries, and high-risk patient profiles.
    data_completeness = 0.9 if hospitals else 0.35
    ambiguity_component = 1 - float(mapped["ambiguity_score"])
    value = 0.35 * data_completeness + 0.35 * cost["coverage"] + 0.30 * ambiguity_component

    reasons = [
        f"Benchmark coverage {round(cost['coverage'] * 100)}%",
        f"Clinical ambiguity {mapped['ambiguity_score']}",
        f"{len(hospitals)} ranked provider options",
    ]

    # Penalise: very few nearby hospitals
    if len(hospitals) < 3:
        value -= 0.10
        reasons.append("Few nearby hospitals available")

    # Penalise: highly ambiguous query
    if float(mapped["ambiguity_score"]) >= 0.6:
        value -= 0.08
        reasons.append("Query needs clarification")

    # Penalise: no-match fallback
    if mapped["procedure_key"] == "__no_match__":
        value -= 0.15
        reasons.append("No procedure match")

    # Penalise: high-risk patient factors
    if profile:
        age = int(profile.get("age") or 40)
        comorbidities = profile.get("comorbidities") or []
        if age >= 65:
            value -= 0.05
            reasons.append("Elderly patient (65+)")
        if len(comorbidities) >= 2:
            value -= 0.05
            reasons.append("Multiple comorbidities")

    if room_type == "icu":
        value -= 0.05
        reasons.append("ICU stay increases variability")

    value = round(max(0.05, min(0.98, value)), 2)

    # Confidence interpretation — human-readable label + explanation
    if value >= 0.75:
        level = "high"
        explanation = "Strong match — the query maps clearly to a known procedure with good hospital coverage in your area."
    elif value >= 0.50:
        level = "medium"
        explanation = "Reasonable match — estimates are directional but may vary. Consider adding more clinical detail."
    else:
        level = "low"
        explanation = "Weak match — the query is ambiguous or data coverage is limited. Treat these numbers as rough guidance only."

    caution = None
    if value < 0.50:
        caution = (
            "⚠ Low confidence: This estimate may not reflect your actual situation. "
            "Please consult a healthcare provider and request a detailed quote."
        )

    return {
        "score": value,
        "level": level,
        "explanation": explanation,
        "caution": caution,
        "reasons": reasons,
    }


def parse_budget(raw: Any) -> int | None:
    if raw in (None, "", 0):
        return None
    try:
        return int(float(raw))
    except (TypeError, ValueError):
        digits = re.sub(r"\D", "", str(raw))
        return int(digits) if digits else None


def extract_city_from_query(query: str) -> str | None:
    q = query.lower()
    cursor = _sqlite_conn.cursor()
    cursor.execute("SELECT DISTINCT city FROM city_tiers")
    cities = [row["city"] for row in cursor.fetchall()]
    for city in cities:
        if re.search(rf"\b{re.escape(city.lower())}\b", q):
            return city.title()
    return None


def extract_age_from_query(query: str) -> int | None:
    q = query.lower()
    patterns = [
        r"\b(\d+)\s*(?:yo|y/o|years?\s*old|year\s*old|yrs?\s*old)\b",
        r"\bage\s*(\d+)\b",
        r"\baged\s*(\d+)\b"
    ]
    for pattern in patterns:
        match = re.search(pattern, q)
        if match:
            try:
                age = int(match.group(1))
                if 0 <= age <= 120:
                    return age
            except ValueError:
                pass
    return None


def extract_budget_from_query(query: str) -> int | None:
    q = query.lower()
    pattern = r"\b(?:budget|inr|rs\.?|rupees?|price|cost)?\s*(?:of\s*)?(\d+(?:\.\d+)?)\s*(k|lakhs?|lacs?|l|cr|crores?|thousand)?\b"
    matches = re.finditer(pattern, q)
    for match in matches:
        val_str = match.group(1)
        suffix = match.group(2)
        full_match_text = match.group(0)
        has_context = any(word in full_match_text for word in ("budget", "inr", "rs", "rupees", "price", "cost"))
        if not suffix and not has_context:
            continue
        try:
            val = float(val_str)
            if suffix:
                if suffix in ("k", "thousand"):
                    val *= 1_000
                elif suffix in ("lakhs", "lakh", "l", "lac", "lacs"):
                    val *= 100_000
                elif suffix in ("cr", "crore", "crores"):
                    val *= 10_000_000
            return int(round(val))
        except ValueError:
            pass
    return None


def extract_comorbidities_from_query(query: str) -> list[str]:
    q = query.lower()
    found = []
    mappings = {
        "diabetes": [r"\bdiabet", r"\bsugar\b"],
        "hypertension": [r"\bhypertension\b", r"\bbp\b", r"\bblood pressure\b"],
        "cardiac_history": [r"\bcardiac\b", r"\bheart\b", r"\bstent\b", r"\barter\w*", r"\bbypass\b", r"\bangio"],
        "kidney_disease": [r"\bkidney\b", r"\brenal\b", r"\bdialysis\b", r"\bcreatinine\b", r"\bnephro"],
        "pregnancy": [r"\bpregnant\b", r"\bpregnancy\b", r"\bmaternity\b", r"\bdelivery\b", r"\bbaby\b", r"\blabor\b", r"\bc\s*-\s*section\b", r"\bc\s+section\b"],
        "immunocompromised": [r"\bimmunocompromised\b", r"\bimmuno", r"\bhiv\b", r"\bcancer\b", r"\bchemo\b", r"\bradiation\b", r"\btumor\b"]
    }
    for comorb, patterns in mappings.items():
        for pattern in patterns:
            if re.search(pattern, q):
                found.append(comorb)
                break
    return found


def run_query(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    
    extracted_city = extract_city_from_query(query)
    extracted_age = extract_age_from_query(query)
    extracted_budget = extract_budget_from_query(query)
    extracted_comorbidities = extract_comorbidities_from_query(query)
    
    payload_city = payload.get("city")
    city = str(payload_city if (payload_city and str(payload_city).strip() != "") else (extracted_city if extracted_city else "Nagpur"))
    
    payload_age = payload.get("age")
    if payload_age is not None and str(payload_age).strip() != "" and int(payload_age) > 0:
        age = int(payload_age)
    else:
        age = extracted_age if extracted_age is not None else 40
        
    payload_budget = payload.get("budget")
    if payload_budget is not None and str(payload_budget).strip() != "":
        budget = parse_budget(payload_budget)
    else:
        budget = extracted_budget
        
    payload_comorbidities = payload.get("comorbidities") or []
    if isinstance(payload_comorbidities, str):
        payload_comorbidities = [c.strip() for c in payload_comorbidities.split(",") if c.strip()]
    merged_comorbidities = list(set([c.lower() for c in payload_comorbidities]) | set(extracted_comorbidities))
    
    profile = {
        "age": age,
        "gender": payload.get("gender") or "",
        "comorbidities": merged_comorbidities,
    }
    room_type = str(payload.get("room_type") or "general")
    
    mapped = map_clinical_query(query)
    
    for flag in mapped.get("comorbidity_flags", []):
        if flag != "urgent_symptoms" and flag not in profile["comorbidities"]:
            profile["comorbidities"].append(flag)
            
    mapped["comorbidity_flags"] = list(set(mapped.get("comorbidity_flags", [])) | set(profile["comorbidities"]))
    
    cost = estimate_cost(mapped, profile, city, room_type)
    hospitals = discover_hospitals(mapped, city, cost, budget)
    conf = confidence(mapped, cost, hospitals, profile=profile, room_type=room_type)
    disclaimer = "Estimate based on demo benchmark and provider data. Verify live quotes, emergency needs, and doctor advice before deciding."
    
    return {
        "request_id": f"ps4b-{int(time.time() * 1000)}",
        "input": {"query": query, "city": city, "profile": profile, "budget": budget, "room_type": room_type},
        "clinical_mapping": mapped,
        "cost_estimate": cost,
        "hospitals": hospitals,
        "confidence": conf,
        "disclaimer": disclaimer,
        "build_notes": {
            "mvp_status": "offline-first deterministic pipeline; no paid/network API calls are required for core functionality",
            "future_scope": "External APIs can be evaluated later as optional extensions, not MVP dependencies.",
            "extracted_inputs": {
                "city": extracted_city,
                "age": extracted_age,
                "budget": extracted_budget,
                "comorbidities": extracted_comorbidities
            }
        },
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "PS4BHTTP/1.0"

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/":
            self._serve_file(FRONTEND_FILE, "text/html; charset=utf-8")
            return
        if parsed.path == "/favicon.ico":
            self.send_response(204)
            self._cors()
            self.end_headers()
            return
        if parsed.path == "/health":
            self._json({"ok": True, "service": "ps4b-cost-intelligence"})
            return
        if parsed.path == "/api/billing-guard":
            self._json(billing_guard())
            return
        if parsed.path == "/api/procedures":
            self._json([p.__dict__ for p in PROCEDURES])
            return
        if parsed.path == "/api/hospitals":
            params = parse_qs(parsed.query)
            city = params.get("city", ["Nagpur"])[0]
            mapped = map_clinical_query(params.get("procedure", ["knee pain"])[0])
            cost = estimate_cost(mapped, {"age": 40, "comorbidities": []}, city, "general")
            self._json(discover_hospitals(mapped, city, cost, None))
            return
        requested = ROOT / parsed.path.lstrip("/")
        if requested.exists() and requested.is_file() and requested.resolve().is_relative_to(ROOT):
            content_type = "text/plain; charset=utf-8"
            if requested.suffix == ".css":
                content_type = "text/css; charset=utf-8"
            elif requested.suffix == ".js":
                content_type = "text/javascript; charset=utf-8"
            elif requested.suffix == ".png":
                content_type = "image/png"
            self._serve_file(requested, content_type)
            return
        self._json({"error": "not_found"}, status=404)

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path not in {"/api/query", "/api/estimate"}:
            self._json({"error": "not_found"}, status=404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8") if length else "{}"
            payload = json.loads(body)
            result = run_query(payload)
            self._json(result)
        except Exception as exc:
            self._json({"error": "server_error", "detail": str(exc)}, status=500)

    def log_message(self, fmt: str, *args: Any) -> None:
        print(f"[{self.log_date_time_string()}] {self.address_string()} {fmt % args}")

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "content-type")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")

    def _json(self, payload: Any, status: int = 200) -> None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _serve_file(self, path: Path, content_type: str) -> None:
        data = path.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def main() -> None:
    port = int(os.environ.get("PORT", "8765"))
    init_services()
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PS4B Healthcare Cost Intelligence running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
