use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::thread;
use std::time::{Duration, Instant};

#[derive(Clone, Debug, Default)]
pub struct ImportReport {
    pub status: String,
    pub added_symbols: Vec<String>,
    pub added_footprints: Vec<String>,
    pub errors: Vec<String>,
    pub warnings: Vec<String>,
    pub flags: HashMap<String, String>,
    pub links: Vec<(String, String, String)>,
    pub cache: Vec<(String, bool)>,
    pub details: Vec<String>,
    pub backups: Vec<PathBuf>,
    pub requires_intervention: bool,
}

impl ImportReport {
    pub fn parse(text: &str) -> Self {
        let mut report = Self::default();
        for line in text.lines() {
            let (key, value) = line.split_once('=').unwrap_or((line, ""));
            let key = key.trim();
            let value = value.trim();
            match key {
                "STATUS" => report.status = value.to_owned(),
                "ADDED_SYMBOL" => report.added_symbols.push(value.to_owned()),
                "ADDED_FOOTPRINT" => report.added_footprints.push(value.to_owned()),
                "ERROR" => report.errors.push(value.to_owned()),
                "WARNING" => report.warnings.push(value.to_owned()),
                "LINK" => {
                    let mut parts = value.splitn(3, '|');
                    report.links.push((
                        parts.next().unwrap_or("").to_owned(),
                        parts.next().unwrap_or("").to_owned(),
                        parts.next().unwrap_or("").to_owned(),
                    ));
                }
                "VERIFY_CACHE" => {
                    if let Some((name, state)) = value.rsplit_once('|') {
                        report.cache.push((name.to_owned(), state == "ok"));
                    }
                }
                "ADDED_3D" | "EXISTING_FOOTPRINT" | "SKIPPED_SYMBOL" | "PARAM"
                | "VERIFY_SYMBOL" | "VERIFY_FOOTPRINT" | "PINCHECK" => {
                    report.details.push(format!("{key}: {value}"));
                }
                "SCH_WAS_OPEN" | "PCB_WAS_OPEN" | "REFRESHED" => {
                    report.flags.insert(key.to_owned(), value.to_owned());
                }
                _ => {}
            }
        }
        report
    }

    pub fn is_ok(&self) -> bool {
        self.status == "OK" && self.errors.is_empty()
    }

    pub fn summary(&self) -> String {
        let mut lines = vec![format!(
            "Result: {}",
            if self.is_ok() { "SUCCESS" } else { "FAILED" }
        )];
        lines.extend(self.errors.iter().map(|s| format!("ERROR: {s}")));
        lines.extend(
            self.added_symbols
                .iter()
                .map(|s| format!("Added symbol: {s}")),
        );
        lines.extend(
            self.added_footprints
                .iter()
                .map(|s| format!("Added footprint: {s}")),
        );
        lines.extend(
            self.links
                .iter()
                .map(|(s, f, l)| format!("Linked: {s} -> {f} (library: {l})")),
        );
        lines.extend(self.details.iter().cloned());
        lines.extend(self.cache.iter().map(|(f, ok)| {
            format!(
                "Library search: {f} {}",
                if *ok {
                    "found"
                } else {
                    "not found; check link mode and Altium library setup"
                }
            )
        }));
        lines.extend(self.warnings.iter().map(|s| format!("Warning: {s}")));
        lines.extend(
            self.backups
                .iter()
                .map(|p| format!("Backup: {}", p.display())),
        );
        lines.push(
            "Check pin functions and 3D placement against the datasheet in Altium.".to_owned(),
        );
        lines.join("\n")
    }
}

pub fn find_x2(r#override: &str) -> Option<PathBuf> {
    if !r#override.trim().is_empty() {
        let path = PathBuf::from(r#override);
        if path.is_file() {
            return Some(path);
        }
    }
    if let Ok(entries) = glob::glob(r"C:\Program Files\Altium\AD*\X2.EXE") {
        let mut candidates: Vec<_> = entries.filter_map(Result::ok).collect();
        candidates.sort();
        candidates.reverse();
        if let Some(path) = candidates.into_iter().next() {
            return Some(path);
        }
    }
    None
}

fn short_path(path: &Path) -> String {
    #[cfg(windows)]
    {
        use std::os::windows::ffi::{OsStrExt, OsStringExt};
        use windows_sys::Win32::Storage::FileSystem::GetShortPathNameW;
        let input: Vec<u16> = path.as_os_str().encode_wide().chain(Some(0)).collect();
        let required = unsafe { GetShortPathNameW(input.as_ptr(), std::ptr::null_mut(), 0) };
        if required > 0 {
            let mut buffer = vec![0u16; required as usize];
            let length = unsafe {
                GetShortPathNameW(input.as_ptr(), buffer.as_mut_ptr(), buffer.len() as u32)
            };
            if length > 0 && (length as usize) < buffer.len() {
                return std::ffi::OsString::from_wide(&buffer[..length as usize])
                    .to_string_lossy()
                    .into_owned();
            }
        }
    }
    path.to_string_lossy().into_owned()
}

fn write_job(
    job_dir: &Path,
    template: &str,
    values: &HashMap<String, String>,
) -> Result<(PathBuf, PathBuf), String> {
    let parent = job_dir.parent().ok_or("Invalid Altium job directory")?;
    fs::create_dir_all(parent).map_err(|e| format!("Cannot create job parent directory: {e}"))?;
    fs::create_dir(job_dir).map_err(|e| format!("Cannot create job directory: {e}"))?;
    let job = job_dir.join("job.txt");
    let result = job_dir.join("result.txt");
    let mut job_text = String::from("\u{feff}");
    for (key, value) in values {
        if key.contains(['=', '\n', '\r']) {
            return Err(format!("Invalid Altium job key: {key:?}"));
        }
        let clean = value.replace(['\r', '\n'], " ");
        job_text.push_str(&format!("{key}={clean}\n"));
    }
    fs::write(&job, job_text).map_err(|e| format!("Cannot write Altium job: {e}"))?;
    let script = job_dir.join("alm_import.pas");
    let result_for_script = PathBuf::from(short_path(job_dir)).join("result.txt");
    let script_text = template
        .replace("{{JOB_FILE}}", &short_path(&job).replace('\'', "''"))
        .replace(
            "{{RESULT_FILE}}",
            &result_for_script.to_string_lossy().replace('\'', "''"),
        );
    fs::write(&script, format!("\u{feff}{script_text}"))
        .map_err(|e| format!("Cannot write Altium script: {e}"))?;
    Ok((script, result))
}

fn decode_result(bytes: &[u8]) -> String {
    if bytes.starts_with(&[0xff, 0xfe]) {
        String::from_utf16_lossy(
            &bytes[2..]
                .chunks_exact(2)
                .map(|c| u16::from_le_bytes([c[0], c[1]]))
                .collect::<Vec<_>>(),
        )
    } else if bytes.starts_with(&[0xef, 0xbb, 0xbf]) {
        String::from_utf8_lossy(&bytes[3..]).into_owned()
    } else {
        match std::str::from_utf8(bytes) {
            Ok(text) => text.to_owned(),
            Err(_) => encoding_rs::WINDOWS_1252.decode(bytes).0.into_owned(),
        }
    }
}

#[derive(Debug)]
pub struct BridgeError {
    pub message: String,
    pub requires_intervention: bool,
}

impl From<String> for BridgeError {
    fn from(message: String) -> Self {
        Self {
            message,
            requires_intervention: false,
        }
    }
}

fn run_template(
    x2: &Path,
    work_dir: &Path,
    values: &HashMap<String, String>,
    timeout: Duration,
    template: &str,
) -> Result<String, BridgeError> {
    let stamp = chrono::Utc::now().format("%Y%m%d-%H%M%S-%f").to_string();
    let job_dir = work_dir.join("jobs").join(stamp);
    let (script, result) = write_job(&job_dir, template, values)?;
    let script_arg = format!(
        "-RScriptingSystem:RunScriptFile(FileName={}|ProcName=Run)",
        short_path(&script),
    );
    Command::new(x2)
        .arg(script_arg)
        .spawn()
        .map_err(|e| format!("Could not start Altium: {e}"))?;

    let deadline = Instant::now() + timeout;
    loop {
        match fs::read(&result) {
            Ok(bytes) => {
                let text = decode_result(&bytes);
                if text.lines().any(|line| line.trim() == "END=1") {
                    return Ok(text);
                }
            }
            Err(error)
                if matches!(
                    error.kind(),
                    std::io::ErrorKind::NotFound | std::io::ErrorKind::PermissionDenied
                ) => {}
            Err(error) => {
                return Err(BridgeError {
                    message: format!("Cannot read Altium result: {error}"),
                    requires_intervention: true,
                })
            }
        }
        if Instant::now() >= deadline {
            return Err(BridgeError {
                message: format!(
                    "No answer from Altium within {} s. Make sure Altium Designer is running.",
                    timeout.as_secs()
                ),
                requires_intervention: true,
            });
        }
        thread::sleep(Duration::from_millis(500));
    }
}

pub fn run_job(
    x2: &Path,
    work_dir: &Path,
    values: &HashMap<String, String>,
    timeout: Duration,
) -> Result<ImportReport, String> {
    run_template(
        x2,
        work_dir,
        values,
        timeout,
        include_str!("../alm/altium/alm_import.pas"),
    )
    .map(|text| ImportReport::parse(&text))
    .map_err(|error| error.message)
}

#[derive(Debug, Default)]
pub struct BuildReport {
    pub status: String,
    pub symbol: String,
    pub footprint: String,
    pub pads: Option<usize>,
    pub primitives: Option<usize>,
    pub warnings: Vec<String>,
    pub errors: Vec<String>,
}

impl BuildReport {
    fn parse(text: &str) -> Self {
        let mut report = Self::default();
        for line in text.lines() {
            let Some((key, value)) = line.split_once('=') else {
                continue;
            };
            let value = value.trim();
            match key.trim() {
                "STATUS" => report.status = value.to_owned(),
                "BUILT_SYMBOL" => report.symbol = value.to_owned(),
                "BUILT_FOOTPRINT" => report.footprint = value.to_owned(),
                "BUILT_PADS" => report.pads = value.parse().ok(),
                "BUILT_PRIMITIVES" => report.primitives = value.parse().ok(),
                "WARNING" => report.warnings.push(value.to_owned()),
                "ERROR" => report.errors.push(value.to_owned()),
                _ => {}
            }
        }
        report
    }
}

pub fn build_easyeda(
    x2: &Path,
    work_dir: &Path,
    values: &HashMap<String, String>,
    timeout: Duration,
) -> Result<BuildReport, BridgeError> {
    run_template(
        x2,
        work_dir,
        values,
        timeout,
        include_str!("../alm/altium/ee_build.pas"),
    )
    .map(|text| BuildReport::parse(&text))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn generated_job_points_to_result_file_and_has_utf8_bom() {
        let dir = std::env::temp_dir().join(format!(
            "alm-bridge-test-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let template = include_str!("../alm/altium/alm_import.pas");
        let values = HashMap::from([("Action".to_owned(), "test\nvalue".to_owned())]);
        let (script, result) = write_job(&dir, template, &values).unwrap();
        let script_text = fs::read_to_string(&script).unwrap();
        let job_bytes = fs::read(dir.join("job.txt")).unwrap();
        assert!(job_bytes.starts_with(&[0xef, 0xbb, 0xbf]));
        assert!(String::from_utf8(job_bytes)
            .unwrap()
            .contains("Action=test value"));
        let expected = PathBuf::from(short_path(&dir)).join("result.txt");
        assert!(script_text.contains(&expected.to_string_lossy().replace('\'', "''")));
        assert_eq!(result, dir.join("result.txt"));
        assert!(!script_text.contains("{{RESULT_FILE}}"));
        fs::remove_file(script).unwrap();
        fs::remove_file(dir.join("job.txt")).unwrap();
        fs::remove_dir(dir).unwrap();
    }

    #[test]
    fn easyeda_builder_template_and_result_use_the_same_bridge_contract() {
        let dir = std::env::temp_dir().join(format!(
            "alm-ee-bridge-test-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        let values = HashMap::from([
            ("PartFile".to_owned(), "input.part.txt".to_owned()),
            ("LibDir".to_owned(), "model directory".to_owned()),
            ("StageSchLib".to_owned(), "stage.SchLib".to_owned()),
            ("StagePcbLib".to_owned(), "stage.PcbLib".to_owned()),
        ]);
        let (script, _) =
            write_job(&dir, include_str!("../alm/altium/ee_build.pas"), &values).unwrap();
        let text = fs::read_to_string(&script).unwrap();
        assert!(text.contains("procedure BuildPart"));
        assert!(!text.contains("{{JOB_FILE}}"));
        assert!(!text.contains("{{RESULT_FILE}}"));
        let job = fs::read_to_string(dir.join("job.txt")).unwrap();
        for (key, value) in values {
            assert!(job.contains(&format!("{key}={value}")));
        }
        let report = BuildReport::parse("STATUS=OK\nBUILT_SYMBOL=Part\nBUILT_FOOTPRINT=FP\nBUILT_PADS=8\nBUILT_PRIMITIVES=19\nWARNING=test\nEND=1\n");
        assert_eq!((report.pads, report.primitives), (Some(8), Some(19)));
        assert_eq!(report.warnings, ["test"]);
        assert_eq!(BuildReport::parse("BUILT_PADS=invalid\n").pads, None);
        fs::remove_file(script).unwrap();
        fs::remove_file(dir.join("job.txt")).unwrap();
        fs::remove_dir(dir).unwrap();
    }
}
