# Confidoc: LLM Export Package / Export to LLM

## Goal

Add a new **Export to LLM** option to the existing Confidoc export flow.

The user should be able to take the approved **PII-public markdown** output and send it to a selected LLM using either:

1. a prewritten prompt selected from a prompt-file list,
2. an ad hoc prompt written in a textarea,
3. or a combination of saved prompt + additional ad hoc instruction.

This should be implemented as an **LLM Export Package** rather than simply sending raw markdown plus a prompt. The package gives us auditability, repeatability, privacy controls, prompt versioning, model tracking, and a clean path to future batch/RAG/tool use.

---

## UX Placement

In the existing **Export** tab, add a new export mode:

> **Export to LLM**

Suggested UI wording:

> Create an LLM-safe package from the approved PII-public markdown and run it with a selected prompt/model.

The flow should be:

1. User selects or confirms the approved PII-public markdown source.
2. User selects prompt mode:
   - Saved prompt
   - Ad hoc prompt
   - Saved prompt + additional instruction
3. User selects provider/model using the existing LLM selector logic.
4. User clicks something like:
   - **Create LLM Package**
   - **Run with LLM**
5. System creates an internal LLM export package.
6. System submits the package content to the selected LLM.
7. System saves the LLM output as a new artifact linked to the job.

---

## Why Package Instead of Direct Markdown Submit?

Do **not** treat this as just “send markdown + prompt”.

Use a package because we will later need:

- batch processing,
- multiple reports for the same patient,
- RAG/tool permissions,
- prompt versioning,
- model/version audit trail,
- output comparison across models,
- safety checks before LLM submission,
- rehydration/deanonymization rules if ever allowed,
- repeatable demonstrations of the same input/prompt/model combination.

For a quick demo, sending markdown directly would work, but architecturally the package is better and safer.

---

## Initial Package Shape

Create an internal JSON-style package similar to this:

```json
{
  "package_id": "...",
  "job_id": "...",
  "source_type": "pii_public_markdown",
  "markdown": "...",
  "prompt_id": "summarize_medical_100_words",
  "prompt_name": "Summarize medical report in 100 words",
  "prompt_text": "...",
  "ad_hoc_prompt": "...",
  "combined_prompt": "...",
  "task_type": "summary | translation | qa | case_review | custom",
  "provider": "openrouter",
  "model": "google/gemini-2.0-flash",
  "rag_enabled": false,
  "tools_allowed": false,
  "rag_scope": null,
  "privacy_level": "pii_public",
  "created_at": "..."
}
```

The exact field names can be adjusted to match existing Confidoc conventions.

---

## Prompt File Support

Add a directory for saved prompts, for example:

```text
data/prompts/llm_exports/
```

Each prompt can be a `.md` file with optional front matter.

Example:

```markdown
---
id: summarize_medical_100_words
title: Summarize medical report in 100 words
task_type: summary
language: en
---

Summarize the following medical report in no more than 100 words.
Use clear, neutral clinical language.
Do not infer facts that are not present in the source text.
```

The UI should list available prompt files by title. If no front matter exists, fall back to filename as title and file body as prompt text.

---

## Initial Prompt Examples

Add a few starter prompts:

### 1. Summarize medical report in 100 words

```markdown
Summarize the following medical report in no more than 100 words.
Use clear, neutral clinical language.
Do not infer facts that are not present in the source text.
```

### 2. Translate medical report into English

```markdown
Translate the following medical report into English.
Preserve the meaning and clinical tone.
Keep placeholders and anonymized entity tokens unchanged.
Do not add explanations unless explicitly requested.
```

### 3. Check whether report suggests a condition

```markdown
Review the following medical report and answer whether it suggests the condition described by the user.
Base your answer only on the report text.
Separate your answer into:

1. Direct evidence
2. Possible indirect evidence
3. Missing information
4. Cautious conclusion

Do not diagnose. State uncertainty clearly.
```

### 4. Plain-language patient summary

```markdown
Rewrite the following medical report as a plain-language summary for a patient.
Use simple language.
Do not remove clinically important information.
Keep anonymized placeholders unchanged.
```

---

## Ad Hoc Prompt Handling

The user should be able to type an additional instruction into a textarea.

Modes:

1. **Saved prompt only**
2. **Ad hoc prompt only**
3. **Saved prompt + ad hoc instruction**

For combined mode, the final prompt can be assembled as:

```text
[SAVED PROMPT]

Additional user instruction:
[AD HOC PROMPT]

Document:
[PII-PUBLIC MARKDOWN]
```

The backend should store both the individual parts and the final combined prompt for auditability.

---

## LLM Selection

Reuse the existing LLM provider/model selection mechanism already used elsewhere in Confidoc.

The package should record:

- provider,
- model,
- whether BYOK/session key/default key was used if this is already tracked elsewhere,
- timestamp,
- prompt ID/version if available.

Do not expose private keys in package output or logs.

---

## Privacy Boundary

Only the approved **PII-public markdown** should be eligible for this export.

Before sending to the LLM, validate that:

- the job has approved/reviewed entities as required,
- the markdown source is the public/anonymized version,
- raw PDF text, mapping files, original PHI, and token maps are not included in the package,
- the package does not include the encrypted mapping file,
- the LLM response is stored separately from raw Zone 1 data.

The package privacy level should be explicit:

```json
"privacy_level": "pii_public"
```

---

## Tool/RAG Future-Proofing

Do not build RAG or tool use yet, but include metadata fields now:

```json
"rag_enabled": false,
"tools_allowed": false,
"rag_scope": null
```

Future use cases may include:

- selecting multiple reports for the same patient,
- private confidential case-history study,
- report comparison,
- contradiction detection,
- retrieval over a local approved corpus,
- tool-enabled structured extraction.

For now these should remain disabled and visible only as internal metadata or a disabled UI hint.

Suggested disabled UI note:

> RAG/tool use will be available in a later version. Current runs use only the selected markdown document and prompt.

---

## Output Artifact

Save each LLM run as a linked artifact, for example:

```text
data/zone2/llm_runs/{job_id}/{run_id}.json
```

Suggested output structure:

```json
{
  "run_id": "...",
  "package_id": "...",
  "job_id": "...",
  "provider": "...",
  "model": "...",
  "prompt_id": "...",
  "task_type": "summary",
  "input_privacy_level": "pii_public",
  "output_text": "...",
  "created_at": "...",
  "status": "completed",
  "error": null
}
```

If the LLM call fails, save a failed run artifact if useful:

```json
{
  "status": "failed",
  "error": "..."
}
```

Do not save secrets.

---

## Suggested API Endpoints

Names can be adjusted to existing routing conventions.

### List prompt files

```http
GET /api/llm-export/prompts
```

Returns available saved prompts.

### Create package preview

```http
POST /api/llm-export/{job_id}/package-preview
```

Creates or returns a preview of what will be sent, without running the LLM.

Useful for demos and safety review.

### Run LLM export

```http
POST /api/llm-export/{job_id}/run
```

Payload:

```json
{
  "prompt_id": "summarize_medical_100_words",
  "ad_hoc_prompt": "Focus on diagnosis and next steps.",
  "prompt_mode": "saved_plus_ad_hoc",
  "provider": "openrouter",
  "model": "google/gemini-2.0-flash"
}
```

Response:

```json
{
  "run_id": "...",
  "package_id": "...",
  "status": "completed",
  "output_text": "..."
}
```

### List LLM runs for job

```http
GET /api/llm-export/{job_id}/runs
```

Returns previous LLM outputs linked to the job.

---

## UI Components

In the Export tab, add a section like:

```text
Export to LLM

Source: Approved PII-public markdown

Prompt mode:
[ Saved prompt v ]
[ Ad hoc prompt textarea ]

Provider/model:
[ existing LLM selector ]

[ Preview Package ] [ Run with LLM ]
```

Below the run button, show:

- current package privacy level,
- selected prompt,
- selected provider/model,
- run status,
- output text,
- previous runs if available.

---

## Demo Use Cases

This feature should support demo examples such as:

1. “Summarize this medical report in 100 words.”
2. “Translate this medical report into English.”
3. “Does this medical report suggest the patient has condition X?”
4. “Create a plain-language summary for a patient.”

Later:

1. Select multiple reports for the same patient.
2. Run a private confidential case-history study.
3. Use RAG over approved local documents.
4. Compare outputs across models.

---

## Implementation Priority

### Phase 1

- Add prompt file directory and prompt loader.
- Add Export tab UI section.
- Add package creation logic.
- Add LLM run endpoint.
- Save output artifact.
- Show output in UI.

### Phase 2

- Add package preview.
- Add previous run list.
- Add starter prompt files.
- Add stronger validation/logging.

### Phase 3

- Batch mode.
- Multi-document selection.
- RAG/tool permissions.
- Output comparison across models.

---

## Key Principle

This is not just another export format.

It is a controlled bridge from Zone 1/Zone 2 protected document processing into selected LLM execution.

The system should make it clear what is being sent, why it is considered safe, which prompt/model is being used, and what output was produced.
