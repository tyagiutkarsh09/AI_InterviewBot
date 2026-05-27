/**
 * AudioWorkletProcessor — resample 48kHz mono PCM → 16kHz, emit 320-sample chunks.
 *
 * Loaded via:  audioContext.audioWorklet.addModule('/worklets/resampler.worklet.js')
 *
 * Downsampling ratio: 3  (48000 / 16000)
 * A simple windowed moving-average low-pass filter precedes decimation to
 * suppress aliasing.  Window size = ratio = 3.
 *
 * Output: transferable { pcm: Float32Array(320) } messages to the main thread.
 */
class ResamplerProcessor extends AudioWorkletProcessor {
  constructor() {
    super();
    this._ratio = 3;           // 48kHz → 16kHz
    this._chunkSize = 320;     // 20ms @ 16kHz
    this._buffer = new Float32Array(this._chunkSize);
    this._bufferIdx = 0;
    this._accumulator = [];    // holds samples for filter window
  }

  process(inputs) {
    const input = inputs[0];
    if (!input || !input[0]) return true;

    // Mix to mono if stereo
    let mono;
    if (input.length > 1) {
      mono = new Float32Array(input[0].length);
      for (let i = 0; i < mono.length; i++) {
        mono[i] = (input[0][i] + input[1][i]) * 0.5;
      }
    } else {
      mono = input[0];
    }

    // Downsample with moving-average anti-aliasing
    for (let i = 0; i < mono.length; i++) {
      this._accumulator.push(mono[i]);

      if (this._accumulator.length === this._ratio) {
        // Average the window
        let sum = 0;
        for (const s of this._accumulator) sum += s;
        const sample = sum / this._ratio;
        this._accumulator = [];

        this._buffer[this._bufferIdx++] = sample;

        if (this._bufferIdx === this._chunkSize) {
          // Send a copy — postMessage is async so we must snapshot
          const chunk = this._buffer.slice(0);
          this.port.postMessage({ pcm: chunk }, [chunk.buffer]);
          this._bufferIdx = 0;
        }
      }
    }

    return true; // keep processor alive
  }
}

registerProcessor('resampler-processor', ResamplerProcessor);
