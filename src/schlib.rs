use std::collections::HashMap;
use std::io::Read;
use std::path::Path;

use crate::ranking::normalize_mpn;

const MAX_STREAM: u64 = 100 * 1024 * 1024;

#[derive(Debug, PartialEq, Eq)]
pub struct Symbol {
    pub name: String,
    pub description: String,
    pub footprints: Vec<String>,
}

pub fn list_symbols(path: &Path) -> Result<Vec<Symbol>, String> {
    let mut compound = cfb::open(path)
        .map_err(|e| format!("Cannot read schematic library {}: {e}", path.display()))?;
    let storages: Vec<_> = compound
        .read_root_storage()
        .filter(|entry| entry.is_storage())
        .map(|entry| (entry.name().to_owned(), entry.path().to_path_buf()))
        .collect();
    let mut symbols = Vec::new();
    for (name, storage) in storages {
        let data = compound
            .read_storage(&storage)
            .map_err(|e| format!("Cannot read {name}: {e}"))?
            .find(|entry| entry.is_stream() && entry.name() == "Data")
            .map(|entry| (entry.path().to_path_buf(), entry.len()));
        let Some((stream_path, size)) = data else {
            continue;
        };
        if size > MAX_STREAM {
            return Err(format!("Symbol data stream for {name} is too large."));
        }
        let mut bytes = Vec::new();
        compound
            .open_stream(&stream_path)
            .and_then(|mut stream| stream.read_to_end(&mut bytes))
            .map_err(|e| format!("Cannot read symbol {name}: {e}"))?;
        let mut position = 0usize;
        let mut symbol: Option<Symbol> = None;
        while position + 4 <= bytes.len() {
            let header = u32::from_le_bytes(bytes[position..position + 4].try_into().unwrap());
            let length = (header & 0x00ff_ffff) as usize;
            position += 4;
            if length > bytes.len() - position {
                return Err(format!("Truncated symbol record in {name}."));
            }
            let record = &bytes[position..position + length];
            position += length;
            if header >> 24 != 0 {
                continue;
            }
            let end = record.iter().rposition(|b| *b != 0).map_or(0, |i| i + 1);
            let (decoded, _, _) = encoding_rs::WINDOWS_1252.decode(&record[..end]);
            let mut fields = HashMap::new();
            for part in decoded.split('|') {
                if let Some((key, value)) = part.split_once('=') {
                    fields.entry(key).or_insert(value);
                }
            }
            match fields.get("RECORD").copied() {
                Some("1") if symbol.is_none() => {
                    symbol = Some(Symbol {
                        name: fields
                            .get("LibReference")
                            .copied()
                            .unwrap_or(&name)
                            .to_owned(),
                        description: fields
                            .get("ComponentDescription")
                            .copied()
                            .unwrap_or("")
                            .to_owned(),
                        footprints: Vec::new(),
                    });
                }
                Some("45")
                    if fields
                        .get("ModelType")
                        .is_some_and(|s| s.eq_ignore_ascii_case("PCBLIB")) =>
                {
                    if let (Some(symbol), Some(model)) = (&mut symbol, fields.get("ModelName")) {
                        if !model.is_empty() && !symbol.footprints.iter().any(|f| f == model) {
                            symbol.footprints.push((*model).to_owned());
                        }
                    }
                }
                _ => {}
            }
        }
        if let Some(symbol) = symbol {
            symbols.push(symbol);
        }
    }
    symbols.sort_by(|a, b| a.name.cmp(&b.name));
    Ok(symbols)
}

pub fn best_match<'a>(symbols: &'a [Symbol], candidates: &[&str]) -> Option<&'a str> {
    for candidate in candidates {
        let normalized = normalize_mpn(candidate);
        if normalized.is_empty() {
            continue;
        }
        let exact: Vec<_> = symbols
            .iter()
            .filter(|s| normalize_mpn(&s.name) == normalized)
            .collect();
        if exact.len() == 1 {
            return Some(&exact[0].name);
        }
        let parts: Vec<_> = symbols
            .iter()
            .filter(|s| {
                let name = normalize_mpn(&s.name);
                name.split('_').any(|p| p == normalized)
                    || name.starts_with(&format!("{normalized}_"))
            })
            .collect();
        if parts.len() == 1 {
            return Some(&parts[0].name);
        }
    }
    None
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reads_real_schlib_symbols_and_footprints() {
        let path = Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests")
            .join("fixtures")
            .join("ThreeSymbols.SchLib");
        let symbols = list_symbols(&path).unwrap();
        assert_eq!(
            symbols.iter().map(|s| s.name.as_str()).collect::<Vec<_>>(),
            ["RT-C06-A1B-AC_C18221846", "STDRIVE141TR", "STDRIVE141TR_1"]
        );
        assert_eq!(symbols[1].footprints, ["VFQFN32-5x5_STM"]);
        assert_eq!(
            best_match(&symbols, &["stdrive141tr"]),
            Some("STDRIVE141TR")
        );
        assert_eq!(
            best_match(&symbols, &["C18221846"]),
            Some("RT-C06-A1B-AC_C18221846")
        );
        assert_eq!(best_match(&symbols, &["RT-C06"]), None);
        assert!(list_symbols(Path::new("missing.SchLib")).is_err());
    }
}
