/**
 * VoiceCapture — orchestrates mic → AudioWorklet → VAD worker → WebSocket.
 *
 * Usage:
 *   const vc = new VoiceCapture(wsUrl);
 *   await vc.start();
 *   vc.stop();
 *
 * Events (set callbacks before calling start):
 *   onStateChange(state)          — 'idle' | 'speaking' | 'processing' | 'bot_speaking'
 *   onTranscript(text, isFinal)   — live transcript updates
 *   onControlMessage(data)        — incoming JSON control frames from server
 *   onError(err)                  — capture or WS error
 */

export type CaptureState = "idle" | "speaking" | "processing" | "bot_speaking";

const SAMPLE_RATE = 48000;
const RECONNECT_DELAY_MS = 2000;
const MAX_RECONNECTS = 5;

export class VoiceCapture {
  onStateChange: (state: CaptureState) => void = () => {};
  onTranscript: (text: string, isFinal: boolean) => void = () => {};
  onControlMessage: (data: Record<string, unknown>) => void = () => {};
  onError: (err: Error) => void = () => {};

  private videoRecorder: MediaRecorder | null = null;
  private recordedChunks: Blob[] = [];
  private wsUrl: string;
  private ws: WebSocket | null = null;
  private audioCtx: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private vadWorker: Worker | null = null;
  private ttsChunks: ArrayBuffer[] = [];
  private scheduledSources: AudioBufferSourceNode[] = [];
  private ttsFlushChain: Promise<void> = Promise.resolve();
  private nextPlayTime = 0;
  private audioGen = 0;
  private reconnectCount = 0;
  private stopped = false;
  private endingSession = false;
  private currentState: CaptureState = "idle";

  constructor(wsUrl: string) {
    this.wsUrl = wsUrl;
  }

  async start(): Promise<void> {
    this.stopped = false;
    this.endingSession = false;
    this.reconnectCount = 0;
    await this._initAudio();
    this._connectWs();
  }

  stop(): void {
    this.stopped = true;
    this._cleanup();
  }

  async endInterview(): Promise<void> {
    this.endingSession = true;
    await this._saveRecording();
    this._setState("processing");
    this._sendControl({ event: "end_session" });
  }

  /** Called when server signals bot is speaking — disables mic sending. */
  setBotSpeaking(speaking: boolean): void {
    this._setState(speaking ? "bot_speaking" : "idle");
    if (!speaking) {
      this.stopBotAudio();
    }
  }

  /**
   * Buffer one streamed TTS MP3 chunk. Individual streamed MP3 frames are not
   * independently decodable, so we accumulate them and decode the whole
   * sentence once the server signals `tts_sentence_complete`.
   */
  private _bufferTtsChunk(chunk: ArrayBuffer): void {
    this.ttsChunks.push(chunk);
  }

  /**
   * A sentence finished streaming: snapshot its chunks and queue them for
   * decode + gapless playback. Snapshotting synchronously (and chaining the
   * async work) keeps sentences from interleaving or racing on nextPlayTime.
   */
  private _onSentenceComplete(): void {
    if (this.ttsChunks.length === 0) return;
    const mp3 = this._concatBuffers(this.ttsChunks);
    this.ttsChunks = [];
    const gen = this.audioGen;
    this.ttsFlushChain = this.ttsFlushChain.then(() =>
      this._scheduleMp3(mp3, gen),
    );
  }

  private async _scheduleMp3(mp3: ArrayBuffer, gen: number): Promise<void> {
    if (!this.audioCtx || gen !== this.audioGen) return;
    if (this.audioCtx.state === "suspended") await this.audioCtx.resume();
    let decoded: AudioBuffer;
    try {
      decoded = await this.audioCtx.decodeAudioData(mp3);
    } catch {
      return; // whole sentence undecodable — skip rather than throw
    }
    if (gen !== this.audioGen) return; // barge-in happened during decode

    const source = this.audioCtx.createBufferSource();
    source.buffer = decoded;
    source.connect(this.audioCtx.destination);

    const startAt = Math.max(this.audioCtx.currentTime, this.nextPlayTime);
    source.start(startAt);
    this.nextPlayTime = startAt + decoded.duration;

    this.scheduledSources.push(source);
    source.onended = () => {
      this.scheduledSources = this.scheduledSources.filter((s) => s !== source);
    };
  }

  private _concatBuffers(buffers: ArrayBuffer[]): ArrayBuffer {
    const total = buffers.reduce((n, b) => n + b.byteLength, 0);
    const out = new Uint8Array(total);
    let offset = 0;
    for (const b of buffers) {
      out.set(new Uint8Array(b), offset);
      offset += b.byteLength;
    }
    return out.buffer;
  }

  stopBotAudio(): void {
    this.audioGen++; // invalidate any in-flight decode/schedule
    for (const source of this.scheduledSources) {
      try {
        source.stop();
      } catch {
        // already stopped
      }
    }
    this.scheduledSources = [];
    this.ttsChunks = [];
    this.nextPlayTime = 0;
  }

  private async _initAudio(): Promise<void> {
    this.mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: {
        sampleRate: SAMPLE_RATE,
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
      },
      video: {
        width: 1280,
        height: 720,
        facingMode: "user",
      },
    });

    this.audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    await this.audioCtx.resume();
    await this.audioCtx.audioWorklet.addModule(
      "/worklets/resampler.worklet.js",
    );
    this._initVideoRecording();

    const source = this.audioCtx.createMediaStreamSource(this.mediaStream);
    this.workletNode = new AudioWorkletNode(
      this.audioCtx,
      "resampler-processor",
    );

    // Worklet sends 320-sample Float32 chunks
    this.workletNode.port.onmessage = (evt: MessageEvent) => {
      const { pcm } = evt.data as { pcm: Float32Array };
      this.vadWorker?.postMessage({ type: "audio", pcm }, [pcm.buffer]);
    };

    source.connect(this.workletNode);
    // Do NOT connect workletNode to destination — we only want to process, not hear ourselves

    this._initVadWorker();
  }

  private _initVideoRecording(): void {
    if (!this.mediaStream) return;

    this.videoRecorder = new MediaRecorder(this.mediaStream, {
      mimeType: "video/webm;codecs=vp9,opus",
    });

    this.videoRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        this.recordedChunks.push(event.data);
      }
    };

    this.videoRecorder.start(10000);
  }

  private async _saveRecording(): Promise<void> {
    if (!this.recordedChunks.length) return;

    const blob = new Blob(this.recordedChunks, {
      type: "video/webm",
    });

    const formData = new FormData();

    formData.append("video", blob, `interview-${Date.now()}.webm`);

    await fetch("/api/interviews/upload-video", {
      method: "POST",
      body: formData,
    });
  }

  private _initVadWorker(): void {
    this.vadWorker = new Worker(
      new URL("../workers/vad.worker.ts", import.meta.url),
    );

    this.vadWorker.onmessage = (evt: MessageEvent) => {
      const { event, pcm } = evt.data as { event: string; pcm?: Int16Array };

      if (event === "speech_start") {
        this._setState("speaking");
        this._sendControl({ event: "speech_start" });
      } else if (event === "speech_end") {
        this._setState("processing");
        this._sendControl({ event: "speech_end" });
      } else if (event === "audio_chunk" && pcm) {
        // Only send during active states — bot_speaking means barge-in
        if (
          (this.currentState === "speaking" ||
            this.currentState === "bot_speaking") &&
          this.ws?.readyState === WebSocket.OPEN
        ) {
          this.ws.send(pcm.buffer);
        }
      }
    };

    this.vadWorker.onerror = (e) => {
      this.onError(new Error(`VAD worker error: ${e.message}`));
    };
  }

  private _connectWs(): void {
    if (this.stopped) return;

    this.ws = new WebSocket(this.wsUrl);
    this.ws.binaryType = "arraybuffer";

    this.ws.onopen = () => {
      this.reconnectCount = 0;
    };

    this.ws.onmessage = (evt: MessageEvent) => {
      if (evt.data instanceof ArrayBuffer) {
        // Binary = TTS audio chunk — buffer until the sentence completes
        this._bufferTtsChunk(evt.data);
      } else if (typeof evt.data === "string") {
        try {
          const data = JSON.parse(evt.data) as Record<string, unknown>;
          this._handleServerControl(data);
        } catch {
          // ignore malformed frames
        }
      }
    };

    this.ws.onclose = () => {
      if (!this.stopped && !this.endingSession) this._scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.onError(new Error("WebSocket connection error"));
    };
  }

  private _handleServerControl(data: Record<string, unknown>): void {
    const event = data.event as string;

    if (event === "transcript") {
      this.onTranscript(data.text as string, data.is_final as boolean);
    } else if (event === "turn") {
      const speaker = data.speaker as string;
      this._setState(speaker === "bot" ? "bot_speaking" : "idle");
    } else if (event === "barge_in") {
      this.stopBotAudio();
      this._setState("speaking");
    } else if (event === "ping") {
      this._sendControl({ event: "pong" });
    } else if (event === "tts_sentence_complete") {
      // Sentence fully streamed — decode and schedule it for playback
      this._onSentenceComplete();
    } else if (event === "interview_complete") {
      this.endingSession = true;
      this._setState("idle");
      this.onControlMessage(data);
    } else if (event === "evaluating") {
      this.endingSession = true;
      this._setState("processing");
      this.onControlMessage(data);
    } else {
      this.onControlMessage(data);
    }
  }

  private _sendControl(data: Record<string, unknown>): void {
    if (this.ws?.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(data));
    }
  }

  private _setState(next: CaptureState): void {
    if (this.currentState !== next) {
      this.currentState = next;
      this.onStateChange(next);
    }
  }

  private _scheduleReconnect(): void {
    if (this.stopped || this.reconnectCount >= MAX_RECONNECTS) return;
    this.reconnectCount++;
    const delay = RECONNECT_DELAY_MS * Math.pow(2, this.reconnectCount - 1);
    setTimeout(() => this._connectWs(), delay);
  }

  private _cleanup(): void {
    this.vadWorker?.terminate();
    this.vadWorker = null;

    this.workletNode?.disconnect();
    this.workletNode = null;

    if (this.videoRecorder && this.videoRecorder.state !== "inactive") {
      this.videoRecorder.stop();
    }

    this.mediaStream?.getTracks().forEach((t) => t.stop());
    this.mediaStream = null;

    this.audioCtx?.close();
    this.audioCtx = null;

    this.ws?.close();
    this.ws = null;
  }
}
