/*
================================================================================
  DCA Demo Seed — Pharma Speaker Program
================================================================================

  What this script creates:
    Tables  ─ speakers, products, topics, venues + related junction tables
    Views   ─ vw_active_products, vw_compliant_topics,
              vw_compliant_speakers, vw_compliant_venues
    Data    ─ ~15 speakers, 5 products, 12 topics, 10 venues, with
              realistic compliance gates so filter rules have real
              edges to find.

  How to use this with the DCA Schema Builder:

    1. Create a fresh database (e.g. CREATE DATABASE DcaDemo;) and run
       this script against it.
    2. In the platform's Connections admin, register that database as a
       new connection. Note the connection_id.
    3. Open the Schema Builder, edit your schema's lookup_data, choose
       Source = database, pick the connection, and reference one of the
       views below as your view name. The Detect button populates the
       column allowlist.

  Notes on column allowlists (the privacy boundary):
    * speakers.compensation_rate is included in the table for realism but
      is intentionally NOT exposed by vw_compliant_speakers. Defense in
      depth: even if a schema author accidentally adds it to
      select_columns, the view doesn't expose it.
    * venues.avg_room_rate has the same treatment.

  Re-runnable: this script drops everything first (objects only, not the
  database itself). Safe to run repeatedly during testing.

  Sensitive "scenarios" deliberately seeded for testing filter rules:
    - Inactive speakers (filtered out by vw_compliant_speakers)
    - Speakers certified only on some products (test product-scoped recs)
    - Speakers with expired certifications (filtered out)
    - Venues with expired compliance approval (filtered out)
    - One speaker per tier so recommend_options has clear ranking outputs
================================================================================
*/

SET NOCOUNT ON;
SET XACT_ABORT ON;

-- ============================================================================
-- 1. Drop existing objects (re-runnable)
-- ============================================================================

IF OBJECT_ID('dbo.vw_compliant_venues', 'V')   IS NOT NULL DROP VIEW dbo.vw_compliant_venues;
IF OBJECT_ID('dbo.vw_compliant_speakers', 'V') IS NOT NULL DROP VIEW dbo.vw_compliant_speakers;
IF OBJECT_ID('dbo.vw_compliant_topics', 'V')   IS NOT NULL DROP VIEW dbo.vw_compliant_topics;
IF OBJECT_ID('dbo.vw_active_products', 'V')    IS NOT NULL DROP VIEW dbo.vw_active_products;

IF OBJECT_ID('dbo.venue_contacts', 'U')                   IS NOT NULL DROP TABLE dbo.venue_contacts;
IF OBJECT_ID('dbo.venues', 'U')                           IS NOT NULL DROP TABLE dbo.venues;
IF OBJECT_ID('dbo.speaker_topic_expertise', 'U')          IS NOT NULL DROP TABLE dbo.speaker_topic_expertise;
IF OBJECT_ID('dbo.speaker_product_certifications', 'U')   IS NOT NULL DROP TABLE dbo.speaker_product_certifications;
IF OBJECT_ID('dbo.speakers', 'U')                         IS NOT NULL DROP TABLE dbo.speakers;
IF OBJECT_ID('dbo.topics', 'U')                           IS NOT NULL DROP TABLE dbo.topics;
IF OBJECT_ID('dbo.products', 'U')                         IS NOT NULL DROP TABLE dbo.products;

-- ============================================================================
-- 2. Tables
-- ============================================================================

CREATE TABLE dbo.products (
    product_id          INT             NOT NULL PRIMARY KEY,
    product_name        NVARCHAR(100)   NOT NULL,
    therapeutic_area    NVARCHAR(80)    NOT NULL,
    launched_date       DATE            NOT NULL,
    active              BIT             NOT NULL DEFAULT 1,
    description         NVARCHAR(400)   NULL
);

CREATE TABLE dbo.topics (
    topic_id            INT             NOT NULL PRIMARY KEY,
    topic_name          NVARCHAR(120)   NOT NULL,
    product_id          INT             NOT NULL REFERENCES dbo.products(product_id),
    compliance_approved BIT             NOT NULL DEFAULT 1,
    description         NVARCHAR(400)   NULL
);

CREATE TABLE dbo.speakers (
    speaker_id          INT             NOT NULL PRIMARY KEY,
    speaker_name        NVARCHAR(120)   NOT NULL,
    tier                NVARCHAR(20)    NOT NULL,           -- 'Tier 1' / 'Tier 2' / 'Tier 3'
    tier_score          INT             NOT NULL,           -- 1=top, 3=lowest (for sorting)
    primary_specialty   NVARCHAR(120)   NOT NULL,
    city                NVARCHAR(80)    NOT NULL,
    state               NVARCHAR(2)     NOT NULL,
    active              BIT             NOT NULL DEFAULT 1,
    joined_date         DATE            NOT NULL,
    total_events_12mo   INT             NOT NULL DEFAULT 0,
    avg_rating          DECIMAL(3,2)    NULL,               -- 1.00–5.00
    compensation_rate   DECIMAL(10,2)   NULL                -- *** SENSITIVE — never in views ***
);

CREATE TABLE dbo.speaker_product_certifications (
    speaker_id          INT             NOT NULL REFERENCES dbo.speakers(speaker_id),
    product_id          INT             NOT NULL REFERENCES dbo.products(product_id),
    certified_on        DATE            NOT NULL,
    expires_on          DATE            NULL,
    PRIMARY KEY (speaker_id, product_id)
);

CREATE TABLE dbo.speaker_topic_expertise (
    speaker_id          INT             NOT NULL REFERENCES dbo.speakers(speaker_id),
    topic_id            INT             NOT NULL REFERENCES dbo.topics(topic_id),
    PRIMARY KEY (speaker_id, topic_id)
);

CREATE TABLE dbo.venues (
    venue_id                INT             NOT NULL PRIMARY KEY,
    venue_name              NVARCHAR(150)   NOT NULL,
    venue_type              NVARCHAR(40)    NOT NULL,       -- 'hotel'|'restaurant'|'conference center'|'university'
    address_line            NVARCHAR(200)   NOT NULL,
    city                    NVARCHAR(80)    NOT NULL,
    state                   NVARCHAR(2)     NOT NULL,
    zip                     NVARCHAR(10)    NOT NULL,
    capacity                INT             NOT NULL,
    has_av_equipment        BIT             NOT NULL DEFAULT 0,
    has_parking             BIT             NOT NULL DEFAULT 0,
    has_private_room        BIT             NOT NULL DEFAULT 0,
    compliance_approved     BIT             NOT NULL DEFAULT 1,
    approval_expires_on     DATE            NULL,           -- past = expired
    avg_room_rate_usd       DECIMAL(8,2)    NULL,           -- *** SENSITIVE — never in views ***
    notes                   NVARCHAR(400)   NULL
);

CREATE TABLE dbo.venue_contacts (
    venue_id            INT             NOT NULL REFERENCES dbo.venues(venue_id),
    contact_name        NVARCHAR(120)   NOT NULL,
    contact_email       NVARCHAR(200)   NULL,
    contact_phone       NVARCHAR(40)    NULL,
    PRIMARY KEY (venue_id)
);

-- ============================================================================
-- 3. Seed data
-- ============================================================================

-- ---- Products (5) ----------------------------------------------------------
INSERT INTO dbo.products (product_id, product_name, therapeutic_area, launched_date, active, description) VALUES
(101, 'Cardiomax XR',     'Cardiology',    '2021-03-15', 1, 'Once-daily extended-release ACE inhibitor for hypertension management.'),
(102, 'Oncoshield',       'Oncology',      '2022-09-01', 1, 'Targeted therapy for HER2+ metastatic breast cancer.'),
(103, 'GlycoControl 500', 'Endocrinology', '2019-11-20', 1, 'Once-weekly GLP-1 receptor agonist for type 2 diabetes.'),
(104, 'NeuroAxis',        'Neurology',     '2023-06-10', 1, 'CGRP-receptor antagonist for chronic migraine prevention.'),
(105, 'PulmoEase',        'Pulmonology',   '2018-04-04', 0, 'Dry-powder inhaler for moderate asthma. Discontinued 2024.');

-- ---- Topics (12, tied to products) -----------------------------------------
INSERT INTO dbo.topics (topic_id, topic_name, product_id, compliance_approved, description) VALUES
(201, 'Managing Resistant Hypertension',         101, 1, 'Real-world evidence updates and case studies.'),
(202, 'New ACE Inhibitor Guidelines (2025)',     101, 1, 'Discussion of revised AHA/ACC guidelines.'),
(203, 'Cardiomax XR — Initial Patient Selection',101, 1, 'Identifying ideal candidates for first-line therapy.'),
(204, 'HER2+ Metastatic Breast Cancer Update',   102, 1, 'Recent trial data and safety profile.'),
(205, 'Sequencing Targeted Therapies',           102, 1, 'Practical considerations for combination protocols.'),
(206, 'GLP-1 in T2DM: Beyond A1c',               103, 1, 'Cardiovascular outcomes and weight management.'),
(207, 'Type 2 Diabetes Management in Elderly',   103, 1, 'Dosing, comorbidities, and monitoring.'),
(208, 'CGRP Mechanism Refresher',                104, 1, 'Migraine pathophysiology and antagonist action.'),
(209, 'Chronic Migraine — Treatment Pathways',   104, 1, 'When to escalate from acute to preventive therapy.'),
(210, 'Asthma Inhaler Technique',                105, 0, 'NOT APPROVED — content predates 2025 guidelines.'),
(211, 'GlycoControl 500 — Off-Label Use',        103, 0, 'NOT APPROVED — pending compliance review.'),
(212, 'Cardiomax XR Safety in Renal Impairment', 101, 1, 'Dosing adjustments for CKD patients.');

-- ---- Speakers (15) ---------------------------------------------------------
INSERT INTO dbo.speakers (speaker_id, speaker_name, tier, tier_score, primary_specialty, city, state, active, joined_date, total_events_12mo, avg_rating, compensation_rate) VALUES
(301, 'Dr. Patricia Chen',     'Tier 1', 1, 'Cardiology',         'Boston',        'MA', 1, '2018-05-12',  18, 4.85, 4500.00),
(302, 'Dr. Marcus Reilly',     'Tier 1', 1, 'Cardiology',         'New York',      'NY', 1, '2019-02-08',  15, 4.70, 4500.00),
(303, 'Dr. Aisha Patel',       'Tier 2', 2, 'Oncology',           'Houston',       'TX', 1, '2020-09-30',  12, 4.55, 3200.00),
(304, 'Dr. James Whitfield',   'Tier 1', 1, 'Oncology',           'Philadelphia',  'PA', 1, '2017-11-04',  22, 4.92, 4500.00),
(305, 'Dr. Rachel Goldberg',   'Tier 2', 2, 'Endocrinology',      'Chicago',       'IL', 1, '2021-04-17',   9, 4.40, 3200.00),
(306, 'Dr. Hiroshi Yamamoto',  'Tier 3', 3, 'Endocrinology',      'San Francisco', 'CA', 1, '2023-01-22',   4, 4.20, 2200.00),
(307, 'Dr. Linda Okonkwo',     'Tier 1', 1, 'Neurology',          'Atlanta',       'GA', 1, '2019-07-09',  16, 4.78, 4500.00),
(308, 'Dr. Bryan Castellanos', 'Tier 2', 2, 'Neurology',          'Miami',         'FL', 1, '2020-12-01',  11, 4.50, 3200.00),
(309, 'Dr. Emma Sutherland',   'Tier 2', 2, 'Cardiology',         'Seattle',       'WA', 1, '2021-08-14',   8, 4.35, 3200.00),
(310, 'Dr. Anthony Russo',     'Tier 3', 3, 'Oncology',           'Denver',        'CO', 1, '2024-03-05',   3, 4.10, 2200.00),
(311, 'Dr. Felicia Bauer',     'Tier 2', 2, 'Endocrinology',      'Minneapolis',   'MN', 1, '2022-06-21',   7, 4.45, 3200.00),
(312, 'Dr. Hassan Ahmadi',     'Tier 1', 1, 'Cardiology',         'San Diego',     'CA', 1, '2018-10-14',  14, 4.65, 4500.00),
-- Inactive — should be excluded from vw_compliant_speakers
(313, 'Dr. Walter Pemberton',  'Tier 2', 2, 'Pulmonology',        'Cleveland',     'OH', 0, '2017-04-03',   0, NULL, 3200.00),
-- New (low events) — exists but doesn't rank well for recommend_options
(314, 'Dr. Nadia Roland',      'Tier 3', 3, 'Neurology',          'Austin',        'TX', 1, '2025-09-12',   1, NULL, 2200.00),
-- Has expired certification (test that the join filter works)
(315, 'Dr. Kenji Watanabe',    'Tier 2', 2, 'Cardiology',         'Portland',      'OR', 1, '2019-03-18',   6, 4.30, 3200.00);

-- ---- Speaker × Product certifications --------------------------------------
INSERT INTO dbo.speaker_product_certifications (speaker_id, product_id, certified_on, expires_on) VALUES
-- Dr. Patricia Chen (Cardiology — Tier 1) — Cardiomax current
(301, 101, '2024-01-15', '2027-01-15'),
-- Dr. Marcus Reilly (Cardiology) — Cardiomax current
(302, 101, '2023-06-10', '2026-06-10'),
-- Dr. Aisha Patel (Oncology — Tier 2) — Oncoshield current
(303, 102, '2024-02-12', '2027-02-12'),
-- Dr. James Whitfield (Oncology — Tier 1) — Oncoshield current
(304, 102, '2023-09-22', '2026-09-22'),
-- Dr. Rachel Goldberg (Endocrinology — Tier 2) — GlycoControl current
(305, 103, '2024-05-01', '2027-05-01'),
-- Dr. Hiroshi Yamamoto (Endocrinology — Tier 3) — GlycoControl current
(306, 103, '2024-08-19', '2027-08-19'),
-- Dr. Linda Okonkwo (Neurology — Tier 1) — NeuroAxis current
(307, 104, '2024-04-03', '2027-04-03'),
-- Dr. Bryan Castellanos (Neurology — Tier 2) — NeuroAxis current
(308, 104, '2024-07-15', '2027-07-15'),
-- Dr. Emma Sutherland — Cardiomax current
(309, 101, '2024-10-30', '2027-10-30'),
-- Dr. Anthony Russo (Oncology — Tier 3) — Oncoshield current
(310, 102, '2025-01-08', '2028-01-08'),
-- Dr. Felicia Bauer (Endocrinology) — GlycoControl current
(311, 103, '2024-11-22', '2027-11-22'),
-- Dr. Hassan Ahmadi (Cardiology — Tier 1) — Cardiomax current
(312, 101, '2024-03-25', '2027-03-25'),
-- Dr. Nadia Roland (Neurology — Tier 3, new) — NeuroAxis current
(314, 104, '2025-10-01', '2028-10-01'),
-- Dr. Kenji Watanabe — Cardiomax EXPIRED (testing the filter)
(315, 101, '2021-04-12', '2024-04-12'),
-- Dual certifications — Linda also certified on Cardiomax (cross-therapy)
(307, 101, '2024-04-03', '2027-04-03');

-- ---- Speaker × Topic expertise ---------------------------------------------
INSERT INTO dbo.speaker_topic_expertise (speaker_id, topic_id) VALUES
-- Cardiology speakers cover Cardiomax topics
(301, 201), (301, 202), (301, 203), (301, 212),
(302, 201), (302, 202), (302, 203),
(309, 201), (309, 203),
(312, 201), (312, 202), (312, 212),
-- Oncology
(303, 204), (303, 205),
(304, 204), (304, 205),
(310, 204),
-- Endocrinology
(305, 206), (305, 207),
(306, 206), (306, 207),
(311, 206), (311, 207),
-- Neurology
(307, 208), (307, 209), (307, 201),  -- Linda also covers a Cardiomax topic
(308, 208), (308, 209),
(314, 208);

-- ---- Venues (10) -----------------------------------------------------------
INSERT INTO dbo.venues (venue_id, venue_name, venue_type, address_line, city, state, zip, capacity, has_av_equipment, has_parking, has_private_room, compliance_approved, approval_expires_on, avg_room_rate_usd, notes) VALUES
(401, 'The Capital Grille',       'restaurant',         '900 Boylston St',          'Boston',        'MA', '02115',  60,  1, 1, 1, 1, '2027-12-31', 95.00,  'Frequently used for cardiology programs. Private dining room sleeps 60 reception or 40 seated.'),
(402, 'Ruth''s Chris Steak House','restaurant',         '148 W 51st St',            'New York',      'NY', '10019', 120,  1, 0, 1, 1, '2027-12-31', 120.00, 'Manhattan staple. Two private rooms.'),
(403, 'The St. Regis Houston',    'hotel',              '1919 Briar Oaks Ln',       'Houston',       'TX', '77027', 200,  1, 1, 1, 1, '2027-06-30', 80.00,  'Full-service hotel with multiple meeting rooms.'),
(404, 'Eddie V''s Prime Seafood',   'restaurant',         '100 E Las Olas Blvd',      'Fort Lauderdale','FL', '33301',  85,  1, 1, 1, 1, '2027-12-31', 90.00,  NULL),
(405, 'Hyatt Regency Chicago',      'hotel',              '151 E Wacker Dr',          'Chicago',       'IL', '60601', 350,  1, 1, 1, 1, '2027-12-31', 70.00,  'Large-capacity option for combined-region programs.'),
(406, 'The Westin San Diego Bayview','hotel',             '400 W Broadway',           'San Diego',     'CA', '92101', 180,  1, 1, 1, 1, '2027-08-15', 75.00,  NULL),
(407, 'Fogo de Chão Brazilian Steakhouse','restaurant',  '645 Hennepin Ave',         'Minneapolis',   'MN', '55403',  90,  1, 1, 1, 1, '2027-12-31', 85.00,  NULL),
(408, 'University Club of Denver','conference center',  '1673 Sherman St',          'Denver',        'CO', '80203', 120,  1, 0, 1, 1, '2027-12-31', 65.00,  'Boutique club with full A/V; member preferred.'),
-- Compliance EXPIRED — should be excluded by vw_compliant_venues
(409, 'Old Town Tavern',          'restaurant',         '212 Main St',              'Cleveland',     'OH', '44113',  40,  0, 1, 0, 1, '2024-06-30', 55.00,  'Approval expired June 2024.'),
-- Explicitly disapproved — never compliant regardless of dates
(410, 'Dockside Pavilion',        'conference center',  '99 Harbor Way',            'Miami',         'FL', '33132', 250,  1, 1, 1, 0, NULL,         NULL,   'Failed 2024 compliance review (open-air space, unsuitable for closed sessions).');

-- ---- Venue contacts --------------------------------------------------------
INSERT INTO dbo.venue_contacts (venue_id, contact_name, contact_email, contact_phone) VALUES
(401, 'Maria Hernandez',  'm.hernandez@example.com',     '617-555-0143'),
(402, 'James Park',       'jpark@example.com',           '212-555-0291'),
(403, 'Diana Reeves',     'd.reeves@example.com',        '713-555-0118'),
(404, 'Carlos Vega',      'c.vega@example.com',          '954-555-0167'),
(405, 'Helen Wu',         'h.wu@example.com',            '312-555-0204'),
(406, 'Olivia Brennan',   'o.brennan@example.com',       '619-555-0188'),
(407, 'Thomas Larson',    'tlarson@example.com',         '612-555-0102'),
(408, 'Naomi Reyes',      'n.reyes@example.com',         '303-555-0149'),
(409, 'Frank McGee',      'fmcgee@example.com',          '216-555-0177'),
(410, 'Priya Shankar',    'p.shankar@example.com',       '305-555-0211');

-- ============================================================================
-- 4. Views — these are what the DCA schema's lookup_data points to
-- ============================================================================
GO

CREATE VIEW dbo.vw_active_products
AS
SELECT
    product_id,
    product_name        AS label,
    therapeutic_area,
    launched_date,
    description
FROM dbo.products
WHERE active = 1;
GO

CREATE VIEW dbo.vw_compliant_topics
AS
SELECT
    t.topic_id,
    t.topic_name        AS label,
    t.product_id,
    p.product_name,
    p.therapeutic_area,
    t.description
FROM dbo.topics t
INNER JOIN dbo.products p ON p.product_id = t.product_id
WHERE t.compliance_approved = 1
  AND p.active = 1;
GO

/*
  vw_compliant_speakers
  ─────────────────────
  Active speakers, with their CURRENT (non-expired) product certifications
  flattened into a single comma-separated string for easy filter_by
  __contains queries from the DCA schema:

      "filter_by": {
        "products_certified__contains": "{{collected.basics.product}}"
      }

  Sensitive columns (compensation_rate) are deliberately omitted from
  the SELECT list — even if a schema author lists them in
  select_columns, they cannot reach the agent.
*/
CREATE VIEW dbo.vw_compliant_speakers
AS
SELECT
    s.speaker_id,
    s.speaker_name              AS label,
    s.tier,
    s.tier_score,
    s.primary_specialty,
    s.city,
    s.state,
    s.total_events_12mo,
    s.avg_rating,
    -- Comma-joined active product certifications (current as of today)
    STUFF((
        SELECT ', ' + p.product_name
        FROM dbo.speaker_product_certifications spc
        INNER JOIN dbo.products p ON p.product_id = spc.product_id
        WHERE spc.speaker_id = s.speaker_id
          AND (spc.expires_on IS NULL OR spc.expires_on > CAST(GETDATE() AS DATE))
        FOR XML PATH(''), TYPE
    ).value('.', 'NVARCHAR(MAX)'), 1, 2, '')   AS products_certified,
    -- Comma-joined topic expertise
    STUFF((
        SELECT ', ' + t.topic_name
        FROM dbo.speaker_topic_expertise ste
        INNER JOIN dbo.topics t ON t.topic_id = ste.topic_id
        WHERE ste.speaker_id = s.speaker_id
          AND t.compliance_approved = 1
        FOR XML PATH(''), TYPE
    ).value('.', 'NVARCHAR(MAX)'), 1, 2, '')   AS topics_certified,
    s.joined_date
FROM dbo.speakers s
WHERE s.active = 1
  -- Speaker must have at least one CURRENT certification — drops people
  -- whose only cert is expired.
  AND EXISTS (
        SELECT 1 FROM dbo.speaker_product_certifications spc
        WHERE spc.speaker_id = s.speaker_id
          AND (spc.expires_on IS NULL OR spc.expires_on > CAST(GETDATE() AS DATE))
  );
GO

CREATE VIEW dbo.vw_compliant_venues
AS
SELECT
    v.venue_id,
    v.venue_name        AS label,
    v.venue_type,
    v.address_line,
    v.city,
    v.state,
    v.zip,
    v.capacity,
    v.has_av_equipment,
    v.has_parking,
    v.has_private_room,
    v.approval_expires_on,
    v.notes,
    vc.contact_name,
    vc.contact_email,
    vc.contact_phone
FROM dbo.venues v
LEFT JOIN dbo.venue_contacts vc ON vc.venue_id = v.venue_id
WHERE v.compliance_approved = 1
  AND (v.approval_expires_on IS NULL OR v.approval_expires_on > CAST(GETDATE() AS DATE));
GO

-- ============================================================================
-- 5. Verification — print row counts so you can confirm the seed worked
-- ============================================================================

PRINT '';
PRINT '=========================================================';
PRINT '  Seed complete';
PRINT '=========================================================';

SELECT 'products'                            AS object_name, COUNT(*) AS row_count FROM dbo.products
UNION ALL SELECT 'topics',                              COUNT(*) FROM dbo.topics
UNION ALL SELECT 'speakers',                            COUNT(*) FROM dbo.speakers
UNION ALL SELECT 'speaker_product_certifications',      COUNT(*) FROM dbo.speaker_product_certifications
UNION ALL SELECT 'speaker_topic_expertise',             COUNT(*) FROM dbo.speaker_topic_expertise
UNION ALL SELECT 'venues',                              COUNT(*) FROM dbo.venues
UNION ALL SELECT 'venue_contacts',                      COUNT(*) FROM dbo.venue_contacts
UNION ALL SELECT 'vw_active_products',                  COUNT(*) FROM dbo.vw_active_products
UNION ALL SELECT 'vw_compliant_topics',                 COUNT(*) FROM dbo.vw_compliant_topics
UNION ALL SELECT 'vw_compliant_speakers',               COUNT(*) FROM dbo.vw_compliant_speakers
UNION ALL SELECT 'vw_compliant_venues',                 COUNT(*) FROM dbo.vw_compliant_venues;

PRINT '';
PRINT 'Try these to validate filter_by behavior the DCA schema will use:';
PRINT '';
PRINT '  -- Speakers certified for Cardiomax XR (test products_certified__contains)';
PRINT '  SELECT speaker_id, label, tier, products_certified';
PRINT '  FROM   dbo.vw_compliant_speakers';
PRINT '  WHERE  products_certified LIKE ''%Cardiomax%'';';
PRINT '';
PRINT '  -- Top 3 speakers by tier_score then events (recommend_options ranking)';
PRINT '  SELECT TOP 3 speaker_id, label, tier, total_events_12mo, avg_rating';
PRINT '  FROM   dbo.vw_compliant_speakers';
PRINT '  WHERE  products_certified LIKE ''%Cardiomax%''';
PRINT '  ORDER  BY tier_score ASC, total_events_12mo DESC;';
PRINT '';
PRINT '  -- Venues big enough for a 100-person program in major NE cities';
PRINT '  SELECT label, city, state, capacity, venue_type';
PRINT '  FROM   dbo.vw_compliant_venues';
PRINT '  WHERE  capacity >= 100 AND state IN (''MA'', ''NY'', ''PA'');';
PRINT '';

-- ============================================================================
-- 6. Suggested DCA schema lookup_data definitions
--    (paste these into the schema builder once you've registered the
--     connection — connection_id will differ in your environment)
-- ============================================================================
/*
  "lookup_data": {

    "products": {
      "source": "database",
      "connection_id": <YOUR_CONN_ID>,
      "view": "dbo.vw_active_products",
      "select_columns": ["product_id", "label", "therapeutic_area", "description"],
      "limit": 50
    },

    "topics": {
      "source": "database",
      "connection_id": <YOUR_CONN_ID>,
      "view": "dbo.vw_compliant_topics",
      "select_columns": ["topic_id", "label", "product_id", "product_name", "description"],
      "filter_by": {
        "product_id": "{{collected.basics.product_id}}"
      },
      "limit": 50
    },

    "speakers": {
      "source": "database",
      "connection_id": <YOUR_CONN_ID>,
      "view": "dbo.vw_compliant_speakers",
      "select_columns": [
        "speaker_id", "label", "tier", "primary_specialty",
        "city", "state", "products_certified", "topics_certified",
        "total_events_12mo", "avg_rating"
      ],
      "filter_by": {
        "products_certified__contains": "{{collected.basics.product_name}}"
      },
      "order_by": [
        {"column": "tier_score", "direction": "asc"},
        {"column": "total_events_12mo", "direction": "desc"},
        {"column": "avg_rating", "direction": "desc"}
      ],
      "limit": 100
    },

    "venues": {
      "source": "database",
      "connection_id": <YOUR_CONN_ID>,
      "view": "dbo.vw_compliant_venues",
      "select_columns": [
        "venue_id", "label", "venue_type", "address_line",
        "city", "state", "zip", "capacity",
        "has_av_equipment", "has_parking", "has_private_room",
        "contact_name", "contact_email", "contact_phone"
      ],
      "limit": 100
    }
  }
*/
