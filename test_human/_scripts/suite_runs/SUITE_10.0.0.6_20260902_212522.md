# Regression Suite — 20260902_212522

- Target: **INSTALLED 10.0.0.6**
- Base: `http://10.0.0.6:5001`
- Competency tier: yes
- Fixtures seeded first: no

| pack | verdict | detail | minutes |
|---|---|---|---:|
| 15_Platform_Regression | FAILURES (no baseline regression) | 2 FAIL / 52 PASS / 47 SKIP / 2 XFAIL / 3 XPASS | 3.9 |
| 24_Installed_Smoke | FAILURES | 3 FAIL / 3 PASS | 0.8 |
| 16_CC_Agent_Matrix | FAILURES (no baseline regression) | 4 FAIL / 15 PASS / 5 SKIP | 4.8 |
| 17_Scheduling_Matrix | CLEAN | 16 PASS / 2 XFAIL / 1 XPASS | 7.7 |
| 18_AuthZ_Matrix | CLEAN | 9 PASS / 6 SKIP / 1 XFAIL / 1 XPASS | 0.1 |
| 19_CC_Tier_C | FAILURES | 3 FAIL | 13.4 |
| 20_The_Agent | BLOCKED | target http://10.0.0.6:5111 has no Anthropic key (anthropic_key_source='none') — configure BYOK or the relay on the box, then rerun | 0.0 |
| 22_GA_Code_Interpreter | SKIPPED | pack is local-only | 0.0 |
