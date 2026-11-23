pub mod altium;
pub mod config;
pub mod credentials;
pub mod cse;
pub mod db;
pub mod downloads;
pub mod easyeda;
pub mod importer;
pub mod lcsc;
pub mod models;
pub mod ranking;
pub mod schlib;
pub mod snapeda;
pub mod sources;
pub mod ui;

#[cfg(test)]
mod tests {
    use super::models::Match;
    use super::ranking::{classify, normalize_mpn};

    #[test]
    fn mpn_matching_ignores_case_and_whitespace_only() {
        assert_eq!(normalize_mpn(" ne555 dr "), "NE555DR");
        assert_eq!(classify("MCP23017-E/ML", " mcp23017-e/ml "), Match::Exact);
        assert_eq!(classify("MCP23017-E/ML", "MCP23017T-E/ML"), Match::Similar);
    }
}
