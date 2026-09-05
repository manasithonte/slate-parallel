import React, { useState, useEffect, useRef } from 'react';
import {
  Play,
  Loader2,
  Square,
  Upload,
  Zap,
  Film,
  Download,
  Volume2,
  Smartphone,
  FileText,
  FileCheck,
  VolumeX
} from 'lucide-react';

const RENDER_POLL_INTERVAL_MS = 5000;
const RENDER_ACTIVE_STATUSES = new Set(["PENDING", "SUBMITTED"]);
const RENDER_TERMINAL_STATUSES = new Set(["SUCCEEDED", "FAILED", "NOT_CONFIGURED"]);

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || "http://127.0.0.1:8000";

const AVAILABLE_LANGUAGES = ["English", "Japanese", "Spanish", "French", "German"];
const AVAILABLE_PLATFORMS = ["TikTok (9:16)", "Instagram Post (1:1)"];

const PLATFORM_FRAME_STYLES = {
  "TikTok (9:16)": { label: "TikTok (9:16 Vertical Cut)", box: "w-32 h-56", border: "border-orange-300" },
  "Instagram Post (1:1)": { label: "Instagram (1:1 Square Cut)", box: "w-44 h-44", border: "border-orange-300" },
};

const LANG_VOICE_CODES = {
  English: 'en-US',
  Japanese: 'ja-JP',
  Spanish: 'es-ES',
  French: 'fr-FR',
  German: 'de-DE'
};

// A raw YouTube page URL isn't a playable video file — the backend resolves
// it server-side via yt-dlp, but a plain <video src> tag in the browser
// can't play it directly, so the crop preview needs YouTube's embed player
// instead whenever the source looks like a YouTube link.
function getYouTubeEmbedUrl(url) {
  const match = url.match(/(?:youtube\.com\/(?:watch\?v=|shorts\/|embed\/)|youtu\.be\/)([a-zA-Z0-9_-]{6,})/);
  return match ? `https://www.youtube.com/embed/${match[1]}` : null;
}

// Selectable-pill color tokens per category — unselected pills share one
// muted look; selected pills pick up an orange-family accent (all shades of
// orange/amber, per category) so the same color language used on the
// worker cards carries through to the form.
const PILL_COLORS = {
  cyan: 'bg-orange-50 border-orange-300 text-orange-700',
  sky: 'bg-amber-50 border-amber-300 text-amber-700',
  violet: 'bg-orange-50 border-orange-300 text-orange-700',
};
const PILL_UNSELECTED = 'bg-white border-stone-300 text-stone-500 hover:border-stone-400 hover:text-stone-700';

function SectionLabel({ children }) {
  return (
    <div className="mb-2">
      <span className="text-[11px] font-semibold tracking-widest text-stone-500 uppercase">{children}</span>
    </div>
  );
}

// Numbered circular step header — ties the form's three stages together
// (1 Project details, 2 Languages, 3 Target platforms) the way the header
// icon chips do for the right-hand cards.
function StepHeader({ number, title, subtitle }) {
  return (
    <div className="flex items-center justify-between">
      <div className="flex items-center gap-3">
        <div className="w-7 h-7 rounded-full bg-orange-600 text-white text-xs font-bold flex items-center justify-center shrink-0">
          {number}
        </div>
        <h2 className="text-sm font-bold text-stone-900">{title}</h2>
      </div>
      <span className="text-[11px] text-stone-400">{subtitle}</span>
    </div>
  );
}

export default function App() {
  const [formData, setFormData] = useState({
    title: "",
    script_text: "",
    video_url: "",
  });

  const [selectedDubLanguages, setSelectedDubLanguages] = useState([]);
  const [selectedSubtitleLanguages, setSelectedSubtitleLanguages] = useState([]);
  const [selectedPlatforms, setSelectedPlatforms] = useState([]);

  // Video file upload — an alternative to pasting a URL. Uploading
  // immediately POSTs to /api/v1/upload-video and, on success, fills
  // formData.video_url with whatever the backend hands back (a gs:// URI
  // when GCS is configured, a local path otherwise) exactly as if the user
  // had typed that value into the URL field themselves.
  const [uploadState, setUploadState] = useState({ status: 'idle', fileName: null, error: null });
  const fileInputRef = useRef(null);

  const handleFileUpload = async (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploadState({ status: 'uploading', fileName: file.name, error: null });

    const body = new FormData();
    body.append('file', file);

    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/upload-video`, { method: 'POST', body });
      if (!res.ok) {
        const detail = await res.json().catch(() => null);
        throw new Error(detail?.detail || `HTTP error ${res.status}`);
      }
      const data = await res.json();
      setFormData(prev => ({ ...prev, video_url: data.video_url }));
      setUploadState({ status: 'success', fileName: file.name, error: null });
    } catch (err) {
      setUploadState({ status: 'error', fileName: file.name, error: err.message });
    } finally {
      e.target.value = '';
    }
  };

  // Per-platform choice of which previously-selected dub/subtitle language to
  // actually bake into that platform's final render. Only holds explicit user
  // overrides — getPlatformRendition below fills in a default (falling back
  // whenever a stored choice drops out of the current selection) at render
  // time, rather than syncing derived state back in via an effect.
  const [platformRenditions, setPlatformRenditions] = useState({});

  const getPlatformRendition = (platform) => {
    const override = platformRenditions[platform] || {};
    return {
      dubLanguage: selectedDubLanguages.includes(override.dubLanguage) ? override.dubLanguage : (selectedDubLanguages[0] || ""),
      subtitleLanguage: selectedSubtitleLanguages.includes(override.subtitleLanguage) ? override.subtitleLanguage : (selectedSubtitleLanguages[0] || ""),
    };
  };

  const [loading, setLoading] = useState(false);
  const [pipelineData, setPipelineData] = useState(null);
  const [playingAudio, setPlayingAudio] = useState(null);
  const workerOneData = pipelineData?.master_release_package?.raw_department_data?.find(
    worker => worker.worker_id === "worker_01"
  )?.data;
  const workerTwoData = pipelineData?.master_release_package?.raw_department_data?.find(
    worker => worker.worker_id === "worker_02"
  )?.data;
  const workerThreeData = pipelineData?.master_release_package?.raw_department_data?.find(
    worker => worker.worker_id === "worker_03"
  )?.data;
  const workerFourData = pipelineData?.master_release_package?.raw_department_data?.find(
    worker => worker.worker_id === "worker_04"
  )?.data;

  const [workerStates, setWorkerStates] = useState({
    worker_01: { status: 'IDLE', time: null, data: null },
    worker_02: { status: 'IDLE', time: null, data: null },
    worker_03: { status: 'IDLE', time: null, data: null },
    worker_04: { status: 'IDLE', time: null, data: null },
  });

  // Final-assembly (Transcoder) stage — additive, triggered manually after
  // the fan-out pipeline succeeds. See src/render_engine.py.
  const [renderJob, setRenderJob] = useState(null);
  const [renderSubmitting, setRenderSubmitting] = useState(false);

  // Elapsed-render-time tracking. Transcoder's Job API exposes no progress
  // percentage (only PENDING/SUBMITTED/SUCCEEDED/FAILED), so "time taken" is
  // measured client-side: created_at from the server, and a completion
  // timestamp captured at the exact moment a fetch response first shows a
  // terminal overall status (set right where that response is handled,
  // not derived reactively in an effect — the best precision available
  // without the backend recording its own end time).
  const [renderCompletedAtMs, setRenderCompletedAtMs] = useState(null);
  const [renderNowTick, setRenderNowTick] = useState(() => Date.now());

  const applyRenderJobUpdate = (data) => {
    setRenderJob(data);
    if (!RENDER_ACTIVE_STATUSES.has(data.overall_status)) {
      setRenderCompletedAtMs(Date.now());
    }
  };

  useEffect(() => {
    if (!renderJob || !RENDER_ACTIVE_STATUSES.has(renderJob.overall_status)) return;

    const intervalId = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/render-status/${renderJob.render_job_id}`);
        if (!res.ok) return;
        const data = await res.json();
        applyRenderJobUpdate(data);
      } catch {
        // transient network error; next poll tick will retry
      }
    }, RENDER_POLL_INTERVAL_MS);

    return () => clearInterval(intervalId);
  }, [renderJob]);

  useEffect(() => {
    if (!renderJob || !RENDER_ACTIVE_STATUSES.has(renderJob.overall_status)) return;
    const intervalId = setInterval(() => setRenderNowTick(Date.now()), 1000);
    return () => clearInterval(intervalId);
  }, [renderJob]);

  // Elapsed fan-out-pipeline timer — same client-side-ticking pattern as the
  // render job above (the API only returns a final pipeline_runtime_seconds
  // once the whole request resolves, so a live "Xs elapsed" while workers
  // are still running has to be timed on the client).
  const [pipelineStartMs, setPipelineStartMs] = useState(null);
  const [pipelineNowTick, setPipelineNowTick] = useState(() => Date.now());

  useEffect(() => {
    if (!loading) return;
    const intervalId = setInterval(() => setPipelineNowTick(Date.now()), 1000);
    return () => clearInterval(intervalId);
  }, [loading]);

  const formatElapsed = (ms) => {
    const totalSeconds = Math.max(0, ms / 1000);
    if (totalSeconds < 60) return `${totalSeconds.toFixed(1)}s`;
    const minutes = Math.floor(totalSeconds / 60);
    const seconds = Math.round(totalSeconds % 60);
    return `${minutes}m ${seconds}s`;
  };

  const handleAssembleFinal = async () => {
    setRenderSubmitting(true);
    setRenderJob(null);
    setRenderCompletedAtMs(null);

    const payload = {
      title: formData.title,
      video_url: formData.video_url,
      platform_renditions: Object.fromEntries(
        selectedPlatforms.map(platform => {
          const rendition = getPlatformRendition(platform);
          return [platform, {
            dub_language: rendition.dubLanguage || null,
            subtitle_language: rendition.subtitleLanguage || null,
          }];
        })
      ),
      localized_dialogues: workerOneData?.localized_dialogues || {},
      subtitle_files: workerOneData?.subtitle_files || {},
      dubbed_tracks: workerTwoData?.dubbed_tracks || {},
    };

    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/assemble-final`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      const data = await res.json();
      applyRenderJobUpdate(data);
    } catch (err) {
      alert("Final Assembly Error: " + err.message);
    } finally {
      setRenderSubmitting(false);
    }
  };

  const toggleDubLanguage = (lang) => {
    setSelectedDubLanguages(prev =>
      prev.includes(lang) ? prev.filter(l => l !== lang) : [...prev, lang]
    );
  };

  const toggleSubtitleLanguage = (lang) => {
    setSelectedSubtitleLanguages(prev =>
      prev.includes(lang) ? prev.filter(l => l !== lang) : [...prev, lang]
    );
  };

  const togglePlatform = (plat) => {
    if (selectedPlatforms.includes(plat)) {
      if (selectedPlatforms.length > 1) setSelectedPlatforms(selectedPlatforms.filter(p => p !== plat));
    } else {
      setSelectedPlatforms([...selectedPlatforms, plat]);
    }
  };

  // Plays the actual Cloud TTS dub audio generated by worker_02 — not a
  // browser-side approximation, so what's heard here is exactly what's in
  // the downloadable MP3 and the final rendered video.
  const audioRef = useRef(null);

  const playDubbedAudio = (language, audioUrl) => {
    if (!audioUrl) return;

    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }

    if (playingAudio === language) {
      setPlayingAudio(null);
      return;
    }

    const audio = new Audio(audioUrl);
    audioRef.current = audio;
    audio.onended = () => setPlayingAudio(null);
    audio.onerror = () => setPlayingAudio(null);
    audio.play().catch(() => setPlayingAudio(null));
    setPlayingAudio(language);
  };

  // Browser download helper
  const triggerDownload = (filename, content) => {
    const element = document.createElement("a");
    const file = new Blob([content], { type: "text/plain;charset=utf-8" });
    element.href = URL.createObjectURL(file);
    element.download = filename;
    document.body.appendChild(element);
    element.click();
    document.body.removeChild(element);
  };

  const handleTriggerPipeline = async (e) => {
    e.preventDefault();
    setLoading(true);
    setPipelineData(null);
    setPipelineStartMs(Date.now());
    if (audioRef.current) {
      audioRef.current.pause();
      audioRef.current = null;
    }
    setPlayingAudio(null);

    setWorkerStates({
      worker_01: { status: 'RUNNING', time: null, data: null },
      worker_02: { status: 'RUNNING', time: null, data: null },
      worker_03: { status: 'RUNNING', time: null, data: null },
      worker_04: { status: 'RUNNING', time: null, data: null },
    });

    const payload = {
      title: formData.title,
      script_text: formData.script_text,
      video_url: formData.video_url,
      dubbing_languages: selectedDubLanguages,
      subtitle_languages: selectedSubtitleLanguages,
      target_platforms: selectedPlatforms
    };

    try {
      const res = await fetch(`${API_BASE_URL}/api/v1/process-media`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload)
      });

      if (!res.ok) throw new Error(`HTTP error ${res.status}`);
      const data = await res.json();
      setPipelineData(data);

      const rawWorkers = data.master_release_package?.raw_department_data || [];
      const updatedStates = {};
      rawWorkers.forEach(w => {
        updatedStates[w.worker_id] = {
          status: 'SUCCESS',
          time: w.execution_time_seconds,
          data: w.data
        };
      });
      setWorkerStates(prev => ({ ...prev, ...updatedStates }));

    } catch (err) {
      alert("Pipeline Execution Error: " + err.message);
    } finally {
      setLoading(false);
    }
  };

  const runningCount = Object.values(workerStates).filter(s => s.status === 'RUNNING').length;
  const totalWorkerCount = Object.keys(workerStates).length;
  const fanOutStateLabel = loading ? 'running' : pipelineData ? 'complete' : 'idle';
  const pipelineElapsedMs = loading && pipelineStartMs ? (pipelineNowTick - pipelineStartMs) : null;

  return (
    <div className="min-h-screen bg-[#FBF7EF] bg-[radial-gradient(ellipse_80%_50%_at_50%_-10%,rgba(6,182,212,0.08),transparent)] text-stone-900 antialiased pb-20">
      {/* Header */}
      <header className="border-b border-stone-200 bg-[#FBF7EF]/90 backdrop-blur sticky top-0 z-50">
        <div className="max-w-[2400px] mx-auto px-6 lg:px-10 py-4 flex items-center">
          <div className="flex items-center gap-3">
            <div className="w-10 h-10 rounded-xl bg-orange-600 flex items-center justify-center shadow-md shadow-orange-600/20 overflow-hidden">
              <img src="/clapperboard.jpg" alt="SlateParallel" className="w-9 h-9 object-contain mix-blend-multiply" />
            </div>
            <h1 className="text-lg font-bold tracking-tight text-stone-900">
              SlateParallel
            </h1>
          </div>
        </div>
      </header>

      {/* Intro — a one-line explanation of what the app actually does,
          since the header alone (name + tagline) doesn't say anything
          about the pipeline itself to a first-time visitor. */}
      <div className="max-w-[2400px] mx-auto px-6 lg:px-10 pt-10 pb-1">
        <p className="text-[11px] font-bold tracking-[0.2em] text-orange-600 uppercase mb-2">
          Agentic Post-Production Pipeline
        </p>
        <h2 className="text-3xl sm:text-4xl font-black tracking-tight leading-[1.15] text-stone-900">
          Four agents walk into the edit bay.<br />
          <span className="bg-clip-text text-transparent bg-gradient-to-r from-orange-600 via-orange-500 to-amber-500">
            A global release walks out.
          </span>
        </h2>
        <p className="text-base text-stone-600 max-w-2xl leading-relaxed mt-4">
          <span className="font-semibold text-stone-800">Subtitles. Dubbing. Reframing. Compliance.</span> Four workers, one pass, running in parallel.
        </p>
      </div>

      {/* Main Grid — a fixed-width form column plus a right column that
          fills whatever space remains, so wide monitors don't just leave
          empty margin while a 12-fraction split stays proportionally cramped
          or bloated. */}
      <main className="max-w-[2400px] mx-auto px-6 lg:px-10 py-10 grid grid-cols-1 lg:grid-cols-[420px_1fr] gap-8">

        {/* Left: Input Form */}
        <div className="space-y-6">
          <div className="bg-white border border-stone-200 rounded-2xl p-6 shadow-sm shadow-stone-200">
            <form onSubmit={handleTriggerPipeline} className="space-y-5">
              <StepHeader number={1} title="Project details" subtitle="Source + script" />
              <div className="space-y-4">
                <div>
                  <label className="block text-xs font-medium text-stone-500 mb-1">Project Title</label>
                  <input
                    type="text"
                    value={formData.title}
                    onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                    placeholder="e.g. Inception 2: Teaser Cut"
                    required
                    className="w-full px-3.5 py-2.5 bg-white border border-stone-300 rounded-xl text-sm focus:border-orange-500 focus:ring-2 focus:ring-orange-100 outline-none text-stone-900 placeholder:text-stone-400 transition"
                  />
                </div>

                <div>
                  <label className="block text-xs font-medium text-stone-500 mb-1">Master Script Dialogue</label>
                  <textarea
                    rows="3"
                    value={formData.script_text}
                    onChange={(e) => setFormData({ ...formData, script_text: e.target.value })}
                    placeholder="e.g. We have to venture into the subconscious mind before the collapse begins."
                    className="w-full px-3.5 py-2.5 bg-white border border-stone-300 rounded-xl text-sm focus:border-orange-500 focus:ring-2 focus:ring-orange-100 outline-none text-stone-900 placeholder:text-stone-400 resize-none transition"
                  />
                  <p className="text-[10px] text-stone-400 mt-1">
                    Optional: leave blank to auto-transcribe the video instead. Whatever text ends up here becomes the dubbed audio and subtitles. Prefix a line with a timestamp (e.g. "[00:05] Line one") to control exactly when each line starts; otherwise every line is treated as one untimed block.
                  </p>
                </div>

                <div>
                  <label className="block text-xs font-medium text-stone-500 mb-1">Source Video Asset URL</label>
                  <div className="flex gap-2">
                    <input
                      type="url"
                      value={formData.video_url}
                      onChange={(e) => setFormData({ ...formData, video_url: e.target.value })}
                      placeholder="e.g. https://vjs.zencdn.net/v/oceans.mp4"
                      required
                      className="flex-1 min-w-0 px-3.5 py-2.5 bg-white border border-stone-300 rounded-xl text-sm focus:border-orange-500 focus:ring-2 focus:ring-orange-100 outline-none text-stone-900 placeholder:text-stone-400 transition"
                    />
                    <input
                      type="file"
                      accept="video/*"
                      ref={fileInputRef}
                      onChange={handleFileUpload}
                      className="hidden"
                    />
                    <button
                      type="button"
                      onClick={() => fileInputRef.current?.click()}
                      disabled={uploadState.status === 'uploading'}
                      title="Upload a video file instead of pasting a URL"
                      className="shrink-0 flex items-center gap-1.5 px-3 py-2.5 bg-stone-100 hover:bg-stone-200 border border-stone-300 rounded-xl text-xs font-semibold text-stone-600 transition disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {uploadState.status === 'uploading' ? (
                        <Loader2 className="w-4 h-4 animate-spin" />
                      ) : (
                        <Upload className="w-4 h-4" />
                      )}
                      <span>Upload</span>
                    </button>
                  </div>
                  {uploadState.status === 'uploading' && (
                    <p className="text-[10px] text-stone-400 mt-1">Uploading {uploadState.fileName}...</p>
                  )}
                  {uploadState.status === 'success' && (
                    <p className="text-[10px] text-emerald-600 mt-1">✓ Uploaded {uploadState.fileName}</p>
                  )}
                  {uploadState.status === 'error' && (
                    <p className="text-[10px] text-rose-600 mt-1">Upload failed: {uploadState.error}</p>
                  )}
                  <p className="text-[10px] text-stone-400 mt-1">
                    Paste a direct video file link (.mp4, etc.) or a gs:// Cloud Storage path, or upload a file directly. YouTube links are not supported, YouTube blocks automated downloads from cloud servers.
                  </p>
                </div>
              </div>

              <div className="border-t border-stone-200 pt-5 space-y-3">
                <StepHeader number={2} title="Languages" subtitle="Dub and subtitle" />
                <div className="grid grid-cols-2 gap-4">
                  {/* Dubbing Languages */}
                  <div>
                    <SectionLabel>Dubbing</SectionLabel>
                    <div className="flex flex-wrap gap-1.5">
                      {AVAILABLE_LANGUAGES.map(lang => (
                        <button
                          type="button"
                          key={lang}
                          onClick={() => toggleDubLanguage(lang)}
                          className={`text-xs px-2.5 py-1 rounded-lg border transition ${
                            selectedDubLanguages.includes(lang) ? PILL_COLORS.cyan : PILL_UNSELECTED
                          }`}
                        >
                          {lang}
                        </button>
                      ))}
                    </div>
                  </div>

                  {/* Subtitle Languages */}
                  <div>
                    <SectionLabel>Subtitles</SectionLabel>
                    <div className="flex flex-wrap gap-1.5">
                      {AVAILABLE_LANGUAGES.map(lang => (
                        <button
                          type="button"
                          key={lang}
                          onClick={() => toggleSubtitleLanguage(lang)}
                          className={`text-xs px-2.5 py-1 rounded-lg border transition ${
                            selectedSubtitleLanguages.includes(lang) ? PILL_COLORS.sky : PILL_UNSELECTED
                          }`}
                        >
                          {lang}
                        </button>
                      ))}
                    </div>
                  </div>
                </div>
              </div>

              <div className="border-t border-stone-200 pt-5 space-y-3">
                <StepHeader number={3} title="Target platforms" subtitle="Output formats" />
                <div className="flex flex-wrap gap-1.5">
                  {AVAILABLE_PLATFORMS.map(plat => (
                    <button
                      type="button"
                      key={plat}
                      onClick={() => togglePlatform(plat)}
                      className={`text-xs px-2.5 py-1 rounded-lg border transition ${
                        selectedPlatforms.includes(plat) ? PILL_COLORS.violet : PILL_UNSELECTED
                      }`}
                    >
                      {plat}
                    </button>
                  ))}
                </div>
              </div>

              <p className="text-center text-[11px] text-stone-400 pt-1">
                {selectedDubLanguages.length} dub · {selectedSubtitleLanguages.length} subtitle language{selectedSubtitleLanguages.length !== 1 ? 's' : ''} · {selectedPlatforms.length} platform{selectedPlatforms.length !== 1 ? 's' : ''}
              </p>

              <button
                type="submit"
                disabled={loading}
                className={`w-full py-3 px-4 rounded-xl text-sm font-semibold transition flex items-center justify-center gap-2 disabled:cursor-not-allowed ${
                  loading
                    ? 'bg-orange-50 border border-orange-300 text-orange-700'
                    : 'bg-orange-600 hover:bg-orange-700 text-white shadow-md shadow-orange-600/20'
                }`}
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Pipeline running...</span>
                  </>
                ) : (
                  <>
                    <Play className="w-4 h-4 fill-white" />
                    <span>Trigger Fan-Out Pipeline</span>
                  </>
                )}
              </button>
            </form>
          </div>

          {/* Performance Benchmark Card — every number here is derived from
              the real per-worker execution_time_seconds already returned by
              the API (the same values shown on each worker card below), not
              a fabricated placeholder.

              Scoped to the fan-out stage specifically (workers 01/03/04 run
              concurrently, 02 depends on 01 and runs after), excluding the
              orchestrator's Gemini synthesis step that follows fan-in —
              that step is constant regardless of how the workers themselves
              executed, so mixing it into only one side of the comparison
              would distort it (confirmed against a real run: total
              pipeline_runtime_seconds includes ~10s of synthesis overhead
              that has nothing to do with worker concurrency). */}
          {pipelineData && (() => {
            const rawWorkers = pipelineData.master_release_package?.raw_department_data || [];
            const timeOf = (id) => rawWorkers.find(w => w.worker_id === id)?.execution_time_seconds || 0;
            const w01 = timeOf('worker_01'), w02 = timeOf('worker_02'), w03 = timeOf('worker_03'), w04 = timeOf('worker_04');

            const actualFanOutSeconds = Math.max(w01, w03, w04) + w02; // 01/03/04 concurrent, then 02
            const estimatedSequentialSeconds = w01 + w02 + w03 + w04; // same 4 calls, one after another
            const speedupMultiplier = actualFanOutSeconds > 0 ? estimatedSequentialSeconds / actualFanOutSeconds : 1;
            const showSpeedupBadge = speedupMultiplier > 1.05; // guard against noise on trivially fast runs

            return (
            <div className="bg-white border border-orange-100 rounded-2xl p-5 shadow-sm shadow-stone-200 space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-stone-500 uppercase tracking-wider flex items-center gap-1.5">
                  <Zap className="w-3.5 h-3.5 text-amber-500" /> Concurrency Benchmark
                </span>
                {showSpeedupBadge && (
                  <span className="text-xs font-semibold text-amber-700 bg-amber-50 border border-amber-200 px-2 py-0.5 rounded-full">
                    {speedupMultiplier.toFixed(1)}× Speedup
                  </span>
                )}
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="p-3 bg-stone-50 rounded-xl border border-stone-200" title="max(worker_01, worker_03, worker_04) + worker_02 — the 4 workers' own measured times, combined the way they actually ran">
                  <p className="text-xs text-stone-500">Parallel Fan-Out</p>
                  <p className="text-2xl font-bold text-amber-600 mt-0.5">
                    {actualFanOutSeconds.toFixed(2)}s
                  </p>
                </div>
                <div className="p-3 bg-stone-50 rounded-xl border border-stone-200" title="worker_01 + worker_02 + worker_03 + worker_04 — the same measured times, summed as if run one after another">
                  <p className="text-xs text-stone-500">Est. Sequential</p>
                  <p className="text-2xl font-bold text-stone-400 mt-0.5">{estimatedSequentialSeconds.toFixed(2)}s</p>
                </div>
              </div>
              <p className="text-[10px] text-stone-400">
                Fan-out stage only ({pipelineData.pipeline_runtime_seconds}s total pipeline, including AI synthesis)
              </p>
            </div>
            );
          })()}
        </div>

        {/* Right: Worker Cards & Live Media Deliverables */}
        <div className="space-y-6 min-w-0">

          {/* Worker Status Grid */}
          <div className="bg-white border border-stone-200 rounded-2xl p-6 shadow-sm shadow-stone-200">
            <div className="flex items-center justify-between mb-4">
              <div className="flex items-center gap-2.5 text-stone-900 font-semibold">
                <IconChip icon={<Square className="w-4 h-4" />} color="cyan" />
                <h2>Department Workers (Fan-Out Stage)</h2>
              </div>
              <div className="flex items-center gap-3">
                <span className={`text-[10px] font-semibold px-2.5 py-1 rounded-full border ${
                  fanOutStateLabel === 'running'
                    ? 'bg-orange-50 border-orange-200 text-orange-700'
                    : fanOutStateLabel === 'complete'
                    ? 'bg-amber-50 border-amber-200 text-amber-700'
                    : 'bg-stone-100 border-stone-200 text-stone-500'
                }`}>
                  {totalWorkerCount} workers · {fanOutStateLabel}{fanOutStateLabel === 'running' ? ` (${runningCount})` : ''}
                </span>
                {pipelineElapsedMs !== null && (
                  <span className="text-[10px] font-mono text-stone-400">{formatElapsed(pipelineElapsedMs)} elapsed</span>
                )}
              </div>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 2xl:grid-cols-4 gap-4">
              <WorkerCard
                icon={<IconChip size="sm" color="sky" icon={<Square className="w-3.5 h-3.5" />} />}
                title="1. Subtitles (.srt)"
                state={workerStates.worker_01}
                placeholder="Generating timecoded subtitle files..."
                summarize={summarizeWorkerOne}
                accent="sky"
              />
              <WorkerCard
                icon={<IconChip size="sm" color="cyan" icon={<Square className="w-3.5 h-3.5" />} />}
                title="2. Dubbing (Audio)"
                state={workerStates.worker_02}
                placeholder="Synthesizing multi-speaker audio tracks..."
                summarize={summarizeWorkerTwo}
                accent="cyan"
              />
              <WorkerCard
                icon={<IconChip size="sm" color="violet" icon={<Square className="w-3.5 h-3.5" />} />}
                title="3. Smart VFX (Video)"
                state={workerStates.worker_03}
                placeholder="Calculating 9:16 and 1:1 crop coordinates..."
                summarize={summarizeWorkerThree}
                accent="violet"
              />
              <WorkerCard
                icon={<IconChip size="sm" color="emerald" icon={<Square className="w-3.5 h-3.5" />} />}
                title="4. Compliance Clearance"
                state={workerStates.worker_04}
                placeholder="Parallel open-web clearance check..."
                summarize={summarizeWorkerFour}
                accent="emerald"
              />
            </div>
          </div>

          {/* Master Media Deliverables Center */}
          {pipelineData && (
            <div className="bg-white border border-stone-200 rounded-2xl p-6 shadow-sm shadow-stone-200 space-y-6">

              <div className="flex items-center justify-between border-b border-stone-200 pb-3">
                <div className="flex items-center gap-2.5 text-stone-900 font-semibold">
                  <IconChip icon={<Square className="w-4 h-4" />} color="emerald" />
                  <h2>Master Distribution Deliverables</h2>
                </div>
                <span className="text-xs font-bold px-2.5 py-0.5 rounded-full bg-amber-50 text-amber-700 border border-amber-200">
                  READY FOR RELEASE
                </span>
              </div>

              {/* 1. Synthesized Dubbing & Dialogue Source */}
              <div className="p-4 bg-stone-50 rounded-xl border border-stone-200 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-stone-700 flex items-center gap-2">
                    <Volume2 className="w-4 h-4 text-orange-600" /> Synthesized Dubbing & Dialogue Source
                  </span>
                  <span className="text-[10px] text-orange-700 bg-orange-50 px-2.5 py-0.5 rounded-full border border-orange-200">
                    Source: {workerOneData?.transcription_source || "Multimodal Video Auto-Detect"}
                  </span>
                </div>

                <div className="p-2.5 bg-white rounded-lg border border-stone-200 text-xs">
                  <span className="text-stone-400 font-mono text-[10px] uppercase">Processed Dialogue Baseline:</span>
                  <p className="text-stone-700 font-mono mt-0.5 italic">
                    "{workerOneData?.active_dialogue || formData.script_text}"
                  </p>
                </div>

                {selectedDubLanguages.map(lang => {
                  const dubbedTrackPath = workerTwoData?.dubbed_tracks?.[lang];
                  const downloadedFilename = dubbedTrackPath?.split("/").pop();
                  const audioUrl = downloadedFilename
                    ? `${API_BASE_URL}/api/v1/downloads/audio/${encodeURIComponent(downloadedFilename)}`
                    : null;

                  return (
                  <div key={lang} className="flex flex-col sm:flex-row items-center justify-between gap-3 p-3 bg-white rounded-lg border border-stone-200">
                    <div className="w-full sm:w-auto">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-stone-900 font-semibold">{lang} Dubbed Voiceover</span>
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-stone-100 text-stone-500 font-mono">
                          {LANG_VOICE_CODES[lang] || 'en-US'}
                        </span>
                      </div>
                      <p className="text-xs text-orange-700 mt-1 font-mono italic">
                        {workerOneData?.localized_dialogues?.[lang]
                          ? `"${workerOneData.localized_dialogues[lang]}"`
                          : "Awaiting Gemini localization output."}
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => playDubbedAudio(lang, audioUrl)}
                        disabled={!audioUrl}
                        title={audioUrl ? "Plays the actual generated Cloud TTS audio" : "No dubbed audio generated for this language yet"}
                        className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-semibold transition ${
                          playingAudio === lang
                            ? 'bg-rose-600 text-white animate-pulse shadow-md shadow-rose-600/20'
                            : 'bg-orange-600 hover:bg-orange-700 text-white shadow-md shadow-orange-600/20'
                        } disabled:cursor-not-allowed disabled:opacity-50`}
                      >
                        {playingAudio === lang ? (
                          <>
                            <VolumeX className="w-4 h-4" />
                            <span>Stop Dub</span>
                          </>
                        ) : (
                          <>
                            <Volume2 className="w-4 h-4" />
                            <span>Play Dub</span>
                          </>
                        )}
                      </button>
                      {audioUrl ? (
                        <a
                          href={audioUrl}
                          download={downloadedFilename}
                          className="flex items-center gap-2 px-3.5 py-2 bg-amber-600 hover:bg-amber-700 rounded-lg text-xs font-semibold text-white transition shadow-md shadow-amber-600/20"
                        >
                          <Download className="w-4 h-4" />
                          <span>Download MP3</span>
                        </a>
                      ) : (
                        <span className="text-[10px] text-stone-400">MP3 unavailable</span>
                      )}
                    </div>
                  </div>
                  );
                })}
              </div>

              {/* 2. Visual Social Video Crop Section */}
              <div className="p-4 bg-stone-50 rounded-xl border border-stone-200 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-stone-700 flex items-center gap-2">
                    <Smartphone className="w-4 h-4 text-orange-600" /> Dynamic Social Video Reframing (Worker 3)
                  </span>
                  <span className="text-[10px] text-amber-700 bg-amber-50 px-2 py-0.5 rounded border border-amber-200">
                    FFmpeg Cropped
                  </span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
                  {(() => { const youtubeEmbedUrl = getYouTubeEmbedUrl(formData.video_url); return selectedPlatforms.map(platform => {
                    const style = PLATFORM_FRAME_STYLES[platform] || {
                      label: platform, box: "w-40 h-40", border: "border-stone-300"
                    };
                    const ffmpegCommand = workerThreeData?.ffmpeg_render_pipeline?.[platform];

                    return (
                      <div key={platform} className="p-3 bg-white rounded-lg border border-stone-200 flex flex-col items-center">
                        <p className="text-xs font-semibold text-stone-700 mb-2">{style.label}</p>
                        <div className={`${style.box} bg-black rounded-lg overflow-hidden border ${style.border} relative shadow-inner`}>
                          {youtubeEmbedUrl ? (
                            <iframe
                              src={youtubeEmbedUrl}
                              title={`${platform} preview`}
                              allow="autoplay; encrypted-media"
                              className="w-full h-full"
                              frameBorder="0"
                            />
                          ) : (
                            <video
                              src={formData.video_url}
                              controls
                              autoPlay
                              muted
                              loop
                              playsInline
                              className="object-cover w-full h-full"
                            />
                          )}
                        </div>
                        <button
                          disabled={!ffmpegCommand}
                          onClick={() => triggerDownload(
                            `${formData.title.toLowerCase().replace(/[^a-z0-9]/g, '_')}_${platform.replace(/[^a-z0-9]/gi, '_')}_crop.sh`,
                            ffmpegCommand || ""
                          )}
                          className="mt-2 w-full flex items-center justify-center gap-1.5 px-2.5 py-1.5 bg-stone-50 hover:bg-stone-100 border border-stone-200 rounded-lg text-[10px] font-mono text-stone-500 disabled:cursor-not-allowed disabled:opacity-50 transition"
                          title={ffmpegCommand}
                        >
                          <Download className="w-3 h-3" />
                          <span>Download FFmpeg Crop Cmd</span>
                        </button>
                      </div>
                    );
                  }); })()}
                </div>

                {/* Final Assembly (Google Cloud Transcoder) — additive stage */}
                <div className="pt-3 mt-1 border-t border-stone-200 space-y-3">
                  <p className="text-[11px] text-stone-500">
                    Render broadcast-ready .mp4s combining the crop, dub audio, and subtitles above via Google Cloud Transcoder.
                    Choose which language to use for each platform (from what you selected above):
                  </p>

                  <div className="space-y-1.5">
                    {selectedPlatforms.map(platform => {
                      const rendition = getPlatformRendition(platform);
                      return (
                        <div key={platform} className="flex flex-col sm:flex-row sm:items-center gap-2 p-2.5 bg-white rounded-lg border border-stone-200">
                          <span className="text-xs font-semibold text-stone-700 sm:w-36 shrink-0">{platform}</span>
                          <select
                            value={rendition.dubLanguage}
                            onChange={(e) => setPlatformRenditions(prev => ({ ...prev, [platform]: { ...prev[platform], dubLanguage: e.target.value } }))}
                            className="flex-1 px-2 py-1.5 bg-white border border-stone-300 rounded-lg text-xs text-stone-700"
                          >
                            <option value="">No dub audio</option>
                            {selectedDubLanguages.map(lang => <option key={lang} value={lang}>{lang} dub</option>)}
                          </select>
                          <select
                            value={rendition.subtitleLanguage}
                            onChange={(e) => setPlatformRenditions(prev => ({ ...prev, [platform]: { ...prev[platform], subtitleLanguage: e.target.value } }))}
                            className="flex-1 px-2 py-1.5 bg-white border border-stone-300 rounded-lg text-xs text-stone-700"
                          >
                            <option value="">No subtitles</option>
                            {selectedSubtitleLanguages.map(lang => <option key={lang} value={lang}>{lang} subs</option>)}
                          </select>
                        </div>
                      );
                    })}
                  </div>

                  <button
                    onClick={handleAssembleFinal}
                    disabled={renderSubmitting || !workerTwoData || selectedPlatforms.length === 0}
                    className="w-full flex items-center justify-center gap-2 px-3.5 py-2 bg-orange-600 hover:bg-orange-700 rounded-lg text-xs font-semibold text-white shadow-md shadow-orange-600/20 transition disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {renderSubmitting ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Film className="w-4 h-4" />
                    )}
                    <span>Render Final Video</span>
                  </button>
                  {selectedPlatforms.length === 0 && (
                    <p className="text-[10px] text-rose-600 text-center">
                      Select at least one target platform above before rendering.
                    </p>
                  )}

                  {renderJob && (() => {
                    const isActive = RENDER_ACTIVE_STATUSES.has(renderJob.overall_status);
                    const completedCount = renderJob.outputs.filter(o => RENDER_TERMINAL_STATUSES.has(o.status)).length;
                    const totalCount = renderJob.outputs.length;
                    const progressPercent = totalCount > 0 ? Math.round((completedCount / totalCount) * 100) : 0;
                    const elapsedMs = (isActive ? renderNowTick : (renderCompletedAtMs || renderNowTick)) - renderJob.created_at * 1000;

                    return (
                    <div className="space-y-2">
                      <div className="flex items-center justify-between text-[10px] uppercase tracking-wider text-stone-400">
                        <span>Render Job {renderJob.render_job_id.slice(0, 8)} — {renderJob.overall_status}</span>
                        <span className="font-mono normal-case text-stone-500">
                          {isActive ? `⏱ ${formatElapsed(elapsedMs)} elapsed` : `Done in ${formatElapsed(elapsedMs)}`}
                        </span>
                      </div>

                      <div className="space-y-1">
                        <div className="w-full h-1.5 bg-stone-200 rounded-full overflow-hidden">
                          <div
                            className={`h-full rounded-full transition-all duration-500 ${
                              renderJob.overall_status === 'FAILED' ? 'bg-rose-500' : 'bg-orange-500'
                            }`}
                            style={{ width: `${progressPercent}%` }}
                          />
                        </div>
                        <p className="text-[10px] text-stone-400">
                          {completedCount} of {totalCount} render{totalCount !== 1 ? 's' : ''} complete
                        </p>
                      </div>

                      {renderJob.outputs.map((output) => (
                        <div
                          key={`${output.platform}-${output.dub_language}-${output.subtitle_language}`}
                          className="flex items-center justify-between gap-3 p-2.5 bg-white rounded-lg border border-stone-200"
                        >
                          <div className="text-xs text-stone-700">
                            <span className="font-semibold text-stone-900">{output.platform}</span>
                            {" · "}{output.dub_language ? `${output.dub_language} dub` : "no dub"}
                            {" + "}{output.subtitle_language ? `${output.subtitle_language} subs` : "no subs"}
                            {output.error && (
                              <p className="text-[10px] text-rose-600 mt-0.5 max-w-xs truncate" title={output.error}>
                                {output.error}
                              </p>
                            )}
                          </div>
                          {output.status === "SUCCEEDED" && output.download_url ? (
                            <a
                              href={output.download_url}
                              className="flex items-center gap-1.5 px-2.5 py-1.5 bg-amber-600 hover:bg-amber-700 rounded-lg text-[11px] font-semibold text-white transition"
                            >
                              <Download className="w-3.5 h-3.5" />
                              <span>Download</span>
                            </a>
                          ) : (
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                              output.status === "FAILED" || output.status === "NOT_CONFIGURED"
                                ? 'bg-rose-50 text-rose-700 border-rose-200'
                                : 'bg-orange-50 text-orange-700 border-orange-200 animate-pulse'
                            }`}>
                              {output.status}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                    );
                  })()}
                </div>
              </div>

              {/* 3. Subtitles & Official Clearance Certificates Downloads */}
              <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
                {selectedSubtitleLanguages.map(lang => {
                  const subtitlePath = workerOneData?.subtitle_files?.[lang];
                  const subtitleFilename = subtitlePath?.split("/").pop();

                  return (
                  <a
                    key={lang}
                    href={subtitleFilename ? `${API_BASE_URL}/api/v1/downloads/subtitle/${encodeURIComponent(subtitleFilename)}` : undefined}
                    download={subtitleFilename}
                    aria-disabled={!subtitleFilename}
                    className={`p-3 bg-white border border-stone-200 rounded-xl flex items-center justify-between transition text-left ${
                      subtitleFilename ? 'hover:bg-stone-50 hover:border-orange-300' : 'cursor-not-allowed opacity-50 pointer-events-none'
                    }`}
                  >
                    <div className="flex items-center gap-2.5">
                      <FileText className="w-4 h-4 text-orange-600" />
                      <div>
                        <p className="text-xs font-semibold text-stone-800">{lang} Subtitle File</p>
                        <p className="text-[10px] text-stone-400">.srt format</p>
                      </div>
                    </div>
                    <Download className="w-4 h-4 text-stone-400" />
                  </a>
                  );
                })}

                {(() => {
                  const runId = workerFourData?.parallel_run_id;
                  const isLiveVerified = Boolean(runId) && runId !== "local_mock_mode";
                  const shortRunId = runId && runId.length > 18 ? `${runId.slice(0, 18)}…` : runId;

                  return (
                    <button
                      disabled={!workerFourData}
                      onClick={() => triggerDownload(
                        `${formData.title.toLowerCase().replace(/[^a-z0-9]/g, '_')}_compliance_certificate.txt`,
                        `========================================\n` +
                        `SLATEPARALLEL COMPLIANCE AUDIT CERTIFICATE\n` +
                        `Project: ${formData.title}\n` +
                        `Status: ${pipelineData?.master_release_package?.gemini_director_report?.status || "APPROVED"}\n` +
                        `Parallel Task Run ID: ${workerFourData?.parallel_run_id || "n/a"}\n` +
                        `Parallel Interaction ID: ${workerFourData?.parallel_interaction_id || "n/a"}\n` +
                        `Live Web Context: ${workerFourData?.live_web_context || "n/a"}\n` +
                        `Compliance Checks:\n${(workerFourData?.compliance_checks || []).map(c => ` - ${c}`).join("\n")}\n` +
                        `========================================`
                      )}
                      className="p-3 bg-white hover:bg-stone-50 border border-stone-200 hover:border-amber-300 rounded-xl flex items-center justify-between transition text-left disabled:cursor-not-allowed disabled:opacity-50"
                    >
                      <div className="flex items-center gap-2.5 min-w-0">
                        <FileCheck className="w-4 h-4 text-amber-600 shrink-0" />
                        <div className="min-w-0">
                          <p className="text-xs font-semibold text-stone-800">Compliance Certificate</p>
                          <p
                            className={`text-[10px] font-mono truncate ${isLiveVerified ? 'text-amber-600' : 'text-stone-400'}`}
                            title={isLiveVerified ? `Full Parallel Task Run ID: ${runId}` : 'No live API key supplied; using local compliance cache'}
                          >
                            {isLiveVerified ? `✓ Verified via Parallel Task ${shortRunId}` : 'Local mock mode — not live-verified'}
                          </p>
                        </div>
                      </div>
                      <Download className="w-4 h-4 text-stone-400 shrink-0" />
                    </button>
                  );
                })()}
              </div>

            </div>
          )}

        </div>
      </main>
    </div>
  );
}

// Turns each worker's raw data payload into a short, readable summary
// instead of dumping JSON.stringify(data) — that was unreadable in a
// two-line-clamped card and told the user nothing they could act on.
function summarizeWorkerOne(data) {
  const langs = Object.keys(data.localized_dialogues || {}).filter(l => data.localized_dialogues[l]);
  const source = (data.transcription_source || '').startsWith('video_transcription')
    ? 'from video transcription'
    : 'from master script';
  return {
    text: langs.length ? `Translated to ${langs.join(', ')} ${source}` : 'No translations produced',
    warning: data.transcription_error ? `Transcription fallback: ${data.transcription_error}` : null,
  };
}

function summarizeWorkerTwo(data) {
  const langs = Object.keys(data.dubbed_tracks || {});
  const errorLangs = Object.keys(data.audio_errors || {});
  return {
    text: langs.length ? `Dubbed audio ready for ${langs.join(', ')}` : 'No dubbed audio synthesized',
    warning: errorLangs.length ? `Dub failed for ${errorLangs.join(', ')}` : null,
  };
}

function summarizeWorkerThree(data) {
  const platforms = Object.keys(data.ffmpeg_render_pipeline || {});
  return {
    text: platforms.length ? `Crop commands ready for ${platforms.join(', ')}` : 'No crop commands generated',
    warning: null,
  };
}

function summarizeWorkerFour(data) {
  const checks = data.compliance_checks || [];
  const failedNote = checks.find(c => /error|fail/i.test(c));
  const runId = data.parallel_run_id;
  const isLiveVerified = Boolean(runId) && runId !== "local_mock_mode";
  return {
    text: `${checks.length} compliance check${checks.length !== 1 ? 's' : ''}${checks[0] ? `: ${checks[0]}` : ''}`,
    warning: failedNote && failedNote !== checks[0] ? failedNote : null,
    verified: isLiveVerified ? `Verified via Parallel Task ${runId}` : null,
  };
}

const ICON_CHIP_COLORS = {
  cyan: 'bg-orange-50 text-orange-600',
  sky: 'bg-amber-50 text-amber-600',
  violet: 'bg-orange-50 text-orange-600',
  emerald: 'bg-amber-50 text-amber-600',
};

// A colored icon "chip" instead of a bare glyph — gives section/card icons
// visual weight. Every category now renders as a shade of orange (orange
// or amber) instead of a distinct hue per domain.
function IconChip({ icon, color = 'cyan', size = 'md' }) {
  const sizeClasses = size === 'sm' ? 'w-7 h-7' : 'w-9 h-9';
  return (
    <div className={`${sizeClasses} rounded-lg flex items-center justify-center shrink-0 ${ICON_CHIP_COLORS[color]}`}>
      {icon}
    </div>
  );
}

// Dot-indicator status pill — mirrors the accent color of the worker it
// belongs to instead of a single generic "running" color, so the badge and
// the card's icon chip read as one unit.
const STATUS_BADGE_COLORS = {
  RUNNING: {
    cyan: 'bg-orange-50 border-orange-200 text-orange-700',
    sky: 'bg-amber-50 border-amber-200 text-amber-700',
    violet: 'bg-orange-50 border-orange-200 text-orange-700',
    emerald: 'bg-amber-50 border-amber-200 text-amber-700',
  },
  SUCCESS: 'bg-amber-50 border-amber-200 text-amber-700',
  IDLE: 'bg-stone-100 border-stone-200 text-stone-500',
};
const DOT_COLORS = {
  cyan: 'bg-orange-500', sky: 'bg-amber-500', violet: 'bg-orange-500', emerald: 'bg-amber-500',
};

function WorkerCard({ icon, title, state, placeholder, summarize, accent = 'cyan' }) {
  const isRunning = state.status === 'RUNNING';
  const isSuccess = state.status === 'SUCCESS';
  const summary = isSuccess && summarize ? summarize(state.data) : null;

  const badgeClasses = isRunning
    ? STATUS_BADGE_COLORS.RUNNING[accent]
    : isSuccess
    ? STATUS_BADGE_COLORS.SUCCESS
    : STATUS_BADGE_COLORS.IDLE;
  const dotClasses = isRunning ? DOT_COLORS[accent] : isSuccess ? 'bg-amber-500' : 'bg-stone-400';

  return (
    <div className="p-4 rounded-xl bg-stone-50 border border-stone-200 transition flex flex-col justify-between min-h-32">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-stone-800">
          {icon}
          <span>{title}</span>
        </div>
        <span className={`flex items-center gap-1.5 text-[10px] font-bold px-2 py-0.5 rounded-full border shrink-0 ${badgeClasses}`}>
          <span className={`w-1.5 h-1.5 rounded-full ${dotClasses} ${isRunning ? 'animate-pulse' : ''}`} />
          {isRunning ? 'Running' : isSuccess ? `${state.time}s` : 'Idle'}
        </span>
      </div>

      <div className="mt-2">
        <p className="text-xs text-stone-500 line-clamp-2">
          {isSuccess ? (summary?.text || placeholder) : placeholder}
        </p>
        {summary?.warning && (
          <p className="text-[10px] text-amber-600 mt-1 line-clamp-1" title={summary.warning}>
            ⚠ {summary.warning}
          </p>
        )}
        {summary?.verified && (
          <p className="text-[10px] text-amber-600 font-mono mt-1 line-clamp-1" title={summary.verified}>
            ✓ {summary.verified}
          </p>
        )}
      </div>

      {/* Activity track — an indeterminate pulse while running and a solid
          fill on success communicate state honestly; there is no per-worker
          progress percentage from the backend to show here, so this never
          fabricates one (RUNNING/SUCCESS/IDLE is all the API reports). */}
      <div className="mt-3 w-full h-1 bg-stone-200 rounded-full overflow-hidden">
        <div className={`h-full rounded-full transition-all duration-500 ${
          isRunning ? `w-full ${dotClasses} opacity-50 animate-pulse` : isSuccess ? `w-full ${dotClasses}` : 'w-0'
        }`} />
      </div>
    </div>
  );
}
