#![windows_subsystem = "windows"]

use std::error::Error;
use std::ffi::OsStr;
use std::io::Cursor;
use std::path::Path;
use std::process::Command;
use windows_sys::Win32::UI::WindowsAndMessaging::{MessageBoxW, MB_ICONERROR, MB_OK};

const RUNTIME_ARCHIVE: &[u8] = include_bytes!(env!("ALM_RUNTIME_ARCHIVE"));
const APP_EXE: &str = "altium-library-manager.exe";

fn extract_runtime(data: &[u8], destination: &Path) -> Result<(), Box<dyn Error>> {
    let mut archive = zip::ZipArchive::new(Cursor::new(data))?;
    archive.extract(destination)?;
    if !destination.join(APP_EXE).is_file() {
        return Err("The embedded runtime does not contain the application executable.".into());
    }
    Ok(())
}

fn run() -> Result<(), Box<dyn Error>> {
    let mut args = std::env::args_os().skip(1).peekable();
    if args
        .next_if(|arg| arg == OsStr::new("--extract-runtime"))
        .is_some()
    {
        let destination = args
            .next()
            .ok_or("Usage: AltiumLibraryManager.exe --extract-runtime <new-directory>")?;
        if args.next().is_some() {
            return Err("Usage: AltiumLibraryManager.exe --extract-runtime <new-directory>".into());
        }
        std::fs::create_dir(&destination)?;
        extract_runtime(RUNTIME_ARCHIVE, Path::new(&destination))?;
        return Ok(());
    }

    let runtime = tempfile::Builder::new()
        .prefix("altium-library-manager-")
        .tempdir()?;
    extract_runtime(RUNTIME_ARCHIVE, runtime.path())?;
    let status = Command::new(runtime.path().join(APP_EXE))
        .args(args)
        .status()?;
    runtime.close()?;
    if !status.success() {
        return Err(format!("The application exited with {status}.").into());
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;
    use zip::write::SimpleFileOptions;

    fn archive(files: &[(&str, &[u8])]) -> Vec<u8> {
        let mut writer = zip::ZipWriter::new(Cursor::new(Vec::new()));
        for (name, contents) in files {
            writer
                .start_file(*name, SimpleFileOptions::default())
                .unwrap();
            writer.write_all(contents).unwrap();
        }
        writer.finish().unwrap().into_inner()
    }

    #[test]
    fn extracts_the_application_and_runtime_files() {
        let destination = tempfile::tempdir().unwrap();
        let data = archive(&[(APP_EXE, b"app"), ("share/license.txt", b"license")]);
        extract_runtime(&data, destination.path()).unwrap();
        assert_eq!(
            std::fs::read(destination.path().join(APP_EXE)).unwrap(),
            b"app"
        );
        assert_eq!(
            std::fs::read(destination.path().join("share/license.txt")).unwrap(),
            b"license"
        );
    }

    #[test]
    fn rejects_invalid_archives_and_archives_without_the_application() {
        let destination = tempfile::tempdir().unwrap();
        assert!(extract_runtime(b"not a zip", destination.path()).is_err());
        let data = archive(&[("license.txt", b"license")]);
        assert!(extract_runtime(&data, destination.path()).is_err());
    }
}

fn main() {
    if let Err(error) = run() {
        let message: Vec<u16> = format!("Could not run Altium Library Manager:\n{error}")
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect();
        let title: Vec<u16> = "Altium Library Manager"
            .encode_utf16()
            .chain(std::iter::once(0))
            .collect();
        unsafe {
            MessageBoxW(
                std::ptr::null_mut(),
                message.as_ptr(),
                title.as_ptr(),
                MB_OK | MB_ICONERROR,
            );
        }
        std::process::exit(1);
    }
}
