CREATE INDEX IF NOT EXISTS idx_documents_status ON documents(status);
CREATE INDEX IF NOT EXISTS idx_documents_url ON documents(url);
CREATE INDEX IF NOT EXISTS idx_documents_document_kind ON documents(document_kind);
CREATE INDEX IF NOT EXISTS idx_documents_company ON documents(company);
CREATE INDEX IF NOT EXISTS idx_documents_incident_date ON documents(incident_date);
CREATE INDEX IF NOT EXISTS idx_documents_is_relevant ON documents(is_relevant);
CREATE INDEX IF NOT EXISTS idx_documents_updated_at ON documents(updated_at);

CREATE INDEX IF NOT EXISTS idx_documents_incident_categories ON documents USING GIN (incident_categories);
CREATE INDEX IF NOT EXISTS idx_documents_tech_stack ON documents USING GIN (tech_stack);
CREATE INDEX IF NOT EXISTS idx_documents_infrastructure ON documents USING GIN (infrastructure);
CREATE INDEX IF NOT EXISTS idx_documents_key_terms ON documents USING GIN (key_terms);