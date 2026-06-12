/**
 * VAD Web Worker — Silero VAD via ONNX Runtime Web.
 *
 * Receives: { type: 'audio', pcm: Float32Array }   — 320 samples @ 16kHz
 * Sends:    { event: 'speech_start' }
 *            { event: 'speech_end' }
 *            { event: 'audio_chunk', pcm: Int16Array }
 *
 * State machine:
 *   SILENT → (prob > 0.5) → SPEECH → (prob < 0.3) → TRAILING
 *   TRAILING → (silenceFrames >= 40) → SILENT  (800ms silence)
 *
 * Falls back to energy-based VAD if ONNX runtime is unavailable.
 */

const VAD_STATES = { SILENT: 0, SPEECH: 1, TRAILING: 2 } as const;
type VadState = typeof VAD_STATES[keyof typeof VAD_STATES];

const SPEECH_THRESHOLD = 0.5;
const SILENCE_THRESHOLD = 0.3;
const SILENCE_FRAMES_THRESHOLD = 40; // 40 × 20ms = 800ms
const ENERGY_THRESHOLD = 0.005;       // fallback energy threshold

let state: VadState = VAD_STATES.SILENT;
let silenceFrames = 0;

// ONNX session — loaded lazily
let ortSession: unknown = null;
let useEnergyFallback = false;

// Silero VAD hidden states. Typed as ArrayBufferLike because ONNX Runtime
// returns tensor data backed by ArrayBufferLike (possibly SharedArrayBuffer).
let h0: Float32Array<ArrayBufferLike> = new Float32Array(2 * 1 * 64);
let c0: Float32Array<ArrayBufferLike> = new Float32Array(2 * 1 * 64);

async function loadOnnxModel(): Promise<void> {
  try {
    // Dynamic import — requires onnxruntime-web installed
    // webpackIgnore: skip bundling — WASM package crashes SWC worker; runtime failure triggers energy fallback
    const ort = await import(/* webpackIgnore: true */ 'onnxruntime-web');
    ort.env.wasm.wasmPaths = '/onnx/';
    ortSession = await ort.InferenceSession.create('/models/silero_vad.onnx', {
      executionProviders: ['wasm'],
    });
  } catch {
    useEnergyFallback = true;
  }
}

async function runSileroVAD(pcm: Float32Array): Promise<number> {
  if (useEnergyFallback || !ortSession) return computeEnergy(pcm);

  try {
    const ort = await import(/* webpackIgnore: true */ 'onnxruntime-web');
    const session = ortSession as InstanceType<typeof ort.InferenceSession>;

    const inputTensor = new ort.Tensor('float32', pcm, [1, pcm.length]);
    const srTensor = new ort.Tensor('int64', BigInt64Array.from([BigInt(16000)]), [1]);
    const h0Tensor = new ort.Tensor('float32', h0, [2, 1, 64]);
    const c0Tensor = new ort.Tensor('float32', c0, [2, 1, 64]);

    const results = await session.run({
      input: inputTensor,
      sr: srTensor,
      h: h0Tensor,
      c: c0Tensor,
    });

    // Update LSTM hidden states
    h0 = results['hn'].data as Float32Array;
    c0 = results['cn'].data as Float32Array;

    return (results['output'].data as Float32Array)[0];
  } catch {
    return computeEnergy(pcm);
  }
}

function computeEnergy(pcm: Float32Array): number {
  let sum = 0;
  for (let i = 0; i < pcm.length; i++) sum += pcm[i] * pcm[i];
  const rms = Math.sqrt(sum / pcm.length);
  // Map RMS to a pseudo-probability: sigmoid-like curve centred on threshold
  return rms > ENERGY_THRESHOLD ? Math.min(rms / ENERGY_THRESHOLD * 0.6, 1) : rms / ENERGY_THRESHOLD * 0.4;
}

function float32ToInt16(pcm: Float32Array): Int16Array<ArrayBuffer> {
  const out = new Int16Array(pcm.length);
  for (let i = 0; i < pcm.length; i++) {
    const s = Math.max(-1, Math.min(1, pcm[i]));
    out[i] = s < 0 ? s * 0x8000 : s * 0x7fff;
  }
  return out;
}

async function handleChunk(pcm: Float32Array): Promise<void> {
  const prob = await runSileroVAD(pcm);

  switch (state) {
    case VAD_STATES.SILENT:
      if (prob > SPEECH_THRESHOLD) {
        state = VAD_STATES.SPEECH;
        silenceFrames = 0;
        self.postMessage({ event: 'speech_start' });
      }
      break;

    case VAD_STATES.SPEECH:
      if (prob < SILENCE_THRESHOLD) {
        state = VAD_STATES.TRAILING;
        silenceFrames = 0;
      }
      break;

    case VAD_STATES.TRAILING:
      if (prob > SPEECH_THRESHOLD) {
        // Resumed speaking
        state = VAD_STATES.SPEECH;
        silenceFrames = 0;
      } else {
        silenceFrames++;
        if (silenceFrames >= SILENCE_FRAMES_THRESHOLD) {
          state = VAD_STATES.SILENT;
          self.postMessage({ event: 'speech_end' });
          return;
        }
      }
      break;
  }

  // Only forward audio when candidate is actively speaking
  if (state === VAD_STATES.SPEECH || state === VAD_STATES.TRAILING) {
    const int16 = float32ToInt16(pcm);
    // `self` is typed as Window under the DOM lib; cast to Worker to reach the
    // postMessage(message, transfer) overload for zero-copy transfer.
    (self as unknown as Worker).postMessage(
      { event: 'audio_chunk', pcm: int16 },
      [int16.buffer],
    );
  }
}

self.onmessage = async (evt: MessageEvent) => {
  const { type, pcm } = evt.data as { type: string; pcm: Float32Array };
  if (type === 'audio') {
    await handleChunk(pcm);
  } else if (type === 'reset') {
    state = VAD_STATES.SILENT;
    silenceFrames = 0;
    h0 = new Float32Array(2 * 1 * 64);
    c0 = new Float32Array(2 * 1 * 64);
  }
};

// Load ONNX model on worker startup
loadOnnxModel();
