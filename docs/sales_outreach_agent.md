# Sales Lead + Outreach Draft Agent

This agent is separate from the YouTube dashboard. It does not send Instagram DMs, WhatsApp messages, LinkedIn DMs, or emails automatically.

It creates a lead research and outreach draft sheet for:

- Andhra Pradesh
- Telangana
- Karnataka
- Mumbai

Target categories:

- film PR agencies
- political campaign consultants
- digital marketing agencies
- celebrity management agencies
- movie promotion agencies
- public relations agencies

## Run

```bash
python3 scripts/sales_outreach_agent.py
```

Output:

```text
data/sales_outreach_leads.csv
```

## Optional Google Sheet

```bash
python3 scripts/sales_outreach_agent.py --write-sheet
```

## With Your Own Leads

Create a CSV with columns like:

```text
state,city,category,company_name,website,instagram,linkedin,office_phone,public_email,decision_maker,decision_maker_role
```

Then run:

```bash
python3 scripts/sales_outreach_agent.py --input-csv data/my_leads.csv
```

## What It Produces

- fit score
- pitch angle
- email subject
- email body
- Instagram DM draft
- LinkedIn DM draft
- WhatsApp message draft
- follow-up date
- research query
- Google search URL
- Apollo search hint

Human approval is required before sending messages.

## Contact Enrichment Reality

The current default rows are research targets, not real companies. To get actual emails, phone numbers, LinkedIn URLs, and Instagram URLs, use one of these paths:

1. Add real company names/websites into the CSV, then run:

```bash
python3 scripts/enrich_sales_leads.py --write-sheet
```

2. Use Apollo/Hunter/Clay to export real companies and contacts, then run:

```bash
python3 scripts/sales_outreach_agent.py --input-csv data/apollo_export.csv --write-sheet
```

3. Manually open the `google_search_url` column, verify the company website/contact page, then paste the public contact details into the sheet.

The enrichment script extracts only public business/contact data from public websites. It does not scrape private personal data, bypass login walls, or send messages.
