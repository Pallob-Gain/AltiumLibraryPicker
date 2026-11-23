use std::fs::{self, File, OpenOptions};
use std::io::{self, Read};
use std::path::{Path, PathBuf};
use std::time::Duration;

const MAX_MEMBERS: usize = 2_000;
const MAX_EXPANDED: u64 = 500 * 1024 * 1024;
const MAX_SCAN_ENTRIES: usize = 20_000;

#[derive(Debug, Default)]
pub struct SourcePackage {
    pub schlib: Option<PathBuf>,
    pub pcblib: Option<PathBuf>,
    pub intlib: Option<PathBuf>,
    pub step_files: Vec<PathBuf>,
    pub notes: Vec<String>,
}

fn safe_member_path(name: &str) -> Result<PathBuf, String> {
    let normalized = name.replace('\\', "/");
    if normalized.starts_with('/') || normalized.contains(':') || normalized.contains('\0') {
        return Err(format!("Unsafe archive path: {name}"));
    }
    let mut result = PathBuf::new();
    for component in normalized.split('/') {
        match component {
            "" | "." => {}
            ".." => return Err(format!("Unsafe archive path: {name}")),
            _ if component.ends_with(['.', ' ']) => {
                return Err(format!("Unsafe archive path: {name}"));
            }
            _ => result.push(component),
        }
    }
    if result.as_os_str().is_empty() {
        return Err("Archive contains an empty path.".to_owned());
    }
    Ok(result)
}

pub fn extract_zip(path: &Path, root: &Path) -> Result<PathBuf, String> {
    let file = File::open(path).map_err(|e| format!("Cannot open archive: {e}"))?;
    let mut archive =
        zip::ZipArchive::new(file).map_err(|e| format!("Invalid ZIP archive: {e}"))?;
    if archive.len() > MAX_MEMBERS {
        return Err(format!("Archive has more than {MAX_MEMBERS} entries."));
    }
    let mut total = 0u64;
    for i in 0..archive.len() {
        let entry = archive
            .by_index(i)
            .map_err(|e| format!("Cannot read ZIP entry: {e}"))?;
        safe_member_path(entry.name())?;
        if entry
            .unix_mode()
            .is_some_and(|mode| mode & 0o170000 == 0o120000)
        {
            return Err(format!("Symbolic link in archive: {}", entry.name()));
        }
        total = total
            .checked_add(entry.size())
            .ok_or("Archive size overflow")?;
        if total > MAX_EXPANDED {
            return Err("Archive expands to more than 500 MB.".to_owned());
        }
    }
    fs::create_dir_all(root).map_err(|e| format!("Cannot create extraction folder: {e}"))?;
    let dest = root.join(format!(
        "package-{}-{}",
        std::process::id(),
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("Invalid timestamp")?
    ));
    fs::create_dir(&dest).map_err(|e| format!("Cannot create extraction directory: {e}"))?;
    let mut written = 0u64;
    for i in 0..archive.len() {
        let mut entry = archive
            .by_index(i)
            .map_err(|e| format!("Cannot read ZIP entry: {e}"))?;
        let target = dest.join(safe_member_path(entry.name())?);
        if entry.is_dir() {
            fs::create_dir_all(&target)
                .map_err(|e| format!("Cannot create archive folder: {e}"))?;
            continue;
        }
        let parent = target.parent().ok_or("Invalid archive path")?;
        fs::create_dir_all(parent).map_err(|e| format!("Cannot create archive folder: {e}"))?;
        let mut output = OpenOptions::new()
            .write(true)
            .create_new(true)
            .open(&target)
            .map_err(|e| format!("Cannot create extracted file {}: {e}", target.display()))?;
        let remaining = MAX_EXPANDED - written;
        let copied = io::copy(&mut (&mut entry).take(remaining + 1), &mut output)
            .map_err(|e| format!("Cannot extract {}: {e}", entry.name()))?;
        if copied > remaining {
            return Err("Archive expands to more than 500 MB.".to_owned());
        }
        written += copied;
    }
    Ok(dest)
}

fn scan(folder: &Path) -> Result<SourcePackage, String> {
    let mut stack = vec![folder.to_path_buf()];
    let mut files = Vec::new();
    let mut count = 0usize;
    while let Some(dir) = stack.pop() {
        for item in fs::read_dir(&dir).map_err(|e| format!("Cannot read {}: {e}", dir.display()))? {
            let item = item.map_err(|e| format!("Cannot read folder entry: {e}"))?;
            count += 1;
            if count > MAX_SCAN_ENTRIES {
                return Err("Source folder contains too many entries.".to_owned());
            }
            let kind = item
                .file_type()
                .map_err(|e| format!("Cannot inspect folder entry: {e}"))?;
            if kind.is_dir() {
                stack.push(item.path());
            } else if kind.is_file() {
                files.push(item.path());
            }
        }
    }
    files.sort();
    let mut package = SourcePackage::default();
    for file in files {
        let ext = file
            .extension()
            .and_then(|v| v.to_str())
            .unwrap_or_default()
            .to_ascii_lowercase();
        let slot = match ext.as_str() {
            "schlib" => Some(&mut package.schlib),
            "pcblib" => Some(&mut package.pcblib),
            "intlib" => Some(&mut package.intlib),
            "step" | "stp" => {
                package.step_files.push(file.clone());
                None
            }
            _ => None,
        };
        if let Some(slot) = slot {
            if slot.is_none() {
                *slot = Some(file);
            } else {
                package.notes.push(format!(
                    "Multiple .{ext} files found; check the selected library."
                ));
            }
        }
    }
    if package.schlib.is_some() && package.pcblib.is_some() {
        package.intlib = None;
    }
    Ok(package)
}

pub fn resolve(path: &Path, extract_root: &Path) -> Result<SourcePackage, String> {
    let extension = path
        .extension()
        .and_then(|v| v.to_str())
        .unwrap_or_default()
        .to_ascii_lowercase();
    if path.is_dir() {
        return scan(path);
    }
    match extension.as_str() {
        "zip" => {
            let extracted = extract_zip(path, extract_root)?;
            let mut result = scan(&extracted)?;
            result
                .notes
                .push(format!("Extracted to {}", extracted.display()));
            Ok(result)
        }
        "schlib" | "pcblib" => {
            let mut result = scan(path.parent().ok_or("Source library has no parent folder")?)?;
            if extension == "schlib" {
                result.schlib = Some(path.to_path_buf());
            } else {
                result.pcblib = Some(path.to_path_buf());
            }
            Ok(result)
        }
        "intlib" => Ok(SourcePackage {
            intlib: Some(path.to_path_buf()),
            ..SourcePackage::default()
        }),
        "libpkg" => {
            let parent = path
                .parent()
                .ok_or("Library package has no parent folder")?;
            let contents = fs::read_to_string(path)
                .map_err(|e| format!("Cannot read LibPkg {}: {e}", path.display()))?;
            let mut package = scan(parent)?;
            let mut sch = None;
            let mut pcb = None;
            for line in contents.lines() {
                if let Some(value) = line.strip_prefix("DocumentPath=") {
                    let document = parent.join(value.trim());
                    if document.is_file() {
                        let ext = document
                            .extension()
                            .and_then(|v| v.to_str())
                            .unwrap_or_default();
                        if ext.eq_ignore_ascii_case("schlib") && sch.is_none() {
                            sch = Some(document);
                        } else if ext.eq_ignore_ascii_case("pcblib") && pcb.is_none() {
                            pcb = Some(document);
                        }
                    }
                }
            }
            if sch.is_some() {
                package.schlib = sch;
            }
            if pcb.is_some() {
                package.pcblib = pcb;
            }
            Ok(package)
        }
        _ => Err(
            "Unsupported source. Select a ZIP, SchLib, PcbLib, IntLib, LibPkg or folder."
                .to_owned(),
        ),
    }
}

pub fn resolve_with_intlib(
    path: &Path,
    work_dir: &Path,
    x2_override: &str,
) -> Result<SourcePackage, String> {
    let package = resolve(path, &work_dir.join("extracted"))?;
    extract_integrated(package, &work_dir.join("extracted"), |intlib, output| {
        let x2 = crate::altium::find_x2(x2_override).ok_or(
            "Altium Designer is required to extract IntLib files, but X2.EXE was not found.",
        )?;
        let values = std::collections::HashMap::from([
            ("Action".to_owned(), "extract".to_owned()),
            ("IntLib".to_owned(), intlib.to_string_lossy().into_owned()),
            ("OutDir".to_owned(), output.to_string_lossy().into_owned()),
        ]);
        let report = crate::altium::run_job(&x2, work_dir, &values, Duration::from_secs(240))
            .map_err(|e| {
                format!("IntLib extraction did not complete: {e}. Check Altium before retrying.")
            })?;
        if !report.is_ok() {
            return Err(format!(
                "Altium could not extract the IntLib: {}",
                report.errors.join("; ")
            ));
        }
        Ok(report.warnings)
    })
}

fn extract_integrated(
    mut package: SourcePackage,
    root: &Path,
    extract: impl FnOnce(&Path, &Path) -> Result<Vec<String>, String>,
) -> Result<SourcePackage, String> {
    let Some(intlib) = package.intlib.take() else {
        return Ok(package);
    };
    if package.schlib.is_some() && package.pcblib.is_some() {
        return Ok(package);
    }
    if intlib.to_string_lossy().contains(['\r', '\n']) {
        return Err("IntLib paths cannot contain line breaks.".into());
    }
    let mut file =
        File::open(&intlib).map_err(|e| format!("Cannot open {}: {e}", intlib.display()))?;
    let mut header = [0; 8];
    file.read_exact(&mut header)
        .map_err(|e| format!("Cannot read {}: {e}", intlib.display()))?;
    if header != [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1] {
        return Err(format!("Not a binary Altium IntLib: {}", intlib.display()));
    }
    fs::create_dir_all(root).map_err(|e| format!("Cannot create extraction folder: {e}"))?;
    let output = root.join(format!(
        "intlib-{}-{}",
        std::process::id(),
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("Invalid timestamp")?
    ));
    fs::create_dir(&output).map_err(|e| format!("Cannot create IntLib extraction folder: {e}"))?;
    let warnings = extract(&intlib, &output)?;
    let extracted = scan(&output)?;
    if extracted.schlib.is_none() && extracted.pcblib.is_none() {
        return Err(format!(
            "Altium returned no source libraries from {}.",
            intlib.display()
        ));
    }
    package.schlib = extracted.schlib;
    package.pcblib = extracted.pcblib;
    package.step_files.extend(extracted.step_files);
    package.notes.extend(extracted.notes);
    package.notes.extend(warnings);
    package.notes.push(format!(
        "Extracted {} to {}",
        intlib.display(),
        output.display()
    ));
    Ok(package)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use zip::write::SimpleFileOptions;

    #[test]
    fn intlib_extraction_uses_altium_results_and_surfaces_failures() {
        let root = std::env::temp_dir().join(format!(
            "alm-intlib-test-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        fs::create_dir(&root).unwrap();
        let intlib = root.join("source.IntLib");
        fs::write(&intlib, [0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1]).unwrap();
        let package = SourcePackage {
            intlib: Some(intlib.clone()),
            ..Default::default()
        };
        let result = extract_integrated(package, &root.join("out"), |input, output| {
            assert_eq!(input, intlib);
            fs::write(output.join("source.SchLib"), b"sch").unwrap();
            fs::write(output.join("source.PcbLib"), b"pcb").unwrap();
            Ok(vec!["extraction warning".into()])
        })
        .unwrap();
        assert!(result.schlib.unwrap().is_file());
        assert!(result.pcblib.unwrap().is_file());
        assert!(result.notes.contains(&"extraction warning".into()));
        let package = SourcePackage {
            intlib: Some(intlib),
            ..Default::default()
        };
        assert!(extract_integrated(package, &root.join("out"), |_, _| Err(
            "Altium failed".into()
        ))
        .unwrap_err()
        .contains("Altium failed"));
        fs::remove_dir_all(root).unwrap();
    }

    #[test]
    fn archive_paths_reject_windows_and_unix_traversal() {
        for path in [
            "../evil",
            "folder\\..\\evil",
            "folder\\.. \\evil",
            "C:\\evil",
            "/absolute",
            "file:stream",
        ] {
            assert!(safe_member_path(path).is_err(), "{path}");
        }
        assert_eq!(
            safe_member_path("Altium\\part.SchLib").unwrap(),
            PathBuf::from("Altium").join("part.SchLib")
        );
    }

    #[test]
    fn zip_extracts_libraries_and_rejects_traversal() {
        let root = std::env::temp_dir().join(format!(
            "alm-zip-test-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        fs::create_dir(&root).unwrap();
        let archive_path = root.join("good.zip");
        let mut zip = zip::ZipWriter::new(File::create(&archive_path).unwrap());
        zip.start_file("Altium/P.SchLib", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(&[0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1])
            .unwrap();
        zip.start_file("Altium/P.PcbLib", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(&[0xd0, 0xcf, 0x11, 0xe0, 0xa1, 0xb1, 0x1a, 0xe1])
            .unwrap();
        zip.finish().unwrap();
        let extracts = root.join("extracts");
        let package = resolve(&archive_path, &extracts).unwrap();
        let sch = package.schlib.unwrap();
        let pcb = package.pcblib.unwrap();
        assert_eq!(sch.file_name().unwrap(), "P.SchLib");
        assert_eq!(pcb.file_name().unwrap(), "P.PcbLib");
        let subfolder = sch.parent().unwrap().to_path_buf();
        let extracted = subfolder.parent().unwrap().to_path_buf();
        fs::remove_file(sch).unwrap();
        fs::remove_file(pcb).unwrap();
        fs::remove_dir(subfolder).unwrap();
        fs::remove_dir(extracted).unwrap();
        fs::remove_dir(&extracts).unwrap();
        fs::remove_file(&archive_path).unwrap();

        let bad = root.join("bad.zip");
        let mut zip = zip::ZipWriter::new(File::create(&bad).unwrap());
        zip.start_file("..\\evil.txt", SimpleFileOptions::default())
            .unwrap();
        zip.write_all(b"bad").unwrap();
        zip.finish().unwrap();
        assert!(extract_zip(&bad, &extracts).is_err());
        assert!(!extracts.exists());
        fs::remove_file(bad).unwrap();
        fs::remove_dir(root).unwrap();
    }
}
