-- Warn (not fail): complaints whose CFPB product is not in var('cfpb_known_products').
-- They stay in the marts as product = 'Unmapped' (original text in product_original), so nothing is
-- dropped; the number of rows returned is how many complaints need the product list updated.
{{ config(severity='warn') }}

select complaint_id, date_received, product_original
from {{ ref('fct_complaints') }}
where product = 'Unmapped'
