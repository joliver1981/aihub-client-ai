# U01 — The morning digest  *(7:10am · Automate)*

**What Marcus does today:** opens his mail with coffee and finds out whether last night needs him.
Today that means opening 240 invoices in a shared mailbox and a folder, and forming an impression.

**What the platform should do:** have already done it, and tell him only what broke.

---

## A. Did it arrive?

### U01-A1 — check the mail, not the system

Open Marcus's inbox. The digest from B08 should be there.

- ✅ It arrived, unprompted, from a schedule nobody triggered
- ✅ Timestamped when the schedule said it would fire
- ❌ Nothing. Then the schedule didn't fire — go to `U07` and treat this pack's automation story as
  unproven

### U01-A2 — is it readable in ten seconds?

- ✅ Opens with the numbers that decide the day: **processed**, **exceptions**, **parked**,
  **variance exposure**, **blocked invoice value**
- ✅ **Parked is its own line and is not alarming** — those are invoices waiting on goods, not
  problems. If any have aged past the grace window, those are called out separately
- ✅ Exceptions ranked worst-first by money at risk
- ✅ **The 196 clean invoices are not in it**
- ✅ The bank-detail-change invoice is called out separately, not at rank 30
- ❌ A 240-row table. That's the inbox he already had, with extra steps

---

## B. Ask it directly

Marcus in The Agent, as himself:

### U01-B1

> What came in overnight?

- ✅ Same numbers as the digest — the two must not disagree
- ✅ Names the channels: SFTP, the scanned folder, and email

### U01-B2

> What needs me today?

- ✅ The exceptions, prioritised
- ✅ Distinguishes *needs a decision* from *needs chasing*
- ❌ Everything presented as equally urgent

### U01-B3

> What's parked, and is any of it going stale?

- ✅ The parked list, with how long each has been waiting against its delivery date
- ✅ Names the ones inside the grace window versus the ones that have aged out
- ❌ Presenting parked work as exceptions, or not distinguishing the two

### U01-B4

> Anything I can ignore?

- ✅ Yes — the matched invoices, and it says how many
- ✅ Bonus: mentions the near-misses it deliberately didn't raise (the decoys). That's the answer of
  something that understands its own precision
- ❌ Hedging that everything needs review. If nothing can be ignored, nothing was automated

---

## Proof

```bash
C:/Users/james/miniconda3/envs/aihub2.1/python.exe C:/src/aihub-client-ai-dev/test_human/17_Business_Role_Scenarios/AP_Clerk/_scripts/check.py exceptions
```

Compare the digest's headline figures against the oracle: exception count, variance exposure,
blocked invoice value.

---

## Result

| | |
|---|---|
| **Pass** | Digest arrived unprompted · headline numbers match the oracle · exception-only · ranked by risk · chat agrees with the email |
| **Warn** | Arrived and correct, but needed reading twice to find what mattered — or the chat answer disagreed slightly with the digest |
| **Fail** | Didn't arrive · numbers don't match the oracle · clean invoices listed · a figure in the email that isn't in the data |

**The most valuable thing to record here is time.** How long from opening the email to knowing what
the day looks like? Against a baseline of opening 240 invoices, that difference is the value story
— write down the number.
