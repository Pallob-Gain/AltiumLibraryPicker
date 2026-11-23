use std::path::Path;

use rusqlite::{params, Connection};

use crate::ranking::normalize_mpn;
use crate::{altium::ImportReport, importer::ImportRequest};

pub struct Database {
    connection: Connection,
}

impl Database {
    pub fn open(path: impl AsRef<Path>) -> rusqlite::Result<Self> {
        let connection = Connection::open(path)?;
        connection.execute_batch(
            "CREATE TABLE IF NOT EXISTS components (
                id INTEGER PRIMARY KEY,
                mpn TEXT NOT NULL,
                mpn_norm TEXT NOT NULL,
                manufacturer TEXT NOT NULL DEFAULT '',
                lcsc_part TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                files_json TEXT NOT NULL DEFAULT '[]',
                dest_schlib TEXT NOT NULL DEFAULT '',
                dest_pcblib TEXT NOT NULL DEFAULT '',
                symbol_name TEXT NOT NULL DEFAULT '',
                footprint_name TEXT NOT NULL DEFAULT '',
                import_date TEXT NOT NULL,
                verification TEXT NOT NULL DEFAULT 'unverified'
             );
             CREATE INDEX IF NOT EXISTS ix_components_mpn ON components(mpn_norm);
             CREATE TABLE IF NOT EXISTS search_log (
                id INTEGER PRIMARY KEY,
                ts TEXT NOT NULL,
                query TEXT NOT NULL,
                providers_json TEXT NOT NULL,
                exact_count INTEGER NOT NULL
             );",
        )?;
        Ok(Self { connection })
    }

    pub fn find_component(&self, mpn: &str) -> rusqlite::Result<Vec<String>> {
        let mut statement = self.connection.prepare(
            "SELECT mpn, dest_schlib, dest_pcblib, import_date FROM components
             WHERE mpn_norm = ? ORDER BY import_date DESC",
        )?;
        let rows = statement.query_map(params![normalize_mpn(mpn)], |row| {
            Ok(format!(
                "{} | {} | {} | {}",
                row.get::<_, String>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, String>(2)?,
                row.get::<_, String>(3)?
            ))
        })?;
        rows.collect()
    }

    pub fn log_search(
        &self,
        query: &str,
        provider_states: &str,
        exact_count: usize,
    ) -> rusqlite::Result<()> {
        self.connection.execute(
            "INSERT INTO search_log(ts, query, providers_json, exact_count) VALUES(?,?,?,?)",
            params![
                chrono::Utc::now().to_rfc3339(),
                query,
                provider_states,
                exact_count
            ],
        )?;
        Ok(())
    }

    pub fn record_import(
        &self,
        req: &ImportRequest,
        report: &ImportReport,
    ) -> rusqlite::Result<()> {
        if !report.is_ok() || report.added_symbols.is_empty() {
            return Ok(());
        }
        let mpn = req
            .params
            .get("Manufacturer Part Number")
            .filter(|s| !s.is_empty())
            .map_or(report.added_symbols[0].as_str(), String::as_str);
        let files = serde_json::json!([req.src_schlib, req.src_pcblib]).to_string();
        let mut footprints: Vec<&str> = report
            .links
            .iter()
            .map(|(_, f, _)| f.as_str())
            .filter(|f| !f.is_empty())
            .collect();
        footprints.sort_unstable();
        footprints.dedup();
        self.connection.execute(
            "INSERT INTO components(mpn, mpn_norm, manufacturer, lcsc_part, source, files_json,
             dest_schlib, dest_pcblib, symbol_name, footprint_name, import_date, verification)
             VALUES(?,?,?,?,?,?,?,?,?,?,?,?)",
            params![
                mpn,
                normalize_mpn(mpn),
                req.params.get("Manufacturer").map_or("", String::as_str),
                req.params.get("LCSC Part #").map_or("", String::as_str),
                "local library",
                files,
                req.dest_schlib.to_string_lossy(),
                req.dest_pcblib.to_string_lossy(),
                report.added_symbols.join(", "),
                footprints.join(", "),
                chrono::Utc::now().to_rfc3339(),
                "NEEDS MANUAL REVIEW"
            ],
        )?;
        Ok(())
    }
}
