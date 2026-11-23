use serde::{Deserialize, Serialize};

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Availability {
    Yes,
    No,
    Unknown,
}

impl Availability {
    pub fn as_str(self) -> &'static str {
        match self {
            Availability::Yes => "Yes",
            Availability::No => "No",
            Availability::Unknown => "?",
        }
    }
}

#[derive(Clone, Copy, Debug, PartialEq, Eq, Serialize, Deserialize)]
pub enum Match {
    Exact,
    Similar,
}

impl Match {
    pub fn as_str(self) -> &'static str {
        match self {
            Match::Exact => "Exact",
            Match::Similar => "Similar - NOT exact",
        }
    }
}

#[derive(Clone, Debug, Serialize, Deserialize)]
pub struct SearchResult {
    pub query: String,
    pub mpn: String,
    pub manufacturer: String,
    pub source: String,
    pub source_kind: String,
    pub description: String,
    pub symbol: Availability,
    pub footprint: Availability,
    pub model3d: Availability,
    pub altium_native: Availability,
    pub download: Availability,
    pub lcsc: String,
    pub datasheet_url: String,
    pub source_url: String,
    pub supplier: String,
    pub supplier_pn: String,
    pub package: String,
    pub stock: Option<i64>,
    pub match_status: Match,
    pub score: i32,
    pub notes: Vec<String>,
}

impl SearchResult {
    pub fn from_lcsc(query: &str, part: &crate::lcsc::LcscPart) -> Self {
        Self {
            query: query.to_owned(),
            mpn: part.mpn.clone(),
            manufacturer: part.manufacturer.clone(),
            source: "LCSC".to_owned(),
            source_kind: "api".to_owned(),
            description: part.description.clone(),
            symbol: Availability::Unknown,
            footprint: Availability::Unknown,
            model3d: Availability::Unknown,
            altium_native: Availability::Unknown,
            download: Availability::Unknown,
            lcsc: part.code.clone(),
            datasheet_url: part.datasheet_url.clone(),
            source_url: part.url.clone(),
            supplier: "LCSC".to_owned(),
            supplier_pn: part.code.clone(),
            package: part.package.clone(),
            stock: Some(part.stock),
            match_status: Match::Similar,
            score: 0,
            notes: Vec::new(),
        }
    }
}

#[derive(Clone, Debug)]
pub struct ProviderStatus {
    pub provider: String,
    pub state: ProviderState,
    pub count: usize,
    pub message: String,
    pub url: String,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ProviderState {
    Ok,
    Error,
    Manual,
}

impl ProviderState {
    pub fn as_str(self) -> &'static str {
        match self {
            ProviderState::Ok => "Searched",
            ProviderState::Error => "Error",
            ProviderState::Manual => "Manual - not searched",
        }
    }
}

#[derive(Clone, Debug)]
pub struct SearchOutcome {
    pub query: String,
    pub results: Vec<SearchResult>,
    pub statuses: Vec<ProviderStatus>,
    pub recommendation: String,
    pub warnings: Vec<String>,
}
