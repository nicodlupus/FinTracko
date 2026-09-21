-- The simplest possible model: one row per user-declared table. Exists
-- mainly to prove the DuckDB -> SQLite attachment actually works before
-- building anything that depends on it.
select
    id as table_id,
    name as table_name,
    sql_name,
    kind,
    position,
    created_at,
    deleted_at
from {{ source('current', '_ft_table') }}
where deleted_at is null
