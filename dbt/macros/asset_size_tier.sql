{# Peer group by total assets ($ thousands in, label out). Same bands as Databricks gold. #}
{% macro asset_size_tier(total_assets_k) -%}
    case
        when {{ total_assets_k }} is null then null
        when {{ total_assets_k }} < 1e5 then '1: <$100M'
        when {{ total_assets_k }} < 1e6 then '2: $100M-$1B'
        when {{ total_assets_k }} < 1e7 then '3: $1B-$10B'
        when {{ total_assets_k }} < 1e8 then '4: $10B-$100B'
        else '5: >$100B'
    end
{%- endmacro %}
