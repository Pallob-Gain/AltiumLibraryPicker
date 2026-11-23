mod convert;
mod geometry;

use std::collections::HashMap;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::altium::{self, BuildReport};

pub const UNOFFICIAL_NOTE: &str = "EasyEDA uses an undocumented API and community library data. Verify the pinout, footprint, and 3D placement against the datasheet.";
const MAX_JSON: u64 = 20 * 1024 * 1024;
const MAX_MODEL: u64 = 150 * 1024 * 1024;

#[derive(Debug)]
pub struct PartFile {
    pub path: PathBuf,
    pub meta: HashMap<String, String>,
    pub params: HashMap<String, String>,
    pub warnings: Vec<String>,
    pub pins: usize,
    pub pads: usize,
}

#[derive(Debug)]
pub struct StagedPart {
    pub lcsc: String,
    pub part: PartFile,
    pub build: BuildReport,
    pub schlib: PathBuf,
    pub pcblib: PathBuf,
}

#[derive(Debug)]
pub struct EasyedaError {
    pub message: String,
    pub requires_intervention: bool,
}

impl From<String> for EasyedaError {
    fn from(message: String) -> Self {
        Self {
            message,
            requires_intervention: false,
        }
    }
}

impl From<&str> for EasyedaError {
    fn from(message: &str) -> Self {
        message.to_owned().into()
    }
}

impl From<altium::BridgeError> for EasyedaError {
    fn from(error: altium::BridgeError) -> Self {
        Self {
            message: error.message,
            requires_intervention: error.requires_intervention,
        }
    }
}

pub fn clean_lcsc(input: &str) -> Result<String, String> {
    let s = input.trim().to_ascii_uppercase();
    let digits = s.strip_prefix('C').unwrap_or(&s);
    if digits.is_empty() || digits.len() > 12 || !digits.bytes().all(|c| c.is_ascii_digit()) {
        return Err("Enter an LCSC part number, for example C2040.".into());
    }
    Ok(format!("C{digits}"))
}

fn get(client: &reqwest::blocking::Client, url: &str, limit: u64) -> Result<Vec<u8>, String> {
    let mut response = client
        .get(url)
        .send()
        .map_err(|e| format!("EasyEDA network request failed: {e}"))?;
    if !response.status().is_success() {
        return Err(format!("EasyEDA returned HTTP {}.", response.status()));
    }
    if response.content_length().is_some_and(|size| size > limit) {
        return Err("EasyEDA response is too large.".into());
    }
    let mut bytes = Vec::new();
    response
        .by_ref()
        .take(limit + 1)
        .read_to_end(&mut bytes)
        .map_err(|e| format!("Cannot read EasyEDA response: {e}"))?;
    if bytes.len() as u64 > limit {
        return Err("EasyEDA response is too large.".into());
    }
    Ok(bytes)
}

fn write_part(path: &Path, records: &[Vec<String>]) -> Result<PartFile, String> {
    let mut part = PartFile {
        path: path.to_owned(),
        meta: HashMap::new(),
        params: HashMap::new(),
        warnings: Vec::new(),
        pins: 0,
        pads: 0,
    };
    let mut text = String::from("\u{feff}");
    for r in records {
        match r.first().map(String::as_str) {
            Some("META") if r.len() >= 3 => {
                part.meta.insert(r[1].clone(), r[2].clone());
            }
            Some("PARAM") if r.len() >= 3 => {
                part.params
                    .entry(r[1].clone())
                    .or_insert_with(|| r[2].clone());
            }
            Some("WARN") if r.len() >= 2 => part.warnings.push(r[1].clone()),
            Some("SPIN") => part.pins += 1,
            Some("FPAD") => part.pads += 1,
            _ => {}
        }
        text.push_str(&r.join("\t"));
        text.push_str("\r\n");
    }
    std::fs::write(path, text).map_err(|e| format!("Cannot write EasyEDA part file: {e}"))?;
    Ok(part)
}

fn fetch_with(
    lcsc: &str,
    base: &Path,
    mut download: impl FnMut(&str, u64) -> Result<Vec<u8>, String>,
) -> Result<PartFile, String> {
    let lcsc = clean_lcsc(lcsc)?;
    let cache = base.join("cache");
    let models = base.join("3D");
    std::fs::create_dir_all(&cache).map_err(|e| format!("Cannot create EasyEDA cache: {e}"))?;
    std::fs::create_dir_all(&models)
        .map_err(|e| format!("Cannot create EasyEDA model folder: {e}"))?;
    let raw = download(
        &format!("https://easyeda.com/api/products/{lcsc}/components?version=6.4.19.5"),
        MAX_JSON,
    )?;
    let data: serde_json::Value =
        serde_json::from_slice(&raw).map_err(|e| format!("Invalid EasyEDA response: {e}"))?;
    let result = data
        .get("result")
        .filter(|v| v.is_object())
        .ok_or_else(|| {
            format!(
                "EasyEDA has no symbol/footprint for {lcsc}: {}",
                data["message"].as_str().unwrap_or("part not found")
            )
        })?;
    if result.get("packageDetail").is_none_or(|v| {
        v.is_null()
            || v.as_str().is_some_and(|s| s.is_empty())
            || v.as_object().is_some_and(|object| object.is_empty())
    }) {
        return Err(format!("{lcsc} has a symbol but no footprint on EasyEDA."));
    }
    std::fs::write(cache.join(format!("{lcsc}.json")), raw)
        .map_err(|e| format!("Cannot save EasyEDA response: {e}"))?;
    let mut records = convert::convert(result, &lcsc);
    if let Some(model) = records.iter().find(|r| r[0] == "MODEL") {
        let uuid = model[1].clone();
        let model_result = (|| -> Result<Option<[f64; 5]>, String> {
            if !(8..=64).contains(&uuid.len()) || !uuid.bytes().all(|c| c.is_ascii_alphanumeric()) {
                return Err("Unexpected EasyEDA 3D model ID.".into());
            }
            let step = download(
                &format!("https://modules.easyeda.com/qAxj6KHrDKw4blvCG8QJPs7Y/{uuid}"),
                MAX_MODEL,
            )?;
            if step.len() < 100 {
                return Err("EasyEDA returned an incomplete STEP model.".into());
            }
            std::fs::write(models.join(format!("{uuid}.step")), step)
                .map_err(|e| format!("Cannot save STEP model: {e}"))?;
            let obj = download(
                &format!("https://modules.easyeda.com/3dmodel/{uuid}"),
                MAX_MODEL,
            )?;
            Ok(convert::obj_bbox(&String::from_utf8_lossy(&obj)))
        })();
        match model_result {
            Ok(bbox) => {
                if bbox.is_none() {
                    records.push(vec!["WARN".into(), "EasyEDA 3D mesh has no vertices; model placement uses the uncorrected origin.".into()]);
                }
                records = convert::finalize_model(records, bbox);
            }
            Err(error) => {
                records.retain(|r| r[0] != "MODEL");
                records.push(vec![
                    "WARN".into(),
                    format!(
                        "3D model could not be downloaded: {}",
                        error.replace(['\r', '\n', '\t'], " ")
                    ),
                ]);
            }
        }
    }
    let mut part = write_part(&cache.join(format!("{lcsc}.part.txt")), &records)?;
    part.warnings.extend(convert::unsupported_warnings(result));
    Ok(part)
}

fn stage_with(
    lcsc: &str,
    base: &Path,
    part: PartFile,
    build: impl FnOnce(&HashMap<String, String>) -> Result<BuildReport, altium::BridgeError>,
) -> Result<StagedPart, EasyedaError> {
    let stage = base.join("stage");
    std::fs::create_dir(&stage).map_err(|e| format!("Cannot create staging directory: {e}"))?;
    let schlib = stage.join(format!("{lcsc}.SchLib"));
    let pcblib = stage.join(format!("{lcsc}.PcbLib"));
    let values = HashMap::from([
        (
            "PartFile".to_owned(),
            part.path.to_string_lossy().into_owned(),
        ),
        ("LibDir".to_owned(), base.to_string_lossy().into_owned()),
        (
            "StageSchLib".to_owned(),
            schlib.to_string_lossy().into_owned(),
        ),
        (
            "StagePcbLib".to_owned(),
            pcblib.to_string_lossy().into_owned(),
        ),
    ]);
    let report = build(&values)?;
    if report.status != "OK" || !report.errors.is_empty() {
        return Err(format!(
            "EasyEDA builder failed: {}",
            if report.errors.is_empty() {
                format!("status {}", report.status)
            } else {
                report.errors.join("; ")
            }
        )
        .into());
    }
    if report.symbol.is_empty() || report.footprint.is_empty() || report.primitives.is_none() {
        return Err("EasyEDA builder returned an incomplete result.".into());
    }
    let pads = report
        .pads
        .ok_or("EasyEDA builder did not report a valid pad count.")?;
    if pads != part.pads {
        return Err(format!("Footprint has {pads} pads but EasyEDA data has {}; staging libraries were not selected.", part.pads).into());
    }
    for path in [&schlib, &pcblib] {
        let mut file = std::fs::File::open(path)
            .map_err(|e| format!("Cannot open staging library {}: {e}", path.display()))?;
        let mut header = [0; 8];
        file.read_exact(&mut header)
            .map_err(|e| format!("Cannot read staging library {}: {e}", path.display()))?;
        if header != [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1] {
            return Err(format!(
                "Staging library {} is not a binary Altium library.",
                path.display()
            )
            .into());
        }
    }
    Ok(StagedPart {
        lcsc: lcsc.to_owned(),
        part,
        build: report,
        schlib,
        pcblib,
    })
}

pub fn fetch_and_build(
    input: &str,
    x2: &Path,
    work_dir: &Path,
) -> Result<StagedPart, EasyedaError> {
    let lcsc = clean_lcsc(input)?;
    let root = work_dir.join("easyeda");
    std::fs::create_dir_all(&root).map_err(|e| format!("Cannot create EasyEDA directory: {e}"))?;
    let base = root.join(format!(
        "{}-{}-{}",
        lcsc,
        std::process::id(),
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("Invalid timestamp")?
    ));
    std::fs::create_dir(&base).map_err(|e| format!("Cannot create EasyEDA work directory: {e}"))?;
    let client = reqwest::blocking::Client::builder()
        .timeout(Duration::from_secs(60))
        .https_only(true)
        .redirect(reqwest::redirect::Policy::limited(5))
        .user_agent("Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/124.0")
        .build()
        .map_err(|e| format!("Cannot initialize EasyEDA client: {e}"))?;
    let part = fetch_with(&lcsc, &base, |url, limit| get(&client, url, limit))?;
    stage_with(&lcsc, &base, part, |values| {
        altium::build_easyeda(x2, work_dir, values, Duration::from_secs(240))
    })
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::path::Path;

    fn test_dir() -> PathBuf {
        let path = std::env::temp_dir().join(format!(
            "alm-easyeda-test-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        std::fs::create_dir(&path).unwrap();
        path
    }

    #[test]
    fn original_converter_fixtures_match() {
        let fixtures = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("easyeda");
        let files = std::fs::read_dir(&fixtures).unwrap();
        let mut count = 0;
        for file in files {
            let path = file.unwrap().path();
            let name = path.file_name().unwrap().to_str().unwrap();
            let Some(stem) = name.strip_suffix(".raweasy.json") else {
                continue;
            };
            count += 1;
            let data: serde_json::Value =
                serde_json::from_slice(&std::fs::read(&path).unwrap()).unwrap();
            let result = data.get("result").unwrap_or(&data);
            let records = convert::finalize_model(convert::convert(result, ""), None);
            let actual: Vec<_> = records.iter().map(|r| r.join("\t")).collect();
            let expected =
                std::fs::read_to_string(fixtures.join(format!("{stem}.expected.txt"))).unwrap();
            let expected: Vec<_> = expected.trim_start_matches('\u{feff}').lines().collect();
            assert_eq!(expected.len(), actual.len(), "{name}: line count");
            for (i, (expected, actual)) in expected.iter().zip(&actual).enumerate() {
                let e: Vec<_> = expected.split('\t').collect();
                let a: Vec<_> = actual.split('\t').collect();
                assert_eq!(
                    e.len(),
                    a.len(),
                    "{name}: line {}: expected {expected:?}, got {actual:?}",
                    i + 1
                );
                for (e, a) in e.iter().zip(a) {
                    if *e != a {
                        let near = e
                            .parse::<i64>()
                            .ok()
                            .zip(a.parse::<i64>().ok())
                            .is_some_and(|(x, y)| (x - y).abs() <= 1);
                        assert!(
                            near,
                            "{name}: line {}: expected {expected:?}, got {actual:?}",
                            i + 1
                        );
                    }
                }
            }
        }
        assert!(count >= 6);
    }

    #[test]
    fn nested_json_strings_produce_the_same_part_as_objects() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("easyeda")
            .join("a555-timer-dip.raweasy.json");
        let mut data: serde_json::Value =
            serde_json::from_slice(&std::fs::read(path).unwrap()).unwrap();
        let expected = convert::convert(&data, "");
        data["dataStr"] =
            serde_json::Value::String(serde_json::to_string(&data["dataStr"]).unwrap());
        data["packageDetail"]["dataStr"] = serde_json::Value::String(
            serde_json::to_string(&data["packageDetail"]["dataStr"]).unwrap(),
        );
        data["packageDetail"] =
            serde_json::Value::String(serde_json::to_string(&data["packageDetail"]).unwrap());
        assert_eq!(convert::convert(&data, ""), expected);
    }

    #[test]
    fn validates_lcsc_and_downloads_model_with_mesh_offsets() {
        for bad in ["../x", "C12/../../a", "ABC", "", "C1234567890123", "CC2"] {
            assert!(clean_lcsc(bad).is_err());
        }
        assert_eq!(clean_lcsc(" 2040 ").unwrap(), "C2040");
        let base = test_dir();
        let fixture: serde_json::Value = serde_json::from_slice(
            &std::fs::read(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests")
                    .join("fixtures")
                    .join("easyeda")
                    .join("a555-timer-dip.raweasy.json"),
            )
            .unwrap(),
        )
        .unwrap();
        let raw = serde_json::to_vec(&serde_json::json!({"result": fixture})).unwrap();
        let mut calls = Vec::new();
        let part = fetch_with("c46749", &base, |url, limit| {
            calls.push(url.to_owned());
            if url.starts_with("https://easyeda.com/") {
                assert_eq!(limit, MAX_JSON);
                Ok(raw.clone())
            } else if url.contains("/3dmodel/") {
                Ok(b"v -2 -1 -3\nv 4 5 1\n".to_vec())
            } else {
                assert_eq!(limit, MAX_MODEL);
                Ok(vec![b'x'; 200])
            }
        })
        .unwrap();
        assert_eq!(calls.len(), 3);
        assert_eq!((part.pins, part.pads), (8, 8));
        assert!(part.warnings.iter().any(|w| w.contains("regions")));
        let text = std::fs::read_to_string(&part.path).unwrap();
        assert!(text.contains("F3D\t5e8e9a2e75ff40abab9e1f1cacdd2cbb.step"));
        assert!(text.contains("\r\n"));
        assert!(text.starts_with('\u{feff}'));
        let model = base
            .join("3D")
            .join("5e8e9a2e75ff40abab9e1f1cacdd2cbb.step");
        assert!(model.is_file());
        std::fs::remove_file(model).unwrap();
        std::fs::remove_file(&part.path).unwrap();
        std::fs::remove_file(base.join("cache").join("C46749.json")).unwrap();
        std::fs::remove_dir(base.join("cache")).unwrap();
        std::fs::remove_dir(base.join("3D")).unwrap();
        std::fs::remove_dir(base).unwrap();
    }

    #[test]
    fn model_failure_is_reported_without_attaching_a_model() {
        let base = test_dir();
        let fixture: serde_json::Value = serde_json::from_slice(
            &std::fs::read(
                Path::new(env!("CARGO_MANIFEST_DIR"))
                    .join("tests")
                    .join("fixtures")
                    .join("easyeda")
                    .join("a555-timer-dip.raweasy.json"),
            )
            .unwrap(),
        )
        .unwrap();
        let raw = serde_json::to_vec(&serde_json::json!({"result": fixture})).unwrap();
        let part = fetch_with("C46749", &base, |url, _| {
            if url.starts_with("https://easyeda.com/") {
                Ok(raw.clone())
            } else {
                Err("model unavailable".into())
            }
        })
        .unwrap();
        assert!(part
            .warnings
            .iter()
            .any(|w| w.contains("model unavailable")));
        assert!(!std::fs::read_to_string(&part.path)
            .unwrap()
            .lines()
            .any(|line| line.starts_with("F3D\t")));
        std::fs::remove_file(part.path).unwrap();
        std::fs::remove_file(base.join("cache").join("C46749.json")).unwrap();
        std::fs::remove_dir(base.join("cache")).unwrap();
        std::fs::remove_dir(base.join("3D")).unwrap();
        std::fs::remove_dir(base).unwrap();
    }

    #[test]
    fn staging_requires_complete_report_matching_pad_count_and_library_files() {
        for pads in [Some(8), Some(7), None] {
            let base = test_dir();
            let part = PartFile {
                path: base.join("part.txt"),
                meta: HashMap::new(),
                params: HashMap::new(),
                warnings: vec![],
                pins: 8,
                pads: 8,
            };
            let result = stage_with("C46749", &base, part, |values| {
                assert_eq!(values["PartFile"], base.join("part.txt").to_string_lossy());
                assert_eq!(values["LibDir"], base.to_string_lossy());
                for key in ["StageSchLib", "StagePcbLib"] {
                    std::fs::write(
                        &values[key],
                        [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1],
                    )
                    .unwrap();
                }
                Ok(BuildReport {
                    status: "OK".into(),
                    symbol: "NE555P".into(),
                    footprint: "DIP".into(),
                    pads,
                    primitives: Some(20),
                    ..Default::default()
                })
            });
            assert_eq!(result.is_ok(), pads == Some(8));
            std::fs::remove_file(base.join("stage").join("C46749.SchLib")).unwrap();
            std::fs::remove_file(base.join("stage").join("C46749.PcbLib")).unwrap();
            std::fs::remove_dir(base.join("stage")).unwrap();
            std::fs::remove_dir(base).unwrap();
        }
    }

    #[test]
    fn model_offsets_use_rotated_mesh_center_and_minimum_height() {
        let records = vec![
            vec!["MODEL", "12345678", "part", "1", "2", "3", "0", "0", "90"]
                .into_iter()
                .map(str::to_owned)
                .collect(),
        ];
        let bbox = convert::obj_bbox("v -2 -1 -3\nv 4 5 1\n").unwrap();
        let out = convert::finalize_model(records, Some(bbox));
        assert_eq!(
            out[0],
            [
                "F3D",
                "12345678.step",
                "11811",
                "3937",
                "23622",
                "0",
                "0",
                "90000"
            ]
        );
    }

    #[test]
    fn unsupported_and_malformed_geometry_is_reported() {
        let data = serde_json::json!({
            "dataStr": {"shape":["UNSUPPORTED~1"]},
            "packageDetail": {"dataStr": {"shape":["SVGNODE", "PAD~CUSTOM", "TRACK~1~900~~0 0 1 1"]}}
        });
        let _ = convert::convert(&data, "C1");
        let warnings = convert::unsupported_warnings(&data).join("\n");
        assert!(warnings.contains("UNSUPPORTED"));
        assert!(warnings.contains("pads"));
        assert!(warnings.contains("unsupported layers"));
        assert!(warnings.contains("SVG/model"));
    }

    #[test]
    fn staging_unknown_outcome_requires_intervention() {
        let base = test_dir();
        let part = PartFile {
            path: base.join("part.txt"),
            meta: HashMap::new(),
            params: HashMap::new(),
            warnings: vec![],
            pins: 1,
            pads: 1,
        };
        let error = stage_with("C1", &base, part, |_| {
            Err(altium::BridgeError {
                message: "Altium timed out".into(),
                requires_intervention: true,
            })
        })
        .unwrap_err();
        assert!(error.requires_intervention);
        std::fs::remove_dir(base.join("stage")).unwrap();
        std::fs::remove_dir(base).unwrap();
    }

    #[test]
    fn staging_rejects_missing_libraries_even_after_success_report() {
        let base = test_dir();
        let part = PartFile {
            path: base.join("part.txt"),
            meta: HashMap::new(),
            params: HashMap::new(),
            warnings: vec![],
            pins: 1,
            pads: 1,
        };
        let error = stage_with("C1", &base, part, |_| {
            Ok(BuildReport {
                status: "OK".into(),
                symbol: "S".into(),
                footprint: "F".into(),
                pads: Some(1),
                primitives: Some(2),
                ..Default::default()
            })
        })
        .unwrap_err();
        assert!(error.message.contains("Cannot open staging library"));
        assert!(!error.requires_intervention);
        std::fs::remove_dir(base.join("stage")).unwrap();
        std::fs::remove_dir(base).unwrap();
    }

    #[test]
    fn response_size_limit_is_enforced() {
        use std::io::{Read, Write};
        use std::net::TcpListener;
        let listener = TcpListener::bind("127.0.0.1:0").unwrap();
        let address = listener.local_addr().unwrap();
        let server = std::thread::spawn(move || {
            let (mut stream, _) = listener.accept().unwrap();
            let mut request = [0; 1024];
            let _ = stream.read(&mut request).unwrap();
            stream
                .write_all(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\nConnection: close\r\n\r\n")
                .unwrap();
        });
        let client = reqwest::blocking::Client::builder()
            .timeout(Duration::from_secs(5))
            .build()
            .unwrap();
        let error = get(&client, &format!("http://{address}/"), 10).unwrap_err();
        assert!(error.contains("too large"));
        server.join().unwrap();
    }
}
