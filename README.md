# slate-parallel

Agentic post-production localization and QC orchestrator for film/media trailers. A studio producer submits a title, source video, and target languages/platforms; the pipeline fans out to four specialized worker agents running concurrently, then fans back in through a Gemini-powered synthesis step that produces a single Master Release Package QC report.

## Pipeline

```
POST /api/v1/process-media
        │
        ├── Worker 01 · Script & Subtitle Supervisor
        │     transcribes source-video dialogue (Gemini multimodal),
        │     falls back to a supplied script, translates per target
        │     language, writes .srt subtitle files
        │
        ├── Worker 03 · Smart Reframing Director (VFX)
        │     generates FFmpeg crop commands per target platform
        │     aspect ratio (16:9 / 9:16 / 1:1)
        │
        ├── Worker 04 · Global Standards & Compliance Guardian
        │     queries the Parallel Task API for live rating,
        │     censorship, and cultural-sensitivity research
        │
        │  (workers 01, 03, 04 run concurrently via asyncio.gather)
        │
        └── Worker 02 · Sound Stage & Dubbing Lead
              runs after Worker 01, since it depends on translated
              dialogue; synthesizes downloadable MP3 dubs via
              Google Cloud Text-to-Speech

        ▼
  Showrunner synthesis (Vertex AI Gemini)
  compiles all four department outputs into a
  Master Release Package QC report
```

Each worker returns a `DepartmentOutput` (`worker_id`, `department_name`, `status`, `data`, `execution_time_seconds`); the orchestrator serializes all four into a single Gemini prompt and returns structured JSON. If Vertex AI isn't configured, synthesis falls back to a locally assembled "approved with warnings" report so the endpoint still succeeds.

### Final assembly (optional, additive)

```
POST /api/v1/assemble-final                 (call after process-media succeeds)
        │
        ▼
  Google Cloud Transcoder job per (platform × language) pair
  crops the video and muxes in the dubbed audio track,
  renders one broadcast-ready .mp4 to GCS
  (no embedded captions — Transcoder can't mux a text track into a
  plain .mp4; subtitles stay available as the separate .srt download)
        │
        ▼
GET /api/v1/render-status/{render_job_id}    (poll until SUCCEEDED/FAILED)
```

A separate, optional stage that combines worker_01/02/03's outputs into an actual rendered video, rather than three standalone artifacts. Submission returns immediately; rendering happens in the background and is polled for completion. Requires a GCS bucket and the Transcoder API enabled — see Setup below. Not wired into `process-media`; the frontend triggers it manually via a "Render Final Video" button once dubbing has finished.

## Status

- Core fan-out/fan-in architecture, all 4 workers, and the FastAPI endpoint are implemented and wired up.
- Worker 01 (subtitles) and Worker 02 (dubbing) use real Gemini transcription/translation and Cloud TTS synthesis.
- Worker 03 (reframing) currently generates the FFmpeg command rather than executing the render.
- Worker 04 (compliance) integrates with the live Parallel Task API when a key is present.
- Compliance certificate generation (`generate_compliance_certificate` in `src/media_engine.py`) exists but is not yet wired into the API.
- Every external integration (Gemini, Vertex AI, Parallel, Cloud TTS, GCS, Transcoder) has a safe fallback, so the pipeline runs end-to-end in "mock mode" without any credentials configured.
- React dashboard (frontend/) polls the endpoint, shows live per-worker status, and lets you play/download dubbed audio and view subtitle and compliance results.
- **Final assembly (Transcoder) is implemented and GCS upload/signed-URL/`NOT_CONFIGURED` fallback are verified against live infrastructure.** Crop, mono dub-audio channel mapping, and job submission are confirmed working against real Transcoder jobs. Embedded captions were attempted and dropped after Transcoder rejected a text stream inside a plain `.mp4` mux (API limitation, not a bug) — subtitles remain a separate `.srt` download. A full render has not yet been confirmed to reach `SUCCEEDED` end-to-end.

## Project layout

```
src/
  api.py          FastAPI app: /health, /api/v1/process-media,
                  /api/v1/assemble-final, /api/v1/render-status
  workers.py      4 department worker agents
  orchestrator.py Gemini fan-in synthesis (Master Release Package)
  media_engine.py File generation: .srt subtitles, MP3 dubs, FFmpeg
                  crop commands, compliance certificates
  gcs_engine.py   GCS upload + signed URL helpers (final assembly)
  render_engine.py Transcoder job submission/polling (final assembly)
  schemas.py      Pydantic request/response models
frontend/         React + Vite + Tailwind dashboard
outputs/          Generated subtitles, audio, compliance artifacts
```

## Setup

### Backend

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Create a `.env` in the project root:

```
GOOGLE_CLOUD_PROJECT=your-gcp-project
GOOGLE_CLOUD_LOCATION=us-central1
GOOGLE_APPLICATION_CREDENTIALS=path/to/service-account.json
GEMINI_API_KEY=your-gemini-api-key
PARALLEL_API_KEY=your-parallel-api-key
GCS_BUCKET_NAME=your-gcs-bucket   # only needed for /api/v1/assemble-final
```

All keys are optional — omitted integrations fall back to mock/local behavior. `GCS_BUCKET_NAME` gates the final-assembly stage specifically: without it, `/api/v1/assemble-final` returns a `NOT_CONFIGURED` status instead of failing. To actually use it, you'll also need (one-time, done via `gcloud`/console — not automated by this repo):
- `gcloud services enable transcoder.googleapis.com` on your project
- A GCS bucket, with the service account behind `GOOGLE_APPLICATION_CREDENTIALS` granted `roles/storage.objectAdmin` on it and `roles/transcoder.admin` on the project
- `GOOGLE_APPLICATION_CREDENTIALS` pointing at an actual service-account JSON key (required for locally signing GCS download URLs — bare ADC won't work for that)

Run the API:

```bash
uvicorn src.api:app --reload
```

### Frontend

```bash
cd frontend
npm install
npm run dev
```

### Docker

```bash
docker build -t slate-parallel .
docker run -p 8080:8080 --env-file .env slate-parallel
```

## API

- `GET /health` — readiness probe
- `POST /api/v1/process-media` — runs the full pipeline; body is a `MediaJobRequest` (`title`, `video_url`, optional `script_text`, `target_languages`, `target_platforms`)
- `GET /api/v1/downloads/audio/{filename}` — download a generated dubbed MP3
- `POST /api/v1/assemble-final` — submit a final-assembly render job; body is a `RenderJobRequest` (worker_01/02 outputs plus job metadata); returns immediately with a `render_job_id`
- `GET /api/v1/render-status/{render_job_id}` — poll a render job's status; each output moves through `PENDING → SUBMITTED → SUCCEEDED/FAILED`, with a signed GCS `download_url` once ready
