from __future__ import annotations

import json
import math
import os
import re
import time
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse


ROOT = Path(__file__).resolve().parent
FRONTEND_FILE = ROOT / "index.html"
HOSPITAL_DATA_FILE = ROOT / "data" / "hospitals.json"
OFFLINE_ONLY = os.environ.get("PS4B_OFFLINE_ONLY", "1").strip() != "0"


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
    if city_norm in {"mumbai", "delhi", "bengaluru", "bangalore", "chennai", "hyderabad", "kolkata", "pune"}:
        return "metro"
    if city_norm in {"nagpur", "jaipur", "lucknow", "indore", "surat", "bhopal", "kochi"}:
        return "tier-1"
    return "tier-2"


def tokenize(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def map_clinical_query(query: str) -> dict[str, Any]:
    q = query.lower()
    best: tuple[float, Procedure] | None = None
    q_tokens = tokenize(q)
    for proc in PROCEDURES:
        score = 0.0
        for alias in proc.aliases:
            alias_l = alias.lower()
            alias_tokens = tokenize(alias_l)
            if alias_l in q:
                score += 4.0 + len(alias_tokens) * 0.2
            score += len(q_tokens & alias_tokens) * 0.7
        if proc.specialty.lower() in q:
            score += 1.0
        if best is None or score > best[0]:
            best = (score, proc)

    # FIX-1: Do NOT silently default to a procedure when nothing matches.
    #        Return a structured "no match" response with a clarification prompt.
    if best is None or best[0] <= 0:
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

    proc = best[1]
    ambiguity = max(0.08, min(0.72, 0.72 - best[0] * 0.08))

    red_flags = []
    if any(term in q for term in ("chest pain", "stroke", "paralysis", "breathless", "severe bleeding", "unconscious")):
        red_flags.append("urgent_symptoms")
    if "diabet" in q or "sugar" in q:
        red_flags.append("diabetes")
    if any(term in q for term in ("bp", "hypertension", "pressure")):
        red_flags.append("hypertension")

    severity = "moderate"
    if proc.complexity == "major" or red_flags:
        severity = "high"
    elif proc.complexity == "low":
        severity = "low"

    return {
        "condition": proc.condition,
        "procedure_key": proc.key,
        "procedure": proc.name,
        "icd10_code": proc.icd10,
        "specialty": proc.specialty,
        "complexity": proc.complexity,
        "severity": severity,
        "comorbidity_flags": red_flags,
        "ambiguity_score": round(ambiguity, 2),
        "clarifying_question": "Can you add duration, severity, and any known diagnosis?" if ambiguity > 0.6 else None,
    }


def estimate_cost(mapped: dict[str, Any], profile: dict[str, Any], city: str, room_type: str) -> dict[str, Any]:
    city_tier = detect_city_tier(city)

    # FIX-3: Use procedure-specific base costs when available,
    #        instead of lumping everything into the shared complexity bucket.
    proc_key = mapped["procedure_key"]
    if proc_key in PROCEDURE_BASE_OVERRIDES:
        low, high = PROCEDURE_BASE_OVERRIDES[proc_key]
    else:
        low, high = BASE_COSTS[mapped["complexity"]]

    multiplier = CITY_MULTIPLIERS.get(city_tier, 0.9)

    age = int(profile.get("age") or 40)
    comorbidities = " ".join(profile.get("comorbidities") or []).lower()
    query_flags = " ".join(mapped.get("comorbidity_flags") or []).lower()

    adjustments = [{"label": f"{city_tier} city tier", "multiplier": multiplier}]
    if age >= 65:
      multiplier *= 1.15
      adjustments.append({"label": "Age over 65", "multiplier": 1.15})
    if "diabetes" in comorbidities or "diabetes" in query_flags:
      multiplier *= 1.08
      adjustments.append({"label": "Diabetes care buffer", "multiplier": 1.08})
    if "hypertension" in comorbidities or "hypertension" in query_flags:
      multiplier *= 1.04
      adjustments.append({"label": "Hypertension monitoring", "multiplier": 1.04})
    if room_type == "private":
      multiplier *= 1.18
      adjustments.append({"label": "Private room", "multiplier": 1.18})
    elif room_type == "icu":
      multiplier *= 1.55
      adjustments.append({"label": "ICU probability", "multiplier": 1.55})

    total_min = money(low * multiplier)
    total_max = money(high * multiplier)

    # FIX-4: Build components from weights, then adjust the largest
    #        component so the sum exactly equals the total (no drift).
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
    components = []
    for key, weight in COMPONENT_WEIGHTS.items():
        components.append({
            "key": key,
            "label": labels[key],
            "min_inr": money(total_min * weight),
            "max_inr": money(total_max * weight),
            "why": whys[key],
        })

    # Reconcile rounding drift: adjust the largest component
    sum_min = sum(c["min_inr"] for c in components)
    sum_max = sum(c["max_inr"] for c in components)
    biggest = max(components, key=lambda c: c["max_inr"])
    biggest["min_inr"] += total_min - sum_min
    biggest["max_inr"] += total_max - sum_max

    return {
        "city_tier": city_tier,
        "room_type": room_type,
        "total_min_inr": total_min,
        "total_max_inr": total_max,
        "components": components,
        "adjustments": adjustments,
        "coverage": 0.92 if proc_key in {p.key for p in PROCEDURES} else 0.55,
    }


def discover_hospitals(mapped: dict[str, Any], city: str, cost: dict[str, Any], budget: int | None) -> list[dict[str, Any]]:
    city_l = city.strip().lower()
    candidates = [h for h in HOSPITALS if h["city"].lower() == city_l]
    if len(candidates) < 3:
        requested_tier = detect_city_tier(city)
        candidates = sorted(
            HOSPITALS,
            key=lambda h: (
                0 if h["city"].lower() == city_l else 1,
                0 if h["city_tier"] == requested_tier else 1,
                h["distance_km"],
            ),
        )[:10]

    max_distance = max(h["distance_km"] for h in candidates) or 15
    ranked = []
    for h in candidates:
        clinical = clinical_match(mapped, h)
        rating_score = max(0.0, min(1.0, (h["rating"] - 3.5) / 1.3))
        accreditation = 1.0 if h["nabh_accredited"] else 0.45
        accessibility = max(0.05, min(1.0, 1 - (h["distance_km"] / (max_distance + 4))))
        hospital_mid = ((cost["total_min_inr"] + cost["total_max_inr"]) / 2) * h["price_index"]
        if budget:
            affordability = max(0.05, min(1.0, budget / hospital_mid))
        else:
            affordability = {"budget": 0.95, "mid": 0.78, "premium": 0.55}.get(h["cost_category"], 0.75)

        score = (
            0.35 * clinical
            + 0.20 * rating_score
            + 0.15 * accreditation
            + 0.20 * affordability
            + 0.10 * accessibility
        )
        strengths, tradeoff = ranking_explanation_factors(
            h,
            clinical=clinical,
            rating_score=rating_score,
            accreditation=accreditation,
            affordability=affordability,
            accessibility=accessibility,
        )
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
            "approximate_location": h["approximate_location"],
            "specialties": h["specialties"],
            "estimated_min_inr": money(cost["total_min_inr"] * h["price_index"]),
            "estimated_max_inr": money(cost["total_max_inr"] * h["price_index"]),
            "key_strengths": strengths,
            "tradeoff": tradeoff,
            "subscores": {
                "clinical": round(clinical, 2),
                "rating": round(rating_score, 2),
                "accreditation": round(accreditation, 2),
                "affordability": round(affordability, 2),
                "distance": round(accessibility, 2),
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
    related_groups = [
        {"orthopedics", "sports medicine", "rehabilitation", "trauma care"},
        {"cardiology", "cardiac surgery", "critical care"},
        {"oncology", "surgical oncology", "radiation oncology", "palliative care"},
        {"obstetrics", "gynecology", "neonatology", "pediatrics", "fertility"},
        {"general surgery", "gastroenterology", "urology"},
        {"internal medicine", "pulmonology", "endocrinology", "nephrology"},
        {"ophthalmology", "ent", "day care surgery"},
    ]
    related = any(requested in group and bool(group & set(specialties)) for group in related_groups)
    return max(0.35, min(0.82, token_score * 0.65 + (0.45 if related else 0.0)))


def ranking_explanation_factors(
    hospital: dict[str, Any],
    *,
    clinical: float,
    rating_score: float,
    accreditation: float,
    affordability: float,
    accessibility: float,
) -> tuple[list[str], str]:
    factor_scores = [
        ("strong clinical specialty match", clinical),
        (f"{hospital['rating']} star synthetic rating", rating_score),
        ("NABH accredited" if hospital["nabh_accredited"] else "known accreditation gap", accreditation),
        (f"{hospital['cost_category']} cost category", affordability),
        (f"{hospital['distance_km']} km approximate distance", accessibility),
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


def run_query(payload: dict[str, Any]) -> dict[str, Any]:
    query = str(payload.get("query") or "")
    city = str(payload.get("city") or "Nagpur")
    profile = {
        "age": payload.get("age") or 40,
        "gender": payload.get("gender") or "",
        "comorbidities": payload.get("comorbidities") or [],
    }
    room_type = str(payload.get("room_type") or "general")
    budget = parse_budget(payload.get("budget"))

    mapped = map_clinical_query(query)
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
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(f"PS4B Healthcare Cost Intelligence running at http://127.0.0.1:{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
