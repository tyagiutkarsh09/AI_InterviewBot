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

export type CaptureState = 'idle' | 'speaking' | 'processing' | 'bot_speaking';

const SAMPLE_RATE = 48000;
const RECONNECT_DELAY_MS = 2000;
const MAX_RECONNECTS = 5;

export class VoiceCapture {
  onStateChange: (state: CaptureState) => void = () => {};
  onTranscript: (text: string, isFinal: boolean) => void = () => {};
  onControlMessage: (data: Record<string, unknown>) => void = () => {};
  onError: (err: Error) => void = () => {};

  private wsUrl: string;
  private ws: WebSocket | null = null;
  private audioCtx: AudioContext | null = null;
  private mediaStream: MediaStream | null = null;
  private workletNode: AudioWorkletNode | null = null;
  private vadWorker: Worker | null = null;
  private botAudioQueue: ArrayBuffer[] = [];
  private botAudioSource: AudioBufferSourceNode | null = null;
  private reconnectCount = 0;
  private stopped = false;
  private currentState: CaptureState = 'idle';

  constructor(wsUrl: string) {
    this.wsUrl = wsUrl;
  }

  async start(): Promise<void> {
    this.stopped = false;
    this.reconnectCount = 0;
    await this._initAudio();
    this._connectWs();
  }

  stop(): void {
    this.stopped = true;
    this._cleanup();
  }

  /** Called when server signals bot is speaking — disables mic sending. */
  setBotSpeaking(speaking: boolean): void {
    this._setState(speaking ? 'bot_speaking' : 'idle');
    if (!speaking) {
      this.botAudioQueue = [];
    }
  }

  /** Play an incoming TTS audio chunk (MP3 ArrayBuffer). */
  async playAudioChunk(chunk: ArrayBuffer): Promise<void> {
    if (!this.audioCtx) return;
    try {
      const decoded = await this.audioCtx.decodeAudioData(chunk.slice(0));
      const source = this.audioCtx.createBufferSource();
      source.buffer = decoded;
      source.connect(this.audioCtx.destination);
      source.start();
      this.botAudioSource = source;
    } catch {
      // Chunk may be a partial MP3 — ignore decode errors for now
    }
  }

  stopBotAudio(): void {
    try {
      this.botAudioSource?.stop();
    } catch {
      // already stopped
    }
    this.botAudioSource = null;
    this.botAudioQueue = [];
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
      video: false,
    });

    this.audioCtx = new AudioContext({ sampleRate: SAMPLE_RATE });
    await this.audioCtx.audioWorklet.addModule('/worklets/resampler.worklet.js');

    const source = this.audioCtx.createMediaStreamSource(this.mediaStream);
    this.workletNode = new AudioWorkletNode(this.audioCtx, 'resampler-processor');

    // Worklet sends 320-sample Float32 chunks
    this.workletNode.port.onmessage = (evt: MessageEvent) => {
      const { pcm } = evt.data as { pcm: Float32Array };
      this.vadWorker?.postMessage({ type: 'audio', pcm }, [pcm.buffer]);
    };

    source.connect(this.workletNode);
    // Do NOT connect workletNode to destination — we only want to process, not hear ourselves

    this._initVadWorker();
  }

  private _initVadWorker(): void {
    this.vadWorker = new Worker(
      new URL('../workers/vad.worker.ts', import.meta.url)
    );

    this.vadWorker.onmessage = (evt: MessageEvent) => {
      const { event, pcm } = evt.data as { event: string; pcm?: Int16Array };

      if (event === 'speech_start') {
        this._setState('speaking');
        this._sendControl({ event: 'speech_start' });
      } else if (event === 'speech_end') {
        this._setState('processing');
        this._sendControl({ event: 'speech_end' });
      } else if (event === 'audio_chunk' && pcm) {
        // Only send during active states — bot_speaking means barge-in
        if (
          this.currentState === 'speaking' ||
          this.currentState === 'bot_speaking'
        ) {
          this.ws?.send(pcm.buffer);
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
    this.ws.binaryType = 'arraybuffer';

    this.ws.onopen = () => {
      this.reconnectCount = 0;
    };

    this.ws.onmessage = (evt: MessageEvent) => {
      if (evt.data instanceof ArrayBuffer) {
        // Binary = TTS audio chunk
        this.playAudioChunk(evt.data);
      } else if (typeof evt.data === 'string') {
        try {
          const data = JSON.parse(evt.data) as Record<string, unknown>;
          this._handleServerControl(data);
        } catch {
          // ignore malformed frames
        }
      }
    };

    this.ws.onclose = () => {
      if (!this.stopped) this._scheduleReconnect();
    };

    this.ws.onerror = () => {
      this.onError(new Error('WebSocket connection error'));
    };
  }

  private _handleServerControl(data: Record<string, unknown>): void {
    const event = data.event as string;

    if (event === 'transcript') {
      this.onTranscript(
        data.text as string,
        data.is_final as boolean
      );
    } else if (event === 'turn') {
      const speaker = data.speaker as string;
      this._setState(speaker === 'bot' ? 'bot_speaking' : 'idle');
    } else if (event === 'barge_in') {
      this.stopBotAudio();
      this._setState('speaking');
    } else if (event === 'ping') {
      this._sendControl({ event: 'pong' });
    } else if (event === 'tts_sentence_complete') {
      // Individual sentence done — next sentence may follow
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

    this.mediaStream?.getTracks().forEach((t) => t.stop());
    this.mediaStream = null;

    this.audioCtx?.close();
    this.audioCtx = null;

    this.ws?.close();
    this.ws = null;
  }
}
