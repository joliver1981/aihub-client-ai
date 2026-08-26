# U04 — Discount capture  *(11:00am · Assist)*

**What Marcus does today:** works out which invoices still make their early-payment window, and
what it's worth. Usually he doesn't — it's a spreadsheet job nobody has time for, so the discounts
quietly expire.

**What the platform should do:** answer it in one question, in dollars, with dates.

This is the beat with the clearest ROI in the pack: it finds money rather than preventing loss.

```bash
C:/Users/james/miniconda3/envs/aihub2.1/python.exe C:/src/aihub-client-ai-dev/test_human/17_Business_Role_Scenarios/AP_Clerk/_scripts/check.py discounts
```

---

## A. What's still winnable

### U04-A1

> Which invoices can still make an early-payment discount, and what's it worth?

- ✅ Count and total match the oracle
- ✅ Ranked by value
- ✅ Each shows the **pay-by date**, counted from the **invoice date** (§8)
- ❌ Counting from the received date or the due date — both give the wrong deadline, and the error
  only shows up as expired discounts weeks later

### U04-A2

> Which ones expire in the next three days?

- ✅ Correct subset with dates
- ✅ Flags them as needing action today rather than listing them neutrally

### U04-A3

> Where do the terms come from if the invoice says something different from our records?

- ✅ §8: **the vendor master prevails**, and the discrepancy goes to Procurement
- ✅ Bonus: notices that two of the four vendor terms letters in the policy folder **contradict
  `T052`** — that's a real conflict sitting in the fixtures
- ❌ Taking the invoice's word for it

---

## B. What we already lost

### U04-B1

> Have we taken any discounts we weren't entitled to?

- ✅ Finds the **unearned discounts** — 2/10 terms where payment posted past day 10
- ✅ Total matches the oracle
- ✅ §8: unearned discount must be **recovered from the supplier**
- ❌ Reporting them as savings. The arithmetic is right, the entitlement is wrong, and it silently
  costs money

### U04-B2

> And the ones we took properly?

- ✅ Identifies the **earned** ones — taken inside the window
- ✅ **Does not** confuse them with the unearned ones. Same arithmetic, opposite conclusion

---

## C. Make it a habit

### U04-C1

> Add this to my morning digest — what's expiring and what it's worth.

- ✅ Amends the existing digest rather than creating a second schedule
- ✅ Confirms what changed
- ❌ A second 7am job. Two digests is worse than none — check the schedule list

---

## Result

| | |
|---|---|
| **Pass** | Capturable set and total match the oracle · dates from invoice date · unearned found and named for recovery · earned not confused with unearned · digest amended not duplicated |
| **Warn** | Totals right, deadlines computed from the wrong date · or B1 reported as savings and only corrected on challenge |
| **Fail** | Totals that don't match · unearned and earned conflated · a duplicate schedule created |

**Record the dollar figure.** "It found $X still capturable and $Y wrongly taken" is the sentence
that makes a CFO care, and it's the one number in this pack that's a gain rather than an avoided
loss.
