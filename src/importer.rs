use std::collections::{BTreeSet, HashMap};
use std::fs;
use std::io::Read;
use std::path::{Path, PathBuf};
use std::time::Duration;

use crate::altium::{self, ImportReport};

#[cfg(test)]
#[path = "importer_tests.rs"]
mod tests;

#[derive(Clone, Debug)]
pub struct ImportRequest {
    pub src_schlib: PathBuf,
    pub src_pcblib: PathBuf,
    pub dest_schlib: PathBuf,
    pub dest_pcblib: PathBuf,
    pub component: String,
    pub params: HashMap<String, String>,
    pub description: String,
    pub conflict: String,
    pub link_mode: String,
    pub step_file: Option<PathBuf>,
    pub step_placement: Option<[f64; 6]>,
}

fn validate_library(path: &Path, extension: &str) -> Result<(), String> {
    if !path
        .extension()
        .and_then(|e| e.to_str())
        .is_some_and(|e| e.eq_ignore_ascii_case(extension))
    {
        return Err(format!("Expected a .{extension} file: {}", path.display()));
    }
    let mut file =
        fs::File::open(path).map_err(|e| format!("Cannot open {}: {e}", path.display()))?;
    if !file.metadata().map_err(|e| e.to_string())?.is_file() {
        return Err(format!("Not a file: {}", path.display()));
    }
    let mut magic = [0; 8];
    file.read_exact(&mut magic)
        .map_err(|e| format!("Cannot read {}: {e}", path.display()))?;
    if magic != [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1] {
        return Err(format!("Not a binary Altium library: {}", path.display()));
    }
    Ok(())
}

pub fn validate(req: &ImportRequest) -> Result<(), String> {
    for (path, ext) in [
        (&req.src_schlib, "SchLib"),
        (&req.src_pcblib, "PcbLib"),
        (&req.dest_schlib, "SchLib"),
        (&req.dest_pcblib, "PcbLib"),
    ] {
        if path.to_string_lossy().contains(['\r', '\n']) {
            return Err("Library paths cannot contain line breaks.".into());
        }
        validate_library(path, ext)?;
    }

    let paths = [
        &req.src_schlib,
        &req.src_pcblib,
        &req.dest_schlib,
        &req.dest_pcblib,
    ];
    for (index, path) in paths.iter().enumerate() {
        for other in &paths[index + 1..] {
            if same_file::is_same_file(path, other)
                .map_err(|e| format!("Cannot compare library files: {e}"))?
            {
                return Err(format!(
                    "Library paths refer to the same file: {} and {}",
                    path.display(),
                    other.display()
                ));
            }
        }
    }
    if !["skip", "rename"].contains(&req.conflict.as_str())
        || !["name", "path", "any"].contains(&req.link_mode.as_str())
    {
        return Err("Invalid conflict or footprint link mode.".to_owned());
    }
    if req.component.is_empty() || req.component.contains(['\r', '\n']) {
        return Err("Select a component to import.".to_owned());
    }
    let symbols = crate::schlib::list_symbols(&req.src_schlib)?;
    if !symbols.iter().any(|s| s.name == req.component) {
        return Err(format!(
            "Component '{}' is not in the source library.",
            req.component
        ));
    }
    if symbols
        .iter()
        .filter(|s| s.name.to_uppercase() == req.component.to_uppercase())
        .count()
        != 1
    {
        return Err("Component name is ambiguous ignoring case; import cancelled.".into());
    }
    if let Some(step) = &req.step_file {
        if step.to_string_lossy().contains(['\r', '\n'])
            || !step.is_file()
            || !step
                .extension()
                .and_then(|e| e.to_str())
                .is_some_and(|e| e.eq_ignore_ascii_case("step") || e.eq_ignore_ascii_case("stp"))
        {
            return Err(format!("Invalid STEP file: {}", step.display()));
        }
    }
    if req
        .step_placement
        .is_some_and(|p| p.iter().any(|v| !v.is_finite() || v.abs() > 1000.0))
    {
        return Err("Invalid STEP placement.".into());
    }
    Ok(())
}

fn backup(req: &ImportRequest, work_dir: &Path) -> Result<Vec<PathBuf>, String> {
    let root = work_dir.join("backups");
    fs::create_dir_all(&root).map_err(|e| format!("Cannot create backup folder: {e}"))?;
    let folder = root.join(format!(
        "{}-{}",
        chrono::Utc::now().format("%Y%m%d-%H%M%S-%f"),
        std::process::id()
    ));
    fs::create_dir(&folder).map_err(|e| format!("Cannot create unique backup folder: {e}"))?;
    let mut backups = Vec::new();
    for path in [&req.dest_schlib, &req.dest_pcblib] {
        let target = folder.join(path.file_name().ok_or("Destination has no file name.")?);
        fs::copy(path, &target).map_err(|e| format!("Cannot back up {}: {e}", path.display()))?;
        backups.push(target);
    }
    Ok(backups)
}

fn destination_values(req: &ImportRequest, action: &str) -> HashMap<String, String> {
    HashMap::from([
        ("Action".into(), action.into()),
        (
            "DestSchLib".into(),
            req.dest_schlib.to_string_lossy().into_owned(),
        ),
        (
            "DestPcbLib".into(),
            req.dest_pcblib.to_string_lossy().into_owned(),
        ),
    ])
}

fn job_values(req: &ImportRequest) -> HashMap<String, String> {
    let mut values = destination_values(req, "import");
    values.extend([
        (
            "SrcSchLib".into(),
            req.src_schlib.to_string_lossy().into_owned(),
        ),
        (
            "SrcPcbLib".into(),
            req.src_pcblib.to_string_lossy().into_owned(),
        ),
        ("OnConflict".into(), req.conflict.clone()),
        ("LinkMode".into(), req.link_mode.clone()),
        ("Component".into(), req.component.clone()),
        ("Description".into(), req.description.clone()),
        (
            "StepFile".into(),
            req.step_file
                .as_ref()
                .map_or_else(String::new, |p| p.to_string_lossy().into_owned()),
        ),
        ("KeepOpen".into(), "1".into()),
    ]);
    let placement = req.step_placement.unwrap_or([0.0; 6]);
    for (index, key) in [
        "StepRotX", "StepRotY", "StepRotZ", "StepDX", "StepDY", "StepDZ",
    ]
    .iter()
    .enumerate()
    {
        let multiplier = if index < 3 { 1000.0 } else { 10000.0 / 0.0254 };
        values.insert(
            (*key).into(),
            (placement[index] * multiplier).round().to_string(),
        );
    }
    let mut names: Vec<_> = req.params.keys().map(String::as_str).collect();
    names.sort_unstable();
    values.insert("ParamCount".into(), names.len().to_string());
    for (index, name) in names.iter().enumerate() {
        values.insert(format!("Param{}Name", index + 1), (*name).into());
        values.insert(
            format!("Param{}Value", index + 1),
            req.params[*name].clone(),
        );
    }
    values
}

fn finish_values(
    req: &ImportRequest,
    prep: &ImportReport,
    report: &ImportReport,
) -> HashMap<String, String> {
    let mut values = destination_values(req, "finish");
    let footprints: BTreeSet<_> = if report.is_ok() {
        report
            .links
            .iter()
            .map(|(_, footprint, _)| footprint)
            .filter(|s| !s.is_empty())
            .collect()
    } else {
        BTreeSet::new()
    };
    values.insert("FootprintCount".into(), footprints.len().to_string());
    for (index, fp) in footprints.iter().enumerate() {
        values.insert(format!("Footprint{}", index + 1), (*fp).clone());
    }
    values.insert("CloseDocs".into(), "1".into());
    values.insert("Refresh".into(), "1".into());
    for (job_key, flag) in [("ReopenSch", "SCH_WAS_OPEN"), ("ReopenPcb", "PCB_WAS_OPEN")] {
        values.insert(
            job_key.into(),
            if prep.flags.get(flag).is_some_and(|v| v == "1") {
                "1"
            } else {
                "0"
            }
            .into(),
        );
    }
    values
}

fn failed(message: String) -> ImportReport {
    ImportReport {
        status: "ERROR".into(),
        errors: vec![message],
        ..Default::default()
    }
}

fn uncertain(message: String) -> ImportReport {
    let mut report = failed(message);
    report.requires_intervention = true;
    report
}

pub fn run_import(req: &ImportRequest, x2: &Path, work_dir: &Path) -> ImportReport {
    run_with(
        req,
        work_dir,
        |values| altium::run_job(x2, work_dir, values, Duration::from_secs(240)),
        |delay| std::thread::sleep(delay),
    )
}

fn run_with(
    req: &ImportRequest,
    work_dir: &Path,
    mut run: impl FnMut(&HashMap<String, String>) -> Result<ImportReport, String>,
    mut wait: impl FnMut(Duration),
) -> ImportReport {
    if let Err(error) = validate(req) {
        return failed(error);
    }
    let prep = match run(&destination_values(req, "prepare")) {
        Ok(report) if report.is_ok() => report,
        Ok(report) => return report,
        Err(error) => {
            return uncertain(format!(
                "Prepare did not complete: {error}. Check Altium before retrying."
            ))
        }
    };
    let mut values = finish_values(req, &prep, &ImportReport::default());
    let reopen = |run: &mut dyn FnMut(&HashMap<String, String>) -> Result<ImportReport, String>,
                  values: &HashMap<String, String>|
     -> Result<ImportReport, String> {
        let mut job = values.clone();
        job.insert("Action".into(), "reopen".into());
        run(&job)
    };
    if values["ReopenSch"] == "1" || values["ReopenPcb"] == "1" {
        let mut closed = false;
        for _ in 0..5 {
            wait(Duration::from_millis(1500));
            match run(&destination_values(req, "prepare")) {
                Ok(again) if again.is_ok() && !again.flags.values().any(|v| v == "1") => { closed = true; break; }
                Ok(again) if again.is_ok() => continue,
                Ok(mut again) => {
                    again.warnings.push("Previously open libraries may need to be reopened manually; check Altium before retrying.".into());
                    again.requires_intervention = true;
                    return again;
                }
                Err(error) => return uncertain(format!("Could not confirm destination libraries closed: {error}. Check Altium before retrying.")),
            }
        }
        if !closed {
            let mut report =
                failed("Altium did not close the destination libraries; import cancelled.".into());
            match reopen(&mut run, &values) {
                Ok(r) if r.is_ok() => {}
                Ok(r) => {
                    report.warnings.extend(r.errors);
                    report.requires_intervention = true;
                }
                Err(e) => {
                    report
                        .warnings
                        .push(format!("Could not reopen libraries: {e}"));
                    report.requires_intervention = true;
                }
            }
            return report;
        }
    }
    let backups = match backup(req, work_dir) {
        Ok(backups) => backups,
        Err(error) => {
            let mut report = failed(error);
            if values["ReopenSch"] == "1" || values["ReopenPcb"] == "1" {
                match reopen(&mut run, &values) {
                    Ok(r) if r.is_ok() => {}
                    Ok(r) => {
                        report.warnings.extend(r.errors);
                        report.requires_intervention = true;
                    }
                    Err(e) => {
                        report
                            .warnings
                            .push(format!("Could not reopen libraries: {e}"));
                        report.requires_intervention = true;
                    }
                }
            }
            return report;
        }
    };
    let mut report = match run(&job_values(req)) {
        Ok(report) => report,
        Err(error) => {
            let mut report = uncertain(format!("Import result is unknown: {error}. Do not retry until Altium finishes; inspect the libraries and backups."));
            report.backups = backups;
            return report;
        }
    };
    report.backups = backups;
    values = finish_values(req, &prep, &report);
    wait(Duration::from_secs(2));
    let mut finish = run(&values);
    if finish
        .as_ref()
        .is_ok_and(|f| f.flags.get("REFRESHED").is_some_and(|v| v == "1"))
    {
        if let Ok(first) = &finish {
            report
                .warnings
                .extend(first.errors.iter().chain(&first.warnings).cloned());
        }
        wait(Duration::from_millis(1500));
        values.insert("Refresh".into(), "0".into());
        finish = run(&values);
    }
    match finish {
        Ok(fin) if fin.is_ok() => {
            report.cache = fin.cache;
            report.warnings.extend(fin.warnings);
        }
        Ok(fin) => {
            report.warnings.extend(fin.errors);
            report.warnings.extend(fin.warnings);
            report.warnings.push("Altium could not finish closing the imported libraries. Check Altium before saving them.".into());
            report.requires_intervention = true;
            return report;
        }
        Err(error) => {
            report.warnings.push(format!("Finish result is unknown: {error}. Do not save the hidden libraries; check Altium before retrying."));
            report.requires_intervention = true;
            return report;
        }
    }
    if values["ReopenSch"] == "1" || values["ReopenPcb"] == "1" {
        wait(Duration::from_millis(1500));
        match reopen(&mut run, &values) {
            Ok(r) if r.is_ok() => report.warnings.extend(r.warnings),
            Ok(r) => {
                report.warnings.extend(r.errors);
                report.requires_intervention = true;
            }
            Err(error) => {
                report
                    .warnings
                    .push(format!("Could not reopen libraries: {error}"));
                report.requires_intervention = true;
            }
        }
    }
    report
}
