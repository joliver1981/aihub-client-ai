/*
================================================================================
  DCA Demo — venue address corrections
================================================================================

  Updates the seeded venues (401-408) to use REAL addresses so Yelp /
  Google Places lookups return the correct business and reviews. Run
  this AFTER demo_pharma_seed.sql against your PHARMA database.

  Idempotent — safe to re-run. Only touches the rows whose addresses /
  names were placeholders.

  Sources verified against Yelp / Marriott / Hyatt / chain location
  pages (May 2026).
================================================================================
*/

-- 404 Eddie V's Fort Lauderdale: 521 -> 100 E Las Olas Blvd
UPDATE dbo.venues
SET address_line = '100 E Las Olas Blvd'
WHERE venue_id = 404;

-- 406 Westin San Diego: rename to the property's actual name
--     (the 400 W Broadway address is correct — it's "The Westin San
--      Diego Bayview", not just "The Westin San Diego")
UPDATE dbo.venues
SET venue_name = 'The Westin San Diego Bayview'
WHERE venue_id = 406;

-- 407 Fogo de Chão Minneapolis: 661 LaSalle Plaza -> 645 Hennepin Ave
--     (the Minneapolis location is in the Hennepin Theatre District)
UPDATE dbo.venues
SET venue_name   = 'Fogo de Chão Brazilian Steakhouse',
    address_line = '645 Hennepin Ave',
    zip          = '55403'
WHERE venue_id = 407;

-- Verification:
SELECT venue_id, venue_name, address_line, city, state, zip
FROM dbo.venues
WHERE venue_id BETWEEN 401 AND 408
ORDER BY venue_id;
