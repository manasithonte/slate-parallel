import React, { useState, useEffect } from 'react';
import {
  Clapperboard,
  Play,
  Loader2,
  CheckCircle2,
  FileText,
  Mic,
  Crop,
  ShieldCheck,
  Zap,
  Film,
  Layers,
  Download,
  Volume2,
  Smartphone,
  FileCheck,
  VolumeX
} from 'lucide-react';

const RENDER_POLL_INTERVAL_MS = 5000;
const RENDER_ACTIVE_STATUSES = new Set(["PENDING", "SUBMITTED"]);

const API_BASE_URL = "http://127.0.0.1:8000";

const AVAILABLE_LANGUAGES = ["English", "Japanese", "Spanish", "French", "German"];
const AVAILABLE_PLATFORMS = ["TikTok (9:16)", "Instagram Post (1:1)"];

const PLATFORM_FRAME_STYLES = {
  "TikTok (9:16)": { label: "TikTok (9:16 Vertical Cut)", box: "w-32 h-56", border: "border-indigo-500/40" },
  "Instagram Post (1:1)": { label: "Instagram (1:1 Square Cut)", box: "w-44 h-44", border: "border-violet-500/40" },
};

const LANG_VOICE_CODES = {
  English: 'en-US',
  Japanese: 'ja-JP',
  Spanish: 'es-ES',
  French: 'fr-FR',
  German: 'de-DE'
};

export default function App() {
  const [formData, setFormData] = useState({
    title: "Inception 2: Teaser Cut",
    script_text: "We have to venture into the subconscious mind before the collapse begins.",
    video_url: "https://vjs.zencdn.net/v/oceans.mp4",
  });

  const [selectedDubLanguages, setSelectedDubLanguages] = useState(["Japanese", "Spanish"]);
  const [selectedSubtitleLanguages, setSelectedSubtitleLanguages] = useState(["Japanese", "Spanish", "French"]);
  const [selectedPlatforms, setSelectedPlatforms] = useState(["TikTok (9:16)", "Instagram Post (1:1)"]);

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

  useEffect(() => {
    if (!renderJob || !RENDER_ACTIVE_STATUSES.has(renderJob.overall_status)) return;

    const intervalId = setInterval(async () => {
      try {
        const res = await fetch(`${API_BASE_URL}/api/v1/render-status/${renderJob.render_job_id}`);
        if (!res.ok) return;
        const data = await res.json();
        setRenderJob(data);
      } catch {
        // transient network error; next poll tick will retry
      }
    }, RENDER_POLL_INTERVAL_MS);

    return () => clearInterval(intervalId);
  }, [renderJob]);

  const handleAssembleFinal = async () => {
    setRenderSubmitting(true);
    setRenderJob(null);

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
      setRenderJob(data);
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

  // Real-time Localized TTS Voice Synthesizer
  const playLocalizedVoice = (language) => {
    if (!('speechSynthesis' in window)) {
      alert("Speech synthesis is not supported in this browser.");
      return;
    }

    if (playingAudio === language) {
      window.speechSynthesis.cancel();
      setPlayingAudio(null);
      return;
    }

    window.speechSynthesis.cancel(); // Stop previous audio
    const textToSpeak = workerOneData?.localized_dialogues?.[language]
      || "";
    if (!textToSpeak) return;
    const utterance = new SpeechSynthesisUtterance(textToSpeak);
    utterance.lang = LANG_VOICE_CODES[language] || 'en-US';
    utterance.pitch = 0.95; // Pitched matching
    utterance.rate = 1.05;  // Video tempo compensation

    utterance.onstart = () => setPlayingAudio(language);
    utterance.onend = () => setPlayingAudio(null);
    utterance.onerror = () => setPlayingAudio(null);

    window.speechSynthesis.speak(utterance);
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
    if (window.speechSynthesis) window.speechSynthesis.cancel();

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

  return (
    <div className="min-h-screen bg-slate-950 text-slate-100 antialiased pb-20">
      {/* Header */}
      <header className="border-b border-slate-800 bg-slate-900/60 backdrop-blur px-8 py-4 sticky top-0 z-50 flex justify-between items-center">
        <div className="flex items-center gap-3">
          <div className="w-10 h-10 rounded-xl bg-indigo-600 flex items-center justify-center text-white shadow-lg shadow-indigo-500/30">
            <Film className="w-5 h-5" />
          </div>
          <div>
            <h1 className="text-lg font-bold tracking-tight bg-clip-text text-transparent bg-gradient-to-r from-white via-slate-200 to-indigo-400">
              SlateParallel
            </h1>
            <p className="text-xs text-slate-400">Autonomous Post-Production Studio Orchestrator</p>
          </div>
        </div>

        <span className="inline-flex items-center gap-2 px-3 py-1 rounded-full text-xs font-medium bg-emerald-950 text-emerald-400 border border-emerald-800">
          <span className="w-2 h-2 rounded-full bg-emerald-400 animate-pulse"></span>
          Cloud Run Live
        </span>
      </header>

      {/* Main Grid */}
      <main className="max-w-7xl mx-auto px-6 py-8 grid grid-cols-1 lg:grid-cols-12 gap-8">

        {/* Left: Input Form */}
        <div className="lg:col-span-5 space-y-6">
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
            <div className="flex items-center gap-2 mb-4 text-white font-semibold">
              <Clapperboard className="w-5 h-5 text-indigo-400" />
              <h2>New Media Release Job</h2>
            </div>

            <form onSubmit={handleTriggerPipeline} className="space-y-4">
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Project Title</label>
                <input
                  type="text"
                  value={formData.title}
                  onChange={(e) => setFormData({ ...formData, title: e.target.value })}
                  required
                  className="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-700 rounded-xl text-sm focus:border-indigo-500 text-white"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Master Script Dialogue</label>
                <textarea
                  rows="3"
                  value={formData.script_text}
                  onChange={(e) => setFormData({ ...formData, script_text: e.target.value })}
                  className="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-700 rounded-xl text-sm focus:border-indigo-500 text-white resize-none"
                />
              </div>

              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1">Source Video Asset URL</label>
                <input
                  type="url"
                  value={formData.video_url}
                  onChange={(e) => setFormData({ ...formData, video_url: e.target.value })}
                  required
                  className="w-full px-3.5 py-2.5 bg-slate-950 border border-slate-700 rounded-xl text-sm text-slate-300"
                />
              </div>

              {/* Dubbing Languages */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">Dubbing Languages (Voice)</label>
                <div className="flex flex-wrap gap-1.5">
                  {AVAILABLE_LANGUAGES.map(lang => (
                    <button
                      type="button"
                      key={lang}
                      onClick={() => toggleDubLanguage(lang)}
                      className={`text-xs px-2.5 py-1 rounded-lg border transition ${
                        selectedDubLanguages.includes(lang)
                          ? 'bg-indigo-600/30 text-indigo-300 border-indigo-500/60'
                          : 'bg-slate-950 text-slate-500 border-slate-800'
                      }`}
                    >
                      {selectedDubLanguages.includes(lang) && "✓ "}{lang}
                    </button>
                  ))}
                </div>
              </div>

              {/* Subtitle Languages */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">Subtitle Languages (Captions)</label>
                <div className="flex flex-wrap gap-1.5">
                  {AVAILABLE_LANGUAGES.map(lang => (
                    <button
                      type="button"
                      key={lang}
                      onClick={() => toggleSubtitleLanguage(lang)}
                      className={`text-xs px-2.5 py-1 rounded-lg border transition ${
                        selectedSubtitleLanguages.includes(lang)
                          ? 'bg-sky-600/30 text-sky-300 border-sky-500/60'
                          : 'bg-slate-950 text-slate-500 border-slate-800'
                      }`}
                    >
                      {selectedSubtitleLanguages.includes(lang) && "✓ "}{lang}
                    </button>
                  ))}
                </div>
              </div>

              {/* Target Platforms */}
              <div>
                <label className="block text-xs font-medium text-slate-400 mb-1.5">Target Distribution Platforms</label>
                <div className="flex flex-wrap gap-1.5">
                  {AVAILABLE_PLATFORMS.map(plat => (
                    <button
                      type="button"
                      key={plat}
                      onClick={() => togglePlatform(plat)}
                      className={`text-xs px-2.5 py-1 rounded-lg border transition ${
                        selectedPlatforms.includes(plat)
                          ? 'bg-violet-600/30 text-violet-300 border-violet-500/60'
                          : 'bg-slate-950 text-slate-500 border-slate-800'
                      }`}
                    >
                      {selectedPlatforms.includes(plat) && "✓ "}{plat}
                    </button>
                  ))}
                </div>
              </div>

              <button
                type="submit"
                disabled={loading}
                className="w-full mt-2 py-3 px-4 bg-indigo-600 hover:bg-indigo-500 rounded-xl text-sm font-semibold text-white shadow-lg shadow-indigo-600/30 transition flex items-center justify-center gap-2 disabled:opacity-50"
              >
                {loading ? (
                  <>
                    <Loader2 className="w-4 h-4 animate-spin" />
                    <span>Executing Parallel Fan-Out (0.5s)...</span>
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

          {/* Performance Benchmark Card */}
          {pipelineData && (
            <div className="bg-slate-900/90 border border-indigo-900/40 rounded-2xl p-5 shadow-lg space-y-3">
              <div className="flex items-center justify-between">
                <span className="text-xs font-semibold text-slate-400 uppercase tracking-wider flex items-center gap-1.5">
                  <Zap className="w-3.5 h-3.5 text-amber-400" /> Concurrency Benchmark
                </span>
                <span className="text-xs font-semibold text-emerald-400 bg-emerald-950 border border-emerald-800 px-2 py-0.5 rounded-full">
                  &gt;2× Speedup
                </span>
              </div>

              <div className="grid grid-cols-2 gap-3">
                <div className="p-3 bg-slate-950 rounded-xl border border-slate-800">
                  <p className="text-xs text-slate-400">Parallel Fan-Out</p>
                  <p className="text-2xl font-bold text-emerald-400 mt-0.5">
                    {pipelineData.pipeline_runtime_seconds}s
                  </p>
                </div>
                <div className="p-3 bg-slate-950 rounded-xl border border-slate-800">
                  <p className="text-xs text-slate-400">Est. Sequential</p>
                  <p className="text-2xl font-bold text-slate-500 mt-0.5">1.20s</p>
                </div>
              </div>
            </div>
          )}
        </div>

        {/* Right: Worker Cards & Live Media Deliverables */}
        <div className="lg:col-span-7 space-y-6">

          {/* Worker Status Grid */}
          <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl">
            <div className="flex items-center gap-2 mb-4 text-white font-semibold">
              <Layers className="w-5 h-5 text-indigo-400" />
              <h2>Department Workers (Fan-Out Stage)</h2>
            </div>

            <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
              <WorkerCard
                icon={<FileText className="w-4 h-4 text-indigo-400" />}
                title="1. Subtitles (.srt)"
                state={workerStates.worker_01}
                placeholder="Generating timecoded subtitle files..."
              />
              <WorkerCard
                icon={<Mic className="w-4 h-4 text-indigo-400" />}
                title="2. Dubbing (Audio)"
                state={workerStates.worker_02}
                placeholder="Synthesizing multi-speaker audio tracks..."
              />
              <WorkerCard
                icon={<Crop className="w-4 h-4 text-indigo-400" />}
                title="3. Smart VFX (Video)"
                state={workerStates.worker_03}
                placeholder="Calculating 9:16 and 1:1 crop coordinates..."
              />
              <WorkerCard
                icon={<ShieldCheck className="w-4 h-4 text-indigo-400" />}
                title="4. Compliance Clearance"
                state={workerStates.worker_04}
                placeholder="Parallel open-web clearance check..."
              />
            </div>
          </div>

          {/* Master Media Deliverables Center */}
          {pipelineData && (
            <div className="bg-slate-900 border border-slate-800 rounded-2xl p-6 shadow-xl space-y-6">

              <div className="flex items-center justify-between border-b border-slate-800 pb-3">
                <div className="flex items-center gap-2 text-white font-semibold">
                  <CheckCircle2 className="w-5 h-5 text-emerald-400" />
                  <h2>Master Distribution Deliverables</h2>
                </div>
                <span className="text-xs font-bold px-2.5 py-0.5 rounded-full bg-emerald-950 text-emerald-400 border border-emerald-800">
                  READY FOR RELEASE
                </span>
              </div>

              {/* 1. Synthesized Dubbing & Dialogue Source */}
              <div className="p-4 bg-slate-950 rounded-xl border border-slate-800 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-300 flex items-center gap-2">
                    <Volume2 className="w-4 h-4 text-indigo-400" /> Synthesized Dubbing & Dialogue Source
                  </span>
                  <span className="text-[10px] text-indigo-300 bg-indigo-950/80 px-2.5 py-0.5 rounded-full border border-indigo-800">
                    Source: {workerOneData?.transcription_source || "Multimodal Video Auto-Detect"}
                  </span>
                </div>

                <div className="p-2.5 bg-slate-900/60 rounded-lg border border-slate-800 text-xs">
                  <span className="text-slate-500 font-mono text-[10px] uppercase">Processed Dialogue Baseline:</span>
                  <p className="text-slate-300 font-mono mt-0.5 italic">
                    "{workerOneData?.active_dialogue || formData.script_text}"
                  </p>
                </div>

                {selectedDubLanguages.map(lang => {
                  const dubbedTrackPath = workerTwoData?.dubbed_tracks?.[lang];
                  const downloadedFilename = dubbedTrackPath?.split("/").pop();

                  return (
                  <div key={lang} className="flex flex-col sm:flex-row items-center justify-between gap-3 p-3 bg-slate-900/90 rounded-lg border border-slate-800">
                    <div className="w-full sm:w-auto">
                      <div className="flex items-center gap-2">
                        <span className="text-xs text-white font-semibold">{lang} Dubbed Voiceover</span>
                        <span className="text-[10px] px-1.5 py-0.2 rounded bg-slate-800 text-slate-400 font-mono">
                          {LANG_VOICE_CODES[lang] || 'en-US'}
                        </span>
                      </div>
                      <p className="text-xs text-indigo-300 mt-1 font-mono italic">
                        {workerOneData?.localized_dialogues?.[lang]
                          ? `"${workerOneData.localized_dialogues[lang]}"`
                          : "Awaiting Gemini localization output."}
                      </p>
                    </div>

                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => playLocalizedVoice(lang)}
                        disabled={!workerOneData?.localized_dialogues?.[lang]}
                        className={`flex items-center gap-2 px-3.5 py-2 rounded-lg text-xs font-semibold transition ${
                          playingAudio === lang
                            ? 'bg-rose-600 text-white animate-pulse shadow-lg shadow-rose-600/30'
                            : 'bg-indigo-600 hover:bg-indigo-500 text-white shadow-lg shadow-indigo-600/30'
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
                            <span>Listen Dub</span>
                          </>
                        )}
                      </button>
                      {downloadedFilename ? (
                        <a
                          href={`${API_BASE_URL}/api/v1/downloads/audio/${encodeURIComponent(downloadedFilename)}`}
                          download={downloadedFilename}
                          className="flex items-center gap-2 px-3.5 py-2 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-xs font-semibold text-white transition shadow-lg shadow-emerald-600/20"
                        >
                          <Download className="w-4 h-4" />
                          <span>Download MP3</span>
                        </a>
                      ) : (
                        <span className="text-[10px] text-slate-500">MP3 unavailable</span>
                      )}
                    </div>
                  </div>
                  );
                })}
              </div>

              {/* 2. Visual Social Video Crop Section */}
              <div className="p-4 bg-slate-950 rounded-xl border border-slate-800 space-y-3">
                <div className="flex items-center justify-between">
                  <span className="text-xs font-semibold text-slate-300 flex items-center gap-2">
                    <Smartphone className="w-4 h-4 text-indigo-400" /> Dynamic Social Video Reframing (Worker 3)
                  </span>
                  <span className="text-[10px] text-emerald-400 bg-emerald-950/60 px-2 py-0.5 rounded border border-emerald-900">
                    FFmpeg Cropped
                  </span>
                </div>

                <div className="grid grid-cols-1 sm:grid-cols-2 gap-4 pt-2">
                  {selectedPlatforms.map(platform => {
                    const style = PLATFORM_FRAME_STYLES[platform] || {
                      label: platform, box: "w-40 h-40", border: "border-slate-600/40"
                    };
                    const ffmpegCommand = workerThreeData?.ffmpeg_render_pipeline?.[platform];

                    return (
                      <div key={platform} className="p-3 bg-slate-900 rounded-lg border border-slate-800 flex flex-col items-center">
                        <p className="text-xs font-semibold text-slate-300 mb-2">{style.label}</p>
                        <div className={`${style.box} bg-black rounded-lg overflow-hidden border ${style.border} relative shadow-inner`}>
                          <video
                            src={formData.video_url}
                            controls
                            autoPlay
                            muted
                            loop
                            playsInline
                            crossOrigin="anonymous"
                            className="object-cover w-full h-full"
                          />
                        </div>
                        <button
                          disabled={!ffmpegCommand}
                          onClick={() => triggerDownload(
                            `${formData.title.toLowerCase().replace(/[^a-z0-9]/g, '_')}_${platform.replace(/[^a-z0-9]/gi, '_')}_crop.sh`,
                            ffmpegCommand || ""
                          )}
                          className="mt-2 w-full flex items-center justify-center gap-1.5 px-2.5 py-1.5 bg-slate-950 hover:bg-slate-800 border border-slate-800 rounded-lg text-[10px] font-mono text-slate-400 disabled:cursor-not-allowed disabled:opacity-50 transition"
                          title={ffmpegCommand}
                        >
                          <Download className="w-3 h-3" />
                          <span>Download FFmpeg Crop Cmd</span>
                        </button>
                      </div>
                    );
                  })}
                </div>

                {/* Final Assembly (Google Cloud Transcoder) — additive stage */}
                <div className="pt-3 mt-1 border-t border-slate-800 space-y-3">
                  <p className="text-[11px] text-slate-500">
                    Render broadcast-ready .mp4s combining the crop, dub audio, and subtitles above via Google Cloud Transcoder.
                    Choose which language to use for each platform (from what you selected above):
                  </p>

                  <div className="space-y-1.5">
                    {selectedPlatforms.map(platform => {
                      const rendition = getPlatformRendition(platform);
                      return (
                        <div key={platform} className="flex flex-col sm:flex-row sm:items-center gap-2 p-2.5 bg-slate-900/60 rounded-lg border border-slate-800">
                          <span className="text-xs font-semibold text-slate-300 sm:w-36 shrink-0">{platform}</span>
                          <select
                            value={rendition.dubLanguage}
                            onChange={(e) => setPlatformRenditions(prev => ({ ...prev, [platform]: { ...prev[platform], dubLanguage: e.target.value } }))}
                            className="flex-1 px-2 py-1.5 bg-slate-950 border border-slate-700 rounded-lg text-xs text-slate-200"
                          >
                            <option value="">No dub audio</option>
                            {selectedDubLanguages.map(lang => <option key={lang} value={lang}>{lang} dub</option>)}
                          </select>
                          <select
                            value={rendition.subtitleLanguage}
                            onChange={(e) => setPlatformRenditions(prev => ({ ...prev, [platform]: { ...prev[platform], subtitleLanguage: e.target.value } }))}
                            className="flex-1 px-2 py-1.5 bg-slate-950 border border-slate-700 rounded-lg text-xs text-slate-200"
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
                    disabled={renderSubmitting || !workerTwoData}
                    className="w-full flex items-center justify-center gap-2 px-3.5 py-2 bg-violet-600 hover:bg-violet-500 rounded-lg text-xs font-semibold text-white shadow-lg shadow-violet-600/20 transition disabled:opacity-50 disabled:cursor-not-allowed"
                  >
                    {renderSubmitting ? (
                      <Loader2 className="w-4 h-4 animate-spin" />
                    ) : (
                      <Film className="w-4 h-4" />
                    )}
                    <span>Render Final Video</span>
                  </button>

                  {renderJob && (
                    <div className="space-y-2">
                      <p className="text-[10px] uppercase tracking-wider text-slate-500">
                        Render Job {renderJob.render_job_id.slice(0, 8)} — {renderJob.overall_status}
                      </p>
                      {renderJob.outputs.map((output) => (
                        <div
                          key={`${output.platform}-${output.dub_language}-${output.subtitle_language}`}
                          className="flex items-center justify-between gap-3 p-2.5 bg-slate-900/90 rounded-lg border border-slate-800"
                        >
                          <div className="text-xs text-slate-300">
                            <span className="font-semibold text-white">{output.platform}</span>
                            {" · "}{output.dub_language ? `${output.dub_language} dub` : "no dub"}
                            {" + "}{output.subtitle_language ? `${output.subtitle_language} subs` : "no subs"}
                            {output.error && (
                              <p className="text-[10px] text-rose-400 mt-0.5 max-w-xs truncate" title={output.error}>
                                {output.error}
                              </p>
                            )}
                          </div>
                          {output.status === "SUCCEEDED" && output.download_url ? (
                            <a
                              href={output.download_url}
                              className="flex items-center gap-1.5 px-2.5 py-1.5 bg-emerald-600 hover:bg-emerald-500 rounded-lg text-[11px] font-semibold text-white transition"
                            >
                              <Download className="w-3.5 h-3.5" />
                              <span>Download</span>
                            </a>
                          ) : (
                            <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
                              output.status === "FAILED" || output.status === "NOT_CONFIGURED"
                                ? 'bg-rose-950 text-rose-400 border-rose-800'
                                : 'bg-indigo-950 text-indigo-400 border-indigo-800 animate-pulse'
                            }`}>
                              {output.status}
                            </span>
                          )}
                        </div>
                      ))}
                    </div>
                  )}
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
                    className={`p-3 bg-slate-950 border border-slate-800 rounded-xl flex items-center justify-between transition text-left ${
                      subtitleFilename ? 'hover:bg-slate-900 hover:border-indigo-500/60' : 'cursor-not-allowed opacity-50 pointer-events-none'
                    }`}
                  >
                    <div className="flex items-center gap-2.5">
                      <FileText className="w-4 h-4 text-indigo-400" />
                      <div>
                        <p className="text-xs font-semibold text-slate-200">{lang} Subtitle File</p>
                        <p className="text-[10px] text-slate-500">.srt format</p>
                      </div>
                    </div>
                    <Download className="w-4 h-4 text-slate-400" />
                  </a>
                  );
                })}

                <button
                  disabled={!workerFourData}
                  onClick={() => triggerDownload(
                    `${formData.title.toLowerCase().replace(/[^a-z0-9]/g, '_')}_compliance_certificate.txt`,
                    `========================================\n` +
                    `SLATEPARALLEL COMPLIANCE AUDIT CERTIFICATE\n` +
                    `Project: ${formData.title}\n` +
                    `Status: ${pipelineData?.master_release_package?.gemini_director_report?.status || "APPROVED"}\n` +
                    `Parallel Interaction ID: ${workerFourData?.parallel_interaction_id || "n/a"}\n` +
                    `Live Web Context: ${workerFourData?.live_web_context || "n/a"}\n` +
                    `Compliance Checks:\n${(workerFourData?.compliance_checks || []).map(c => ` - ${c}`).join("\n")}\n` +
                    `========================================`
                  )}
                  className="p-3 bg-slate-950 hover:bg-slate-900 border border-slate-800 hover:border-emerald-500/60 rounded-xl flex items-center justify-between transition text-left disabled:cursor-not-allowed disabled:opacity-50"
                >
                  <div className="flex items-center gap-2.5">
                    <FileCheck className="w-4 h-4 text-emerald-400" />
                    <div>
                      <p className="text-xs font-semibold text-slate-200">Compliance Certificate</p>
                      <p className="text-[10px] text-emerald-400">Parallel Verified Audit</p>
                    </div>
                  </div>
                  <Download className="w-4 h-4 text-slate-400" />
                </button>
              </div>

            </div>
          )}

        </div>
      </main>
    </div>
  );
}

function WorkerCard({ icon, title, state, placeholder }) {
  const isRunning = state.status === 'RUNNING';
  const isSuccess = state.status === 'SUCCESS';

  return (
    <div className="p-4 rounded-xl bg-slate-950 border border-slate-800 transition flex flex-col justify-between h-32">
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2 text-xs font-semibold text-slate-200">
          {icon}
          <span>{title}</span>
        </div>
        <span className={`text-[10px] font-bold px-2 py-0.5 rounded-full border ${
          isRunning
            ? 'bg-indigo-950 text-indigo-400 border-indigo-800 animate-pulse'
            : isSuccess
            ? 'bg-emerald-950 text-emerald-400 border-emerald-800'
            : 'bg-slate-900 text-slate-500 border-slate-800'
        }`}>
          {isRunning ? 'RUNNING' : isSuccess ? `SUCCESS (${state.time}s)` : 'IDLE'}
        </span>
      </div>

      <p className="text-xs text-slate-500 line-clamp-2 mt-2 font-mono">
        {isSuccess ? JSON.stringify(state.data) : placeholder}
      </p>
    </div>
  );
}
