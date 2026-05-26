/*
================================================================================
  DCA Demo — view patch for vw_compliant_speakers
================================================================================

  Adds a product_ids_certified column (comma-bounded CSV like ",101,102,")
  so the demo schema's filter rule can do a safe substring match:

      "filter_by": {
        "product_ids_certified__contains": ",{{collected.program.product_id}},"
      }

  Run this AFTER demo_pharma_seed.sql (which creates the original view).
  Idempotent — uses CREATE OR ALTER.
================================================================================
*/

CREATE OR ALTER VIEW dbo.vw_compliant_speakers
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
    -- Comma-joined active product NAMES (for display + agent context)
    STUFF((
        SELECT ', ' + p.product_name
        FROM dbo.speaker_product_certifications spc
        INNER JOIN dbo.products p ON p.product_id = spc.product_id
        WHERE spc.speaker_id = s.speaker_id
          AND (spc.expires_on IS NULL OR spc.expires_on > CAST(GETDATE() AS DATE))
        FOR XML PATH(''), TYPE
    ).value('.', 'NVARCHAR(MAX)'), 1, 2, '')   AS products_certified,
    -- Comma-bounded product ID CSV (for filter rules to safely substring-match)
    -- Format: ",101,102,103," — leading + trailing comma so LIKE '%,101,%' works
    -- without false-matching '1010' etc.
    ',' + ISNULL(STUFF((
        SELECT ',' + CAST(spc.product_id AS NVARCHAR(20))
        FROM dbo.speaker_product_certifications spc
        WHERE spc.speaker_id = s.speaker_id
          AND (spc.expires_on IS NULL OR spc.expires_on > CAST(GETDATE() AS DATE))
        FOR XML PATH(''), TYPE
    ).value('.', 'NVARCHAR(MAX)'), 1, 1, ''), '') + ','   AS product_ids_certified,
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
  AND EXISTS (
        SELECT 1 FROM dbo.speaker_product_certifications spc
        WHERE spc.speaker_id = s.speaker_id
          AND (spc.expires_on IS NULL OR spc.expires_on > CAST(GETDATE() AS DATE))
  );
GO

-- Verification:
SELECT speaker_id, label, tier, products_certified, product_ids_certified
FROM dbo.vw_compliant_speakers
ORDER BY tier_score, total_events_12mo DESC;

-- Test: speakers certified for Cardiomax XR (product_id=101)
PRINT '';
PRINT 'Speakers certified for Cardiomax XR (product_id=101):';
SELECT speaker_id, label, tier, total_events_12mo, avg_rating
FROM dbo.vw_compliant_speakers
WHERE product_ids_certified LIKE '%,101,%'
ORDER BY tier_score, total_events_12mo DESC;
