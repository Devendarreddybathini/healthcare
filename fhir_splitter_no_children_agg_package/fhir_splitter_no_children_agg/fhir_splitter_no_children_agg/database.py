from __future__ import annotations

import hashlib
import json
import os
import re
from collections import defaultdict
from typing import Any

from sqlalchemy import (
    Column,
    Index,
    Integer,
    JSON,
    MetaData,
    String,
    Table,
    Text,
    create_engine,
    delete,
    inspect,
    select,
    text,
    update,
)

from .splitter import _normalize_child_basename


def _safe_identifier(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_]", "_", value)
    if not cleaned or cleaned[0].isdigit():
        cleaned = f"t_{cleaned}"
    if cleaned == "_row_id":
        cleaned = "data_row_id"
    if cleaned != value or len(cleaned) > 48:
        suffix = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        cleaned = f"{cleaned[:48]}_{suffix}"
    return cleaned


def _collect_table_rows(
    grouped: dict[str, dict[str, Any]],
) -> tuple[dict[str, list[dict[str, Any]]], dict[str, str]]:
    tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
    child_prefixes: dict[str, str] = {}
    for resource_type, data in grouped.items():
        parent_table = _safe_identifier(f"fhir_{resource_type}_parent")
        tables[parent_table].extend(data.get("parent") or [])
        child_prefixes[parent_table] = f"fhir_{resource_type}_"
        for child_key, rows in (data.get("children") or {}).items():
            child_name = _normalize_child_basename(resource_type, child_key)
            child_table = _safe_identifier(f"fhir_{child_name}")
            tables[child_table].extend(rows or [])
    return dict(tables), child_prefixes


def _record_hash(row: dict[str, Any]) -> str:
    snapshot = row.get("__snapshot")
    if isinstance(snapshot, str):
        try:
            snapshot = json.loads(snapshot)
        except json.JSONDecodeError:
            pass
    canonical = json.dumps(snapshot if snapshot is not None else row, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _normalize_row(row: dict[str, Any]) -> dict[str, Any]:
    normalized = {}
    for key, value in row.items():
        if key == "_row_id":
            continue
        if isinstance(value, str) and (key == "__snapshot" or value.lstrip().startswith(("[", "{"))):
            try:
                parsed_value = json.loads(value)
            except json.JSONDecodeError:
                pass
            else:
                if isinstance(parsed_value, (dict, list)):
                    value = parsed_value
        normalized[_safe_identifier(str(key))] = value
    if normalized.get("id") is not None:
        normalized["id"] = str(normalized["id"])
    return normalized


def _column_type(values: list[Any]):
    if values and all(value is None or isinstance(value, str) for value in values):
        return Text()
    return JSON(none_as_null=True)


def _ensure_table(
    connection,
    table_name: str,
    rows: list[dict[str, Any]],
    is_parent: bool,
) -> Table:
    normalized_rows = [_normalize_row(row) for row in rows]
    column_names = sorted({key for row in normalized_rows for key in row} - {"id", "__record_hash"})
    column_types = {
        name: _column_type([row.get(name) for row in normalized_rows])
        for name in column_names
    }
    metadata = MetaData()
    columns = [
        Column("_row_id", Integer, primary_key=True, autoincrement=True),
        Column("id", String(255), nullable=True),
    ]
    if is_parent:
        columns.append(Column("__record_hash", String(64), nullable=True))
    columns.extend(Column(name, column_types[name], nullable=True) for name in column_names)
    Table(table_name, metadata, *columns).create(connection, checkfirst=True)

    preparer = connection.dialect.identifier_preparer
    quoted_table = preparer.quote(table_name)
    column_info = {column["name"]: column for column in inspect(connection).get_columns(table_name)}

    if "id" not in column_info:
        connection.execute(text(f"ALTER TABLE {quoted_table} ADD COLUMN `id` VARCHAR(255) NULL"))
    elif isinstance(column_info["id"]["type"], JSON):
        migration_column = "__id_text_migration"
        if migration_column in column_info:
            raise RuntimeError(f"Incomplete id column migration in table {table_name}")
        quoted_migration = preparer.quote(migration_column)
        quoted_id = preparer.quote("id")
        connection.execute(
            text(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_migration} VARCHAR(255) NULL")
        )
        if connection.dialect.name == "mysql":
            conversion = f"JSON_UNQUOTE({quoted_id})"
        elif connection.dialect.name == "sqlite":
            conversion = f"CAST(json_extract({quoted_id}, '$') AS TEXT)"
        else:
            raise RuntimeError("Migrating the existing JSON id column is supported on MySQL and SQLite")
        connection.execute(
            text(f"UPDATE {quoted_table} SET {quoted_migration} = {conversion} WHERE {quoted_id} IS NOT NULL")
        )
        connection.execute(text(f"ALTER TABLE {quoted_table} DROP COLUMN {quoted_id}"))
        if connection.dialect.name == "mysql":
            connection.execute(
                text(f"ALTER TABLE {quoted_table} RENAME COLUMN {quoted_migration} TO {quoted_id}")
            )
        else:
            connection.execute(
                text(f"ALTER TABLE {quoted_table} RENAME COLUMN {quoted_migration} TO {quoted_id}")
            )

    column_info = {column["name"] for column in inspect(connection).get_columns(table_name)}
    if is_parent and "__record_hash" not in column_info:
        connection.execute(
            text(f"ALTER TABLE {quoted_table} ADD COLUMN `__record_hash` VARCHAR(64) NULL")
        )
        column_info.add("__record_hash")

    column_info = {column["name"]: column for column in inspect(connection).get_columns(table_name)}
    for column_name in column_names:
        quoted_column = preparer.quote(column_name)
        sql_type = column_types[column_name].compile(dialect=connection.dialect)
        if column_name not in column_info:
            connection.execute(
                text(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_column} {sql_type} NULL")
            )
            continue

        if isinstance(column_info[column_name]["type"], JSON) and isinstance(column_types[column_name], Text):
            migration_column = _safe_identifier(f"__{column_name}_text_migration")
            if migration_column in column_info:
                raise RuntimeError(f"Incomplete text column migration in table {table_name}: {column_name}")
            quoted_migration = preparer.quote(migration_column)
            connection.execute(
                text(f"ALTER TABLE {quoted_table} ADD COLUMN {quoted_migration} {sql_type} NULL")
            )
            if connection.dialect.name == "mysql":
                conversion = f"JSON_UNQUOTE({quoted_column})"
            elif connection.dialect.name == "sqlite":
                conversion = f"CAST(json_extract({quoted_column}, '$') AS TEXT)"
            else:
                raise RuntimeError("Migrating existing JSON text columns is supported on MySQL and SQLite")
            connection.execute(
                text(
                    f"UPDATE {quoted_table} SET {quoted_migration} = {conversion} "
                    f"WHERE {quoted_column} IS NOT NULL"
                )
            )
            connection.execute(text(f"ALTER TABLE {quoted_table} DROP COLUMN {quoted_column}"))
            connection.execute(
                text(f"ALTER TABLE {quoted_table} RENAME COLUMN {quoted_migration} TO {quoted_column}")
            )

    return Table(table_name, MetaData(), autoload_with=connection)


def _ensure_parent_id_index(connection, table: Table) -> None:
    inspector = inspect(connection)
    indexes = inspector.get_indexes(table.name)
    if any(index["unique"] and index["column_names"] == ["id"] for index in indexes):
        return

    preparer = connection.dialect.identifier_preparer
    quoted_table = preparer.quote(table.name)
    quoted_id = preparer.quote("id")
    quoted_row_id = preparer.quote("_row_id")
    connection.execute(
        text(
            f"DELETE FROM {quoted_table} WHERE {quoted_id} IS NOT NULL "
            f"AND {quoted_row_id} NOT IN (SELECT keep_row FROM "
            f"(SELECT MAX({quoted_row_id}) AS keep_row FROM {quoted_table} "
            f"WHERE {quoted_id} IS NOT NULL GROUP BY {quoted_id}) AS latest_rows)"
        )
    )
    index_name = _safe_identifier(f"uq_{table.name}_id")
    Index(index_name, table.c.id, unique=True).create(connection, checkfirst=True)


def _upsert_parent(connection, table: Table, row: dict[str, Any]) -> None:
    updates = {
        key: value
        for key, value in row.items()
        if key not in ("id", "_row_id")
    }
    dialect = connection.dialect.name
    if dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert

        statement = insert(table).values(**row)
        connection.execute(statement.on_duplicate_key_update(**updates))
    elif dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert

        statement = insert(table).values(**row)
        connection.execute(
            statement.on_conflict_do_update(
                index_elements=[table.c.id],
                set_={key: statement.excluded[key] for key in updates},
            )
        )
    else:
        row_id = connection.execute(select(table.c._row_id).where(table.c.id == row["id"])).scalar_one_or_none()
        if row_id is None:
            connection.execute(table.insert().values(**row))
        else:
            connection.execute(update(table).where(table.c._row_id == row_id).values(**updates))


def save_grouped_resources(grouped: dict[str, dict[str, Any]]) -> dict[str, Any]:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL is not configured")

    engine = create_engine(database_url, pool_pre_ping=True)
    table_rows, child_prefixes = _collect_table_rows(grouped)
    parent_tables = set(child_prefixes)
    inserted_rows = 0
    updated_rows = 0
    unchanged_rows = 0

    try:
        with engine.begin() as connection:
            existing_names = set(inspect(connection).get_table_names())
            child_tables_by_parent: dict[str, set[str]] = {}
            all_table_rows = dict(table_rows)
            for parent_table, prefix in child_prefixes.items():
                matching_children = {
                    name for name in existing_names
                    if name.startswith(prefix) and name != parent_table
                }
                matching_children.update(
                    name for name in table_rows
                    if name.startswith(prefix) and name != parent_table
                )
                child_tables_by_parent[parent_table] = matching_children
                for child_table in matching_children:
                    all_table_rows.setdefault(child_table, [])

            tables = {
                table_name: _ensure_table(
                    connection,
                    table_name,
                    rows,
                    table_name in parent_tables,
                )
                for table_name, rows in all_table_rows.items()
            }
            for parent_table in parent_tables:
                _ensure_parent_id_index(connection, tables[parent_table])
                tables[parent_table] = Table(parent_table, MetaData(), autoload_with=connection)

            changed_ids_by_parent: dict[str, set[str]] = {}
            unchanged_ids_by_parent: dict[str, set[str]] = {}
            parent_rows_to_save: dict[str, list[dict[str, Any]]] = {}
            for parent_table in parent_tables:
                rows_by_id: dict[str, dict[str, Any]] = {}
                rows_without_id = []
                for source_row in table_rows.get(parent_table, []):
                    row = _normalize_row(source_row)
                    row["__record_hash"] = _record_hash(source_row)
                    if row.get("id") is None:
                        rows_without_id.append(row)
                    else:
                        rows_by_id[row["id"]] = row

                table = tables[parent_table]
                ids = list(rows_by_id)
                existing_hashes = {}
                if ids:
                    existing_hashes = dict(
                        connection.execute(
                            select(table.c.id, table.c.__record_hash).where(table.c.id.in_(ids))
                        ).all()
                    )

                changed_ids = set()
                rows_to_save = []
                for resource_id, row in rows_by_id.items():
                    if existing_hashes.get(resource_id) == row["__record_hash"]:
                        unchanged_rows += 1
                    else:
                        changed_ids.add(resource_id)
                        rows_to_save.append(row)
                        if resource_id in existing_hashes:
                            updated_rows += 1
                        else:
                            inserted_rows += 1
                for row in rows_without_id:
                    row["__record_hash"] = _record_hash(row)
                    rows_to_save.append(row)
                    inserted_rows += 1

                changed_ids_by_parent[parent_table] = changed_ids
                unchanged_ids_by_parent[parent_table] = set(rows_by_id) - changed_ids
                parent_rows_to_save[parent_table] = rows_to_save

            for parent_table, changed_ids in changed_ids_by_parent.items():
                if not changed_ids:
                    continue
                for child_table in child_tables_by_parent[parent_table]:
                    table = tables[child_table]
                    connection.execute(delete(table).where(table.c.id.in_(changed_ids)))

            for parent_table, rows in parent_rows_to_save.items():
                table = tables[parent_table]
                for row in rows:
                    if row.get("id") is None:
                        connection.execute(table.insert().values(**row))
                    else:
                        _upsert_parent(connection, table, row)

            for table_name, source_rows in table_rows.items():
                if table_name in parent_tables:
                    continue
                table = tables[table_name]
                parent_table = next(
                    (parent for parent, prefix in child_prefixes.items() if table_name.startswith(prefix)),
                    None,
                )
                changed_ids = changed_ids_by_parent.get(parent_table or "", set())
                for source_row in source_rows:
                    row = _normalize_row(source_row)
                    resource_id = row.get("id")
                    if resource_id is not None and resource_id not in changed_ids:
                        if resource_id in unchanged_ids_by_parent.get(parent_table or "", set()):
                            unchanged_rows += 1
                        continue
                    connection.execute(table.insert().values(**row))
                    inserted_rows += 1
    finally:
        engine.dispose()

    return {
        "tables": sorted(table_rows),
        "rows_inserted": inserted_rows,
        "rows_updated": updated_rows,
        "rows_unchanged": unchanged_rows,
    }