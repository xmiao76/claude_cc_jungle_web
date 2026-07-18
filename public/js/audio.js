// Sound effects via Web Audio. Browsers block audio until a user gesture,
// so the AudioContext is created/resumed lazily on first play attempt.

const SOUND_FILES = {
  move: 'assets/sounds/move.wav',
  capture: 'assets/sounds/capture.wav',
  win: 'assets/sounds/win.wav',
};

export class AudioManager {
  constructor() {
    this.muted = false;
    this._ctx = null;
    this._buffers = {};
    this._loading = null;
  }

  toggleMute() {
    this.muted = !this.muted;
    return this.muted;
  }

  async _ensureLoaded() {
    if (!this._ctx) {
      this._ctx = new (window.AudioContext || window.webkitAudioContext)();
    }
    if (this._ctx.state === 'suspended') {
      await this._ctx.resume().catch(() => {});
    }
    if (!this._loading) {
      this._loading = Promise.all(
        Object.entries(SOUND_FILES).map(async ([name, url]) => {
          try {
            const buf = await (await fetch(url)).arrayBuffer();
            this._buffers[name] = await this._ctx.decodeAudioData(buf);
          } catch {
            // Missing/undecodable sound: degrade silently, like the desktop app.
          }
        })
      );
    }
    return this._loading;
  }

  play(name) {
    if (this.muted) return;
    this._ensureLoaded().then(() => {
      const buffer = this._buffers[name];
      if (!buffer || this._ctx.state !== 'running') return;
      const src = this._ctx.createBufferSource();
      src.buffer = buffer;
      src.connect(this._ctx.destination);
      src.start();
    }).catch(() => {});
  }
}
