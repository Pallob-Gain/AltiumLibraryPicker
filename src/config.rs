use std::collections::HashMap;
use std::fs;
use std::path::{Path, PathBuf};

use serde_json::{Map, Value};

pub fn app_dir() -> PathBuf {
    if let Ok(path) = std::env::var("ALM_HOME") {
        return ensure_dir(PathBuf::from(path));
    }
    if let Some(appdata) = std::env::var_os("APPDATA") {
        return ensure_dir(PathBuf::from(appdata).join("AltiumLibraryManager"));
    }
    let mut path = dirs::data_local_dir().unwrap_or_else(|| std::env::temp_dir());
    path.push("AltiumLibraryManager");
    ensure_dir(path)
}

fn ensure_dir(path: PathBuf) -> PathBuf {
    let _ = fs::create_dir_all(&path);
    path
}

pub struct Settings {
    path: PathBuf,
    data: Map<String, Value>,
}

impl Settings {
    pub fn load() -> Self {
        let path = app_dir().join("settings.json");
        let mut data = default_settings();
        if let Ok(text) = fs::read_to_string(&path) {
            if let Ok(Value::Object(loaded)) = serde_json::from_str(&text) {
                for (key, value) in loaded {
                    data.insert(key, value);
                }
            }
        }
        Self { path, data }
    }

    pub fn get(&self, key: &str) -> Option<&Value> {
        self.data.get(key)
    }

    pub fn get_string(&self, key: &str) -> String {
        self.data
            .get(key)
            .and_then(Value::as_str)
            .unwrap_or_default()
            .to_owned()
    }

    pub fn get_bool(&self, key: &str, default: bool) -> bool {
        self.data
            .get(key)
            .and_then(Value::as_bool)
            .unwrap_or(default)
    }

    pub fn set(&mut self, key: &str, value: impl Into<Value>) {
        self.data.insert(key.to_owned(), value.into());
        let _ = atomic_write_json(&self.path, &Value::Object(self.data.clone()));
    }
}

fn default_settings() -> Map<String, Value> {
    let pairs: HashMap<&str, Value> = HashMap::from([
        ("schlib", Value::String(String::new())),
        ("pcblib", Value::String(String::new())),
        ("last_mpn", Value::String(String::new())),
        ("last_download_dir", Value::String(String::new())),
        ("on_conflict", Value::String("skip".to_owned())),
        ("link_mode", Value::String("name".to_owned())),
        ("x2_path", Value::String(String::new())),
        ("cse_watch_dir", Value::String(String::new())),
        ("cse_auto_import", Value::Bool(true)),
    ]);
    pairs
        .into_iter()
        .map(|(key, value)| (key.to_owned(), value))
        .collect()
}

fn atomic_write_json(path: &Path, value: &Value) -> std::io::Result<()> {
    let tmp = path.with_extension("json.tmp");
    fs::write(
        &tmp,
        serde_json::to_string_pretty(value).unwrap_or_default(),
    )?;
    fs::rename(tmp, path)
}
