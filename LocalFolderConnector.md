Build Phase 1 of the Secure Gateway for Confidoc using a local folder connector.
Goal:
Allow files dropped into a local incoming folder to be imported into Confidoc as jobs, processed through the existing pipeline, and exported back to a processed/export folder with job status visible in the UI.
Folder structure:
data/gateway/local/
  incoming/
  processing/
  processed/
  failed/
  exports/
  registry.jsonl
Required behaviour:
1. Scan incoming/ for new files.
2. For each supported file, create a Confidoc job using the existing upload/job creation logic.
3. Move the source file to processing/ while the job is active.
4. Run the normal Confidoc pipeline or queue it for normal processing.
5. When complete, write the normal export package to exports/{job_id}/.
6. Move the original input file to processed/{job_id}_{filename}.
7. On error, move it to failed/{timestamp}_{filename}.
8. Append all events to registry.jsonl:
   - detected
   - imported
   - processing_started
   - completed
   - failed
   - exported
Add backend endpoints:
GET /api/gateway/local/status
POST /api/gateway/local/scan
POST /api/gateway/local/process-next
POST /api/gateway/local/process-all
Add frontend panel:
"Secure Gateway"
- show incoming count
- processing count
- completed count
- failed count
- last events from registry
- buttons: Scan, Process next, Process all
- show export path when complete
Keep it simple:
- No filesystem watcher yet.
- No SFTP yet.
- No auth changes yet.
- No Nextcloud/WebDAV yet.
- Local folder only.
- Reuse existing Confidoc job creation, extraction, anonymization, export, and status logic wherever possible.
Demo flow:
1. User drops fake medical PDF into data/gateway/local/incoming/
2. Click Scan
3. Click Process all
4. Confidoc creates job
5. Existing review/export pipeline runs
6. Export package appears in data/gateway/local/exports/{job_id}/
7. UI shows completed status

Key point: do not let Claude build a separate pipeline. It should call the same internal functions/routes Confidoc already uses for upload, OCR/extraction, entity detection, review status, and export. The gateway is just another intake channel.