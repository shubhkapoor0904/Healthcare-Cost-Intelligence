# PS4B Healthcare Cost Intelligence

Offline-first hackathon MVP for explainable healthcare cost estimation and hospital ranking.

## Run

```powershell
cd C:\Users\shubh\Documents\Codex\2026-04-17-files-mentioned-by-the-user-ps4b-2
.\run_product.ps1
```

Then open:

```text
http://127.0.0.1:8765
```

## Offline-First MVP

The MVP must work without paid or network APIs.

- SQLite: cost benchmark tables and deterministic seeded procedure costs.
- ChromaDB: local retrieval over hospital specialization descriptions.
- sentence-transformers: local semantic embeddings for query/procedure and hospital matching.
- Static datasets: hospitals, NABH flags, synthetic ratings/review summaries, city tiers.
- Rule-based logic: deterministic clinical intent mapping, cost multipliers, ranking, explanations, confidence, and disclaimers.

The current runnable app uses local seeded data and deterministic logic. The productionized offline version should move the in-memory seed data into SQLite/ChromaDB as described in `OFFLINE_MVP_SPEC.md`.

## What Works

- Patient query form for symptom/procedure, city, age, room preference, budget, and comorbidities.
- Rule-based clinical mapper over 50 seeded procedures.
- 5-component treatment cost ranges.
- City tier, age, comorbidity, room, and ICU multipliers.
- Static hospital retrieval and ranking.
- Static hospital dataset with 43 synthetic but realistic hospital records in `data/hospitals.json`.
- Weighted ranking across clinical match, reputation, accessibility, and affordability.
- Confidence score, uncertainty handling, and no-diagnosis medical disclaimer.
- JSON endpoints:
  - `GET /health`
  - `GET /api/billing-guard`
  - `GET /api/procedures`
  - `GET /api/hospitals?city=Nagpur&procedure=knee pain`
  - `POST /api/query`

## Billing Safety

This project defaults to no paid calls.

- No paid API SDK is required for core functionality.
- No API keys are stored in this repo.
- `.env.example` defaults to `PS4B_OFFLINE_ONLY=1`.
- `GET /api/billing-guard` reports `external_calls_allowed: false`.
- Any paid/network service is optional future scope only and must not be required for MVP judging.

## Optional Future Scope

Only after the offline MVP is judged, external services can be evaluated as optional enrichment. They must stay behind feature flags and hard budgets, and the local deterministic path must remain the default.
