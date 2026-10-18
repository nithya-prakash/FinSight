{# Use the configured schema verbatim (stg / int / marts) instead of <target>_<schema>. #}
{% macro generate_schema_name(custom_schema_name, node) -%}
  {{ custom_schema_name | trim if custom_schema_name is not none else target.schema }}
{%- endmacro %}
