use base64::{engine::general_purpose::STANDARD, Engine as _};
use std::fs;

use crate::config::app_dir;

pub struct CredentialStore {
    path: std::path::PathBuf,
}

#[cfg(windows)]
fn dpapi_protect(data: &[u8]) -> Result<Vec<u8>, String> {
    use windows_sys::Win32::Security::Cryptography::{
        CryptProtectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let input = CRYPT_INTEGER_BLOB {
        cbData: data.len().try_into().unwrap_or(u32::MAX),
        pbData: data.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: std::ptr::null_mut(),
    };
    let description: Vec<u16> = "AltiumLibraryManager\0".encode_utf16().collect();
    unsafe {
        if CryptProtectData(
            &input,
            description.as_ptr(),
            std::ptr::null(),
            std::ptr::null(),
            std::ptr::null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        ) == 0
        {
            return Err("Windows DPAPI could not protect the credential".to_owned());
        }
        let result = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        windows_sys::Win32::Foundation::LocalFree(output.pbData as *mut std::ffi::c_void);
        Ok(result)
    }
}

#[cfg(windows)]
fn dpapi_unprotect(data: &[u8]) -> Result<Vec<u8>, String> {
    use windows_sys::Win32::Security::Cryptography::{
        CryptUnprotectData, CRYPTPROTECT_UI_FORBIDDEN, CRYPT_INTEGER_BLOB,
    };

    let input = CRYPT_INTEGER_BLOB {
        cbData: data.len().try_into().unwrap_or(u32::MAX),
        pbData: data.as_ptr() as *mut u8,
    };
    let mut output = CRYPT_INTEGER_BLOB {
        cbData: 0,
        pbData: std::ptr::null_mut(),
    };
    unsafe {
        if CryptUnprotectData(
            &input,
            std::ptr::null_mut(),
            std::ptr::null(),
            std::ptr::null(),
            std::ptr::null(),
            CRYPTPROTECT_UI_FORBIDDEN,
            &mut output,
        ) == 0
        {
            return Err("Windows DPAPI could not unprotect the credential".to_owned());
        }
        let result = std::slice::from_raw_parts(output.pbData, output.cbData as usize).to_vec();
        windows_sys::Win32::Foundation::LocalFree(output.pbData as *mut std::ffi::c_void);
        Ok(result)
    }
}

#[cfg(not(windows))]
fn dpapi_protect(_data: &[u8]) -> Result<Vec<u8>, String> {
    Err("Encrypted credential storage requires Windows DPAPI".to_owned())
}

#[cfg(not(windows))]
fn dpapi_unprotect(_data: &[u8]) -> Result<Vec<u8>, String> {
    Err("Encrypted credential storage requires Windows DPAPI".to_owned())
}

impl CredentialStore {
    pub fn load() -> Self {
        Self {
            path: app_dir().join("credentials.json"),
        }
    }

    pub fn get(&self, name: &str) -> String {
        let env_name = format!("ALM_{}", name.to_uppercase());
        if let Ok(value) = std::env::var(env_name) {
            if !value.is_empty() {
                return value;
            }
        }
        let text = match fs::read_to_string(&self.path) {
            Ok(text) => text,
            Err(_) => return String::new(),
        };
        let data: serde_json::Value = match serde_json::from_str(&text) {
            Ok(value) => value,
            Err(_) => return String::new(),
        };
        data.get(name)
            .and_then(|v| v.as_str())
            .and_then(|encoded| STANDARD.decode(encoded).ok())
            .and_then(|ciphertext| dpapi_unprotect(&ciphertext).ok())
            .and_then(|plaintext| String::from_utf8(plaintext).ok())
            .unwrap_or_default()
    }

    pub fn set(&self, name: &str, value: &str) -> bool {
        let mut data: serde_json::Map<String, serde_json::Value> = fs::read_to_string(&self.path)
            .ok()
            .and_then(|text| serde_json::from_str(&text).ok())
            .unwrap_or_default();
        if value.is_empty() {
            data.remove(name);
        } else {
            match dpapi_protect(value.as_bytes()) {
                Ok(ciphertext) => {
                    data.insert(
                        name.to_owned(),
                        serde_json::Value::String(STANDARD.encode(ciphertext)),
                    );
                }
                Err(_) => return false,
            }
        }
        let tmp = self.path.with_extension("json.tmp");
        if fs::write(
            &tmp,
            serde_json::to_string_pretty(&data).unwrap_or_default(),
        )
        .is_err()
        {
            return false;
        }
        fs::rename(tmp, &self.path).is_ok()
    }
}
