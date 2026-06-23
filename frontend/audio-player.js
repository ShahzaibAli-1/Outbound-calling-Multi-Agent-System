const OUTPUT_SAMPLE_RATE = 16000;

export function downsampleTo16k(float32Array, inputSampleRate) {
    if (inputSampleRate === OUTPUT_SAMPLE_RATE) {
        const pcm = new Int16Array(float32Array.length);
        for (let i = 0; i < float32Array.length; i += 1) {
            const sample = Math.max(-1, Math.min(1, float32Array[i]));
            pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        }
        return pcm.buffer;
    }
    const ratio = inputSampleRate / OUTPUT_SAMPLE_RATE;
    const newLength = Math.round(float32Array.length / ratio);
    const pcm = new Int16Array(newLength);
    let offset = 0;
    for (let i = 0; i < newLength; i += 1) {
        const nextOffset = Math.round((i + 1) * ratio);
        let sum = 0;
        let count = 0;
        for (let j = offset; j < nextOffset && j < float32Array.length; j += 1) {
            sum += float32Array[j];
            count += 1;
        }
        const sample = Math.max(-1, Math.min(1, sum / Math.max(count, 1)));
        pcm[i] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
        offset = nextOffset;
    }
    return pcm.buffer;
}

export function pcmBufferToBase64(buffer) {
    const bytes = new Uint8Array(buffer);
    let binary = "";
    for (let i = 0; i < bytes.length; i += 1) binary += String.fromCharCode(bytes[i]);
    return btoa(binary);
}

export class AgentAudioPlayer {
    constructor(sampleRate = OUTPUT_SAMPLE_RATE) {
        this.sampleRate = sampleRate;
        this.context = null;
        this.nextTime = 0;
        this.activeSources = [];
    }

    async ensureReady() {
        if (!this.context || this.context.state === "closed") {
            this.context = new AudioContext({ sampleRate: this.sampleRate });
        }
        if (this.context.state === "suspended") {
            await this.context.resume();
        }
    }

    async playBase64Pcm(base64Payload, sampleRate = this.sampleRate) {
        if (!base64Payload) return;
        await this.ensureReady();

        const raw = atob(base64Payload);
        const sampleCount = Math.floor(raw.length / 2);
        if (!sampleCount) return;

        const samples = new Int16Array(sampleCount);
        for (let i = 0; i < sampleCount; i += 1) {
            const lo = raw.charCodeAt(i * 2);
            const hi = raw.charCodeAt(i * 2 + 1);
            samples[i] = (hi << 8) | lo;
        }

        const audioBuffer = this.context.createBuffer(1, sampleCount, sampleRate);
        const channel = audioBuffer.getChannelData(0);
        for (let i = 0; i < sampleCount; i += 1) {
            channel[i] = samples[i] / 32768;
        }

        const source = this.context.createBufferSource();
        source.buffer = audioBuffer;
        source.connect(this.context.destination);

        const startAt = Math.max(this.context.currentTime, this.nextTime);
        source.start(startAt);
        this.nextTime = startAt + audioBuffer.duration;
        this.activeSources.push(source);
        source.onended = () => {
            this.activeSources = this.activeSources.filter((item) => item !== source);
        };
    }

    clear() {
        for (const source of this.activeSources) {
            try {
                source.stop();
            } catch {
                /* already stopped */
            }
        }
        this.activeSources = [];
        this.nextTime = 0;
    }

    async close() {
        this.clear();
        if (this.context) {
            await this.context.close();
            this.context = null;
        }
    }
}

export class MicCapture {
    constructor(onPcmChunk) {
        this.onPcmChunk = onPcmChunk;
        this.context = null;
        this.processor = null;
        this.stream = null;
    }

    async start() {
        this.context = new AudioContext();
        await this.context.resume();
        this.stream = await navigator.mediaDevices.getUserMedia({
            audio: {
                echoCancellation: true,
                noiseSuppression: true,
                autoGainControl: true,
            },
        });

        const source = this.context.createMediaStreamSource(this.stream);
        const processor = this.context.createScriptProcessor(4096, 1, 1);
        processor.onaudioprocess = (event) => {
            const input = event.inputBuffer.getChannelData(0);
            const pcmBuffer = downsampleTo16k(input, this.context.sampleRate);
            this.onPcmChunk(pcmBuffer);
        };

        const silentGain = this.context.createGain();
        silentGain.gain.value = 0;
        source.connect(processor);
        processor.connect(silentGain);
        silentGain.connect(this.context.destination);
        this.processor = processor;
    }

    stop() {
        this.processor?.disconnect();
        this.processor = null;
        this.stream?.getTracks().forEach((track) => track.stop());
        this.stream = null;
        if (this.context) {
            this.context.close();
            this.context = null;
        }
    }
}
