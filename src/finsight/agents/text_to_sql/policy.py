"""SQL policy: parse with sqlglot, then default-deny validate. This - not the prompt - is the security boundary
(together with the read-only database role). Returns normalized SQL with an enforced LIMIT."""
from dataclasses import dataclass

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError

from .semantic import Catalog, load_catalog

MAX_SQL_CHARS = 4000
MAX_DEPTH = 5
ALLOWED_FUNCS = {
    "sum", "count", "avg", "min", "max", "round", "coalesce", "nullif", "cast", "extract", "if", "case",
    "timestamptrunc", "datetrunc", "currentdate", "currenttimestamp", "lower", "upper", "abs", "ceil", "floor",
    "greatest", "least", "percentilecont", "percentiledisc", "stddev", "stddevpop", "stddevsamp", "variance",
    "length", "dateadd", "datesub", "datediff", "tsordsto", "unnest", "arraylength", "cardinality", "div",
    "substring", "trim", "concat", "left", "right", "sign", "sqrt", "power", "ln", "log", "exp",
}
ALLOWED_ANON = {"date_trunc", "date_part", "age", "to_char", "array_length"}
FORBIDDEN_NODES = (exp.Insert, exp.Update, exp.Delete, exp.Drop, exp.Create, exp.Alter, exp.Command, exp.Copy,
                   exp.Set, exp.Into, exp.Lock, exp.Merge, exp.TruncateTable, exp.Transaction, exp.Commit,
                   exp.Rollback, exp.Use, exp.Grant, exp.Describe, exp.Show, exp.Pragma, exp.Kill)


class PolicyViolation(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code, self.message = code, message


@dataclass
class Validated:
    sql: str
    tables: list[str]
    sensitive_projections: list[int]   # output positions to pseudonymize
    quasi_identifiers_used: bool


def _depth(node: exp.Expression) -> int:
    best = 1
    for sel in node.find_all(exp.Select):
        d, p = 1, sel.parent
        while p is not None:
            d += isinstance(p, exp.Select)
            p = p.parent
        best = max(best, d)
    return best


def validate_sql(sql: str, row_limit: int = 200, catalog: Catalog | None = None) -> Validated:
    cat = catalog or load_catalog()
    if not sql or not sql.strip():
        raise PolicyViolation("empty", "empty query")
    if len(sql) > MAX_SQL_CHARS:
        raise PolicyViolation("too_long", f"query longer than {MAX_SQL_CHARS} characters")
    try:
        stmts = sqlglot.parse(sql, read="postgres")
    except ParseError as e:
        raise PolicyViolation("syntax", f"could not parse SQL: {str(e).splitlines()[0][:150]}") from None
    parsed = [s for s in stmts if s is not None]
    if len(parsed) != 1:
        raise PolicyViolation("multiple_statements", "exactly one statement is allowed")
    tree = parsed[0]
    if isinstance(tree, FORBIDDEN_NODES) or any(tree.find(n) for n in FORBIDDEN_NODES):
        raise PolicyViolation("not_read_only", "only read-only SELECT queries are allowed")
    if not isinstance(tree, (exp.Select, exp.Union, exp.Subquery)):
        raise PolicyViolation("not_select", "only SELECT queries are allowed")
    if tree.find(exp.Lock) or tree.args.get("locks"):
        raise PolicyViolation("locking", "locking clauses are not allowed")
    with_ = tree.args.get("with") or tree.args.get("with_")
    if with_ is not None and with_.args.get("recursive"):
        raise PolicyViolation("recursive_cte", "recursive CTEs are not allowed")
    if _depth(tree) > MAX_DEPTH:
        raise PolicyViolation("too_deep", "subqueries nested too deeply")

    cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    used_tables: list[str] = []
    for t in tree.find_all(exp.Table):
        name = t.name.lower()
        schema = (t.db or "").lower()
        if t.catalog:
            raise PolicyViolation("catalog", "cross-database references are not allowed")
        if not schema and name in cte_names:
            continue
        if isinstance(t.this, exp.Anonymous | exp.Func) or not isinstance(t.this, exp.Identifier):
            raise PolicyViolation("table_function", "table-valued functions are not allowed")
        if schema and schema != cat.schema:
            raise PolicyViolation("schema_not_allowed", f"schema '{schema}' is not allowed")
        if name not in cat.tables:
            raise PolicyViolation("table_not_allowed", f"table '{name}' is not in the allowed analytics schema")
        used_tables.append(name)
    if not used_tables:
        raise PolicyViolation("no_table", "query must read from the analytics tables")

    allowed_cols = set().union(*(cat.columns(t) for t in used_tables))
    aliases = {a.alias.lower() for a in tree.find_all(exp.Alias)} | cte_names
    aliases |= {s.alias.lower() for s in tree.find_all(exp.Subquery) if s.alias}
    aliases |= {t.alias.lower() for t in tree.find_all(exp.Table) if t.alias}
    aliases |= {u.name.lower() for u in tree.find_all(exp.TableAlias) if u.name}
    for u in tree.find_all(exp.TableAlias):  # column aliases e.g. UNNEST(x) AS t(r)
        aliases |= {c.name.lower() for c in u.columns}
    for col in tree.find_all(exp.Column):
        if isinstance(col.this, exp.Star):
            continue
        n = col.name.lower()
        tbl = (col.table or "").lower()
        if tbl and tbl not in aliases and tbl not in cat.tables and tbl not in cte_names:
            raise PolicyViolation("unknown_alias", f"unknown table alias '{tbl}'")
        if n not in allowed_cols and n not in aliases:
            raise PolicyViolation("column_not_allowed", f"column '{n}' is not available in the referenced tables")

    for f in tree.find_all(exp.Func):
        if isinstance(f, exp.Binary | exp.Unary | exp.Connector):
            continue  # operators (AND/OR/NOT/=,+,...) are not function calls
        key = f.key.lower()
        if isinstance(f, exp.Anonymous):
            if f.name.lower() not in ALLOWED_ANON:
                raise PolicyViolation("function_not_allowed", f"function '{f.name.lower()}' is not allowed")
        elif key not in ALLOWED_FUNCS:
            raise PolicyViolation("function_not_allowed", f"function '{key}' is not allowed")
    for fn in tree.find_all(exp.Var):
        if fn.name.lower().startswith(("pg_", "current_setting")):
            raise PolicyViolation("function_not_allowed", "system functions are not allowed")

    outer = tree if isinstance(tree, exp.Select) else None
    sensitive = cat.sensitive_columns()
    sens_pos: list[int] = []
    if outer is not None:
        for i, p in enumerate(outer.expressions):
            inner = p.this if isinstance(p, exp.Alias) else p
            for col in inner.find_all(exp.Column):
                if col.name.lower() in sensitive and col.find_ancestor(exp.AggFunc) is None:
                    sens_pos.append(i)
                    break
            if isinstance(inner, exp.Star) or (isinstance(inner, exp.Column) and isinstance(inner.this, exp.Star)):
                sens_pos.append(-1)  # star: resolved by output column name at execution time
    else:
        sens_pos.append(-1)  # UNION etc.: fall back to name-based masking

    if isinstance(tree, exp.Select):
        lim = tree.args.get("limit")
        requested: int | None = None
        if lim is not None and isinstance(lim.expression, exp.Literal) and lim.expression.is_int:
            requested = int(lim.expression.name)
        tree = tree.limit(min(requested, row_limit) if requested is not None else row_limit)
        final = tree.sql(dialect="postgres")
    else:
        final = f"SELECT * FROM ({tree.sql(dialect='postgres')}) AS q LIMIT {row_limit}"
    quasi = cat.quasi_identifiers()
    uses_quasi = any(c.name.lower() in quasi for c in tree.find_all(exp.Column))
    return Validated(final, sorted(set(used_tables)), sens_pos, uses_quasi)
