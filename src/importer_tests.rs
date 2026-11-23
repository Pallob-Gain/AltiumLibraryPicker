use super::*;

fn setup() -> (PathBuf, ImportRequest) {
    let dir = std::env::temp_dir().join(format!(
        "alm-import-test-{}-{}",
        std::process::id(),
        chrono::Utc::now().timestamp_nanos_opt().unwrap()
    ));
    fs::create_dir(&dir).unwrap();
    let sch = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests")
        .join("fixtures")
        .join("ThreeSymbols.SchLib");
    let src_pcb = dir.join("src.PcbLib");
    let dest_sch = dir.join("dest.SchLib");
    let dest_pcb = dir.join("dest.PcbLib");
    fs::write(&src_pcb, [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]).unwrap();
    fs::copy(&sch, &dest_sch).unwrap();
    fs::copy(&src_pcb, &dest_pcb).unwrap();
    let req = ImportRequest {
        src_schlib: sch,
        src_pcblib: src_pcb,
        dest_schlib: dest_sch,
        dest_pcblib: dest_pcb,
        component: "STDRIVE141TR".into(),
        params: HashMap::new(),
        description: String::new(),
        conflict: "skip".into(),
        link_mode: "name".into(),
        step_file: None,
        step_placement: None,
    };
    (dir, req)
}

#[test]
fn backs_up_and_runs_separate_refresh_and_reopen_jobs() {
    let (dir, req) = setup();
    let mut jobs = Vec::new();
    let report = run_with(
        &req,
        &dir,
        |values| {
            jobs.push(values.clone());
            Ok(match jobs.len() {
                1 => ImportReport::parse("STATUS=OK\nSCH_WAS_OPEN=1"),
                2 => ImportReport::parse("STATUS=OK"),
                3 => {
                    assert_eq!(fs::read_dir(dir.join("backups")).unwrap().count(), 1);
                    ImportReport::parse(
                        "STATUS=OK\nADDED_SYMBOL=STDRIVE141TR\nLINK=STDRIVE141TR|FP|dest.PcbLib",
                    )
                }
                4 => ImportReport::parse("STATUS=OK\nREFRESHED=1"),
                5 => ImportReport::parse("STATUS=OK\nVERIFY_CACHE=FP|ok"),
                6 => ImportReport::parse("STATUS=OK"),
                _ => panic!("unexpected job"),
            })
        },
        |_| {},
    );
    assert!(report.is_ok());
    assert_eq!(report.backups.len(), 2);
    assert_eq!(
        fs::read(&report.backups[0]).unwrap(),
        fs::read(&req.dest_schlib).unwrap()
    );
    assert_eq!(
        jobs.iter()
            .map(|j| j["Action"].as_str())
            .collect::<Vec<_>>(),
        ["prepare", "prepare", "import", "finish", "finish", "reopen"]
    );
    assert_eq!(jobs[3]["Footprint1"], "FP");
    assert_eq!(jobs[4]["Refresh"], "0");
    assert_eq!(report.cache, [("FP".into(), true)]);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn rejects_same_file_and_does_not_run_on_invalid_request() {
    let (dir, mut req) = setup();
    req.dest_schlib = req.src_schlib.clone();
    let result = run_with(
        &req,
        &dir,
        |_| panic!("invalid request must not run Altium"),
        |_| {},
    );
    assert!(!result.is_ok());
    assert!(result.errors[0].contains("same file"));
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn unknown_import_result_does_not_start_another_job() {
    let (dir, req) = setup();
    let mut actions = Vec::new();
    let report = run_with(
        &req,
        &dir,
        |values| {
            actions.push(values["Action"].clone());
            if values["Action"] == "prepare" {
                Ok(ImportReport::parse("STATUS=OK"))
            } else {
                Err("timed out".into())
            }
        },
        |_| {},
    );
    assert_eq!(actions, ["prepare", "import"]);
    assert!(report.errors[0].contains("unknown"));
    assert!(report.requires_intervention);
    assert_eq!(report.backups.len(), 2);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn records_only_successful_imports() {
    let (dir, req) = setup();
    let db = crate::db::Database::open(":memory:").unwrap();
    let failed = ImportReport::parse("STATUS=ERROR\nADDED_SYMBOL=STDRIVE141TR\nERROR=save failed");
    db.record_import(&req, &failed).unwrap();
    assert!(db.find_component("STDRIVE141TR").unwrap().is_empty());
    let success = ImportReport::parse(
        "STATUS=OK\nADDED_SYMBOL=STDRIVE141TR\nLINK=STDRIVE141TR|FP|dest.PcbLib",
    );
    db.record_import(&req, &success).unwrap();
    assert_eq!(db.find_component("STDRIVE141TR").unwrap().len(), 1);
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn cse_step_placement_is_sent_in_altium_units() {
    let (dir, mut req) = setup();
    req.step_placement = Some([90.0, 0.0, 0.0, 1.0, 0.63, 0.0]);
    let values = job_values(&req);
    assert_eq!(values["StepRotX"], "90000");
    assert_eq!(values["StepDX"], "393701");
    assert_eq!(values["StepDY"], "248031");
    req.step_placement = Some([f64::NAN, 0.0, 0.0, 0.0, 0.0, 0.0]);
    assert!(validate(&req).is_err());
    fs::remove_dir_all(dir).unwrap();
}

#[test]
fn failed_backup_prevents_import_and_reopens_previously_open_library() {
    let (dir, req) = setup();
    let work_dir = dir.join("not-a-directory");
    fs::write(&work_dir, b"file").unwrap();
    let mut actions = Vec::new();
    let report = run_with(
        &req,
        &work_dir,
        |values| {
            actions.push(values["Action"].clone());
            Ok(if actions.len() == 1 {
                ImportReport::parse("STATUS=OK\nSCH_WAS_OPEN=1")
            } else {
                ImportReport::parse("STATUS=OK")
            })
        },
        |_| {},
    );
    assert!(!report.is_ok());
    assert_eq!(actions, ["prepare", "prepare", "reopen"]);
    fs::remove_dir_all(dir).unwrap();
}
