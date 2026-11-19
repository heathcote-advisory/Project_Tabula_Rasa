# Google Maps mileage setup

Google address suggestions and route mileage are optional, app-wide features.
They are deliberately separate from each business profile's human Sheets/Drive
OAuth grant. When Maps is not configured or Google is
unavailable, Mileage continues to accept manual addresses and miles.

This setup creates paid Google Maps Platform access. Complete the quota and
budget steps before a live request. Pricing and product limits change; confirm
the current values in Google Cloud rather than relying on a price copied into
this repository.

## Operator checklist

1. Create or select a Google Cloud project dedicated to this deployment's Maps
   usage. A dedicated project makes its keys, quota, and billing easier to
   isolate from Sheets and Drive.
2. Link the project to the intended Cloud Billing account.
3. Enable **Maps JavaScript API**, **Places API (New)**, and **Routes API**.
   Do not enable legacy Directions or Distance Matrix APIs for this feature.
4. Create a browser API key for address autocomplete:
   - Application restriction: **Websites**.
   - Hosted referrer: the exact deployed origin, for example
     `https://books.example.test/*`.
   - Local referrers, on a separate development key where practical:
     `http://localhost:5173/*` for a configured Vite development server. Add only
     exact origins used by the approved deployment.
   - API restrictions: **Maps JavaScript API** and **Places API (New)** only.
5. Create a different server API key for route calculation:
   - API restriction: **Routes API** only.
   - For Render, apply the IP-address restriction in
     [Hosted key restrictions](#hosted-key-restrictions).
   - Never put this server key in browser component data or source control.
6. In **Google Maps Platform > Quotas**, select each API and read the effective
   limits in **Quotas & System Limits**. Check the ComputeRoutes request
   quotas for Routes, the AutocompletePlacesRequest and GetPlaceRequest quotas
   for Places (New), and the Map loads quotas for Maps JavaScript. Review
   existing consumers before lowering shared project limits. A conservative
   single-operator starting point is 10 Compute Routes requests per minute
   and 100 per day when a daily control is available. Start browser/Places
   request quotas at 60 per minute, then raise them only from observed
   legitimate use. A one-way calculation consumes one Routes request. A
   round trip consumes two because each direction is calculated separately.
7. Open the project's linked **Cloud Billing** account, then **Budgets & caps**
   (also labeled **Budgets & alerts**). Select **Create new**, choose an
   alerts-only budget, and set the project filter to the Maps project.
   Confirm the amount with the operator and set actual and forecast alert
   thresholds. Use a monitored email channel. [Cloud Billing sends budget
   emails when a configured threshold is met](https://docs.cloud.google.com/billing/docs/how-to/budgets)
   and delivery may lag by several hours. Record the receipt time once the
   recipient confirms delivery. If no alert has arrived, record delivery as
   pending. If environments share a project, record how their combined usage
   affects the quotas and budget.
8. Budget alerts do not stop usage, and cost reporting can be delayed. Verify
   the effective Routes, Places (New), and Maps JavaScript limits in Cloud
   Console for the selected project. Record the time windows and whether daily
   limits are available. The optional quota panel only reads project usage.
9. Configure deployment-specific keys through the protected API server configuration
   below and use the approved deploy procedure to activate them.
10. Before public deployment, review the checked-in [Terms of Use](TERMS_OF_USE.md)
    and [Privacy Policy](PRIVACY_POLICY.md). The application footer exposes both
    documents without requiring the application password. Confirm they still
    satisfy the current Routes API policy and reference Google's terms and
    privacy policy as required. Keep the visible Google Maps attribution beside
    Google-derived suggestions and route mileage.
11. Perform [controlled validation](#controlled-validation) separately for staging
    and production, then inspect key usage, API metrics, quotas and billing.

## Commercial browser/API configuration

The shared HTTP runtime accepts deployment-wide environment settings:

| Variable | Purpose |
| --- | --- |
| `TABULA_RASA_MAPS_BROWSER_API_KEY` | Website-restricted Maps JavaScript / Places (New) key; this key alone is exposed to the authorized browser |
| `TABULA_RASA_MAPS_ROUTES_API_KEY` | Separate server-only Routes key |
| `TABULA_RASA_MAPS_ROUTES_TIMEOUT_SECONDS` | Bounded request timeout, default 15 seconds |
| `TABULA_RASA_MAPS_REGION_CODE` | Optional two-letter autocomplete country restriction |

For an operator-bound migrated runtime, reuse its trusted application-wide
`google_maps` table below, including `autocomplete_region_code`. Do not copy
human Drive grants into Maps configuration. The optional dedicated monitoring
configuration is described below and is separate from the mileage keys.
Absent configuration preserves manual entry; equal browser/server keys suppress
the browser key so a server credential cannot accidentally cross the API.

### Hosted key restrictions

Inventory both deployed environments before changing either. The same-origin
React/FastAPI web service serves Mileage and makes the Routes request from
FastAPI. In Render, open each web service and read its region, live deploy,
domains and **Connect > Outbound**. Match the live deploy to the release SHA
at the canonical origin's `/version`. Record whether each key is configured
and identify its Google Cloud project and credential ID or label without
revealing the value.

| Environment | Browser key in Cloud Console | Server key in Cloud Console |
| --- | --- | --- |
| Staging | **Websites** with each approved staging HTTPS origin as `https://host/*`. **Restrict key** to **Maps JavaScript API** and **Places API (New)**. | **IP addresses** with every current CIDR from the staging API service's **Connect > Outbound**. **Restrict key** to **Routes API**. |
| Production | **Websites** with each approved production HTTPS origin as `https://host/*`. **Restrict key** to **Maps JavaScript API** and **Places API (New)**. | **IP addresses** with every current CIDR from the production API service's **Connect > Outbound**. **Restrict key** to **Routes API**. |

After operator approval, select the intended project in Cloud Console. Open
**APIs & Services > Credentials**, select the key by its label, set the
application and API restrictions in the matrix, save, then reopen the
credential to verify the settings. Use distinct browser and server keys and
prefer a separate pair for each environment. For proposed sharing, identify
all callers, record the combined restrictions and effects on rotation and
usage isolation, and get explicit operator approval before binding either
environment to the shared key. Keep local development origins on a separate key.

[Render outbound ranges](https://render.com/docs/outbound-ip-addresses) are
shared with other Render services, so the IP allowlist is not proof of a
specific service's identity. Oregon services in workspaces created before
January 23, 2022 may lack fixed ranges. Confirm each service independently
even when both are in the same region. If the ranges are unavailable, Google
rejects them, or caller ownership is unclear, stop and document the cause.
Any Routes-only exception without IP restrictions needs explicit operator
approval before use.

The widget uses the [Places autocomplete
element](https://developers.google.com/maps/documentation/javascript/reference/places-widget),
and restrictions follow [Google's API security
guidance](https://developers.google.com/maps/api-security-best-practices). Retain the
existing attribution, terms, privacy and transient-content policy. See
[workflow ports](browser/IMPORTS_EXPORTS_AND_INTEGRATIONS.md).

### Optional HTTP quota monitoring

The authenticated sidebar's **Google Maps API usage** expander restores the
read-only monitoring view. Opening the expander checks setup only;
**Load usage** contacts Google. **Refresh** bypasses only the monitoring cache.
Successful display rows are cached for 120 seconds per API process. There is no
automatic polling, focus refresh or quota request on an ordinary page load.
An expired displayed report is labeled stale until the user refreshes. Missing
or invalid monitoring configuration leaves lookup, routing, manual mileage and
the rest of the app usable.

For normal HTTP startup, including startup using migrated business bindings,
the operator may configure these server-only variables:

| Variable | Purpose |
| --- | --- |
| `TABULA_RASA_MAPS_QUOTA_PROJECT_NUMBER` | Numeric Maps project number |
| `TABULA_RASA_MAPS_QUOTA_SERVICE_ACCOUNT_FILE` | Absolute path to a server-mounted JSON file for the dedicated read-only quota identity |
| `TABULA_RASA_MAPS_QUOTA_TIMEOUT_SECONDS` | Per-request timeout, default 15 seconds; supported range 1–60 |

The file must be readable by the runtime user, kept outside the repository and
image, and managed through the host's secret-file facility. This configuration
does not accept browser-supplied project numbers, paths, identities or human
Google grants. No application-default credential fallback is used. Restart
the runtime after changing configuration to replace its client and cache.

The explicit operator-bound `open_migrated_runtime` factory instead reuses the
trusted app-wide `google_maps.quota_monitoring` table documented below. It does
not look inside `business_profiles`. Neither path changes migration bindings,
receipt grants or the monitoring identity's IAM policy.

Later production configuration and verification are separate operator steps:

1. Select the deployment's numeric Maps project and a dedicated read-only quota
   identity. Confirm Service Usage API and Cloud Monitoring API are enabled and
   the identity has the read permissions and scopes documented below. Any IAM,
   API enablement or credential provisioning change needs its own authorization.
2. Through the approved server secret mechanism, mount that identity's JSON and
   set the project number and file path above (or supply the trusted table to
   the explicit migrated factory). Keep it separate from both Maps keys and all
   businesses' human Google grants. Configuration transfer and deployment need
   separate authorization; do not copy credentials through issue/PR evidence.
3. After an authorized rollout, sign in to an authorized business. Expand the
   panel, select **Load usage**, and verify separate Routes and Places rows or
   safe API/status-specific guidance. Check **Refresh** independently of mileage
   lookup/routing, then switch businesses and sign out to verify state resets.
4. Allow Google's reporting delay before comparing sampled rates with Cloud
   Monitoring. The query looks back 15 minutes and uses the latest sampled rate
   in each quota's units; reporting can lag up to approximately 240 seconds
   after sampling. Zero is shown only after a successful Monitoring response
   without matching data. This is not a remaining-request or billing/spend meter.
5. Retain sanitized revision-specific evidence in the owning issue/PR. Synthetic
   tests and a local commercial image prove runtime separation and display
   behavior; they cannot prove hosted secret mounting, IAM, API enablement,
   actual quota availability, reporting delay or production monitoring access.

`google_maps_quota_core.py` owns the shared read-only client, parsers, rate
conversion and compact display projection. The former Streamlit facade is
retired; `http/maps_quota.py` owns HTTP configuration, lazy client
lifecycle and its bounded display cache. Neither HTTP path imports Streamlit,
gspread or legacy Sheets initialization.

## Trusted migrated-runtime factory configuration

For the explicit migrated-runtime factory, supply this trusted application-wide
configuration table through protected server configuration:

```toml
[google_maps]
browser_api_key = "RESTRICTED_BROWSER_KEY"
routes_api_key = "RESTRICTED_SERVER_KEY"
routes_timeout_seconds = 15
# Optional two-letter region restriction, for example "us".

autocomplete_region_code = "us"

[google_maps.quota_monitoring]
project_number = "123456789"
timeout_seconds = 15

[google_maps.quota_monitoring.service_account]
type = "service_account"
project_id = "maps-project-id"
private_key_id = "..."
private_key = "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----\n"
client_email = "maps-quota-reader@maps-project-id.iam.gserviceaccount.com"
client_id = "..."
auth_uri = "https://accounts.google.com/o/oauth2/auth"
token_uri = "https://oauth2.googleapis.com/token"
auth_provider_x509_cert_url = "https://www.googleapis.com/oauth2/v1/certs"
client_x509_cert_url = "..."
```

`project_number` is the numeric Google Cloud project number (not the project
ID). Service Usage's consumer-quota endpoint requires the number; keep the
service-account JSON's own `project_id` field unchanged.

Omit `autocomplete_region_code` when the deployment has no clear country rule.
Do not copy Maps keys or the quota-monitoring credential into a
`business_profiles` entry. Streamlit Cloud Secrets export is retired. Configure
commercial Maps credentials through the protected server environment described
in [deployment](operations/DEPLOYMENT.md); local Streamlit configuration does not
update that environment.

The quota reader is an app-wide service account, separate from every profile's
human Google OAuth grant used by Sheets and Drive. Grant it only the
read-only **Service Usage Viewer** role
(`roles/serviceusage.serviceUsageViewer`) and **Monitoring Viewer** role
(`roles/monitoring.viewer`) in the Maps project. These grant the documented
consumer-quota and time-series read permissions needed for this view; do not
grant editor/admin roles. If the organization's IAM catalog has renamed or
split these permissions, use the current read-only equivalents. Enable Service
Usage API and Cloud Monitoring API in that project. The application requests
only the `cloud-platform.read-only` and `monitoring.read` OAuth scopes required
by those read APIs. It reads the `routes.googleapis.com` and
`places.googleapis.com` consumer quota metrics and recent
`serviceruntime.googleapis.com/quota/rate/net_usage` data; it does not mutate
quotas or use this account for Routes/Places requests.

Safe request failures identify Service Usage or Cloud Monitoring and include
only the HTTP status and remediation guidance. Google response bodies and
credential material are never displayed.

Cloud Monitoring is sampled data, not a live counter. Reporting can lag several
minutes, including up to about 240 seconds after sampling. Each quota row uses
the compact format `Endpoint: usage/limit (time period)`; raw project/user
dimensions, utilization, and per-row timestamps are omitted. Google's
maximum-integer quota sentinel is shown as **Unlimited**. When a successful
Monitoring request has no time-series points for a quota, the view shows zero.
The 15-minute query is a lookback; each row shows Google's latest sampled rate
in the quota's limit units, so zero is not a guarantee that Google has finished
sampling every recent request.

## Controlled validation

Run these steps separately at the live staging and production origins. Obtain
separate production operator authorization before any production Maps probe.

1. Verify quotas and budget coverage before testing. In an approved test entity,
   open **Mileage > Add trip**. Use public landmarks for the two suggestions and
   make one **Calculate mileage** request. Confirm the authorized browser lookup
   works, FastAPI returns a route and the business miles can be edited. Close
   the draft without saving. A round trip can be tested when approved and uses
   two Routes requests.
2. For a negative check, use an approved controlled origin outside the website
   allowlist with the already browser-visible key and confirm Google rejects it.
   Keep the key out of source, logs and evidence. Verify the server IP and API
   restrictions in the provider console. If a runtime IP denial is needed, use
   a disposable equivalently restricted Routes test key from a non-allowed
   source. Record which credential and restriction each check actually covers
   without exporting the deployed server key.
3. Confirm a provider rejection or quota response produces a safe message and
   leaves manual address and mileage fields usable. Use an isolated test
   binding for fault rehearsal, or verify during a real provider failure.
   Keep active credentials and shared quotas in place. Record the tested
   release and environment and leave any live fallback check pending if the
   approved setup cannot safely reproduce it.
4. After reporting catches up, check Google Maps Platform metrics filtered by
   credential ID and API, plus Billing reports. Record the deploy ID, release
   SHA, UTC test time, restrictions, probe outcomes, effective quotas, budget
   alert delivery, and any owner-approved exception in the release evidence.
   Keep keys, addresses, provider payloads and screenshots containing them out
   of the record.

Automated tests always mock Google and must never use either live key.

## Policy and operations notes

- The application requests only the primary driving route's distance in meters,
  converts using `meters / 1609.344`, and rounds the editable suggestion to one
  decimal place.
- Calculate/Recalculate is explicit. Ordinary page loads do not make a
  route request.
- The autocomplete widget manages its own prediction session. The application
  does not create a second session-token system.
- The ledger stores the existing user-authored or user-confirmed location and
  mileage fields. It does not store autocomplete display content, complete
  Places or Routes responses, route polylines, or a durable round-trip link.
- Browser keys are visible to browsers by design and therefore depend on
  website plus API restrictions. Server keys remain in protected server configuration and
  are never sent to the browser.
- Monitor usage after deployment. Unexpected API/key traffic is a credential
  incident: restrict or disable the affected key, investigate, then rotate it
  carefully.

## Google Maps data-retention decision

**Decision date:** September 1, 2026

The Application uses Google-derived API content transiently to assist the user.
Durable bookkeeping values are treated as application/user-entered or
user-confirmed ledger data only when the user supplies or confirms them. Simply
pressing Save does not convert otherwise restricted Google Maps Content into
unrestricted customer-owned data.

| Data | Required treatment |
| --- | --- |
| Autocomplete predictions | Session/transient only |
| Google Places formatted address / display name | Do not persist as Google-derived content |
| Google Place ID | May be retained if a future implementation genuinely needs it and current Google terms permit it; the current Application does not need or persist it |
| Raw Places response | Never persist |
| Raw Routes response | Never persist |
| Route polyline | Do not request or persist |
| Routes `distanceMeters` | Transient only |
| Google-calculated mileage suggestion | Transient only |
| User-confirmed business mileage recorded in the ledger | Persist as application/user business-record data |
| User-authored location text stored in the ledger | Persist as application/user business-record data |

This table is the canonical implementation policy for mileage cache invalidation.
Selecting a Google suggestion provides transient routing input only. The Mileage
workflow shows the selected text for lookup and uses the transient route result to fill
editable, unsaved mileage fields; it does not write a durable mileage value until the
user reviews and saves the trip. The selected display text pre-fills an editable
location field but is not written to a durable ledger field until the user saves the
trip.

No Google response cache should be introduced, and no route polyline should be stored.
No Google provenance fields should be added to the Mileage worksheet unless a future
product requirement justifies them and the applicable policy is reviewed again. The
current Mileage and backup schemas therefore do not require a migration for mileage
cache invalidation.

## Billing jurisdiction

- **Billing-account country:** United States
- **Applicable jurisdiction classification:** non-EEA
- **Review date:** September 1, 2026

This deployment is documented as governed by the applicable non-EEA Google Maps platform
terms. If the billing-account country changes, especially to an EEA country, the
applicable Google Maps Platform terms and this retention decision must be reviewed again
before relying on them.

## Current authoritative references

- [Google Maps Platform Terms of Service](https://cloud.google.com/maps-platform/terms)
- [Maps Service Specific Terms](https://cloud.google.com/maps-platform/terms/maps-service-terms/)
- [Google Maps Platform API security](https://developers.google.com/maps/api-security-best-practices)
- [Place Autocomplete Widget](https://developers.google.com/maps/documentation/javascript/place-autocomplete-new)
- [Maps JavaScript API policies and attribution](https://developers.google.com/maps/documentation/javascript/policies)
- [Routes API usage, billing, and quotas](https://developers.google.com/maps/documentation/routes/usage-and-billing)
- [Routes API policies and attribution](https://developers.google.com/maps/documentation/routes/policies)
- [Google Maps Platform EEA Terms of Service](https://cloud.google.com/terms/maps-platform/eea)
- [EEA customer FAQ](https://developers.google.com/maps/comms/eea/faq)
- [Cloud Billing budgets and alerts](https://cloud.google.com/billing/docs/how-to/budgets)
