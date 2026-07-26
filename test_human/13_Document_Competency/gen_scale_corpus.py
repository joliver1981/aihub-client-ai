"""Generate the pack-13 SCALE corpus: 120 synthetic leases with exact ground truth.

Deterministic (seeded) — the generator OWNS the text, so ground truth is exact by
construction. Class distribution over stores S101..S220:
  - 40 landlord-responsibility HVAC leases (6 of them phrased ONLY as "climate control
    systems" / "heating, ventilation and air-conditioning plant" — never the word HVAC)
  - 40 tenant-responsibility (4 synonym-only)
  - 34 split-by-cost-threshold
  -  6 silent (maintenance article covers plumbing/electrical only — HVAC never addressed)

Outputs:
  scale_corpus/SCALE13_<store>_<name>.txt   (3 pages, separated by \f)
  scale_ground_truth.json                   (single source of truth for grading)
"""
import json
import os
import random

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, 'scale_corpus')
SEED = 13
N_LANDLORD, N_TENANT, N_SPLIT, N_SILENT = 40, 40, 34, 6
SYN_LANDLORD, SYN_TENANT = 6, 4

CITIES = ['Boston', 'Hartford', 'Chicago', 'Dallas', 'Houston', 'Atlanta', 'Denver', 'Phoenix',
          'Seattle', 'Portland', 'Columbus', 'Austin', 'Tampa', 'Orlando', 'Nashville', 'Memphis',
          'Raleigh', 'Charlotte', 'Detroit', 'Cleveland']
CENTERS = ['Gateway Plaza', 'Northgate Mall', 'Riverside Commons', 'Summit Center', 'Oakwood Square',
           'Harbor Point', 'Eastview Crossing', 'Liberty Marketplace', 'Cedar Hills', 'Stonebridge Court',
           'Willow Creek', 'Fairfield Station', 'Metro Exchange', 'Union Yards', 'Lakeshore Galleria']

HVAC_LANDLORD = ("6.3 HVAC Responsibility\nLANDLORD'S FULL RESPONSIBILITY: Landlord shall perform, "
                 "at Landlord's sole cost and expense, all maintenance, repairs, and replacements of "
                 "the HVAC equipment serving the Premises, including {tons}-ton rooftop units, with "
                 "costs included in Common Area Maintenance charges.")
HVAC_LANDLORD_SYN = ("6.3 Climate Control Responsibility\nLandlord shall maintain, repair and replace, "
                     "at its sole cost, all climate control systems and the heating, ventilation and "
                     "air-conditioning plant serving the Premises ({tons}-ton capacity), with costs "
                     "included in Common Area Maintenance charges.")
HVAC_TENANT = ("6.3 HVAC Responsibility\nTENANT'S RESPONSIBILITY: Tenant shall, at its sole cost and "
               "expense, maintain, repair and replace all HVAC equipment exclusively serving the "
               "Premises ({tons}-ton rooftop units), including quarterly service contracts and filter "
               "changes, and shall bear 100% of HVAC costs.")
HVAC_TENANT_SYN = ("6.3 Climate Control Responsibility\nTenant shall, at its sole cost and expense, "
                   "maintain and service all climate control systems and the heating, ventilation and "
                   "air-conditioning plant serving the Premises ({tons}-ton capacity), including all "
                   "repairs and eventual replacement.")
HVAC_SPLIT = ("6.3 HVAC Responsibility\nSPLIT RESPONSIBILITY: Tenant shall be responsible for routine "
              "HVAC maintenance, filter changes, service contracts, and any single repair not exceeding "
              "${threshold:,} per occurrence. Landlord shall be responsible for repairs exceeding "
              "${threshold:,} per occurrence and for complete unit replacement when required.")
MAINT_SILENT = ("6.3 Building Systems\nTenant shall maintain interior plumbing fixtures and interior "
                "electrical distribution within the Premises. Landlord shall maintain the roof, "
                "foundation, structural elements, exterior walls, and Common Area utilities.")


def _page1(store, name, city, expiry, rent, deposit):
    return (f"COMMERCIAL LEASE AGREEMENT\n\nStore ID: {store}\nProperty: {name}, {city}\n"
            f"Landlord: {city.upper()} RETAIL PROPERTIES LLC\nTenant: SKYLINE STORES, a Delaware corporation\n\n"
            f"ARTICLE 1 - BASIC LEASE TERMS\nPremises: approximately {random.randint(8, 45)},000 square feet\n"
            f"Lease Term: {random.choice([3, 5, 7, 10])} years\nExpiration Date: {expiry}\n"
            f"Base Rent: ${rent:,} per month\nSecurity Deposit: {deposit}\n"
            f"Permitted Use: retail store operations\n")


def _page2(hvac_clause):
    return ("ARTICLE 6 - MAINTENANCE AND REPAIRS\n\n6.1 Tenant's General Obligations\nTenant shall keep "
            "the interior of the Premises in good order, including storefront glass, interior walls, "
            "floor coverings, and trade fixtures, and shall provide janitorial services and pest control "
            "within the Premises.\n\n6.2 Landlord's General Obligations\nLandlord shall maintain the "
            "structural elements of the building, the parking areas, and the Common Areas.\n\n"
            f"{hvac_clause}\n\n6.4 Standards\nAll maintenance shall be performed to first-class retail "
            "standards by qualified contractors.\n")


def _page3(store):
    return ("ARTICLE 9 - ASSIGNMENT AND MISCELLANEOUS\n\n9.1 Assignment\nTenant shall not assign this "
            "Lease or sublet the Premises without Landlord's prior written consent, not to be "
            "unreasonably withheld.\n\n9.2 Notices\nAll notices shall be in writing and delivered to the "
            "addresses stated in Article 1.\n\n9.3 Entire Agreement\nThis Lease constitutes the entire "
            f"agreement between the parties regarding store {store}.\n\nLANDLORD: ____________________\n"
            "TENANT: SKYLINE STORES ____________________\n")


def main():
    random.seed(SEED)
    os.makedirs(OUT, exist_ok=True)

    classes = (['landlord'] * N_LANDLORD + ['tenant'] * N_TENANT +
               ['split'] * N_SPLIT + ['silent'] * N_SILENT)
    random.shuffle(classes)
    syn_landlord = syn_tenant = 0
    truth = {}

    for i, cls in enumerate(classes):
        store = f"S{101 + i}"
        name = random.choice(CENTERS)
        city = random.choice(CITIES)
        expiry = f"{random.choice(['January', 'March', 'April', 'June', 'September', 'November'])} " \
                 f"{random.randint(1, 28)}, {random.randint(2027, 2034)}"
        rent = random.randint(18, 95) * 1000
        deposit = random.choice(['None required (tenant creditworthiness)',
                                 f"${random.randint(40, 300) * 1000:,} (Letter of Credit)",
                                 f"${random.randint(40, 300) * 1000:,} (cash)"])
        tons = random.choice([20, 40, 60, 80])
        threshold = random.choice([5000, 7500, 10000])
        synonym = False
        if cls == 'landlord':
            if syn_landlord < SYN_LANDLORD:
                clause, synonym, syn_landlord = HVAC_LANDLORD_SYN.format(tons=tons), True, syn_landlord + 1
            else:
                clause = HVAC_LANDLORD.format(tons=tons)
        elif cls == 'tenant':
            if syn_tenant < SYN_TENANT:
                clause, synonym, syn_tenant = HVAC_TENANT_SYN.format(tons=tons), True, syn_tenant + 1
            else:
                clause = HVAC_TENANT.format(tons=tons)
        elif cls == 'split':
            clause = HVAC_SPLIT.format(threshold=threshold)
        else:
            clause = MAINT_SILENT

        pages = [_page1(store, name, city, expiry, rent, deposit), _page2(clause), _page3(store)]
        fname = f"SCALE13_{store}_{name.replace(' ', '')}.txt"
        open(os.path.join(OUT, fname), 'w', encoding='utf-8').write('\f'.join(pages))
        truth[store] = dict(filename=fname, name=name, city=city, hvac=cls, synonym=synonym,
                            expiry=expiry, deposit=deposit,
                            split_threshold=threshold if cls == 'split' else None)

    json.dump(truth, open(os.path.join(HERE, 'scale_ground_truth.json'), 'w', encoding='utf-8'), indent=1)
    by = {}
    for t in truth.values():
        by[t['hvac']] = by.get(t['hvac'], 0) + 1
    print(f"generated {len(truth)} leases -> {OUT}")
    print(f"classes: {by} | synonym-only: {sum(1 for t in truth.values() if t['synonym'])}")
    print(f"landlord store ids ({by.get('landlord')}): "
          f"{sorted(s for s, t in truth.items() if t['hvac'] == 'landlord')}")


if __name__ == '__main__':
    main()
