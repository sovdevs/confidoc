# Confidoc — Server Tab / Source Ingestion Spec

## Goal

Build a new **Server** tab for Confidoc that lets the operator pull source documents from external storage locations into the secure Zone 1 data store.

This is the next step before batch processing. The tab is not meant to process the documents immediately. Its job is to discover, select, and import files from configured sources so they appear in the existing document/job selection flow for OCR, entity review, policy packaging, export, and LLM export.

The core workflow:

```text
Server tab → choose source → list available remote files → pull all or unseen files → store in Zone 1 → files appear in Start / document selection
```

## Design Principle

This must remain a **Zone 1 operation**.

External source files may contain raw PHI/PII. Therefore:

- files are pulled only into the secure Zone 1 data area
- raw files are never exposed to Zone 2
- imported files must follow the same lifecycle as manually uploaded files
- no raw document text should be sent to external LLMs during import
- import logs should avoid storing sensitive content
- credentials must never be written into normal run artifacts or frontend state

## Supported Source Types — Phase 1

Implement a connector framework, but only build the simplest useful connectors first.

Recommended Phase 1 connectors:

1. **SSH/SFTP source**
   - Host
   - Port
   - Username
   - Private key path or uploaded private key
   - Remote directory
   - Optional filename pattern, e.g. `*.pdf`, `*.docx`

2. **Nextcloud/WebDAV source**
   - Base WebDAV URL
   - Username
   - App password/token
   - Remote directory
   - Optional filename pattern

3. **GitHub private repo source**
   - Repo URL or owner/repo
   - Branch
   - Path/folder inside repo
   - Personal access token or existing environment token
   - Optional filename pattern

Keep the connector interface generic so more can be added later:

- S3-compatible storage
- Google Drive
- SharePoint/OneDrive
- local watched folder
- FTPS
- API-based customer portals

## Supported File Types

The initial user need is PDF ingestion, but the server tab should not be PDF-only.

Supported document extensions for import:

```text
.pdf
.docx
.doc
.rtf
.txt
.md
.odt
```

Optional later:

```text
.pptx
.xlsx
.csv
.html
.xml
```

Important processing rule:

- PDFs go through the existing PDF/OCR/Markdown pipeline.
- Word-like or text-native formats should skip OCR.
- They should instead enter a document extraction/conversion step that produces normalized markdown.

For Phase 1, importing non-PDFs can be supported even if downstream conversion is minimal. The key is to preserve metadata so later processors know whether OCR is required.

Add metadata:

```json
{
  "requires_ocr": true,
  "source_format": "pdf",
  "ingest_source_type": "sftp",
  "ingest_source_id": "...",
  "remote_path": "/incoming/report.pdf"
}
```

For `.docx`, `.txt`, `.md`, etc.:

```json
{
  "requires_ocr": false,
  "source_format": "docx"
}
```

## UX — Server Tab

Add a new top-level tab:

```text
Start | Review | Policy | Export | Server
```

Or, if the current UI groups tabs differently, place **Server** near upload/import.

### Server Tab Sections

#### 1. Source selector

A dropdown/list of configured sources:

```text
Choose source:
[ SFTP: Clinic incoming folder          v ]
```

Buttons:

```text
Test connection
List files
Pull unseen
Pull selected
```

#### 2. Source configuration panel

For Phase 1, configuration can be simple JSON-backed local config. It does not need a full credential manager yet.

Example source config:

```json
{
  "id": "clinic_sftp_incoming",
  "label": "Clinic SFTP incoming folder",
  "type": "sftp",
  "enabled": true,
  "remote_path": "/incoming/reports",
  "filename_patterns": ["*.pdf", "*.docx"],
  "credentials_ref": "clinic_sftp_key"
}
```

Credentials should be resolved server-side only.

Do not return secrets to the frontend.

#### 3. Remote file listing

After clicking **List files**, display:

| Status | Filename | Type | Size | Modified | Remote path | Action |
|---|---|---:|---:|---|---|---|
| New | report_001.pdf | PDF | 1.2 MB | 2026-05-17 | /incoming/report_001.pdf | Select |
| Seen | report_002.docx | DOCX | 92 KB | 2026-05-16 | /incoming/report_002.docx | Select |

Statuses:

- `new` — not yet imported
- `seen` — already imported previously
- `changed` — same remote path but changed size/mtime/hash
- `unsupported` — file extension not supported
- `error` — metadata could not be read

#### 4. Pull controls

Actions:

```text
Pull unseen files
Pull selected files
Refresh list
```

After pulling, show import result summary:

```text
Imported 7 files into Zone 1.
Skipped 3 already seen files.
1 unsupported file ignored.
```

Each imported file should appear in the normal document/job list.

## Backend Endpoints

Add a new router, e.g. `server_sources.py` or `source_ingest.py`.

Suggested endpoints:

### List configured sources

```http
GET /api/server-sources
```

Returns safe source metadata only:

```json
[
  {
    "id": "clinic_sftp_incoming",
    "label": "Clinic SFTP incoming folder",
    "type": "sftp",
    "enabled": true,
    "filename_patterns": ["*.pdf", "*.docx"]
  }
]
```

### Test source connection

```http
POST /api/server-sources/{source_id}/test
```

Returns:

```json
{
  "ok": true,
  "message": "Connection successful"
}
```

On failure:

```json
{
  "ok": false,
  "message": "Authentication failed or remote path unavailable"
}
```

Do not return stack traces or secrets.

### List remote files

```http
GET /api/server-sources/{source_id}/files
```

Optional query params:

```text
?include_seen=false&pattern=*.pdf
```

Returns:

```json
{
  "source_id": "clinic_sftp_incoming",
  "files": [
    {
      "remote_id": "hash-or-provider-id",
      "filename": "report_001.pdf",
      "remote_path": "/incoming/report_001.pdf",
      "extension": ".pdf",
      "size_bytes": 1200344,
      "modified_at": "2026-05-17T09:30:00Z",
      "status": "new",
      "supported": true,
      "requires_ocr": true
    }
  ]
}
```

### Pull unseen files

```http
POST /api/server-sources/{source_id}/pull-unseen
```

Body:

```json
{
  "pattern": null,
  "limit": 100
}
```

Returns:

```json
{
  "source_id": "clinic_sftp_incoming",
  "imported": [
    {
      "job_id": "...",
      "filename": "report_001.pdf",
      "source_format": "pdf",
      "requires_ocr": true
    }
  ],
  "skipped_seen": 3,
  "unsupported": 1,
  "errors": []
}
```

### Pull selected files

```http
POST /api/server-sources/{source_id}/pull
```

Body:

```json
{
  "remote_paths": [
    "/incoming/report_001.pdf",
    "/incoming/report_002.docx"
  ]
}
```

Returns the same shape as `pull-unseen`.

## Service Layer

Create a generic source connector interface.

Example:

```python
class SourceConnector:
    def test(self) -> SourceTestResult:
        ...

    def list_files(self, pattern: str | None = None) -> list[RemoteFile]:
        ...

    def download_file(self, remote_path: str, destination_path: Path) -> DownloadResult:
        ...
```

Connector implementations:

```text
connectors/
  base.py
  sftp_connector.py
  webdav_connector.py
  github_connector.py
```

Ingestion services:

```text
services/
  source_config_loader.py
  source_ingest_service.py
  ingest_registry.py
```

## Source Config and Credentials

For local/dev Phase 1, use config files plus environment variables.

Example:

```text
data/source_configs/sources.json
```

Example:

```json
[
  {
    "id": "clinic_sftp_incoming",
    "label": "Clinic SFTP incoming folder",
    "type": "sftp",
    "host": "example.com",
    "port": 22,
    "username_env": "CONFIDOC_SFTP_USER",
    "private_key_path_env": "CONFIDOC_SFTP_KEY_PATH",
    "remote_path": "/incoming/reports",
    "filename_patterns": ["*.pdf", "*.docx"],
    "enabled": true
  },
  {
    "id": "clinic_nextcloud",
    "label": "Clinic Nextcloud folder",
    "type": "webdav",
    "base_url_env": "CONFIDOC_NEXTCLOUD_WEBDAV_URL",
    "username_env": "CONFIDOC_NEXTCLOUD_USER",
    "password_env": "CONFIDOC_NEXTCLOUD_APP_PASSWORD",
    "remote_path": "/Reports",
    "filename_patterns": ["*.pdf", "*.docx"],
    "enabled": true
  }
]
```

Secrets should be looked up from env vars or a protected local secrets file. They should not be included in API responses.

## Zone 1 Storage

Pulled files should be stored as if they were manually uploaded.

Recommended layout:

```text
data/zone1/uploads/{job_id}/original/{filename}
data/zone1/uploads/{job_id}/metadata.json
```

Or use the existing Confidoc job layout if already defined.

The metadata should include import provenance:

```json
{
  "job_id": "...",
  "original_filename": "report_001.pdf",
  "source_format": "pdf",
  "requires_ocr": true,
  "ingest_method": "server_pull",
  "ingest_source_id": "clinic_sftp_incoming",
  "ingest_source_type": "sftp",
  "remote_path": "/incoming/report_001.pdf",
  "remote_size_bytes": 1200344,
  "remote_modified_at": "2026-05-17T09:30:00Z",
  "content_sha256": "...",
  "imported_at": "...",
  "processing_status": "imported"
}
```

## Dedupe / Seen File Logic

Maintain an ingest registry:

```text
data/zone1/ingest_registry.jsonl
```

Each line:

```json
{
  "source_id": "clinic_sftp_incoming",
  "remote_path": "/incoming/report_001.pdf",
  "remote_size_bytes": 1200344,
  "remote_modified_at": "2026-05-17T09:30:00Z",
  "content_sha256": "...",
  "job_id": "...",
  "imported_at": "..."
}
```

A file is considered `seen` if:

- same `source_id`
- same `remote_path`
- same size and modified timestamp, or same content hash if available

A file is considered `changed` if:

- same `source_id`
- same `remote_path`
- different size, mtime, or hash

For Phase 1, it is acceptable to calculate the hash only after download. Remote listing can classify based on path + size + mtime first.

On changed files, do not overwrite the previous job. Import as a new job and mark relation:

```json
{
  "previous_job_id": "...",
  "change_detected": true
}
```

## Processing Status Integration

Imported files should appear in the normal job/document list with status:

```text
Imported — awaiting processing
```

For PDFs:

```text
Imported — OCR not started
```

For DOCX/TXT/MD:

```text
Imported — extraction not started
```

Do not automatically run OCR or extraction in Phase 1 unless the user explicitly triggers it.

Later batch processing can add:

```text
Pull unseen → OCR all → entity detect all → review queue
```

But not yet.

## Non-PDF Handling

The import layer should not attempt full conversion for non-PDFs yet unless there is already existing functionality.

But it should set enough metadata to allow a future processor to route correctly:

```python
if extension == ".pdf":
    requires_ocr = True
    processor_hint = "pdf_ocr"
elif extension in {".docx", ".doc", ".odt", ".rtf"}:
    requires_ocr = False
    processor_hint = "office_to_markdown"
elif extension in {".txt", ".md"}:
    requires_ocr = False
    processor_hint = "text_to_markdown"
else:
    supported = False
```

Later conversion options:

- `python-docx` for DOCX
- LibreOffice headless for DOC/DOCX/ODT/RTF
- Pandoc if available
- direct read for TXT/MD

## Security Requirements

- Do not expose credentials to frontend.
- Do not save credentials in ingest registry.
- Do not save API tokens in job metadata.
- Avoid writing full remote URLs if they contain credentials.
- Sanitize filenames before writing to disk.
- Prevent path traversal from remote filenames.
- Enforce allowed extensions.
- Enforce maximum file size per source config.
- Keep source ingestion behind the existing Zone 1 authentication.
- Add audit events for source listing and imports, but do not log document contents.

Example audit events:

```json
{
  "event": "source_list_files",
  "source_id": "clinic_sftp_incoming",
  "user": "...",
  "count": 12,
  "created_at": "..."
}
```

```json
{
  "event": "source_pull_file",
  "source_id": "clinic_sftp_incoming",
  "remote_path_hash": "...",
  "job_id": "...",
  "created_at": "..."
}
```

Prefer hashing remote paths in audit logs if filenames might reveal PHI.

## Error Handling

Common errors:

- authentication failed
- remote path does not exist
- permission denied
- unsupported file type
- file too large
- download interrupted
- duplicate/seen file
- connector dependency missing

Return user-safe messages.

Example:

```json
{
  "ok": false,
  "error_code": "REMOTE_PERMISSION_DENIED",
  "message": "The source was reachable, but the configured account cannot read this folder."
}
```

## Starter Implementation Order

1. Add source config loader.
2. Add base connector interface.
3. Implement SFTP connector first.
4. Add ingest registry and seen-file logic.
5. Add backend endpoints for sources, test, list, pull.
6. Add Server tab UI with source selector, file table, and pull actions.
7. Store pulled files in Zone 1 using the same job layout as manual uploads.
8. Make imported jobs appear in the existing document list.
9. Add WebDAV/Nextcloud connector.
10. Add GitHub private repo connector.

## Recommended Phase 1 Scope

Build fully:

- source list endpoint
- SFTP connector
- remote listing
- pull selected
- pull unseen
- ingest registry
- Zone 1 storage
- UI Server tab

Stub or implement lightly:

- WebDAV/Nextcloud connector
- GitHub connector
- non-PDF downstream conversion

Do not build yet:

- scheduled polling
- automatic batch OCR
- automatic entity extraction
- multi-document patient grouping
- RAG indexing
- tool execution

Those belong to later phases.

## UX Copy

At top of Server tab:

```text
Pull source documents from a trusted server into the secure Zone 1 store.
Files imported here are treated like manually uploaded documents and are not sent to any LLM or external service during import.
```

For unsupported non-PDFs:

```text
This file type can be imported, but downstream conversion is not yet enabled.
```

For Word documents:

```text
Word/text-native documents skip OCR and will use a document-to-markdown extraction step when processing is started.
```

For pull unseen:

```text
Pull Unseen imports only files that have not already been copied from this source path with the same size and modification time.
```

## Acceptance Criteria

- User can open Server tab.
- User can select a configured source.
- User can test connection without exposing credentials.
- User can list remote files.
- UI distinguishes new/seen/changed/unsupported files.
- User can pull selected files.
- User can pull all unseen files.
- Imported files are stored in Zone 1.
- Imported files get job IDs and metadata.
- Imported files appear in the existing document/job list.
- Import does not trigger OCR automatically.
- Credentials are never returned to frontend or saved in artifacts.
- Ingest registry prevents duplicate import of unchanged files.

## Open Design Notes

### Should source ingestion support Word files?

Yes. The Server tab should allow Word/text-native formats because real medical/legal workflows often receive mixed document types. However, OCR should only apply to PDFs/scans/images. Word/text-native files should later route into a document-to-markdown extractor.

### Should the user choose source files manually or import all unseen?

Both. Manual selection is useful for controlled demos and sensitive workflows. Pull unseen is useful for operational workflows and is the basis for later batch processing.

### Should this use SSH keys?

Yes for SFTP. Prefer private key auth over passwords. In local/dev, key paths can be supplied via environment variables. Later, a proper secrets store can be added.

### Should GitHub private repos be supported?

Yes, but treat GitHub as a document source only when customers actually store files there. It is useful for demos and controlled test corpora. It is less likely for real clinical production intake than SFTP, WebDAV/Nextcloud, or SharePoint.

### Should Nextcloud be supported?

Yes. Nextcloud/WebDAV is a good fit for privacy-conscious EU/local-server workflows and aligns well with Confidoc’s self-hosted positioning.
