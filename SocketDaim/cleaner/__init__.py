"""sd-cleaner — retention enforcement service.

Runs daily at the configured local time and applies gateway_plan.md §9.3
data retention policy:
  - normal video  (is_valid AND NOT is_excluded)   → 14 days
  - anomaly video (NOT is_valid OR is_excluded)    → 180 days
  - sensor_sample                                  → 180 days
  - ingestion_log                                  → 180 days
"""
