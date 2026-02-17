-- Add invoice_pdf_url to invoices table
ALTER TABLE invoices ADD COLUMN IF NOT EXISTS invoice_pdf_url VARCHAR(500);
