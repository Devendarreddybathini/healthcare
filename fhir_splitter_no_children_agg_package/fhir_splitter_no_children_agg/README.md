# FHIR Splitter

This FastAPI application accepts FHIR JSON Bundles, extracts and groups resources by `resourceType`, and separates parent rows from child rows. It can save the results to MySQL or download them as a ZIP of JSON files.

## Requirements

- Python 3.9 or newer (Python 3.11 or 3.12 is recommended)
- MySQL 8.0 or newer for database saving
- A current Chrome or Edge browser for selecting a whole folder; selecting individual files works in other modern browsers

The computer running FastAPI must be able to connect to the configured MySQL server. If MySQL is on another computer, use its network hostname or IP address instead of `127.0.0.1` in `.env`, and configure MySQL/network access accordingly.

## Install

Open PowerShell, Terminal, or a command prompt and change to the directory containing `pyproject.toml`:

```text
Healthcare-POC-2/fhir_splitter_no_children_agg_package/fhir_splitter_no_children_agg
```

Create a virtual environment and install the app and dependencies.

### Windows PowerShell

```powershell
py -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e .
```

### macOS or Linux

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e .
```

## Configure MySQL

Create a database and an app user using MySQL Workbench or the MySQL command-line client. Run these statements as a MySQL administrator and choose a real password:

```sql
CREATE DATABASE IF NOT EXISTS fhir_poc
	CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER IF NOT EXISTS 'fhir_app'@'localhost' IDENTIFIED BY 'REPLACE_WITH_PASSWORD';
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX
	ON fhir_poc.* TO 'fhir_app'@'localhost';
SHOW GRANTS FOR 'fhir_app'@'localhost';
```

If the user already exists, run the `GRANT` and `SHOW GRANTS` statements. The app needs these permissions to create and evolve tables, detect unchanged records, update changed records, and replace their child rows.

The repository includes a tracked `.env` template with placeholder values. Keep those placeholders in the committed file. For your machine's actual credentials, copy `.env` to `.env.local` and edit `.env.local`; it is excluded by `.gitignore` and is loaded before `.env`. Shell environment variables take precedence over both files.

### Windows PowerShell

```powershell
Copy-Item .env .env.local
notepad .env.local
```

### macOS or Linux

```sh
cp .env .env.local
${EDITOR:-nano} .env.local
```

Set the URL in `.env` to this format, replacing the username, password, host, port, and database name as needed:

```text
DATABASE_URL=mysql+pymysql://fhir_app:YOUR_PASSWORD@127.0.0.1:3306/fhir_poc?charset=utf8mb4
```

URL-encode special characters in the password (for example `@` as `%40`). The database name in the URL must be a database that already exists.

## Start the App

Run the command from the project directory (the directory containing `.env` and `pyproject.toml`).

### Windows PowerShell

```powershell
.\.venv\Scripts\python.exe -m uvicorn fhir_splitter_no_children_agg.server:app --reload
```

### macOS or Linux

```sh
.venv/bin/python -m uvicorn fhir_splitter_no_children_agg.server:app --reload
```

Open <http://127.0.0.1:8000>. Keep the terminal running while you use the app. Stop the server with `Ctrl+C`.

## Process Bundles

1. Select one or more `.json` files, or use **JSON folder** to choose a folder containing JSON files. Folder selection is supported in Chrome and Edge.
2. Select **Run**. The app analyzes the selected files together and displays resource types and output file names.
3. Select **Save to MySQL** to persist the parent and child rows, or **Download All Output** to download a ZIP.

For example, the included `Bundle_json.json` and `Bundle_json2.json` can be selected together by choosing their containing folder. The folder picker includes `.json` files in the selected folder; place only files you intend to process there.

Tables are created automatically. Parent tables have names such as `fhir_Patient_parent`; child tables have names such as `fhir_Patient_Identifier`. FHIR IDs are stored as `VARCHAR(255)`, scalar strings as `TEXT`, and arrays/objects (including the original resource snapshot) as JSON. Existing JSON-typed string columns are migrated when the app next saves.

When a resource has an ID, an unchanged resource is skipped on later saves. If its content changes, its parent row is updated and its child rows are replaced. Resources without IDs cannot be matched across saves, so they are appended. The UI reports inserted, updated, and unchanged row counts.

## Troubleshooting

- **Unknown database (MySQL 1049):** Create the database named in `DATABASE_URL`, or change the URL to the name of an existing database. Restart FastAPI after changing `.env`.
- **Access denied to database (MySQL 1044):** Run the `GRANT` statement above as a MySQL administrator. Confirm `SHOW GRANTS` shows permissions on the same database named in `DATABASE_URL` and for the exact MySQL account host, such as `'fhir_app'@'localhost'`.
- **Can't connect:** Confirm MySQL is running, the host and port are reachable from the FastAPI computer, and the username/password in `.env` are correct.
- **Environment changes not applied:** Stop FastAPI with `Ctrl+C` and start it again. The app loads `.env.local` then `.env` when it starts; existing shell environment variables take precedence over both.

## Security

Uploads are sent to the FastAPI server. This proof of concept has no authentication or authorization; run it only in a trusted environment and do not expose it publicly or use it for production patient data without appropriate security controls.
