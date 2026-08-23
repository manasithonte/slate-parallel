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

## Status

- Core fan-out/fan-in architecture, all 4 workers, and the FastAPI endpoint are implemented and wired up.
- Worker 01 (subtitles) and Worker 02 (dubbing) use real Gemini transcription/translation and Cloud TTS synthesis.
- Worker 03 (reframing) currently generates the FFmpeg command rather than executing the render.
- Worker 04 (compliance) integrates with the live Parallel Task API when a key is present.
- Compliance certificate generation (`generate_compliance_certificate` in `src/media_engine.py`) exists but is not yet wired into the API.
- Every external integration (Gemini, Vertex AI, Parallel, Cloud TTS) has a safe fallback, so the pipeline runs end-to-end in "mock mode" without any credentials configured.
- React dashboard (frontend/) polls the endpoint, shows live per-worker status, and lets you play/download dubbed audio and view subtitle and compliance results.

## Project layout

```
src/
  api.py          FastAPI app, /health and /api/v1/process-media
  workers.py      4 department worker agents
  orchestrator.py Gemini fan-in synthesis (Master Release Package)
  media_engine.py File generation: .srt subtitles, MP3 dubs, FFmpeg
                  crop commands, compliance certificates
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
```

All keys are optional — omitted integrations fall back to mock/local behavior.

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
