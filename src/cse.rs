use std::io::{Cursor, Read};
use std::path::{Path, PathBuf};
use std::time::Duration;

use serde_json::Value;

use crate::ranking::normalize_mpn;

const SEARCH: &str = "https://ad.componentsearchengine.com/ga/search.php";
const MODEL: &str = "https://componentsearchengine.com/ga/model.php";
const PLACEMENT: &str = "https://ad.componentsearchengine.com/ga/model.php";
const MAX_DOWNLOAD: u64 = 150 * 1024 * 1024;

#[derive(Debug, Clone)]
pub struct Placement {
    pub filename: String,
    pub values: [f64; 6],
}

#[derive(Debug)]
struct Part {
    id: u64,
    mpn: String,
    manufacturer: String,
    available: bool,
}

fn field(value: Option<&Value>) -> String {
    match value {
        Some(Value::String(s)) if s != "null" => s.trim().to_owned(),
        Some(Value::Number(n)) => n.to_string(),
        _ => String::new(),
    }
}

fn select_part(data: &Value, mpn: &str, manufacturer: &str) -> Result<u64, String> {
    if data.get("PartCount").is_none() {
        return Err("Component Search Engine returned an unexpected response.".into());
    }
    let mut parts = Vec::new();
    if let Some(items) = data.get("Parts").and_then(Value::as_object) {
        for (key, value) in items {
            let id = field(value.get("PartID"));
            let id = if id.is_empty() {
                key.strip_prefix("pid").unwrap_or(key)
            } else {
                &id
            };
            if let Ok(id) = id.parse() {
                parts.push(Part {
                    id,
                    mpn: field(value.get("PartNumber")),
                    manufacturer: field(value.get("Manufacturer")),
                    available: field(value.get("ModelAvailable")) == "1"
                        && !field(value.get("Symbol")).is_empty()
                        && !field(value.get("Footprint")).is_empty(),
                });
            }
        }
    }
    let matching: Vec<_> = parts
        .iter()
        .filter(|p| p.available && normalize_mpn(&p.mpn) == normalize_mpn(mpn))
        .collect();
    if matching.is_empty() {
        return Err(format!(
            "Component Search Engine has no exact downloadable model for {mpn}."
        ));
    }
    let manufacturer = manufacturer.trim();
    let candidates: Vec<_> = if manufacturer.is_empty() {
        matching
    } else {
        matching
            .into_iter()
            .filter(|p| p.manufacturer.trim().eq_ignore_ascii_case(manufacturer))
            .collect()
    };
    match candidates.as_slice() {
        [part] => Ok(part.id),
        [] => Err("No exact CSE model matches the selected manufacturer. Check the manufacturer or choose another search result.".into()),
        _ => Err("More than one CSE model matches. Select a manufacturer to avoid downloading the wrong part.".into()),
    }
}

pub fn parse_placement(text: &str) -> Option<Placement> {
    for line in text.lines() {
        let fields: Vec<_> = line.split(',').map(str::trim).collect();
        if fields.len() < 8 || fields[0] != "AssignSTEPmodel" {
            continue;
        }
        let mut values = [0.0; 6];
        for (slot, text) in values.iter_mut().zip(&fields[2..8]) {
            *slot = text.parse::<f64>().ok()?;
            if !slot.is_finite() || slot.abs() > 1000.0 {
                return None;
            }
        }
        let name = fields[1].replace('\\', "/");
        let filename = name.rsplit('/').next()?.to_owned();
        if filename.is_empty() {
            return None;
        }
        return Some(Placement { filename, values });
    }
    None
}

fn response(
    client: &reqwest::blocking::Client,
    url: &str,
    query: &[(&str, &str)],
    email: &str,
    password: &str,
    limit: u64,
) -> Result<Vec<u8>, String> {
    let mut response = client
        .get(url)
        .query(query)
        .basic_auth(email, Some(password))
        .send()
        .map_err(|_| "Network error contacting Component Search Engine.".to_owned())?;
    let status = response.status();
    if status.as_u16() == 401 {
        return Err("Component Search Engine rejected the email or password.".into());
    }
    if !status.is_success() {
        return Err(format!(
            "Component Search Engine returned HTTP {status}; redirects are not followed."
        ));
    }
    if response.content_length().is_some_and(|size| size > limit) {
        return Err("Component Search Engine response is too large.".into());
    }
    let mut bytes = Vec::new();
    response
        .by_ref()
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|_| "Could not read the Component Search Engine response.".to_owned())?;
    if bytes.len() as u64 > limit {
        return Err("Component Search Engine response is too large.".into());
    }
    Ok(bytes)
}

pub struct Download {
    pub path: PathBuf,
    pub placement: Option<Placement>,
    pub warning: Option<String>,
}

pub fn download(
    mpn: &str,
    manufacturer: &str,
    email: &str,
    password: &str,
    work_dir: &Path,
) -> Result<Download, String> {
    if mpn.trim().is_empty() || email.is_empty() || password.is_empty() {
        return Err("Enter a part number and CSE email and password.".into());
    }
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(60))
        .redirect(reqwest::redirect::Policy::none())
        .user_agent("AltiumLibraryManager/1.4")
        .build()
        .map_err(|_| "Could not initialize the CSE HTTP client.")?;
    let search = response(
        &client,
        SEARCH,
        &[("kws", mpn.trim()), ("v", "1")],
        email,
        password,
        5_000_000,
    )?;
    let data: Value = serde_json::from_slice(&search)
        .map_err(|_| "Component Search Engine search did not return valid JSON.")?;
    let id = select_part(&data, mpn, manufacturer)?.to_string();
    let bytes = response(
        &client,
        MODEL,
        &[("partID", &id)],
        email,
        password,
        MAX_DOWNLOAD,
    )?;
    let mut archive = zip::ZipArchive::new(Cursor::new(&bytes)).map_err(|_| {
        "CSE did not return a valid ZIP model. Check your account and selected ECAD format."
    })?;
    let has_library = (0..archive.len()).any(|i| {
        archive.by_index(i).is_ok_and(|entry| {
            let name = entry.name().to_ascii_lowercase();
            [".schlib", ".pcblib", ".intlib", ".libpkg"]
                .iter()
                .any(|e| name.ends_with(e))
        })
    });
    if !has_library {
        return Err("The CSE download has no Altium library. Select Altium Designer as the ECAD tool in your CSE account.".into());
    }
    let placement_result = response(
        &client,
        PLACEMENT,
        &[("partID", &id), ("st", "4"), ("lt", "4"), ("pi", "2")],
        email,
        password,
        4 * 1024 * 1024,
    );
    let (placement, warning) = match placement_result {
        Ok(bytes) => match std::str::from_utf8(&bytes).ok().and_then(parse_placement) {
            Some(p) => (Some(p), None),
            None => (None, Some("CSE did not provide a usable STEP placement; a separate STEP will not be attached automatically.".into())),
        },
        Err(error) => (None, Some(format!("Could not retrieve CSE STEP placement: {error}. A separate STEP will not be attached automatically."))),
    };
    let root = work_dir.join("downloads");
    std::fs::create_dir_all(&root).map_err(|e| format!("Cannot create download cache: {e}"))?;
    let folder = root.join(format!(
        "cse-{}-{}",
        std::process::id(),
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("Invalid timestamp")?
    ));
    std::fs::create_dir(&folder).map_err(|e| format!("Cannot create download folder: {e}"))?;
    let path = folder.join(format!("CSE_{id}.zip"));
    std::fs::write(&path, bytes).map_err(|e| format!("Cannot save CSE download: {e}"))?;
    Ok(Download {
        path,
        placement,
        warning,
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::{Read, Write};
    use std::net::TcpListener;
    #[test]
    fn exact_matching_rejects_variants_and_ambiguous_manufacturers() {
        let data = serde_json::json!({"PartCount": 3, "Parts": {
            "1": {"PartNumber": "NE555DR", "Manufacturer": "TI", "ModelAvailable": "1", "Symbol": "s", "Footprint": "f"},
            "pid2": {"PartNumber": "ne555dr", "Manufacturer": "Other", "ModelAvailable": "1", "Symbol": "s", "Footprint": "f"},
            "3": {"PartNumber": "NE555DRG4", "Manufacturer": "TI", "ModelAvailable": "1", "Symbol": "s", "Footprint": "f"}
        }});
        assert_eq!(select_part(&data, "ne555dr", "TI").unwrap(), 1);
        assert!(select_part(&data, "ne555dr", "").is_err());
        assert!(select_part(&data, "ne555dr", "Missing").is_err());
    }
    #[test]
    fn validates_step_placement() {
        let p = parse_placement("AssignSTEPmodel, foo.stp, 90, 0, 0, 1, 0.63, 0").unwrap();
        assert_eq!(p.filename, "foo.stp");
        assert_eq!(p.values, [90.0, 0.0, 0.0, 1.0, 0.63, 0.0]);
        assert!(parse_placement("AssignSTEPmodel, x.stp, NaN, 0, 0, 0, 0, 0").is_none());
    }

    #[test]
    fn authenticated_request_does_not_follow_redirects() {
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0; 4096];
            let size = stream.read(&mut request).unwrap();
            let request = String::from_utf8_lossy(&request[..size]).to_ascii_lowercase();
            assert!(request.contains(
                "authorization: basic dXNlcjpwYXNz"
                    .to_ascii_lowercase()
                    .as_str()
            ));
            stream.write_all(b"HTTP/1.1 302 Found\r\nLocation: http://127.0.0.1:1/\r\nContent-Length: 0\r\nConnection: close\r\n\r\n").unwrap();
        });
        let client = reqwest::blocking::Client::builder()
            .redirect(reqwest::redirect::Policy::none())
            .timeout(Duration::from_secs(5))
            .build()
            .unwrap();
        let err = response(
            &client,
            &format!("http://{address}/"),
            &[],
            "user",
            "pass",
            100,
        )
        .unwrap_err();
        assert!(err.contains("302"));
        server.join().unwrap();
    }
}
