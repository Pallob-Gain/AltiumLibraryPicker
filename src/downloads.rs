use std::collections::{HashMap, HashSet};
use std::fs;
use std::path::{Path, PathBuf};
use std::time::{Duration, Instant, SystemTime};

const PARTIAL: [&str; 6] = [
    ".crdownload",
    ".part",
    ".partial",
    ".download",
    ".opdownload",
    ".tmp",
];
const MAX_FILE: u64 = 500 * 1024 * 1024;

#[derive(Clone, Debug, Eq, PartialEq, Hash)]
struct Signature {
    size: u64,
    modified: SystemTime,
}

type Listing = HashMap<PathBuf, Signature>;

fn listing(folder: &Path) -> Result<Listing, String> {
    let mut result = HashMap::new();
    for entry in fs::read_dir(folder)
        .map_err(|e| format!("Cannot read downloads folder {}: {e}", folder.display()))?
    {
        let entry = entry.map_err(|e| format!("Cannot inspect downloads folder: {e}"))?;
        if !entry.file_type().map_err(|e| e.to_string())?.is_file() {
            continue;
        }
        let metadata = entry
            .metadata()
            .map_err(|e| format!("Cannot inspect {}: {e}", entry.path().display()))?;
        result.insert(
            entry.path(),
            Signature {
                size: metadata.len(),
                modified: metadata.modified().map_err(|e| e.to_string())?,
            },
        );
    }
    Ok(result)
}

fn accepted(path: &Path) -> bool {
    path.extension().and_then(|e| e.to_str()).is_some_and(|e| {
        ["zip", "intlib", "schlib", "pcblib", "libpkg"]
            .iter()
            .any(|a| e.eq_ignore_ascii_case(a))
    })
}

fn matches_mpn(path: &Path, mpn: &str) -> bool {
    let norm = |s: &str| {
        s.chars()
            .filter(|c| c.is_ascii_alphanumeric())
            .flat_map(char::to_uppercase)
            .collect::<String>()
    };
    let mpn = norm(mpn);
    !mpn.is_empty() && norm(&path.file_stem().unwrap_or_default().to_string_lossy()).contains(&mpn)
}

fn ready(path: &Path, sig: &Signature, files: &Listing) -> bool {
    if !accepted(path) || sig.size == 0 || sig.size > MAX_FILE {
        return false;
    }
    let full = path.as_os_str().to_string_lossy().to_lowercase();
    if PARTIAL.iter().any(|s| {
        files
            .keys()
            .any(|p| p.as_os_str().to_string_lossy().to_lowercase() == format!("{full}{s}"))
    }) {
        return false;
    }
    fs::File::open(path).is_ok()
}

#[derive(Default)]
pub struct UsedDownloads(HashSet<(PathBuf, Signature)>);

pub struct ClaimedDownload {
    pub path: PathBuf,
    original: PathBuf,
    signature: Signature,
}

impl UsedDownloads {
    pub fn record(&mut self, claim: &ClaimedDownload) {
        self.0
            .insert((claim.original.clone(), claim.signature.clone()));
    }
}

pub struct DownloadWatcher {
    folder: PathBuf,
    mpn: String,
    baseline: Listing,
    seen: HashMap<PathBuf, (Signature, Instant)>,
    started: Instant,
}

impl DownloadWatcher {
    pub fn new(folder: &Path, mpn: &str) -> Result<Self, String> {
        Ok(Self {
            folder: folder.to_path_buf(),
            mpn: mpn.to_owned(),
            baseline: listing(folder)?,
            seen: HashMap::new(),
            started: Instant::now(),
        })
    }

    pub fn recent(&self, used: &UsedDownloads) -> Result<Option<PathBuf>, String> {
        let files = listing(&self.folder)?;
        let now = SystemTime::now();
        let mut candidates: Vec<_> = files
            .iter()
            .filter(|(p, s)| {
                !used.0.contains(&((*p).clone(), (*s).clone()))
                    && now.duration_since(s.modified).is_ok_and(|age| {
                        age >= Duration::from_secs(2) && age <= Duration::from_secs(1800)
                    })
                    && ready(p, s, &files)
            })
            .collect();
        candidates.sort_by_key(|(p, s)| (matches_mpn(p, &self.mpn), s.modified));
        Ok(candidates.last().map(|(p, _)| (*p).clone()))
    }

    pub fn poll(&mut self, used: &UsedDownloads) -> Result<Option<PathBuf>, String> {
        if self.started.elapsed() > Duration::from_secs(900) {
            return Err("No library download arrived within 15 minutes.".into());
        }
        let files = listing(&self.folder)?;
        let now = Instant::now();
        let mut candidates = Vec::new();
        for (path, sig) in &files {
            if self.baseline.get(path) == Some(sig)
                || used.0.contains(&(path.clone(), sig.clone()))
                || !accepted(path)
            {
                continue;
            }
            let seen = self
                .seen
                .entry(path.clone())
                .or_insert_with(|| (sig.clone(), now));
            if seen.0 != *sig {
                *seen = (sig.clone(), now);
            }
            if now.duration_since(seen.1) >= Duration::from_secs(2) && ready(path, sig, &files) {
                candidates.push((path, sig));
            }
        }
        candidates.sort_by_key(|(p, s)| (matches_mpn(p, &self.mpn), s.modified));
        Ok(candidates.last().map(|(p, _)| (*p).clone()))
    }
}

pub fn claim(path: &Path, work_dir: &Path) -> Result<ClaimedDownload, String> {
    let before = fs::metadata(path).map_err(|e| format!("Cannot read download: {e}"))?;
    let signature = Signature {
        size: before.len(),
        modified: before.modified().map_err(|e| e.to_string())?,
    };
    if signature.size == 0 || signature.size > MAX_FILE {
        return Err("Download is empty or larger than 500 MB.".into());
    }
    if path.extension().and_then(|e| e.to_str()).is_some_and(|e| {
        ["schlib", "pcblib", "libpkg"]
            .iter()
            .any(|a| e.eq_ignore_ascii_case(a))
    }) {
        return Ok(ClaimedDownload {
            path: path.to_owned(),
            original: path.to_owned(),
            signature,
        });
    }
    let root = work_dir.join("downloads");
    fs::create_dir_all(&root).map_err(|e| format!("Cannot create download cache: {e}"))?;
    let folder = root.join(format!(
        "{}-{}",
        std::process::id(),
        chrono::Utc::now()
            .timestamp_nanos_opt()
            .ok_or("Invalid timestamp")?
    ));
    fs::create_dir(&folder).map_err(|e| format!("Cannot create download folder: {e}"))?;
    let target = folder.join(path.file_name().ok_or("Download has no file name.")?);
    fs::copy(path, &target).map_err(|e| format!("Cannot copy download: {e}"))?;
    let after = fs::metadata(path).map_err(|e| format!("Cannot recheck download: {e}"))?;
    if after.len() != signature.size
        || after.modified().map_err(|e| e.to_string())? != signature.modified
    {
        return Err(
            "Download changed while it was being copied; wait for it to finish and try again."
                .into(),
        );
    }
    Ok(ClaimedDownload {
        path: target,
        original: path.to_owned(),
        signature,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn watcher_ignores_existing_and_partial_downloads_and_claims_only_complete_files() {
        let root = std::env::temp_dir().join(format!(
            "alm-watch-{}-{}",
            std::process::id(),
            chrono::Utc::now().timestamp_nanos_opt().unwrap()
        ));
        fs::create_dir(&root).unwrap();
        fs::write(root.join("old.zip"), b"old").unwrap();
        let mut watcher = DownloadWatcher::new(&root, "NE555").unwrap();
        let mut used = UsedDownloads::default();
        let file = root.join("NE555.zip");
        fs::write(&file, b"zip").unwrap();
        fs::write(root.join("NE555.zip.crdownload"), b"partial").unwrap();
        assert!(watcher.poll(&used).unwrap().is_none());
        watcher.seen.get_mut(&file).unwrap().1 -= Duration::from_secs(3);
        assert!(watcher.poll(&used).unwrap().is_none());
        fs::remove_file(root.join("NE555.zip.crdownload")).unwrap();
        assert_eq!(watcher.poll(&used).unwrap(), Some(file.clone()));
        let claimed = claim(&file, &root.join("cache")).unwrap();
        assert_eq!(fs::read(&claimed.path).unwrap(), b"zip");
        used.record(&claimed);
        assert!(watcher.poll(&used).unwrap().is_none());
        fs::remove_dir_all(root).unwrap();
    }
}
